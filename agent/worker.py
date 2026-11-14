"""LiveKit voice agent for Apprentice.

Run:  python agent/worker.py dev
STT: Deepgram | LLM: Claude (FAST_MODEL) or OpenAI-compatible | TTS: Deepgram Aura or ElevenLabs
Avatar: Synthesia (AVATAR_ENABLED=true), falling back to voice-only automatically.
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

from apprentice import engine, interviewer, store, vision  # noqa: E402  (loads .env)
from apprentice.models import Session  # noqa: E402
from livekit import agents  # noqa: E402
from livekit.agents import (  # noqa: E402
    Agent, AgentServer, AgentSession, JobContext, JobProcess, llm, room_io)
from livekit.agents.voice.generation import update_instructions  # noqa: E402
from livekit import rtc  # noqa: E402
from livekit.agents.utils.images import EncodeOptions, ResizeOptions, encode  # noqa: E402
from livekit.plugins import deepgram, silero  # noqa: E402

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


def make_tts():
    if os.getenv("TTS_PROVIDER", "deepgram") == "elevenlabs" and os.getenv("ELEVENLABS_API_KEY"):
        from livekit.plugins import elevenlabs
        return elevenlabs.TTS()
    return deepgram.TTS(model=os.getenv("DEEPGRAM_TTS_MODEL", "aura-2-thalia-en"))


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
        super().__init__(instructions=interviewer.build_instructions(s), chat_ctx=ctx)
        self.s = s
        self.runner = engine.runner(s, on_log=lambda line: log.info("[analyst] %s", line))
        self.turn_done_at: float | None = None
        self.video: VideoWatcher | None = None

    async def on_user_turn_completed(self, turn_ctx: llm.ChatContext, new_message: llm.ChatMessage) -> None:
        await handle_expert_turn(self, turn_ctx, new_message.text_content or "")


async def handle_expert_turn(agent: Apprentice, turn_ctx: llm.ChatContext, text: str) -> None:
    """The integration point: record turn -> analyst in background -> refresh instructions."""
    t0 = time.time()
    text = text.strip()
    if not text:
        return
    s = agent.s
    engine.add_expert_turn(s, text)          # 1. append + save
    await maybe_look(agent, turn_ctx, text)   # M7: "look at this" -> grab frame -> Claude vision
    agent.runner.trigger()                    # 2. analyst in background (never awaited)
    instr = interviewer.build_instructions(s) # 3. fresh context with current candidates
    update_instructions(turn_ctx, instructions=instr, add_if_missing=True)  # this reply
    await agent.update_instructions(instr)    # future replies
    agent.turn_done_at = time.time()
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


async def start_avatar(session: AgentSession, room) -> bool:
    if os.getenv("AVATAR_ENABLED", "false").lower() != "true":
        log.info("avatar disabled (AVATAR_ENABLED=false) -> voice-only")
        return False
    avatar_id = os.getenv("SYNTHESIA_AVATAR_ID")
    if not (os.getenv("SYNTHESIA_API_KEY") and avatar_id):
        log.warning("AVATAR_ENABLED but SYNTHESIA_API_KEY/SYNTHESIA_AVATAR_ID missing -> voice-only")
        return False
    t0 = time.time()
    try:
        from livekit.plugins import synthesia
        avatar = synthesia.AvatarSession(synthesia.AvatarConfig(avatar_ids=[avatar_id]))
        await asyncio.wait_for(avatar.start(session, room=room), timeout=float(os.getenv("AVATAR_TIMEOUT_S", "25")))
        log.info("Synthesia avatar started in %.1fs", time.time() - t0)
        return True
    except Exception as e:
        log.warning("AVATAR FAILED (%s: %s) -> falling back to voice-only", type(e).__name__, e)
        return False


def prewarm(proc: JobProcess) -> None:
    proc.userdata["vad"] = silero.VAD.load()


server = AgentServer(setup_fnc=prewarm)


@server.rtc_session(agent_name="apprentice")
async def entrypoint(ctx: JobContext) -> None:
    await ctx.connect()
    s = resolve_session(ctx)
    log.info("joined room %s for session %s (%s)", ctx.room.name, s.id, s.objective)

    session = AgentSession(
        stt=deepgram.STT(model="nova-3"),
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
        if text and not (last and last.text == text):
            engine.add_agent_turn(s, text)

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

    await start_avatar(session, ctx.room)

    nc = noise_cancellation()
    audio_in = room_io.AudioInputOptions(noise_cancellation=nc) if nc else room_io.AudioInputOptions()
    await session.start(room=ctx.room, agent=agent, room_options=room_io.RoomOptions(audio_input=audio_in))

    if s.expert_turn_count() == 0:
        opening = s.last_agent_turn().text if s.last_agent_turn() else interviewer.opening_line(s)
        session.say(opening)
    else:
        session.generate_reply(instructions="Welcome the expert back in one short sentence, then ask your next question.")


if __name__ == "__main__":
    agents.cli.run_app(server)
