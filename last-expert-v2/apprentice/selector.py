"""Deterministic question ranking + phase logic (no LLM)."""
from __future__ import annotations

import os

from .models import Gap, Session

OPENING_TURNS = 2


def _matches_topic(g: Gap, topic: str) -> bool:
    if not topic:
        return False
    t = topic.lower()
    words = [w for w in t.replace("/", " ").split() if len(w) > 3]
    hay = f"{g.topic} {g.slot} {g.question}".lower()
    return g.slot in t or t in g.topic.lower() or any(w in hay for w in words)


def score(g: Gap, s: Session) -> float:
    sc = g.priority * 2
    if _matches_topic(g, s.current_topic):
        sc += 1          # small pull towards the current topic...
    status = s.coverage[g.slot].status
    sc += {"empty": 3, "partial": 1, "good": -1}[status]   # ...but breadth wins
    sc -= 2 * g.times_asked
    return sc


def candidates(s: Session, k: int = 3) -> list[Gap]:
    live = []
    for g in s.gaps:
        if g.status in ("open", "asked") and g.times_asked >= 2:
            g.status = "dropped"   # don't badger the expert
        if g.status in ("open", "asked"):
            live.append(g)
    live.sort(key=lambda g: (-score(g, s), g.created_turn))
    return live[:k]


def wrap_up_ready(s: Session) -> bool:
    cov = s.coverage.values()
    all_partial = all(c.status != "empty" for c in cov)
    good = sum(c.status == "good" for c in cov)
    high_open = any(g.status in ("open", "asked") and g.priority >= 4 for g in s.gaps)
    return all_partial and good >= 5 and not high_open


def update_phase(s: Session) -> str:
    n = s.expert_turn_count()
    max_turns = int(os.getenv("MAX_TURNS", "30"))
    if s.phase == "opening" and n >= OPENING_TURNS:
        s.phase = "deep_dive"
    if s.phase == "deep_dive" and (wrap_up_ready(s) or s.analyst_ready_to_wrap or n >= max_turns):
        s.phase = "wrap_up"
        s.log("Entering wrap-up", "phase")
    if s.phase == "wrap_up" and s.wrap_up_expert_turns >= 2:
        s.phase = "done"
        s.log("Interview complete", "phase")
    return s.phase


def on_expert_turn(s: Session) -> None:
    if s.phase == "wrap_up":
        s.wrap_up_expert_turns += 1
    update_phase(s)
