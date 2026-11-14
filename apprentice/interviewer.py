"""Fast loop: interviewer prompt builder + text-mode reply."""
from __future__ import annotations

from pathlib import Path

from . import llm, selector
from .checklist import CHECKLIST
from .models import Session

TEMPLATE = (Path(__file__).parent / "prompts" / "interviewer.md").read_text()

PHASE_GUIDANCE = {
    "opening": ("First establish the goal (what doing this well achieves). Then ask for a high-level, "
                "end-to-end walkthrough: \"Can you walk me through the whole thing from start to finish?\" "
                "Do NOT probe details yet; just note them for later."),
    "deep_dive": ("Dig in. Chase vague phrases, hidden steps, missing reasons, failure stories and the "
                  "concrete cues the expert relies on."),
    "wrap_up": ("Wrap up. Ask one of these (whichever you haven't asked yet): \"If a beginner remembered only "
                "one thing, what should it be?\" or \"What haven't I asked that I should have?\""),
    "done": ("The interview is over. Thank the expert warmly and specifically in one or two sentences. "
             "Do not ask any question."),
}


def summary(s: Session) -> str:
    if not s.items:
        return "Nothing yet."
    cov = ", ".join(f"{CHECKLIST[k]['label']}={v.status}" for k, v in s.coverage.items())
    recent = "; ".join(i.content for i in s.items[-3:])
    return f"{len(s.items)} items. Coverage: {cov}.\nMost recent: {recent}"


def candidates_text(s: Session) -> str:
    if s.phase in ("wrap_up", "done"):
        return "(none — follow the phase guidance)"
    c = selector.candidates(s)
    if not c:
        return "(none yet — follow the expert's lead and probe their latest answer)"
    return "\n".join(
        f"{n}. [{g.slot}{', topic: ' + g.topic if g.topic else ''}] anchor: \"{g.anchor_quote}\" -> {g.question}"
        for n, g in enumerate(c, 1))


VOICE_TOOLS = """
## Tools (use them; they show up live on the expert's screen)
- flag_important(summary, slot): when the expert stresses something ("the key thing is…", "never…", "most important…"), call it with a one-sentence standalone summary, then keep interviewing.
- request_visual(reason): when seeing the thing would really help (a texture, colour, technique, tool), call it, then ask them to show it on camera.
- end_interview(): ONLY when the phase is "done" or the expert says they must stop. Then say a warm goodbye.
Never read tool names aloud."""


def build_instructions(s: Session, voice: bool = False) -> str:
    return (TEMPLATE.replace("{{expert_name}}", s.expert_name or "the expert")
            .replace("{{objective}}", s.objective or "(they will tell you)")
            .replace("{{phase}}", s.phase)
            .replace("{{phase_guidance}}", PHASE_GUIDANCE[s.phase])
            .replace("{{summary}}", summary(s))
            .replace("{{candidates}}", candidates_text(s))) + (VOICE_TOOLS if voice else "")


def opening_line(s: Session) -> str:
    name = f" {s.expert_name}" if s.expert_name and s.expert_name != "Expert" else ""
    if s.objective:
        return (f"Hi{name}, thanks so much for teaching me. I'd love to learn {s.objective.rstrip('.?').lower()}"
                f" — to start, what does doing it really well look like?")
    return f"Hi{name}, thanks so much for doing this. What are you going to teach me today?"


async def reply(s: Session) -> str:
    return await llm.complete("fast", build_instructions(s), llm.to_chat(s.transcript), max_tokens=150)
