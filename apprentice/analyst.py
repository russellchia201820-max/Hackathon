"""Slow loop: the analyst. Runs after every expert turn, in the background."""
from __future__ import annotations

import asyncio
import difflib
import logging
from pathlib import Path
from typing import Awaitable, Callable

from . import llm, selector
from .checklist import SLOTS, describe
from .models import Gap, KnowledgeItem, Session, SlotCoverage, now

log = logging.getLogger("apprentice.analyst")
PROMPT = (Path(__file__).parent / "prompts" / "analyst.md").read_text()
GAP_KINDS = ["vague_term", "undefined_jargon", "skipped_step", "missing_why",
             "missing_failure_case", "missing_cue", "contradiction", "missing_slot"]


def _obj(props: dict) -> dict:
    return {"type": "object", "properties": props, "required": list(props), "additionalProperties": False}


_str = {"type": "string"}
_strs = {"type": "array", "items": {"type": "string"}}
SCHEMA = _obj({
    "new_items": {"type": "array", "items": _obj({
        "slot": {"type": "string", "enum": SLOTS},
        "content": _str,
        "source_quote": _str,
        "turn_index": {"type": "integer"},
        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
    })},
    "new_gaps": {"type": "array", "items": _obj({
        "kind": {"type": "string", "enum": GAP_KINDS},
        "anchor_quote": _str,
        "question": _str,
        "slot": {"type": "string", "enum": SLOTS},
        "priority": {"type": "integer", "description": "1-5, 5 = most important"},
        "topic": _str,
    })},
    "asked_gap_ids": _strs,
    "resolved": {"type": "array", "items": _obj({"gap_id": _str, "item_ids": _strs})},
    "dropped_gap_ids": _strs,
    "coverage_updates": {"type": "array", "items": _obj({
        "slot": {"type": "string", "enum": SLOTS},
        "status": {"type": "string", "enum": ["empty", "partial", "good"]},
        "note": _str,
    })},
    "current_topic": _str,
    "ready_to_wrap_up": {"type": "boolean"},
})


def build_input(s: Session, window: int = 8) -> str:
    cov = "\n".join(f"- {k}: {v.status}{(' — ' + v.note) if v.note else ''}" for k, v in s.coverage.items())
    items = "\n".join(f"{i.id} [{i.slot}] {i.content}" for i in s.items) or "(none yet)"
    gaps = "\n".join(
        f"{g.id} ({g.status}, p{g.priority}, {g.slot}, asked {g.times_asked}x) anchor: \"{g.anchor_quote}\" Q: {g.question}"
        for g in s.gaps if g.status in ("open", "asked")) or "(none)"
    turns = "\n".join(f"[T{t.index} {t.speaker}] {t.text}" for t in s.transcript[-window:])
    return (f"# Objective\n{s.objective}\n\n# Checklist slots\n{describe()}\n\n"
            f"# Current coverage\n{cov}\n\n# Existing knowledge items\n{items}\n\n"
            f"# Open / asked gaps\n{gaps}\n\n# Phase: {s.phase}\n\n"
            f"# Recent transcript (latest last)\n{turns}\n\n"
            "Update the knowledge base for the latest expert turn(s).")


def _similar(a: str, b: str, thr: float) -> bool:
    return difflib.SequenceMatcher(None, a.lower(), b.lower()).ratio() >= thr


