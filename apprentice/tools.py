"""Effects of the voice agent's function tools on session state (pure Python, unit-testable)."""
from __future__ import annotations

from . import store
from .checklist import SLOTS
from .models import KnowledgeItem, Session


def flag_important(s: Session, summary: str, slot: str = "mistakes") -> str:
    slot = slot if slot in SLOTS else "mistakes"
    last_expert = next((t for t in reversed(s.transcript) if t.speaker == "expert"), None)
    item = KnowledgeItem(id=s.next_item_id(), slot=slot, content=summary.strip(), confidence="high",
                         source_quote=(last_expert.text[:160] if last_expert else ""),
                         turn_index=last_expert.index if last_expert else -1, pinned=True)
    s.items.append(item)
    s.log(f"🔧 flagged as key insight: {summary.strip()[:90]}", "tool")
    store.save(s)
    return "Pinned on the panel as a key insight. Briefly acknowledge it, then continue with one question."


def request_visual(s: Session, reason: str) -> str:
    s.visual_request = reason.strip()
    s.log(f"🔧 asked to see it on camera: {reason.strip()[:90]}", "tool")
    store.save(s)
    return ("A 'Show me' prompt is now on the expert's screen. Ask them, in one short sentence, to hold it "
            "up to the camera and press the Show me button, and to tell you when it's in view.")


def end_interview(s: Session) -> str:
    s.phase = "done"
    s.log("🔧 interview ended — playbook ready to generate", "tool")
    store.save(s)
    return "Interview marked complete. Thank the expert warmly and specifically in one or two sentences."
