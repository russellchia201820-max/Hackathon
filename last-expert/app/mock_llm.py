"""Offline stand-in for Claude, used when no API key is set (LLM_PROVIDER=mock or no key).

It uses simple heuristics so the whole app can be developed, tested and demoed without credits.
The real intelligence lives in the prompts used with Claude; this just keeps the plumbing honest.
"""
from __future__ import annotations

import os
import re
import time
from typing import Any, Optional

_SENT = re.compile(r"(?<=[.!?])\s+|\n+")
_TOK = re.compile(r"[a-z0-9']+")

VAGUE = re.compile(
    r"\b(a bit|a little|a while|until it looks right|until it's right|properly|tight|loose|usually|normally|"
    r"sometimes|enough|about right|feels right|roughly|the usual|ages)\b",
    re.I,
)
AUTOMATIC = re.compile(
    r"\b(?:just|quickly|always)\s+((?:check|clean|tap|test|look at|wipe|adjust|reset|top up|bleed|prime|tighten|"
    r"flush|swap|give)\w*(?:\s+\w+){0,2})",
    re.I,
)
SITE = re.compile(
    r"\b((?:the|our)\s+(?:old\s+|blue\s+|red\s+|green\s+|back\s+|front\s+|main\s+|spare\s+|big\s+|small\s+)?"
    r"(?:cabinet|cage|store|shed|room|shelf|drawer|locker|bay|workshop|panel|gauge|valve|machine|rig|trolley|"
    r"one))\b",
    re.I,
)
JUDGEMENT = re.compile(r"\b(if (?:it|the \w+) (?:sounds|looks|feels|smells|seems)[^.,;]*)", re.I)
ACRONYM = re.compile(r"\b([A-Z]{2,5})\b")

CATEGORY_WORDS = [
    ("location", ("where", "kept", "located", "cabinet", "shelf", "drawer", "store", "room", "locker", "cage")),
    ("maintenance", ("maintain", "maintenance", "service", "replace", "calibrat", "grease", "every month", "weekly")),
    ("problem_fix", ("problem", "fault", "broken", "issue", "fails", "stuck", "leak", "jam", "workaround")),
    ("safety", ("safety", "careful", "danger", "gloves", "goggles", "ppe", "burn", "hazard", "never")),
    ("judgement_rule", ("if it", "when it", "sounds", "looks like", "feels", "smell", "judge")),
    ("tip", ("tip", "trick", "always", "i like to", "my way", "faster")),
    ("quality_check", ("check", "verify", "reading", "test", "should read", "measure")),
    ("people_process", ("ask", "sign off", "supervisor", "handover", "log", "record", "permit")),
    ("tool_equipment", ("tool", "spanner", "wrench", "gauge", "meter", "balance", "machine", "kit")),
]

DEFAULT_PLAN = [
    ("d1", "Purpose and context", "What it's for and what good looks like."),
    ("d2", "What you need and where it is", "Tools, systems, files or access, and where to find them."),
    ("d3", "Quirks and upkeep", "What needs looking after and how to tell something is off."),
    ("d4", "The steps or routine", "The actual sequence, including automatic steps."),
    ("d5", "Judgement calls", "What signals to watch for."),
    ("d6", "Problems and fixes", "What goes wrong here and the workarounds."),
    ("d7", "Risks and consequences", "What happens if something is skipped."),
    ("d8", "People and unwritten rules", "Who to ask and local norms."),
]


def _between(text: str, start: str, end: str = '"""') -> str:
    i = text.find(start)
    if i < 0:
        return ""
    i += len(start)
    j = text.find(end, i)
    return text[i : j if j >= 0 else None].strip()


def _sentences(text: str) -> list[str]:
    return [s.strip(" -•\t") for s in _SENT.split(text) if len(s.split()) >= 5]


def _category(sentence: str) -> str:
    low = sentence.lower()
    for cat, words in CATEGORY_WORDS:
        if any(w in low for w in words):
            return cat
    return "procedure_step"


def _specificity(sentence: str) -> str:
    low = sentence.lower()
    if re.search(r"\b(i always|i like|my |i tend|personally|my trick)\b", low):
        return "personal"
    if SITE.search(sentence) or re.search(r"\b(here|our|this site|on this rig|in this lab)\b", low):
        return "site"
    return "general"


def _knowledge(text: str, limit: int = 6) -> list[dict]:
    items = []
    for s in _sentences(text)[:limit]:
        words = s.split()
        items.append(
            {
                "category": _category(s),
                "title": " ".join(words[:7]).rstrip(".,"),
                "content": s,
                "why": s.split("because", 1)[1].strip(" .") if "because" in s.lower() else "",
                "source_quote": " ".join(words[:14]),
                "specificity": _specificity(s),
                "in_reference": False,
            }
        )
    return items


