"""LiveKit voice agent for Apprentice.

Run:  python agent/worker.py dev
STT: ElevenLabs Scribe v2 realtime | LLM: Claude (FAST_MODEL) or OpenAI-compatible | TTS: ElevenLabs
Avatar: Anam or Synthesia (AVATAR_PROVIDER, AVATAR_ENABLED=true), falling back to voice-only automatically.
Tools: end_interview, flag_important, request_visual.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from apprentice import engine, interviewer, store, tools, vision  # noqa: E402  (loads .env)
from apprentice.models import Session  # noqa: E402
from livekit import agents  # noqa: E402
from livekit.agents import (  # noqa: E402
    Agent, AgentServer, AgentSession, JobContext, JobProcess, llm, room_io)
from livekit.agents.voice.generation import update_instructions  # noqa: E402
from livekit import rtc  # noqa: E402
from livekit.agents.utils.images import EncodeOptions, ResizeOptions, encode  # noqa: E402
from livekit.agents import RunContext, function_tool  # noqa: E402
from livekit.plugins import elevenlabs, silero  # noqa: E402

from avatar import start_avatar  # noqa: E402

# the LiveKit ElevenLabs plugin reads ELEVEN_API_KEY
if os.getenv("ELEVENLABS_API_KEY") and not os.getenv("ELEVEN_API_KEY"):
    os.environ["ELEVEN_API_KEY"] = os.environ["ELEVENLABS_API_KEY"]

log = logging.getLogger("apprentice.worker")
log.setLevel(logging.INFO)


# ---------------------------------------------------------------- model factories
def make_llm():
    provider = os.getenv("FAST_PROVIDER", "anthropic")
    model = os.getenv("FAST_MODEL", "claude-haiku-5-5")
    if provider == "openai_compatible":
        from livekit.plugins import openai
        return openai.LLM(model=model, base_url=os.getenv("OPENAI_COMPAT_BASE_URL"),
                          api_key=os.getenv("OPENAI_COMPAT_API_KEY"))
    from livekit.plugins import anthropic

    class FastClaude(anthropic.LLM):
        """Injects low-latency settings (no thinking, low effort) into every request."""

        def chat(self, *, chat_ctx, tools=None, extra_kwargs=agents.NOT_GIVEN, **kw):
            extra = dict(extra_kwargs) if agents.utils.is_given(extra_kwargs) else {}
            extra.setdefault("output_config", {"effort": "low"})
            if "haiku" in model:
                extra.setdefault("thinking", {"type": "disabled"})
            return super().chat(chat_ctx=chat_ctx, tools=tools, extra_kwargs=extra, **kw)

    return FastClaude(model=model, max_tokens=160)


def make_stt():
    return elevenlabs.STT(model=os.getenv("ELEVENLABS_STT_MODEL", "scribe_v2_realtime"), language_code="en")


def make_tts():
    kw = {"model": os.getenv("ELEVENLABS_TTS_MODEL", "eleven_flash_v2_5")}
    if os.getenv("ELEVENLABS_VOICE_ID"):
        kw["voice_id"] = os.environ["ELEVENLABS_VOICE_ID"]
    return elevenlabs.TTS(**kw)


def make_turn_handling() -> dict:
    delay = float(os.getenv("TURN_END_DELAY_S", "1.2"))
    th: dict = {
        "endpointing": {"min_delay": delay, "max_delay": max(6.0, delay * 3)},
        # instructions change every turn, so preemptive replies would be discarded anyway
        "preemptive_generation": {"enabled": False},
    }
    try:
        from livekit.agents import inference
        th["turn_detection"] = inference.TurnDetector()
    except Exception as e:  # fall back to VAD-only endpointing
        log.warning("turn detector unavailable (%s); using VAD endpointing", e)
        th["turn_detection"] = "vad"
    return th


def noise_cancellation():
    try:
        from livekit.plugins import noise_cancellation as nc
        return nc.BVC()
    except Exception as e:
        log.warning("noise cancellation unavailable: %s", e)
        return None


# ---------------------------------------------------------------- M7: expert camera
class VideoWatcher:
    """Keeps the latest frame of the expert's camera."""

    def __init__(self) -> None:
        self.latest: rtc.VideoFrame | None = None
        self.latest_at = 0.0
        self._tasks: set[asyncio.Task] = set()

    def attach(self, room: rtc.Room) -> None:
        @room.on("track_subscribed")
        def _sub(track, pub, participant) -> None:
            if track.kind == rtc.TrackKind.KIND_VIDEO and participant.identity.startswith("expert-"):
                log.info("expert camera subscribed")
                t = asyncio.create_task(self._read(track))
                self._tasks.add(t)
                t.add_done_callback(self._tasks.discard)

        for p in room.remote_participants.values():  # camera already on when we joined
            for pub in p.track_publications.values():
                if pub.track is not None:
                    _sub(pub.track, pub, p)

    async def _read(self, track) -> None:
        async for ev in rtc.VideoStream(track):
            self.latest, self.latest_at = ev.frame, time.time()

    def jpeg(self) -> bytes | None:
        if self.latest is None or time.time() - self.latest_at > 3:
            return None
        return encode(self.latest, EncodeOptions(format="JPEG", resize_options=ResizeOptions(
            width=1024, height=768, strategy="scale_aspect_fit")))


