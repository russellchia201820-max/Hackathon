"""The knowledge map: a structured, growing model of what the expert knows.

The map is the agent's working memory. Every expert answer is turned into a
patch that is applied here, and the gaps in the map drive the next question.
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from typing import Optional

# Slots every step must have before a beginner could perform it unaided.
STEP_SLOTS = {
    "action": "What exactly is done",
    "why": "Why this step matters / what it achieves",
    "how": "Technique, amounts, settings, timing (the 'how much / how long')",
    "done_cue": "How you know it is done or done right (what you see/hear/feel/measure)",
    "pitfalls": "What commonly goes wrong, and how to notice and recover",
    "tools": "Tools, materials or inputs needed",
}
# Slots that apply to the task as a whole.
TASK_SLOTS = {
    "goal": "What the task achieves and what a good result looks like",
    "context": "When/why the task is done, who does it, how often",
    "preconditions": "What must be true or prepared before starting",
    "safety": "Hazards, things never to do, when to stop and get help",
    "variations": "When the procedure changes (conditions, equipment, site quirks)",
}

# Phrases experts use when skipping over tacit knowledge.
TACIT_MARKERS = [
    r"\bjust\b", r"\bobviously\b", r"\bof course\b", r"\byou know\b", r"\bbasically\b",
    r"\bsimply\b", r"\bnormally\b", r"\busually\b", r"\bit depends\b", r"\bby feel\b",
    r"\bfeels? right\b", r"\blooks? right\b", r"\bsounds? right\b", r"\buntil it\b",
    r"\ba bit\b", r"\ba little\b", r"\bsome\b", r"\benough\b", r"\bkind of\b", r"\bsort of\b",
    r"\bthe usual\b", r"\bas always\b", r"\bthe trick is\b", r"\byou'?ll see\b",
    r"\bexperience\b", r"\binstinct\b", r"\bsecond nature\b", r"\bwithout thinking\b",
    r"\band so on\b", r"\betc\b", r"\bthe rest\b", r"\bthen you'?re done\b",
]


def tacit_signals(text: str) -> list[str]:
    """Return the tacit-knowledge phrases found in an utterance."""
    found = []
    low = text.lower()
    for pat in TACIT_MARKERS:
        for m in re.finditer(pat, low):
            found.append(m.group(0))
    return sorted(set(found))


@dataclass
class Evidence:
    quote: str            # the expert's own words
    turn: int


@dataclass
class Step:
    id: str
    action: str = ""
    why: str = ""
    how: str = ""
    done_cue: str = ""
    pitfalls: str = ""
    tools: str = ""
    conditions: str = ""          # branches: "if X then ..."
    parent: Optional[str] = None  # for sub-steps
    evidence: list[Evidence] = field(default_factory=list)
    na_slots: list[str] = field(default_factory=list)  # slots the expert confirmed don't apply

    def missing(self) -> list[str]:
        return [s for s in STEP_SLOTS if not getattr(self, s).strip() and s not in self.na_slots]


@dataclass
class Concept:
    term: str
    meaning: str = ""
    evidence: list[Evidence] = field(default_factory=list)


@dataclass
class Assumption:
    id: str
    text: str                  # what is being taken for granted
    about: str = ""            # step id or 'task'
    status: str = "open"       # open | resolved | dropped
    resolution: str = ""


@dataclass
class KnowledgeMap:
    topic: str
    objective: str
    task: dict = field(default_factory=lambda: {k: "" for k in TASK_SLOTS})
    steps: list[Step] = field(default_factory=list)          # ordered
    concepts: list[Concept] = field(default_factory=list)
    assumptions: list[Assumption] = field(default_factory=list)
    facts: list[Evidence] = field(default_factory=list)      # loose knowledge not tied to a step
    asked: list[dict] = field(default_factory=list)          # questions already asked {target, slot, type, turn}
    _n: int = 0

    # ---------- lookup ----------
    def step(self, sid: str) -> Optional[Step]:
        return next((s for s in self.steps if s.id == sid), None)

    def _new_id(self, prefix: str) -> str:
        self._n += 1
        return f"{prefix}{self._n}"

    # ---------- patching ----------
    def apply_patch(self, patch: dict, turn: int) -> list[str]:
        """Apply a model-produced patch. Returns a human-readable change log."""
        log: list[str] = []
        idmap: dict[str, str] = {}

        for k, v in (patch.get("task") or {}).items():
            if k in self.task and v and v.strip():
                self.task[k] = _merge(self.task[k], v)
                log.append(f"task.{k} updated")

        for s in patch.get("steps") or []:
            sid = s.get("id") or ""
            existing = self.step(sid)
            if existing is None:
                new_id = self._new_id("S")
                idmap[sid] = new_id
                existing = Step(id=new_id)
                pos = _insert_pos(self, s.get("after"), idmap)
                self.steps.insert(pos, existing)
                log.append(f"new step {new_id}: {s.get('action','')[:60]}")
            for slot in list(STEP_SLOTS) + ["conditions"]:
                val = (s.get(slot) or "").strip()
                if val:
                    setattr(existing, slot, _merge(getattr(existing, slot), val))
            if s.get("parent"):
                existing.parent = idmap.get(s["parent"], s["parent"])
            for slot in s.get("not_applicable") or []:
                if slot in STEP_SLOTS and slot not in existing.na_slots:
                    existing.na_slots.append(slot)
            if s.get("quote"):
                existing.evidence.append(Evidence(s["quote"], turn))

        if patch.get("reorder"):
            order = [idmap.get(i, i) for i in patch["reorder"]]
            known = [self.step(i) for i in order if self.step(i)]
            rest = [s for s in self.steps if s not in known]
            if known:
                self.steps = known + rest
                log.append("steps reordered")

        for c in patch.get("concepts") or []:
            term = (c.get("term") or "").strip()
            if not term:
                continue
            cur = next((x for x in self.concepts if x.term.lower() == term.lower()), None)
            if cur is None:
                cur = Concept(term=term)
                self.concepts.append(cur)
                log.append(f"new concept: {term}")
            if c.get("meaning"):
                cur.meaning = _merge(cur.meaning, c["meaning"])
            if c.get("quote"):
                cur.evidence.append(Evidence(c["quote"], turn))

        for a in patch.get("new_assumptions") or []:
            about = idmap.get(a.get("about", ""), a.get("about", "task"))
            if any(x.text.lower() == a["text"].lower() for x in self.assumptions):
                continue
            self.assumptions.append(Assumption(id=self._new_id("A"), text=a["text"], about=about))
            log.append(f"assumption flagged: {a['text'][:60]}")

        for r in patch.get("resolved_assumptions") or []:
            a = next((x for x in self.assumptions if x.id == r.get("id")), None)
            if a and a.status == "open":
                a.status = r.get("status", "resolved")
                a.resolution = r.get("resolution", "")
                log.append(f"assumption {a.id} {a.status}")

        for f in patch.get("facts") or []:
            if f.strip():
                self.facts.append(Evidence(f.strip(), turn))
        return log

    # ---------- coverage ----------
    def gaps(self) -> list[dict]:
        """Every unfilled slot, as a target the planner can ask about."""
        out = []
        for k, desc in TASK_SLOTS.items():
            if not self.task[k].strip():
                out.append({"target": "task", "slot": k, "desc": desc})
        for s in self.steps:
            for slot in s.missing():
                out.append({"target": s.id, "slot": slot, "desc": STEP_SLOTS[slot], "step": s.action})
        for c in self.concepts:
            if not c.meaning.strip():
                out.append({"target": f"concept:{c.term}", "slot": "meaning", "desc": "Undefined term"})
        for a in self.assumptions:
            if a.status == "open":
                out.append({"target": a.id, "slot": "assumption", "desc": a.text, "about": a.about})
        return out

    def coverage(self) -> dict:
        total = len(TASK_SLOTS) + len(self.steps) * len(STEP_SLOTS) + len(self.concepts)
        filled = sum(1 for k in TASK_SLOTS if self.task[k].strip())
        filled += sum(len(STEP_SLOTS) - len(s.missing()) for s in self.steps)
        filled += sum(1 for c in self.concepts if c.meaning.strip())
        open_a = sum(1 for a in self.assumptions if a.status == "open")
        score = filled / total if total else 0.0
        # open assumptions drag the score down: an unresolved assumption is a known unknown
        score *= 1 / (1 + 0.05 * open_a)
        return {"score": round(score, 3), "filled": filled, "total": total,
                "steps": len(self.steps), "open_assumptions": open_a}

    def times_asked(self, target: str, slot: str) -> int:
        return sum(1 for q in self.asked if q["target"] == target and q["slot"] == slot)

    # ---------- views ----------
    def outline(self) -> str:
        """Compact text view given to the model each turn."""
        lines = [f"TOPIC: {self.topic}", f"OBJECTIVE: {self.objective}", "TASK:"]
        for k in TASK_SLOTS:
            lines.append(f"  {k}: {self.task[k] or '??'}")
        lines.append("STEPS (in order):")
        for i, s in enumerate(self.steps, 1):
            ind = "    " if s.parent else "  "
            lines.append(f"{ind}{i}. [{s.id}] {s.action or '??'}")
            for slot in list(STEP_SLOTS)[1:] + ["conditions"]:
                v = getattr(s, slot)
                mark = "n/a" if slot in s.na_slots else (v or "??")
                lines.append(f"{ind}     {slot}: {mark}")
        if self.concepts:
            lines.append("CONCEPTS:")
            for c in self.concepts:
                lines.append(f"  - {c.term}: {c.meaning or '??'}")
        oa = [a for a in self.assumptions if a.status == "open"]
        if oa:
            lines.append("OPEN ASSUMPTIONS:")
            for a in oa:
                lines.append(f"  - [{a.id}] ({a.about}) {a.text}")
        if self.facts:
            lines.append("OTHER FACTS:")
            for f in self.facts[-15:]:
                lines.append(f"  - {f.quote}")
        return "\n".join(lines)

    def to_markdown(self) -> str:
        md = [f"# {self.topic}", "", f"_{self.task['goal'] or self.objective}_", ""]
        for k in ("context", "preconditions", "safety", "variations"):
            if self.task[k]:
                md.append(f"**{k.capitalize()}:** {self.task[k]}")
        md += ["", "## Steps"]
        for i, s in enumerate(self.steps, 1):
            md.append(f"\n### {i}. {s.action}")
            for slot, label in [("why", "Why"), ("how", "How"), ("tools", "Tools"),
                                ("done_cue", "You know it's right when"), ("pitfalls", "Watch out"),
                                ("conditions", "If")]:
                if getattr(s, slot):
                    md.append(f"- **{label}:** {getattr(s, slot)}")
            for e in s.evidence[:2]:
                md.append(f"  > \"{e.quote}\"")
        if self.concepts:
            md += ["", "## Terms"] + [f"- **{c.term}:** {c.meaning}" for c in self.concepts]
        if self.facts:
            md += ["", "## Other tips"] + [f"- {f.quote}" for f in self.facts]
        return "\n".join(md)

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2)

    @classmethod
    def from_json(cls, s: str) -> "KnowledgeMap":
        d = json.loads(s)
        km = cls(topic=d["topic"], objective=d["objective"])
        km.task = d["task"]
        km.steps = [Step(**{**x, "evidence": [Evidence(**e) for e in x["evidence"]]}) for x in d["steps"]]
        km.concepts = [Concept(**{**x, "evidence": [Evidence(**e) for e in x["evidence"]]}) for x in d["concepts"]]
        km.assumptions = [Assumption(**x) for x in d["assumptions"]]
        km.facts = [Evidence(**e) for e in d["facts"]]
        km.asked = d["asked"]
        km._n = d["_n"]
        return km


def _merge(old: str, new: str) -> str:
    old, new = old.strip(), new.strip()
    if not old:
        return new
    if new.lower() in old.lower():
        return old
    if old.lower() in new.lower():
        return new
    return f"{old}; {new}"


def _insert_pos(km: KnowledgeMap, after: Optional[str], idmap: dict) -> int:
    if not after:
        return len(km.steps)
    if after == "START":
        return 0
    after = idmap.get(after, after)
    for i, s in enumerate(km.steps):
        if s.id == after:
            return i + 1
    return len(km.steps)