def _assumptions(text: str, limit: int = 4) -> list[dict]:
    found: list[dict] = []

    def add(kind: str, phrase: str, gap: str, question: str, importance: int) -> None:
        if len(found) < limit and not any(f["evidence_quote"].lower() == phrase.lower() for f in found):
            found.append(
                {
                    "kind": kind,
                    "text": gap,
                    "evidence_quote": phrase,
                    "importance": importance,
                    "suggested_question": question,
                }
            )

    for m in AUTOMATIC.finditer(text):
        p = m.group(1).strip()
        add("automatic_action", m.group(0), f"What '{p}' actually involves",
            f"You said you {m.group(0).lower()}. What exactly are you checking for?", 4)
    for m in JUDGEMENT.finditer(text):
        p = m.group(1).strip()
        add("judgement_call", p, f"How to tell '{p}'", f"You said '{p}'. What would I actually notice?", 4)
    for m in SITE.finditer(text):
        p = m.group(1).strip()
        add("site_reference", p, f"Where {p} is and what it is",
            f"Where exactly is {p.lower()}, and how would I find it on my first day?", 3)
    for m in VAGUE.finditer(text):
        p = m.group(1).strip()
        add("vague_term", p, f"What '{p}' means in practice",
            f"You said '{p}'. How would I know exactly when it's {p.lower()}?", 3)
    for m in ACRONYM.finditer(text):
        p = m.group(1)
        if p not in ("I", "OK", "AM", "PM"):
            add("jargon", p, f"What {p} stands for", f"What does {p} mean here?", 2)
    return found