# ---------------------------------------------------------------- the agent
class Apprentice(Agent):
    def __init__(self, s: Session) -> None:
        ctx = llm.ChatContext.empty()
        for t in s.transcript:  # resume an existing conversation
            if t.speaker == "agent":
                ctx.add_message(role="assistant", content=t.text)
            elif t.speaker == "expert":
                ctx.add_message(role="user", content=t.text)
        super().__init__(instructions=interviewer.build_instructions(s, voice=True), chat_ctx=ctx)
        self.s = s
        self.runner = engine.runner(s, on_log=lambda line: log.info("[analyst] %s", line))
        self.turn_done_at: float | None = None
        self.on_turn = None   # set in entrypoint: arms the no-reply watchdog
        self.video: VideoWatcher | None = None

    async def llm_node(self, chat_ctx, tools, model_settings):
        """Stream the LLM reply but stop speaking after the first question mark (one question per turn).
        Tool-call chunks always pass through."""
        done_talking = self.s.phase == "done"
        cut = False
        async for chunk in Agent.default.llm_node(self, chat_ctx, tools, model_settings):
            text = chunk if isinstance(chunk, str) else getattr(getattr(chunk, "delta", None), "content", None)
            has_tools = bool(getattr(getattr(chunk, "delta", None), "tool_calls", None))
            if cut:
                if has_tools:
                    chunk.delta.content = None
                    yield chunk
                continue
            if text and not done_talking and "?" in text:
                head = text[: text.index("?") + 1]
                if isinstance(chunk, str):
                    chunk = head
                else:
                    chunk.delta.content = head
                cut = True
            yield chunk

    async def on_user_turn_completed(self, turn_ctx: llm.ChatContext, new_message: llm.ChatMessage) -> None:
        await handle_expert_turn(self, turn_ctx, new_message.text_content or "")

    # ---- function tools (shown on the panel as event chips) ----
    @function_tool
    async def flag_important(self, context: RunContext, summary: str, slot: str = "mistakes") -> str:
        """Pin a key insight when the expert stresses something ("this is the key thing", "the most
        important part", "never ever do X"). `summary`: one standalone sentence a beginner understands.
        `slot`: one of goal, steps, decisions, cues, mistakes, edge_cases, tools_terms, quality_check."""
        return tools.flag_important(self.s, summary, slot)

    @function_tool
    async def request_visual(self, context: RunContext, reason: str) -> str:
        """Ask the expert to show something on camera when seeing it would help you understand
        (a texture, a colour, a technique, a tool). `reason`: what you want to see and why."""
        return tools.request_visual(self.s, reason)

    @function_tool
    async def end_interview(self, context: RunContext) -> str:
        """End the interview. Call this only when the interview phase is 'done', or the expert
        clearly says they have to stop."""
        return tools.end_interview(self.s)


