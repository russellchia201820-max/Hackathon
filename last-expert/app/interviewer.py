"""The core engine: interviews the expert, hunts assumptions, builds the knowledge base, answers trainees."""
from __future__ import annotations

import logging
import re
import threading
from collections import defaultdict
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any, Optional

from .config import settings
from .models import (
    ASSUMPTION_KINDS,
    CATEGORY_LABELS,
    KIND_LABELS,
    KNOWLEDGE_CATEGORIES,
    Assumption,
    CoverageDimension,
    Document,
    KnowledgeItem,
    PendingQuestion,
    Session,
    TraineeExchange,
    Turn,
)
from .prompts import (
    ANSWER_TRAINEE_TOOL,
    ASK_NEXT_TOOL,
    EXTRACT_TOOL,
    INTERVIEWER_SYSTEM,
    MANUAL_SYSTEM,
    PLAN_SESSION_TOOL,
    RECORD_TOOL,
    SUMMARIZE_REFERENCE_TOOL,
    TRAINEE_SYSTEM,
)

log = logging.getLogger("last_expert.interviewer")

MAX_DOC_CHARS = 60_000
DOC_CHUNK_CHARS = 9_000
MAX_DOC_CHUNKS = 5
CLOSING_QUESTION = "Before we finish, what's the one thing new starters always get wrong here?"
REFERENCE_SYSTEM = (
    "You read reference documents such as manuals, SOPs and procedures, and list exactly what they already "
    "state. Never add anything the document does not say."
)


class NotFound(Exception):
    pass


# ---------------------------------------------------------------------------
# Small defensive helpers: model output is checked before it touches the knowledge base.
# ---------------------------------------------------------------------------
def _s(value: Any, limit: int = 600) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    return text[:limit]


def _i(value: Any, default: int, lo: int, hi: int) -> int:
    try:
        return max(lo, min(hi, int(round(float(value)))))
    except (TypeError, ValueError):
        return default


def _f(value: Any, default: float = 0.0) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return default


def _list(value: Any) -> list:
    return value if isinstance(value, list) else []


_WORD = re.compile(r"[a-z0-9']+")


def _tokens(text: str) -> set[str]:
    return set(_WORD.findall(text.lower()))


def _similar(a: str, b: str) -> float:
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def _spoken(text: str) -> str:
    """Make a question safe to say aloud: no markdown, single line."""
    text = re.sub(r"[*_#`>]+", "", text or "")
    text = re.sub(r"^\s*[-•]\s*", "", text, flags=re.MULTILINE)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:500]