class MockLLM:
    name = "mock"

    def call_tool(self, system: str, user_text: str, tool: dict, model: Optional[str] = None, max_tokens: int = 0) -> dict[str, Any]:
        handler = getattr(self, f"_tool_{tool['name']}", None)
        if handler is None:
            raise RuntimeError(f"Mock has no handler for tool {tool['name']}")
        return handler(user_text)

    def complete_text(self, system: str, user_text: str, model: Optional[str] = None, max_tokens: int = 0) -> str:
        topic = _between(user_text, "TOPIC: ", "\n") or "this task"
        knowledge = _between(user_text, "CAPTURED KNOWLEDGE (★ = specific to this site or expert, not in the manual):\n", "\n\nREFERENCE MANUAL POINTS:")
        gaps = _between(user_text, "OPEN QUESTIONS:\n", "\n\nWrite the training guide")
        out = [f"# Training guide: {topic}", "", "_Generated offline by the mock brain. Set ANTHROPIC_API_KEY for the real guide._", ""]
        for block in knowledge.split("\n\n"):
            lines = block.strip().split("\n")
            if not lines or not lines[0]:
                continue
            out += [f"## {lines[0].title()}", *lines[1:], ""]
        out += ["## Not yet covered", gaps or "(nothing)"]
        return "\n".join(out)

    # -- tools -----------------------------------------------------------
    def _tool_plan_session(self, prompt: str) -> dict:
        answer = _between(prompt, 'THE EXPERT ANSWERED:\n"""\n')
        topic = re.sub(
            r"^(i want to|i'd like to|i would like to|let's|lets)?\s*(talk about|show you|teach you|explain)?\s*(how to|how we|how i)?\s*",
            "",
            answer,
            flags=re.I,
        ).strip(" .")
        topic = " ".join(topic.split()[:8]) or "this task"
        return {
            "topic": topic[:1].upper() + topic[1:],
            "objective_type": "task",
            "objective": f"A new starter can do {topic.lower()} here, unsupervised.",
            "coverage_plan": [{"id": i, "name": n, "description": d} for i, n, d in DEFAULT_PLAN],
            "next_question": {
                "text": f"Walk me through {topic.lower()} from the very start. What's the first thing you do?",
                "target_type": "coverage",
                "target_ref": "d4",
                "why": "Start with the expert's own account of the task.",
            },
        }

    def _tool_ask_next(self, prompt: str) -> dict:
        answer = _between(prompt, 'THE EXPERT JUST SAID:\n"""\n')
        wants_stop = bool(re.search(r"\b(that's all|that is all|i have to go|gotta go|let's stop|we're done)\b", answer, re.I))
        if wants_stop or "TIME CHECK" in prompt:
            return {
                "expert_intent": "wants_to_stop" if wants_stop else "answering",
                "next_question": {
                    "text": "Thanks, that's really useful. Before we finish, what's the one thing new starters always get wrong here?",
                    "target_type": "wrap_up", "target_ref": "", "why": "Wrapping up.",
                },
            }
        assumptions = _assumptions(answer)
        if assumptions:
            a = max(assumptions, key=lambda x: x["importance"])
            return {
                "expert_intent": "answering",
                "focus_assumption": a,
                "next_question": {"text": a["suggested_question"], "target_type": "assumption", "target_ref": "focus",
                                  "why": f"The expert's {a['kind'].replace('_', ' ')} would trip up a new starter."},
            }
        target = re.search(r"^1\. (assumption|coverage|trainee_question) (\w+): (.+)$", prompt, flags=re.M)
        if target:
            kind, ref, label = target.groups()
            label = re.sub(r"^\[[^\]]*\]\s*", "", label)
            label = re.sub(r"\s*\(expert said:.*$", "", label)
            if kind == "coverage":
                name = label.split(" (")[0]
                text = f"Let's talk about {name.lower()}. What would a new starter need to know?"
            elif kind == "trainee_question":
                q = label.replace("A trainee asked: ", "").strip('"')
                text = f"A trainee asked me something I couldn't answer: {q} What's the answer here?"
            else:
                text = f"Earlier something wasn't clear to me: {label[0].lower() + label[1:]}. Can you explain?"
            return {"expert_intent": "answering",
                    "next_question": {"text": text, "target_type": kind, "target_ref": ref, "why": "Top-ranked open target."}}
        return {"expert_intent": "answering",
                "next_question": {"text": "What usually goes wrong for people doing this for the first time?",
                                  "target_type": "coverage", "target_ref": "", "why": "Probe problems and fixes."}}

    def _tool_record_knowledge(self, prompt: str) -> dict:
        delay = float(os.getenv("MOCK_RECORD_DELAY", "0") or 0)  # simulate Claude's latency for UI work
        if delay:
            time.sleep(delay)
        answer = _between(prompt, 'THE EXPERT\'S LATEST ANSWER:\n"""\n')
        knowledge = _knowledge(answer)
        already = _between(prompt, "Your next question is already chosen and targets", "\n")
        assumptions = [a for a in _assumptions(answer) if a["text"] not in already]

        resolved = []
        m = re.search(r"THE QUESTION THE EXPERT WAS ANSWERING \(assumption (a\d+)\)", prompt)
        if m and len(answer.split()) >= 6:
            resolved.append({"id": m.group(1), "resolution": answer[:200]})

        dims = re.findall(r"^- (d\d+) ([^:\n]+): ([0-9.]+)", prompt, flags=re.M)
        current = {d: float(score) for d, _, score in dims}
        mapping = {
            "location": "d2", "tool_equipment": "d2", "maintenance": "d3", "procedure_step": "d4",
            "quality_check": "d4", "judgement_rule": "d5", "problem_fix": "d6", "safety": "d7",
            "people_process": "d8", "tip": "d4", "context": "d1",
        }
        coverage = []
        for cat in {k["category"] for k in knowledge}:
            d = mapping.get(cat)
            if d in current:
                coverage.append({"dimension_id": d, "score": min(1.0, current[d] + 0.3), "note": ""})
        answered = re.findall(r'^- (q\d+) "', prompt, flags=re.M) if "supervisor" in answer.lower() else []
        return {
            "knowledge": knowledge,
            "assumptions": assumptions,
            "resolved": resolved,
            "answered_trainee_questions": answered,
            "coverage": coverage,
        }

    def _tool_extract_knowledge(self, prompt: str) -> dict:
        material = _between(prompt, 'MATERIAL:\n"""\n')
        return {
            "suggested_topic": " ".join(material.split()[:6]),
            "knowledge": _knowledge(material, 10),
            "assumptions": _assumptions(material, 8),
        }

    def _tool_summarize_reference(self, prompt: str) -> dict:
        body = _between(prompt, '"""\n')
        points = _sentences(body)[:20]
        return {"title": "Reference", "summary": f"Reference document with {len(points)} stated points.", "points": points}

    def _tool_answer_trainee(self, prompt: str) -> dict:
        question = _between(prompt, 'TRAINEE QUESTION:\n"""', '"""')
        lines = re.findall(r"^- (k\d+) \[[^\]]*\] ([^\n]+)$", prompt, flags=re.M)
        stop = {"the", "a", "an", "is", "are", "how", "what", "do", "does", "i", "to", "where", "who", "you", "it",
                "of", "on", "in", "off", "and", "or", "for", "with", "my", "we", "be", "should", "can", "when", "why"}
        q = set(_TOK.findall(question.lower())) - stop
        best = None
        best_score = 0
        for kid, text in lines:
            score = len(q & set(_TOK.findall(text.lower())))
            if score > best_score:
                best, best_score = (kid, text), score
        if best and best_score >= 2:
            return {"answer": best[1], "cited_ids": [best[0]], "covered": "yes" if best_score >= 3 else "partial",
                    "question_for_expert": "" if best_score >= 3 else question}
        return {"answer": "The expert hasn't covered that yet. I've saved it as a question for them.",
                "cited_ids": [], "covered": "no", "question_for_expert": question}