EMPHASIS = __import__("re").compile(
    r"\b(key thing|most important|the important (thing|bit|part)|crucial|critical|never ever|always,? always|"
    r"the one thing|whatever you do|golden rule|number one rule|biggest mistake)\b", __import__("re").I)


async def handle_expert_turn(agent: Apprentice, turn_ctx: llm.ChatContext, text: str) -> None:
    """The integration point: record turn -> analyst in background -> refresh instructions."""
    t0 = time.time()
    text = text.strip()
    if not text:
        return
    s = agent.s
    engine.add_expert_turn(s, text)          # 1. append + save
    await maybe_look(agent, turn_ctx, text)   # M7: "look at this" -> grab frame -> Claude vision
    if EMPHASIS.search(text):                 # nudge the LLM to use its tool on stressed points
        turn_ctx.add_message(role="system", content=(
            "The expert just stressed a key point. Call flag_important with a one-sentence standalone "
            "summary of it, then ask your one question."))
    agent.runner.trigger()                    # 2. analyst in background (never awaited)
    instr = interviewer.build_instructions(s, voice=True)  # 3. fresh context with current candidates
    update_instructions(turn_ctx, instructions=instr, add_if_missing=True)  # this reply
    await agent.update_instructions(instr)    # future replies
    agent.turn_done_at = time.time()
    if agent.on_turn:
        agent.on_turn(agent.turn_done_at)
    log.info("turn hook %.0fms (phase=%s, candidates=%d)", 1000 * (time.time() - t0), s.phase,
             len([g for g in s.gaps if g.status in ("open", "asked")]))


async def maybe_look(agent: Apprentice, turn_ctx: llm.ChatContext, text: str) -> None:
    if not (agent.video and vision.wants_to_show(text)):
        return
    jpeg = agent.video.jpeg()
    if not jpeg:
        return
    t0 = time.time()
    try:
        out = await asyncio.wait_for(vision.describe(agent.s, jpeg, text), timeout=8)
    except Exception as e:
        log.warning("vision failed: %s", e)
        return
    vision.record(agent.s, jpeg, out["description"])
    store.save(agent.s)
    turn_ctx.add_message(role="user", content=(
        f"[What you can see on the expert's camera: {out['description']}] "
        f"(A good question about it: {out['question']})"))
    log.info("vision %.1fs: %s", time.time() - t0, out["description"][:100])


# ---------------------------------------------------------------- session bootstrap
def resolve_session(ctx: JobContext) -> Session:
    sid = None
    try:
        sid = json.loads(ctx.job.metadata or "{}").get("session_id")
    except Exception:
        pass
    if not sid and ctx.room.name.startswith("apprentice-"):
        sid = ctx.room.name.split("-")[1]
    s = store.load(sid) if sid else None
    if s is None:
        log.warning("session %s not found; creating a fresh one", sid)
        s = engine.new_session("", "Expert", mode="voice")
    s.mode = "voice"
    store.save(s)
    return s


def prewarm(proc: JobProcess) -> None:
    proc.userdata["vad"] = silero.VAD.load()


def prewarm_all(proc: JobProcess) -> None:
    prewarm(proc)
    import anthropic.types  # noqa: F401  (avoid a ~2 s import stall on the first reply)


# keep one process warm so a call starts instantly (dev mode defaults to 0)
server = AgentServer(setup_fnc=prewarm_all, num_idle_processes=1)