class Interviewer:
    """Each expert turn makes two Claude calls:

    1. ask_next (in the request): a short response with the next question, so the expert hears it in seconds.
    2. record_knowledge (in the background): the full extraction of knowledge, assumptions, resolutions and
       coverage. The UI polls until it lands. The next turn waits for it, so context is never stale.
    """

    def __init__(self, llm, store, background: bool = True):
        self.llm = llm
        self.store = store
        self.background = background
        self._locks: dict[str, threading.Lock] = defaultdict(threading.Lock)
        self._locks_guard = threading.Lock()
        self._pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="record")
        self._jobs: dict[str, Future] = {}
        self._job_errors: dict[str, str] = {}

    # ------------------------------------------------------------------ basics
    def _lock(self, sid: str) -> threading.Lock:
        with self._locks_guard:
            return self._locks[sid]

    def load(self, sid: str) -> Session:
        session = self.store.get(sid)
        if session is None:
            raise NotFound(f"Session {sid} not found")
        return session

    def is_processing(self, sid: str) -> bool:
        job = self._jobs.get(sid)
        return bool(job and not job.done())

    def wait_idle(self, sid: str, timeout: float = 45.0) -> None:
        job = self._jobs.get(sid)
        if job and not job.done():
            try:
                job.result(timeout=timeout)
            except Exception:  # errors are reported via last_error
                pass

    def public(self, s: Session) -> dict:
        data = s.to_public()
        data["processing"] = self.is_processing(s.id)
        data["last_error"] = self._job_errors.get(s.id, "")
        return data

    def get_public(self, sid: str) -> dict:
        return self.public(self.load(sid))

    def create_session(self, expert_name: str = "", expert_role: str = "", site: str = "") -> Session:
        session = Session(expert_name=_s(expert_name, 80), expert_role=_s(expert_role, 120), site=_s(site, 120))
        session.transcript.append(
            Turn(
                role="agent",
                text=settings.opening_line,
                target_type="opening",
                why="Let the expert choose what they want to teach.",
            )
        )
        self.store.save(session)
        return session

    # ------------------------------------------------------------ conversation
    def handle_expert_message(self, sid: str, text: str) -> dict:
        text = _s(text, 8000)
        if not text:
            raise ValueError("Empty message")
        self.wait_idle(sid)  # the previous answer must be fully recorded before choosing the next question
        with self._lock(sid):
            s = self.load(sid)
            snapshot = len(s.transcript)
            answered_q = s.last_agent_turn()
            s.transcript.append(Turn(role="expert", text=text))
            try:
                if s.phase == "opening" or not s.coverage:
                    result = self._kickoff(s, text)
                elif s.phase == "closing":
                    s.phase = "done"
                    reply = self._closing_message(s)
                    self._record_agent_turn(s, reply, {"target_type": "wrap_up", "why": "The expert answered the final question."})
                    result = {"reply": reply, "phase": s.phase, "intent": "answering", "_depth": 0}
                else:
                    if s.phase == "done":
                        s.phase = "interview"  # the expert has more to say: reopen
                    result = self._next_question(s, text, answered_q)
            except Exception:
                # Don't keep a half-processed turn: the client can simply resend.
                del s.transcript[snapshot:]
                raise
            depth = result.pop("_depth", 0)
            record_prompt = self._record_prompt(s, text, answered_q)
            result["question"] = s.last_agent_turn().model_dump()
            self.store.save(s)
        self._schedule_record(sid, record_prompt, depth)
        result["session"] = self.public(s if self.background else self.load(sid))
        return result

    def _kickoff(self, s: Session, text: str) -> dict:
        data = self.llm.call_tool(INTERVIEWER_SYSTEM, self._kickoff_prompt(s, text), PLAN_SESSION_TOOL, max_tokens=1500)

        s.topic = _s(data.get("topic"), 120) or _s(text, 60)
        s.objective_type = _s(data.get("objective_type"), 20) or "task"
        s.objective = _s(data.get("objective"), 400)

        id_map: dict[str, str] = {}
        s.coverage = []
        for idx, dim in enumerate(_list(data.get("coverage_plan"))[:9]):
            if not isinstance(dim, dict) or not _s(dim.get("name")):
                continue
            new_id = f"d{len(s.coverage) + 1}"
            id_map[_s(dim.get("id"), 10) or f"d{idx + 1}"] = new_id
            s.coverage.append(
                CoverageDimension(id=new_id, name=_s(dim.get("name"), 80), description=_s(dim.get("description"), 240))
            )
        if not s.coverage:
            s.coverage = default_coverage()

        question = dict(data.get("next_question") or {})
        if question.get("target_type") == "coverage":
            question["target_ref"] = id_map.get(_s(question.get("target_ref"), 10), question.get("target_ref"))
        self._apply_focus(s, data.get("focus_assumption"), question, depth=0)
        s.phase = "interview"
        reply = self._ask(s, question, fallback="Walk me through it from the very start. What's the first thing you do?")
        return {"reply": reply, "phase": s.phase, "intent": "answering", "_depth": 0}

    def _next_question(self, s: Session, text: str, answered_q: Optional[Turn]) -> dict:
        parent_depth = -1
        if answered_q and answered_q.target_type == "assumption" and answered_q.target_ref:
            target = s.find_assumption(answered_q.target_ref)
            if target:
                parent_depth = target.depth
        depth = parent_depth + 1

        candidates = self.rank_candidates(s)
        data = self.llm.call_tool(
            INTERVIEWER_SYSTEM,
            self._ask_prompt(s, text, answered_q, candidates),
            ASK_NEXT_TOOL,
            max_tokens=800,
        )
        intent = _s(data.get("expert_intent"), 30)
        question = dict(data.get("next_question") or {})

        if intent == "wants_to_stop" or question.get("target_type") == "wrap_up":
            s.phase = "closing"
            text_out = _spoken(question.get("text", "")) if question.get("target_type") == "wrap_up" else ""
            reply = text_out or f"Thanks, that's really useful. {CLOSING_QUESTION}"
            self._record_agent_turn(s, reply, {"target_type": "wrap_up", "why": "Wrapping up the session."})
        else:
            self._apply_focus(s, data.get("focus_assumption"), question, depth=depth)
            fallback = candidates[0]["suggestion"] if candidates else "Can you walk me through that step by step?"
            reply = self._ask(s, question, fallback=fallback)
        return {"reply": reply, "phase": s.phase, "intent": intent, "_depth": depth}

    def _apply_focus(self, s: Session, raw: Any, question: dict, depth: int) -> Optional[str]:
        """Store the assumption the next question probes, and point the question at it."""
        ref = _s(question.get("target_ref"), 20)
        focus_id = None
        if isinstance(raw, dict) and _s(raw.get("text")):
            text = _s(raw.get("text"), 300)
            existing = next((a for a in s.assumptions if _similar(a.text, text) >= 0.7), None)
            if existing:
                focus_id = existing.id
            else:
                a = Assumption(
                    id=s.next_id("a"),
                    kind=raw.get("kind") if raw.get("kind") in ASSUMPTION_KINDS else "unstated_assumption",
                    text=text,
                    evidence_quote=_s(raw.get("evidence_quote"), 300),
                    importance=_i(raw.get("importance"), 3, 1, 5),
                    suggested_question=_spoken(_s(raw.get("suggested_question"), 400)),
                    depth=max(0, depth),
                    turn_detected=s.expert_turns,
                )
                s.assumptions.append(a)
                focus_id = a.id
        if focus_id and (ref in ("", "focus", "new", "new:0") or question.get("target_type") in ("follow_up", "clarify", "opening")):
            question["target_type"] = "assumption"
            question["target_ref"] = focus_id
        elif ref == "focus":
            question["target_ref"] = ""
        return focus_id

    # ------------------------------------------------------------ background recording
    def _schedule_record(self, sid: str, prompt: str, depth: int) -> None:
        self._job_errors.pop(sid, None)
        if not self.background:
            self._record_job(sid, prompt, depth)
            return
        self._jobs[sid] = self._pool.submit(self._record_job, sid, prompt, depth)

    def _record_job(self, sid: str, prompt: str, depth: int) -> None:
        try:
            data = self.llm.call_tool(INTERVIEWER_SYSTEM, prompt, RECORD_TOOL, max_tokens=3500)
        except Exception as exc:  # never crash the worker; tell the UI instead
            log.exception("Recording the expert's answer failed")
            self._job_errors[sid] = f"Couldn't record the last answer ({exc}). Keep going; the next answer will be recorded."
            return
        with self._lock(sid):
            try:
                s = self.load(sid)
            except NotFound:
                return
            self._merge(s, data, depth=depth)
            self.store.save(s)

    def _ask(self, s: Session, question: dict, fallback: str) -> str:
        text = _spoken(_s(question.get("text"), 600)) or fallback
        self._record_agent_turn(s, text, question)
        return text

    def _record_agent_turn(self, s: Session, text: str, question: dict) -> None:
        target_type = _s(question.get("target_type"), 30) or "follow_up"
        target_ref = _s(question.get("target_ref"), 20) or None
        s.transcript.append(
            Turn(role="agent", text=text, target_type=target_type, target_ref=target_ref, why=_s(question.get("why"), 300))
        )
        if target_type == "assumption" and target_ref:
            a = s.find_assumption(target_ref)
            if a and a.status in ("open", "asked"):
                a.status = "asked"
                a.times_asked += 1

    def _closing_message(self, s: Session) -> str:
        st = s.stats()
        name = f", {s.expert_name}" if s.expert_name else ""
        return (
            f"Thank you{name}. I've captured {st['knowledge']} pieces of know-how, {st['not_in_manual']} of them "
            f"not in any manual, and cleared up {st['assumptions_resolved']} assumptions. "
            f"Anyone can now ask me about {s.topic or 'this'} in trainee mode."
        )

    # ------------------------------------------------------- candidate ranking
    def rank_candidates(self, s: Session, limit: int = 6) -> list[dict]:
        """Server-side priority list: keeps curiosity on track with the objective."""
        turn = s.expert_turns
        out: list[dict] = []
        for q in s.pending_questions:
            if q.status == "open":
                out.append(
                    {
                        "type": "trainee_question",
                        "ref": q.id,
                        # Real demand from a trainee outranks everything except fresh safety-critical gaps.
                        "score": 11.0,
                        "label": f'A trainee asked: "{q.text}"',
                        "suggestion": q.text,
                    }
                )
        for a in s.assumptions:
            if a.status not in ("open", "asked"):
                continue
            score = a.importance * 2.0
            if turn - a.turn_detected <= 1:
                score += 2.0  # fresh assumptions are easiest to ask about naturally
            if a.depth >= 3 and a.importance < 5:
                score -= 5.0  # avoid rabbit holes
            score -= a.times_asked * 3.0
            out.append(
                {
                    "type": "assumption",
                    "ref": a.id,
                    "score": round(score, 1),
                    "label": f"[{a.kind}, importance {a.importance}] {a.text}"
                    + (f' (expert said: "{a.evidence_quote}")' if a.evidence_quote else ""),
                    "suggestion": a.suggested_question or f"Can you tell me more about {a.text.lower()}?",
                }
            )
        for d in s.coverage:
            if d.score < 0.75:
                out.append(
                    {
                        "type": "coverage",
                        "ref": d.id,
                        "score": round((1 - d.score) * 7.0, 1),
                        "label": f"{d.name} ({int(d.score * 100)}% covered): {d.description}",
                        "suggestion": f"Can you tell me about {d.name.lower()}?",
                    }
                )
        out.sort(key=lambda c: c["score"], reverse=True)
        return out[:limit]

    def _wrap_hint(self, s: Session) -> str:
        important_open = [a for a in s.assumptions if a.status in ("open", "asked") and a.importance >= 4]
        if s.expert_turns >= settings.max_turns:
            return (
                "TIME CHECK: the session has reached its turn limit. Wrap up now: target_type 'wrap_up', and in "
                f"two sentences say what you've captured, then ask: \"{CLOSING_QUESTION}\""
            )
        if s.expert_turns >= 6 and s.avg_coverage >= 0.8 and not important_open:
            return (
                "Coverage is high and no important assumptions remain. If nothing important is left to ask, wrap up: "
                f"target_type 'wrap_up', briefly say what you've captured, then ask: \"{CLOSING_QUESTION}\""
            )
        return ""

    # ------------------------------------------------------------------ merge
    def _merge(self, s: Session, data: dict, depth: int, source: str = "conversation") -> dict:
        turn = s.expert_turns
        delta: dict[str, list[str]] = {"knowledge": [], "updated": [], "assumptions": [], "resolved": [], "coverage": []}

        for raw in _list(data.get("knowledge")):
            if not isinstance(raw, dict):
                continue
            title, content = _s(raw.get("title"), 140), _s(raw.get("content"), 1200)
            if not (title or content):
                continue
            category = raw.get("category") if raw.get("category") in KNOWLEDGE_CATEGORIES else "context"
            specificity = raw.get("specificity") if raw.get("specificity") in ("general", "site", "personal") else "general"
            existing = s.find_knowledge(_s(raw.get("updates_id"), 12)) if raw.get("updates_id") else None
            if existing is None:
                existing = next(
                    (
                        k
                        for k in s.knowledge
                        if k.category == category and title and _similar(k.title, title) >= 0.85
                    ),
                    None,
                )
            if existing is not None:
                if content and content != existing.content:
                    existing.history.append(existing.content)
                    existing.content = content
                existing.why = _s(raw.get("why"), 600) or existing.why
                existing.conditions = _s(raw.get("conditions"), 300) or existing.conditions
                existing.source_quote = _s(raw.get("source_quote"), 300) or existing.source_quote
                if specificity != "general":
                    existing.specificity = specificity
                existing.source = "correction" if source == "conversation" else existing.source
                if existing.id not in delta["updated"]:
                    delta["updated"].append(existing.id)
                continue
            item = KnowledgeItem(
                id=s.next_id("k"),
                category=category,
                title=title or content[:60],
                content=content or title,
                why=_s(raw.get("why"), 600),
                conditions=_s(raw.get("conditions"), 300),
                source_quote=_s(raw.get("source_quote"), 300),
                specificity=specificity,
                in_reference=bool(raw.get("in_reference")) and bool(s.reference_points),
                source=source,
                turn=turn,
            )
            s.knowledge.append(item)
            delta["knowledge"].append(item.id)

        for raw in _list(data.get("assumptions")):
            if not isinstance(raw, dict):
                continue
            text = _s(raw.get("text"), 300)
            if not text or any(_similar(a.text, text) >= 0.7 for a in s.assumptions):
                continue
            a = Assumption(
                id=s.next_id("a"),
                kind=raw.get("kind") if raw.get("kind") in ASSUMPTION_KINDS else "unstated_assumption",
                text=text,
                evidence_quote=_s(raw.get("evidence_quote"), 300),
                importance=_i(raw.get("importance"), 3, 1, 5),
                suggested_question=_spoken(_s(raw.get("suggested_question"), 400)),
                depth=max(0, depth),
                turn_detected=turn,
            )
            s.assumptions.append(a)
            delta["assumptions"].append(a.id)

        for raw in _list(data.get("resolved")):
            if not isinstance(raw, dict):
                continue
            a = s.find_assumption(_s(raw.get("id"), 12))
            if a and a.status in ("open", "asked"):
                a.status = "resolved"
                a.resolution = _s(raw.get("resolution"), 600)
                a.turn_resolved = turn
                delta["resolved"].append(a.id)

        for raw in _list(data.get("coverage")):
            if not isinstance(raw, dict):
                continue
            dim = next((d for d in s.coverage if d.id == _s(raw.get("dimension_id"), 10)), None)
            if dim:
                dim.score = max(dim.score, _f(raw.get("score"), dim.score))
                note = _s(raw.get("note"), 240)
                if note:
                    dim.note = note
                delta["coverage"].append(dim.id)

        for qid in _list(data.get("answered_trainee_questions")):
            q = next((p for p in s.pending_questions if p.id == _s(qid, 12)), None)
            if q and q.status == "open":
                q.status = "answered"
                q.answer_ids = delta["knowledge"] + delta["updated"]
        return delta

    # -------------------------------------------------------------- documents
    def add_document(self, sid: str, filename: str, kind: str, text: str) -> dict:
        text = (text or "").strip()
        if not text:
            raise ValueError("No text could be read from that file.")
        if kind not in ("reference", "expert"):
            raise ValueError("kind must be 'reference' or 'expert'")
        truncated = len(text) > MAX_DOC_CHARS
        text = text[:MAX_DOC_CHARS]
        self.wait_idle(sid)

        with self._lock(sid):
            s = self.load(sid)
            doc = Document(filename=_s(filename, 200) or "document", kind=kind, chars=len(text))
            delta: dict[str, list[str]] = {"knowledge": [], "updated": [], "assumptions": [], "resolved": [], "coverage": []}

            if kind == "reference":
                prompt = f'Reference document "{doc.filename}":\n"""\n{text}\n"""\nList what it already states.'
                data = self.llm.call_tool(REFERENCE_SYSTEM, prompt, SUMMARIZE_REFERENCE_TOOL, max_tokens=2500)
                doc.summary = _s(data.get("summary"), 600)
                for point in _list(data.get("points"))[:30]:
                    p = _s(point, 300)
                    if p and not any(_similar(p, q) >= 0.8 for q in s.reference_points):
                        s.reference_points.append(p)
                s.reference_points = s.reference_points[:80]
            else:
                chunks = [text[i : i + DOC_CHUNK_CHARS] for i in range(0, len(text), DOC_CHUNK_CHARS)][:MAX_DOC_CHUNKS]
                for n, chunk in enumerate(chunks, 1):
                    data = self.llm.call_tool(
                        INTERVIEWER_SYSTEM,
                        self._extract_prompt(s, doc.filename, chunk, n, len(chunks)),
                        EXTRACT_TOOL,
                        max_tokens=3500,
                    )
                    part = self._merge(s, data, depth=0, source="expert_document")
                    for key in delta:
                        delta[key].extend(part[key])
                    if not s.topic and _s(data.get("suggested_topic")):
                        s.topic = _s(data.get("suggested_topic"), 120)
                doc.summary = (
                    f"Extracted {len(delta['knowledge'])} knowledge items and "
                    f"{len(delta['assumptions'])} assumptions to ask about."
                )
            if truncated:
                doc.summary += " (Document was long, so only the first part was read.)"
            s.documents.append(doc)
            s.transcript.append(Turn(role="system", text=f"Added {kind} document: {doc.filename}. {doc.summary}"))
            self.store.save(s)
            return {"document": doc.model_dump(), "delta": delta, "session": self.public(s)}

    # ---------------------------------------------------------------- trainee
    def ask(self, sid: str, question: str) -> dict:
        question = _s(question, 1000)
        if not question:
            raise ValueError("Empty question")
        self.wait_idle(sid)
        with self._lock(sid):
            s = self.load(sid)
            if not s.knowledge and not s.reference_points:
                data = {
                    "answer": "I haven't learned anything about this yet. I've saved your question for the expert.",
                    "cited_ids": [],
                    "covered": "no",
                    "question_for_expert": question,
                }
            else:
                data = self.llm.call_tool(
                    TRAINEE_SYSTEM,
                    self._trainee_prompt(s, question),
                    ANSWER_TRAINEE_TOOL,
                    model=settings.llm_fast_model,
                    max_tokens=1000,
                )
            answer = _spoken(_s(data.get("answer"), 1500)) or "I'm not sure."
            covered = data.get("covered") if data.get("covered") in ("yes", "partial", "no") else "no"
            cited = [k for k in (_s(c, 12) for c in _list(data.get("cited_ids"))) if s.find_knowledge(k)]

            pending: Optional[PendingQuestion] = None
            if covered != "yes":
                q_text = _s(data.get("question_for_expert"), 400) or question
                existing = next((p for p in s.pending_questions if _similar(p.text, q_text) >= 0.7), None)
                if existing is None:
                    pending = PendingQuestion(id=s.next_id("q"), text=q_text)
                    s.pending_questions.append(pending)
                else:
                    pending = existing

            s.trainee_log.append(TraineeExchange(question=question, answer=answer, covered=covered, cited_ids=cited))
            self.store.save(s)
            return {
                "answer": answer,
                "covered": covered,
                "cited": [s.find_knowledge(k).model_dump() for k in cited],
                "pending_question": pending.model_dump() if pending else None,
                "session": self.public(s),
            }

    # ---------------------------------------------------------------- outputs
    def generate_manual(self, sid: str) -> str:
        self.wait_idle(sid)
        with self._lock(sid):
            s = self.load(sid)
            if not s.knowledge and not s.reference_points:
                raise ValueError("Nothing has been captured yet. Talk to the apprentice first.")
            markdown = self.llm.complete_text(MANUAL_SYSTEM, self._manual_prompt(s), max_tokens=6000)
            s.manual_markdown = markdown
            self.store.save(s)
            return markdown

    def agent_pack(self, sid: str) -> str:
        """A portable system prompt that turns any LLM agent into a copy of this expert's know-how."""
        self.wait_idle(sid)
        s = self.load(sid)
        who = s.expert_name or "the expert"
        extra = ", ".join(x for x in (s.expert_role, s.site) if x)
        lines = [
            f"# Expert pack: {s.topic or 'untitled'}",
            "",
            f"You are an assistant carrying the know-how of {who}{f' ({extra})' if extra else ''}.",
            "Answer trainees' questions using ONLY the knowledge below. Prefer site-specific and personal knowledge",
            "over general practice, give the reasons when known, and if something isn't covered, say so and suggest",
            f"asking {who}. Never invent steps, numbers, locations or names.",
            "",
            "## Objective",
            s.objective or "(not set)",
            "",
            "## Captured knowledge",
        ]
        for cat in KNOWLEDGE_CATEGORIES:
            items = [k for k in s.knowledge if k.category == cat]
            if not items:
                continue
            lines += ["", f"### {CATEGORY_LABELS.get(cat, cat)}"]
            for k in items:
                tag = " [specific to this site/expert]" if k.not_in_any_manual else ""
                line = f"- {k.title}: {k.content}{tag}"
                if k.why:
                    line += f" Why: {k.why}"
                if k.conditions:
                    line += f" When: {k.conditions}"
                lines.append(line)
        if s.reference_points:
            lines += ["", "## What the reference manual already says"] + [f"- {p}" for p in s.reference_points]
        gaps = [a for a in s.assumptions if a.status in ("open", "asked")]
        pend = [q for q in s.pending_questions if q.status == "open"]
        if gaps or pend:
            lines += ["", "## Known gaps (say you don't know)"]
            lines += [f"- {a.text}" for a in gaps] + [f"- {q.text}" for q in pend]
        return "\n".join(lines) + "\n"

    def set_assumption_status(self, sid: str, aid: str, status: str) -> dict:
        if status not in ("open", "dismissed", "resolved"):
            raise ValueError("Invalid status")
        with self._lock(sid):
            s = self.load(sid)
            a = s.find_assumption(aid)
            if not a:
                raise NotFound(f"Assumption {aid} not found")
            a.status = status
            if status == "resolved" and not a.resolution:
                a.resolution = "Marked resolved by the expert."
            self.store.save(s)
            return self.public(s)

    def delete_knowledge(self, sid: str, kid: str) -> dict:
        with self._lock(sid):
            s = self.load(sid)
            before = len(s.knowledge)
            s.knowledge = [k for k in s.knowledge if k.id != kid]
            if len(s.knowledge) == before:
                raise NotFound(f"Knowledge item {kid} not found")
            self.store.save(s)
            return self.public(s)

    # ---------------------------------------------------------------- prompts
    def _profile(self, s: Session) -> str:
        parts = [p for p in (s.expert_name, s.expert_role, s.site) if p]
        return "EXPERT: " + " | ".join(parts) if parts else "EXPERT: (not specified)"

    def _reference_block(self, s: Session) -> str:
        if not s.reference_points:
            return "REFERENCE MANUAL: none uploaded."
        pts = "\n".join(f"- {p}" for p in s.reference_points[:60])
        return f"REFERENCE MANUAL (already written down; don't ask about these unless checking for differences):\n{pts}"

    def _knowledge_block(self, s: Session, limit: int = 80) -> str:
        if not s.knowledge:
            return "KNOWLEDGE ALREADY CAPTURED: nothing yet."
        items = s.knowledge[-limit:]
        lines = []
        for k in items:
            line = f"- {k.id} [{k.category}, {k.specificity}] {k.title}: {k.content}"
            if k.why:
                line += f" (why: {k.why})"
            lines.append(line[:320])
        older = len(s.knowledge) - len(items)
        head = "KNOWLEDGE ALREADY CAPTURED (don't re-ask):"
        if older > 0:
            head += f" [{older} older items omitted]"
        return head + "\n" + "\n".join(lines)

    def _assumption_block(self, s: Session) -> str:
        open_items = [a for a in s.assumptions if a.status in ("open", "asked")]
        if not open_items:
            return "OPEN ASSUMPTIONS: none."
        lines = [
            f"- {a.id} ({a.kind}, importance {a.importance}, depth {a.depth}, asked {a.times_asked}x) {a.text}"
            + (f' | expert said: "{a.evidence_quote}"' if a.evidence_quote else "")
            for a in open_items[-40:]
        ]
        return "OPEN ASSUMPTIONS (resolve these by id when the expert answers them):\n" + "\n".join(lines)

    def _coverage_block(self, s: Session) -> str:
        if not s.coverage:
            return "COVERAGE PLAN: not set."
        return "COVERAGE PLAN (score 0-1):\n" + "\n".join(
            f"- {d.id} {d.name}: {d.score:.2f}" + (f" ({d.note})" if d.note else "") for d in s.coverage
        )

    def _pending_block(self, s: Session) -> str:
        pend = [q for q in s.pending_questions if q.status == "open"]
        if not pend:
            return ""
        return "TRAINEE QUESTIONS WAITING FOR THE EXPERT (mark answered by id):\n" + "\n".join(
            f'- {q.id} "{q.text}"' for q in pend
        )

    def _transcript_block(self, s: Session, turns: int = 12) -> str:
        convo = [t for t in s.transcript if t.role != "system"][-turns:]
        lines = [f"{'Apprentice' if t.role == 'agent' else 'Expert'}: {t.text}" for t in convo]
        return "RECENT CONVERSATION:\n" + "\n".join(lines)

    def _kickoff_prompt(self, s: Session, text: str) -> str:
        parts = [
            "A new session is starting.",
            self._profile(s),
            self._reference_block(s),
        ]
        if s.knowledge or s.assumptions:
            parts += [
                "The expert also provided material in their own words before the conversation:",
                self._knowledge_block(s),
                self._assumption_block(s),
            ]
        parts += [
            f'You opened with: "{settings.opening_line}"',
            f'THE EXPERT ANSWERED:\n"""\n{text}\n"""',
            "Now:\n"
            "1. Work out the topic and the objective: what a trainee should be able to do or know by the end. If they "
            "named a task, the objective is to be able to do that task here, unsupervised. If they named their role "
            "or themselves, it's to understand how they do it here.\n"
            "2. Draft a coverage plan of 6-9 areas a newcomer needs for THIS topic. Tailor it to the type: for a "
            "task (purpose, what you need and where it is, the steps, judgement calls, problems and fixes, risks, "
            "tips); for a role (responsibilities, routines, people and relationships, tools and systems, decisions, "
            "pitfalls, unwritten rules); for a skill or craft (fundamentals, signals to watch for, common mistakes, "
            "how to practise, tips); for a system or process (purpose, inputs and outputs, quirks, failure modes, "
            "who owns what). Name the areas in the topic's own terms.\n"
            "3. If their answer already hides an important assumption worth asking about first, describe it in "
            "focus_assumption (knowledge is recorded separately, so don't list it here).\n"
            "4. Ask the first real question. Usually: get them to walk you through it from the very beginning, "
            "anchored in what they said. If their answer is very broad, help them narrow it to something they "
            "could show a new starter in one day. If a reference manual exists, you may ask how reality differs "
            "from it.",
        ]
        return "\n\n".join(parts)

    def _ask_prompt(self, s: Session, text: str, last_q: Optional[Turn], candidates: list[dict]) -> str:
        parts = [
            f"TOPIC: {s.topic}\nOBJECTIVE ({s.objective_type}): {s.objective}",
            self._profile(s),
            self._reference_block(s),
            self._coverage_block(s),
            self._knowledge_block(s),
            self._assumption_block(s),
        ]
        pending = self._pending_block(s)
        if pending:
            parts.append(pending)
        parts.append(self._transcript_block(s))
        if last_q:
            target = f"{last_q.target_type} {last_q.target_ref or ''}".strip()
            parts.append(f'YOUR LAST QUESTION ({target}): "{last_q.text}"')
        parts.append(f'THE EXPERT JUST SAID:\n"""\n{text}\n"""')
        if candidates:
            parts.append(
                "SUGGESTED NEXT TARGETS (highest priority first). Pick one unless the latest answer raised something "
                "more important that needs an immediate follow-up:\n"
                + "\n".join(f"{i + 1}. {c['type']} {c['ref']}: {c['label']}" for i, c in enumerate(candidates))
            )
        parts.append(
            f"Expert turn {s.expert_turns} of max {settings.max_turns}. Average coverage {s.avg_coverage:.2f}."
        )
        hint = self._wrap_hint(s)
        if hint:
            parts.append(hint)
        parts.append(
            "Now choose the single best next question. Usually the most valuable question probes the most important "
            "thing the expert just did automatically, assumed, left vague or referred to by a local name: describe "
            "it in focus_assumption and set target_ref to 'focus'. Otherwise pick from the suggested targets. Keep "
            "this response short; the knowledge itself is recorded separately."
        )
        return "\n\n".join(parts)

    def _record_prompt(self, s: Session, text: str, answered_q: Optional[Turn]) -> str:
        """Context for the background call that records what the expert's latest answer taught us."""
        parts = [
            f"TOPIC: {s.topic}\nOBJECTIVE ({s.objective_type}): {s.objective}",
            self._profile(s),
            self._reference_block(s),
            self._coverage_block(s),
            self._knowledge_block(s),
            self._assumption_block(s),
        ]
        pending = self._pending_block(s)
        if pending:
            parts.append(pending)
        parts.append(self._transcript_block(s, turns=10))
        if answered_q and answered_q.role == "agent":
            target = f"{answered_q.target_type} {answered_q.target_ref or ''}".strip()
            parts.append(f'THE QUESTION THE EXPERT WAS ANSWERING ({target}): "{answered_q.text}"')
        parts.append(f'THE EXPERT\'S LATEST ANSWER:\n"""\n{text}\n"""')
        asked = s.last_agent_turn()
        if asked and asked.target_type == "assumption" and asked.target_ref:
            a = s.find_assumption(asked.target_ref)
            if a:
                parts.append(
                    f"Your next question is already chosen and targets {a.id} ({a.text}). It is already recorded, "
                    "so don't list it again."
                )
        parts.append(
            "Now record the NEW knowledge in the expert's latest answer (only what they said or clearly implied), "
            "list any OTHER assumptions in it that aren't already listed, mark which open assumptions and trainee "
            "questions it resolves, and update coverage scores for the areas it contributed to."
        )
        return "\n\n".join(parts)

    def _extract_prompt(self, s: Session, filename: str, chunk: str, n: int, total: int) -> str:
        parts = [
            f'The expert provided material in their own words: "{filename}" (part {n} of {total}). It may be a '
            "transcript, notes or a written explanation.",
        ]
        if s.topic:
            parts.append(f"TOPIC: {s.topic}\nOBJECTIVE: {s.objective}")
        parts += [
            self._reference_block(s),
            self._knowledge_block(s),
            f'MATERIAL:\n"""\n{chunk}\n"""',
            "Extract the knowledge it contains, and every assumption, automatic action, vague term, site reference, "
            "judgement call, missing step or piece of jargon a new starter would trip over. Each assumption should "
            "include a natural suggested_question: these become your interview questions.",
        ]
        return "\n\n".join(parts)

    def _relevant_knowledge(self, s: Session, question: str, limit: int = 70) -> list[KnowledgeItem]:
        if len(s.knowledge) <= limit:
            return list(s.knowledge)
        q = _tokens(question)
        scored = sorted(
            s.knowledge,
            key=lambda k: len(q & _tokens(f"{k.title} {k.content} {k.why} {k.conditions}")),
            reverse=True,
        )
        return scored[:limit]

    def _trainee_prompt(self, s: Session, question: str) -> str:
        items = self._relevant_knowledge(s, question)
        knowledge = "\n".join(
            f"- {k.id} [{k.category}; {'site/personal' if k.not_in_any_manual else k.specificity}] {k.title}: "
            f"{k.content}" + (f" Why: {k.why}" if k.why else "") + (f" When: {k.conditions}" if k.conditions else "")
            for k in items
        ) or "(nothing captured yet)"
        ref = "\n".join(f"- {p}" for p in s.reference_points[:60]) or "(none)"
        return "\n\n".join(
            [
                f"TOPIC: {s.topic}\nOBJECTIVE: {s.objective}",
                self._profile(s),
                f"CAPTURED KNOWLEDGE:\n{knowledge}",
                f"REFERENCE MANUAL:\n{ref}",
                f'TRAINEE QUESTION:\n"""{question}"""',
            ]
        )

    def _manual_prompt(self, s: Session) -> str:
        sections = []
        for cat in KNOWLEDGE_CATEGORIES:
            items = [k for k in s.knowledge if k.category == cat]
            if not items:
                continue
            body = "\n".join(
                f"- {'★ ' if k.not_in_any_manual else ''}{k.title}: {k.content}"
                + (f" | Why: {k.why}" if k.why else "")
                + (f" | When: {k.conditions}" if k.conditions else "")
                for k in items
            )
            sections.append(f"{CATEGORY_LABELS.get(cat, cat).upper()}\n{body}")
        gaps = [a.text for a in s.assumptions if a.status in ("open", "asked")]
        gaps += [q.text for q in s.pending_questions if q.status == "open"]
        ref = "\n".join(f"- {p}" for p in s.reference_points) or "(none)"
        return "\n\n".join(
            [
                f"TOPIC: {s.topic}\nOBJECTIVE: {s.objective}",
                self._profile(s),
                "CAPTURED KNOWLEDGE (★ = specific to this site or expert, not in the manual):\n" + "\n\n".join(sections),
                f"REFERENCE MANUAL POINTS:\n{ref}",
                "OPEN QUESTIONS:\n" + ("\n".join(f"- {g}" for g in gaps) or "(none)"),
                "Write the guide. Choose a structure that fits the topic. For a task, for example: Overview; Before "
                "you start (what you need and where it is); Step by step; Judgement calls; Problems and fixes; Upkeep; "
                "Risks; Tips; Not yet covered. For a role or skill, organise by responsibilities, routines, decisions "
                f"and pitfalls instead. Include tips from {s.expert_name or 'the expert'}, and always end with Not yet "
                "covered. Skip sections with no content.",
            ]
        )


def default_coverage() -> list[CoverageDimension]:
    names = [
        ("Purpose and context", "What it's for, when it happens and what good looks like."),
        ("What you need and where it is", "Tools, materials, systems, files or access, and exactly where to find them."),
        ("The steps or routine", "The actual sequence, including the steps done automatically."),
        ("Judgement calls", "How decisions are made and what signals to watch for."),
        ("Problems and fixes", "What goes wrong here and the workarounds."),
        ("Risks and consequences", "What happens if something is skipped or done badly."),
        ("People and unwritten rules", "Who to ask, who decides, handovers and local norms."),
        ("Tips and shortcuts", "The expert's own habits that make it faster or better."),
    ]
    return [CoverageDimension(id=f"d{i + 1}", name=n, description=d) for i, (n, d) in enumerate(names)]


def kind_label(kind: str) -> str:
    return KIND_LABELS.get(kind, kind)
