from __future__ import annotations

import time
import uuid
from typing import Literal, Optional

from pydantic import BaseModel, Field

from .checklist import SLOTS

Slot = str  # one of checklist.SLOTS
Phase = Literal["opening", "deep_dive", "wrap_up", "done"]
GapKind = Literal[
    "vague_term", "undefined_jargon", "skipped_step", "missing_why",
    "missing_failure_case", "missing_cue", "contradiction", "missing_slot",
]
GapStatus = Literal["open", "asked", "resolved", "dropped"]


def now() -> float:
    return time.time()


class Turn(BaseModel):
    speaker: Literal["agent", "expert", "observation"]
    text: str
    ts: float = Field(default_factory=now)
    index: int = 0


class KnowledgeItem(BaseModel):
    id: str
    slot: Slot
    content: str
    source_quote: str = ""
    turn_index: int = -1
    confidence: Literal["high", "medium", "low"] = "medium"
    pinned: bool = False          # flagged as key insight by the agent's tool
    created_at: float = Field(default_factory=now)


class Gap(BaseModel):
    id: str
    kind: GapKind
    anchor_quote: str = ""
    question: str
    slot: Slot
    priority: int = 3
    topic: str = ""
    status: GapStatus = "open"
    times_asked: int = 0
    asked_turns: list[int] = Field(default_factory=list)
    created_turn: int = -1
    resolved_by_item_ids: list[str] = Field(default_factory=list)
    updated_at: float = Field(default_factory=now)


class SlotCoverage(BaseModel):
    status: Literal["empty", "partial", "good"] = "empty"
    note: str = ""


class Frame(BaseModel):
    """M7: a captured camera frame and what the agent saw in it."""
    id: str
    path: str
    description: str
    turn_index: int
    created_at: float = Field(default_factory=now)


class Session(BaseModel):
    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:8])
    objective: str = ""
    expert_name: str = "Expert"
    phase: Phase = "opening"
    transcript: list[Turn] = Field(default_factory=list)
    items: list[KnowledgeItem] = Field(default_factory=list)
    gaps: list[Gap] = Field(default_factory=list)
    coverage: dict[str, SlotCoverage] = Field(
        default_factory=lambda: {s: SlotCoverage() for s in SLOTS})
    current_topic: str = ""
    analyst_ready_to_wrap: bool = False
    analyst_busy: bool = False
    wrap_up_expert_turns: int = 0
    events: list[dict] = Field(default_factory=list)   # {ts, text, kind}
    frames: list[Frame] = Field(default_factory=list)
    avatar_disabled: bool = False  # set when the avatar dropped mid-call; reconnects go voice-only
    visual_request: str = ""      # set by request_visual tool; UI shows a 'Show me' prompt
    mode: Literal["text", "voice"] = "text"
    version: int = 0
    created_at: float = Field(default_factory=now)
    updated_at: float = Field(default_factory=now)

    # ---- helpers ----
    def add_turn(self, speaker: str, text: str) -> Turn:
        t = Turn(speaker=speaker, text=text.strip(), index=len(self.transcript))
        self.transcript.append(t)
        return t

    def expert_turn_count(self) -> int:
        return sum(1 for t in self.transcript if t.speaker == "expert")

    def last_agent_turn(self) -> Optional[Turn]:
        for t in reversed(self.transcript):
            if t.speaker == "agent":
                return t
        return None

    def next_item_id(self) -> str:
        return f"k{len(self.items) + 1}"

    def next_gap_id(self) -> str:
        return f"g{len(self.gaps) + 1}"

    def gap(self, gid: str) -> Optional[Gap]:
        return next((g for g in self.gaps if g.id == gid), None)

    def log(self, text: str, kind: str = "info") -> None:
        self.events.append({"ts": now(), "text": text, "kind": kind})
        self.events = self.events[-40:]

    def coverage_pct(self) -> int:
        pts = {"empty": 0, "partial": 0.5, "good": 1}
        return round(100 * sum(pts[c.status] for c in self.coverage.values()) / len(SLOTS))