def merge(s: Session, out: dict) -> str:
    """Merge analyst output into the live session. Returns a compact log line."""
    added_items: list[KnowledgeItem] = []
    new_index_to_id: dict[int, str] = {}
    for n, it in enumerate(out.get("new_items", [])):
        content = (it.get("content") or "").strip()
        if not content:
            continue
        dup = next((x for x in s.items if _similar(x.content, content, 0.85)), None)
        if dup:
            new_index_to_id[n] = dup.id
            continue
        item = KnowledgeItem(id=s.next_item_id(), slot=it["slot"], content=content,
                             source_quote=it.get("source_quote", ""), turn_index=it.get("turn_index", -1),
                             confidence=it.get("confidence", "medium"))
        s.items.append(item)
        added_items.append(item)
        new_index_to_id[n] = item.id

    added_gaps = []
    live = [g for g in s.gaps if g.status in ("open", "asked")]
    for g in out.get("new_gaps", [])[:3]:
        q = (g.get("question") or "").strip()
        anchor = (g.get("anchor_quote") or "").strip()
        if not q or any(_similar(x.question, q, 0.75) or
                        (anchor and x.anchor_quote and _similar(x.anchor_quote, anchor, 0.8)) for x in live):
            continue
        gap = Gap(id=s.next_gap_id(), kind=g["kind"], anchor_quote=g.get("anchor_quote", ""), question=q,
                  slot=g["slot"], priority=max(1, min(5, int(g.get("priority", 3)))),
                  topic=g.get("topic", ""), created_turn=len(s.transcript) - 1)
        s.gaps.append(gap)
        live.append(gap)
        added_gaps.append(gap)

    last_agent = s.last_agent_turn()
    asked = []
    for gid in out.get("asked_gap_ids", []):
        g = s.gap(gid)
        if g and g.status in ("open", "asked") and last_agent and last_agent.index not in g.asked_turns:
            g.asked_turns.append(last_agent.index)
            g.times_asked = len(g.asked_turns)
            g.status = "asked"
            g.updated_at = now()
            asked.append(gid)

    resolved = []
    for r in out.get("resolved", []):
        g = s.gap(r.get("gap_id", ""))
        if not g or g.status == "resolved":
            continue
        ids = []
        for ref in r.get("item_ids", []):
            if ref.startswith("new:"):
                try:
                    ref = new_index_to_id.get(int(ref[4:]), "")
                except ValueError:
                    ref = ""
            if ref:
                ids.append(ref)
        g.status, g.resolved_by_item_ids, g.updated_at = "resolved", ids, now()
        resolved.append(g.id)

    dropped = []
    for gid in out.get("dropped_gap_ids", []):
        g = s.gap(gid)
        if g and g.status in ("open", "asked"):
            g.status, g.updated_at = "dropped", now()
            dropped.append(gid)

    cov_changes = []
    for c in out.get("coverage_updates", []):
        if c.get("slot") in s.coverage:
            old = s.coverage[c["slot"]].status
            s.coverage[c["slot"]] = SlotCoverage(status=c["status"], note=c.get("note", ""))
            if old != c["status"]:
                cov_changes.append(f"{c['slot']}={c['status']}")
    # any slot with an item is at least partial
    for it in s.items:
        if s.coverage[it.slot].status == "empty":
            s.coverage[it.slot].status = "partial"
            cov_changes.append(f"{it.slot}=partial")

    if out.get("current_topic"):
        s.current_topic = out["current_topic"]
    s.analyst_ready_to_wrap = bool(out.get("ready_to_wrap_up"))

    parts = [f"+{len(added_items)} items", f"+{len(added_gaps)} gaps"]
    if asked:
        parts.append("asked " + ",".join(asked))
    if resolved:
        parts.append("resolved " + ",".join(resolved))
    if dropped:
        parts.append("dropped " + ",".join(dropped))
    if cov_changes:
        parts.append("coverage: " + " ".join(dict.fromkeys(cov_changes)))
    return ", ".join(parts)


async def analyse(s: Session) -> dict:
    return await llm.complete("analyst", PROMPT, [{"role": "user", "content": build_input(s)}],
                              max_tokens=4000, schema=SCHEMA)


class AnalystRunner:
    """One per session. Never stacks calls: if busy, mark dirty and rerun once at the end."""

    def __init__(self, session: Session, save: Callable[[Session], None],
                 on_log: Callable[[str], None] | None = None):
        self.s = session
        self.save = save
        self.on_log = on_log
        self.lock = asyncio.Lock()
        self.dirty = False
        self.task: asyncio.Task | None = None

    def trigger(self) -> None:
        """Fire-and-forget from any coroutine."""
        if self.task is not None and not self.task.done():
            self.dirty = True
            return
        self.task = asyncio.create_task(self._run())

    async def wait_idle(self) -> None:
        """Wait until any running/pending analyst work is finished (does not trigger)."""
        while self.task is not None and not self.task.done():
            await self.task

    async def _run(self) -> None:
        async with self.lock:
            while True:
                self.dirty = False
                self.s.analyst_busy = True
                self.save(self.s)
                try:
                    out = await analyse(self.s)
                    line = merge(self.s, out)
                    self.s.log(line, "analyst")
                except Exception as e:
                    log.exception("analyst failed")
                    line = f"analyst error: {e}"
                    self.s.log(line, "error")
                selector.update_phase(self.s)
                self.s.analyst_busy = False
                self.save(self.s)
                if self.on_log:
                    self.on_log(line)
                if not self.dirty:
                    break