@server.rtc_session(agent_name="apprentice")
async def entrypoint(ctx: JobContext) -> None:
    await ctx.connect()
    s = resolve_session(ctx)
    log.info("joined room %s for session %s (%s)", ctx.room.name, s.id, s.objective)

    session = AgentSession(
        stt=make_stt(),
        llm=make_llm(),
        tts=make_tts(),
        vad=ctx.proc.userdata.get("vad"),
        turn_handling=make_turn_handling(),
    )
    agent = Apprentice(s)
    agent.video = VideoWatcher()
    agent.video.attach(ctx.room)

    @session.on("conversation_item_added")
    def _on_item(ev) -> None:
        item = ev.item
        if getattr(item, "role", None) != "assistant":
            return
        text = (item.text_content or "").strip()
        last = s.last_agent_turn()
        # skip repeats, including an interrupted (truncated) copy of the line we just recorded
        if text and not (last and last.text.startswith(text)):
            engine.add_agent_turn(s, text)
        m = getattr(item, "metrics", None)
        if m:
            log.info("TURN METRICS %s", {k: round(v, 2) if isinstance(v, float) else v for k, v in dict(m).items()})

    @session.on("function_tools_executed")
    def _on_tools(ev) -> None:
        for c in ev.function_calls:
            log.info("TOOL CALL %s(%s)", c.name, c.arguments)

    @session.on("agent_state_changed")
    def _on_state(ev) -> None:
        if ev.new_state == "speaking" and agent.turn_done_at:
            log.info("LATENCY expert-turn-end -> agent speaking: %.2fs", time.time() - agent.turn_done_at)
            agent.turn_done_at = None

    @session.on("error")
    def _on_error(ev) -> None:
        msg = f"voice pipeline error: {getattr(ev, 'error', ev)}"
        log.error(msg)
        s.log(msg[:300], "error")
        store.save(s)

    # ---- resilience: the avatar can die mid-call (Anam session/concurrency limits) and
    # take all agent audio with it. Detect it, mark the session voice-only and end this
    # job; the browser auto-reconnects and the next job resumes without the avatar.
    avatar_ids = {"anam-avatar-agent", "synthesia-avatar-agent"}
    state = {"avatar": None, "lost": False}

    def avatar_lost(reason: str) -> None:
        if state["lost"]:
            return
        state["lost"] = True
        log.warning("AVATAR LOST (%s) -> ending call so the browser reconnects voice-only", reason)
        s.avatar_disabled = True
        s.log(f"Avatar dropped ({reason}). Reconnecting voice-only…", "error")
        store.save(s)
        ctx.shutdown(reason=f"avatar lost: {reason}")

    @ctx.room.on("participant_disconnected")
    def _on_left(p) -> None:
        if state["avatar"] and p.identity in avatar_ids:
            avatar_lost("avatar left the room")

    reply_timeout = float(os.getenv("REPLY_TIMEOUT_S", "8"))

    async def watchdog(token: float) -> None:
        await asyncio.sleep(reply_timeout)
        if agent.turn_done_at != token or state["lost"]:
            return  # it spoke, or a newer turn superseded this one
        if getattr(session, "user_state", "") == "speaking":
            return
        if state["avatar"]:
            avatar_lost(f"no reply audio for {reply_timeout:.0f}s")
        else:
            log.warning("WATCHDOG no reply after %.0fs -> re-prompting", reply_timeout)
            s.log("Apprentice went quiet; nudged it to reply", "info")
            agent.turn_done_at = time.time()
            session.generate_reply(instructions="Respond to the expert's last message with one short question.")

    watch_tasks: set = set()

    def arm(token: float) -> None:
        t = asyncio.create_task(watchdog(token))
        watch_tasks.add(t)
        t.add_done_callback(watch_tasks.discard)

    agent.on_turn = arm

    if s.avatar_disabled:
        log.info("avatar disabled for this session after an earlier drop -> voice-only")
        provider = None
    else:
        provider = await start_avatar(session, ctx.room)
    state["avatar"] = provider
    s.log(f"avatar: {provider}" if provider else "avatar: off (voice-only)", "info")

    nc = noise_cancellation()
    audio_in = room_io.AudioInputOptions(noise_cancellation=nc) if nc else room_io.AudioInputOptions()
    await session.start(room=ctx.room, agent=agent, room_options=room_io.RoomOptions(audio_input=audio_in))

    if s.expert_turn_count() == 0:
        opening = s.last_agent_turn().text if s.last_agent_turn() else interviewer.opening_line(s)
        session.say(opening, allow_interruptions=False)
    else:
        session.generate_reply(instructions="Welcome the expert back in one short sentence, then ask your next question.")


if __name__ == "__main__":
    agents.cli.run_app(server)
