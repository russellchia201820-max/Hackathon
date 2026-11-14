"""Data model for an interview session and its knowledge base."""
from __future__ import annotations

import time
import uuid
from typing import Literal, Optional

from pydantic import BaseModel, Field

KnowledgeCategory = Literal[
    "procedure_step",
    "location",
    "tool_equipment",
    "maintenance",
    "problem_fix",
    "judgement_rule",
    "safety",
    "tip",
    "quality_check",
    "people_process",
    "context",
]
KNOWLEDGE_CATEGORIES: list[str] = list(KnowledgeCategory.__args__)  # type: ignore[attr-defined]

AssumptionKind = Literal[
    "unstated_assumption",
    "automatic_action",
    "vague_term",
    "site_reference",
    "judgement_call",
    "missing_step",
    "jargon",
]
ASSUMPTION_KINDS: list[str] = list(AssumptionKind.__args__)  # type: ignore[attr-defined]

CATEGORY_LABELS = {
    "procedure_step": "Steps",
    "location": "Where things are",
    "tool_equipment": "Tools, systems & materials",
    "maintenance": "Upkeep & maintenance",
    "problem_fix": "Problems & fixes",
    "judgement_rule": "Judgement calls",
    "safety": "Risks & safety",
    "tip": "Tips",
    "quality_check": "Quality checks",
    "people_process": "People & process",
    "context": "Context",
}

KIND_LABELS = {
    "unstated_assumption": "Unstated assumption",
    "automatic_action": "Automatic action",
    "vague_term": "Vague term",
    "site_reference": "Local reference",
    "judgement_call": "Judgement call",
    "missing_step": "Missing step",
    "jargon": "Jargon",
}


def now() -> float:
    return time.time()


def new_id() -> str:
    return uuid.uuid4().hex[:10]


class Turn(BaseModel):
    role: Literal["agent", "expert", "system"]
    text: str
    ts: float = Field(default_factory=now)
    # For agent turns: what the question was aimed at and why.
    target_type: Optional[str] = None
    target_ref: Optional[str] = None
    why: Optional[str] = None


class KnowledgeItem(BaseModel):
    id: str
    category: str = "context"
    title: str = ""
    content: str = ""
    why: str = ""
    conditions: str = ""
    source_quote: str = ""
    specificity: Literal["general", "site", "personal"] = "general"
    in_reference: bool = False
    source: Literal["conversation", "expert_document", "correction"] = "conversation"
    turn: int = 0
    history: list[str] = Field(default_factory=list)
    created: float = Field(default_factory=now)

    @property
    def not_in_any_manual(self) -> bool:
        return self.specificity in ("site", "personal") and not self.in_reference


class Assumption(BaseModel):
    id: str
    kind: str = "unstated_assumption"
    text: str = ""
    evidence_quote: str = ""
    importance: int = 3
    suggested_question: str = ""
    status: Literal["open", "asked", "resolved", "dismissed"] = "open"
    resolution: str = ""
    depth: int = 0
    times_asked: int = 0
    turn_detected: int = 0
    turn_resolved: Optional[int] = None


class CoverageDimension(BaseModel):
    id: str
    name: str
    description: str = ""
    score: float = 0.0
    note: str = ""


class Document(BaseModel):
    id: str = Field(default_factory=new_id)
    filename: str
    kind: Literal["reference", "expert"]
    chars: int = 0
    summary: str = ""
    added: float = Field(default_factory=now)


class PendingQuestion(BaseModel):
    id: str
    text: str
    status: Literal["open", "answered"] = "open"
    answer_ids: list[str] = Field(default_factory=list)
    created: float = Field(default_factory=now)


class TraineeExchange(BaseModel):
    question: str
    answer: str
    covered: str = "no"
    cited_ids: list[str] = Field(default_factory=list)
    ts: float = Field(default_factory=now)


class Session(BaseModel):
    id: str = Field(default_factory=new_id)
    created: float = Field(default_factory=now)
    updated: float = Field(default_factory=now)
    expert_name: str = ""
    expert_role: str = ""
    site: str = ""

    phase: Literal["opening", "interview", "closing", "done"] = "opening"
    topic: str = ""
    objective_type: str = ""
    objective: str = ""

    coverage: list[CoverageDimension] = Field(default_factory=list)
    knowledge: list[KnowledgeItem] = Field(default_factory=list)
    assumptions: list[Assumption] = Field(default_factory=list)
    transcript: list[Turn] = Field(default_factory=list)

    documents: list[Document] = Field(default_factory=list)
    reference_points: list[str] = Field(default_factory=list)

    pending_questions: list[PendingQuestion] = Field(default_factory=list)
    trainee_log: list[TraineeExchange] = Field(default_factory=list)
    manual_markdown: str = ""

    counters: dict[str, int] = Field(default_factory=lambda: {"k": 0, "a": 0, "q": 0})

    # ---- helpers -------------------------------------------------------
    def next_id(self, prefix: str) -> str:
        self.counters[prefix] = self.counters.get(prefix, 0) + 1
        return f"{prefix}{self.counters[prefix]}"

    @property
    def expert_turns(self) -> int:
        return sum(1 for t in self.transcript if t.role == "expert")

    @property
    def avg_coverage(self) -> float:
        if not self.coverage:
            return 0.0
        return sum(d.score for d in self.coverage) / len(self.coverage)

    def last_agent_turn(self) -> Optional[Turn]:
        for t in reversed(self.transcript):
            if t.role == "agent":
                return t
        return None

    def find_assumption(self, aid: str) -> Optional[Assumption]:
        return next((a for a in self.assumptions if a.id == aid), None)

    def find_knowledge(self, kid: str) -> Optional[KnowledgeItem]:
        return next((k for k in self.knowledge if k.id == kid), None)

    def summary(self) -> dict:
        return {
            "id": self.id,
            "topic": self.topic or "(new session)",
            "expert_name": self.expert_name,
            "phase": self.phase,
            "knowledge": len(self.knowledge),
            "assumptions": len(self.assumptions),
            "updated": self.updated,
        }

    def stats(self) -> dict:
        return {
            "knowledge": len(self.knowledge),
            "not_in_manual": sum(1 for k in self.knowledge if k.not_in_any_manual),
            "assumptions_caught": len(self.assumptions),
            "assumptions_resolved": sum(1 for a in self.assumptions if a.status == "resolved"),
            "assumptions_open": sum(1 for a in self.assumptions if a.status in ("open", "asked")),
            "coverage": round(self.avg_coverage, 2),
            "expert_turns": self.expert_turns,
            "pending_questions": sum(1 for q in self.pending_questions if q.status == "open"),
        }

    def to_public(self) -> dict:
        data = self.model_dump()
        data["stats"] = self.stats()
        for k, item in zip(data["knowledge"], self.knowledge):
            k["not_in_any_manual"] = item.not_in_any_manual
        return data
