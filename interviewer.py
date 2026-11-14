"""The interview loop.

Each expert turn runs:
  1. tacit-language scan (code)        -> phrases like "just", "until it looks right"
  2. ANALYSE pass (model)              -> patch: steps, order, slots, terms, assumptions
  3. apply patch + compute gaps (code) -> coverage score and the list of unfilled slots
  4. PLAN pass (model)                 -> sequence check, novice simulation, autopilot hunt,
                                          3-6 scored candidate questions
  5. select (code)                     -> weighted score, repeat penalty, depth bonus, gating
                                          for teach-back and wrap-up
Everything is returned so the UI can show the agent's thinking live.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .knowledge import STEP_SLOTS, TASK_SLOTS, KnowledgeMap, tacit_signals
from .prompts import (ANALYSE_SYSTEM, ANALYSE_TOOL, LEARNER_SYSTEM, OPENING_TEMPLATE, PLAN_SYSTEM,
                      PLAN_TOOL)

WEIGHTS = {"beginner_blocking": 0.45, "uncertainty": 0.30, "flow_fit": 0.25}
REPEAT_PENALTY = 1.5      # per time the same target+slot was already asked
DEPTH_BONUS = 0.6         # stay on a vague answer before moving on
TEACH_BACK_AT = 0.75      # coverage needed before teach-back is allowed
MAX_TURNS = 30


@dataclass
class TurnResult:
    question: str
    done: bool
    coverage: dict
    changes: list[str]
    tacit: list[str]
    reasoning: dict = field(default_factory=dict)      # sequence_check, novice_simulation, autopilot_hunt
    candidates: list[dict] = field(default_factory=list)  # all candidates with final scores
    chosen: Optional[dict] = None


class Interviewer:
    def __init__(self, llm, topic: str, objective: str = ""):
        self.llm = llm
        self.km = KnowledgeMap(topic=topic, objective=objective or f"Learn to do '{topic}' exactly as this expert does it.")
        self.transcript: list[dict] = []   # {"role": "agent"|"expert", "text", "turn"}
        self.turn = 0
        self.last_q: Optional[dict] = None
        self.teach_back_done = False

    # ------------------------------------------------------------------ public
    def start(self) -> str:
        q = OPENING_TEMPLATE.format(topic=self.km.topic)
        self._say(q, {"target": "task", "slot": "overview", "type": "context"})
        return q

    def respond(self, expert_text: str) -> TurnResult:
        self.turn += 1
        self.transcript.append({"role": "expert", "text": expert_text, "turn": self.turn})
        tacit = tacit_signals(expert_text)

        # ---- pass 1: analyse
        patch = self.llm.call_tool(ANALYSE_SYSTEM, self._analyse_input(expert_text, tacit), ANALYSE_TOOL)
        changes = self.km.apply_patch(patch, self.turn)
        if self.last_q and self.last_q.get("type") == "teach_back":
            self.teach_back_done = True
        cov = self.km.coverage()

        # ---- pass 2: plan
        plan = self.llm.call_tool(PLAN_SYSTEM, self._plan_input(expert_text, tacit, cov), PLAN_TOOL)
        cands = self._score(plan.get("candidates") or [], expert_text, tacit, cov)
        chosen = cands[0] if cands else self._fallback()

        done = chosen.get("type") == "wrap_up" or self.turn >= MAX_TURNS
        self._say(chosen["question"], chosen)
        return TurnResult(
            question=chosen["question"], done=done, coverage=cov, changes=changes, tacit=tacit,
            reasoning={k: plan.get(k, "") for k in ("sequence_check", "novice_simulation", "autopilot_hunt")}
                      | {"summary_of_answer": patch.get("summary_of_answer", "")},
            candidates=cands, chosen=chosen,
        )

    def ask_as_learner(self, question: str) -> str:
        user = f"KNOWLEDGE MAP:\n{self.km.outline()}\n\nLEARNER QUESTION: {question}"
        return self.llm.text(LEARNER_SYSTEM, user)

    # ---------------------------------------------------------------- internals
    def _say(self, text: str, meta: dict):
        self.transcript.append({"role": "agent", "text": text, "turn": self.turn})
        self.km.asked.append({"target": meta.get("target", ""), "slot": meta.get("slot", ""),
                              "type": meta.get("type", ""), "turn": self.turn})
        self.last_q = meta

    def _recent(self, n: int = 8) -> str:
        return "\n".join(f"{t['role'].upper()}: {t['text']}" for t in self.transcript[-n:])

    def _analyse_input(self, expert_text: str, tacit: list[str]) -> str:
        last = self.last_q or {}
        return (f"CURRENT KNOWLEDGE MAP:\n{self.km.outline()}\n\n"
                f"RECENT CONVERSATION:\n{self._recent()}\n\n"
                f"THE QUESTION THAT WAS ASKED targeted: {last.get('target')} / {last.get('slot')} ({last.get('type')})\n"
                f"EXPERT'S LATEST ANSWER:\n\"\"\"{expert_text}\"\"\"\n\n"
                f"TACIT-LANGUAGE SIGNALS FOUND: {', '.join(tacit) or 'none'}\n"
                "Update the map. Pay special attention to the signals: each one usually hides an assumption.")

    def _plan_input(self, expert_text: str, tacit: list[str], cov: dict) -> str:
        gaps = self.km.gaps()
        gap_lines = "\n".join(
            f"- {g['target']} / {g['slot']}: {g['desc']}"
            + (f" (step: {g['step'][:50]})" if g.get('step') else "")
            + (f" [asked {self.km.times_asked(g['target'], g['slot'])}x]" if self.km.times_asked(g['target'], g['slot']) else "")
            for g in gaps[:60])
        asked = "\n".join(f"- turn {q['turn']}: {q['type']} -> {q['target']}/{q['slot']}" for q in self.km.asked[-12:])
        phase = ("TEACH-BACK ALLOWED" if cov["score"] >= TEACH_BACK_AT and not self.teach_back_done
                 else "WRAP-UP ALLOWED" if self.teach_back_done else "KEEP EXPLORING (no teach-back yet)")
        return (f"KNOWLEDGE MAP:\n{self.km.outline()}\n\n"
                f"COVERAGE: {cov}\nPHASE: {phase}\n\n"
                f"UNFILLED SLOTS ({len(gaps)}):\n{gap_lines or 'none'}\n\n"
                f"QUESTIONS ALREADY ASKED:\n{asked}\n\n"
                f"RECENT CONVERSATION:\n{self._recent()}\n\n"
                f"TACIT SIGNALS IN LAST ANSWER: {', '.join(tacit) or 'none'}\n"
                f"LAST ANSWER LENGTH: {len(expert_text.split())} words")

    def _score(self, cands: list[dict], expert_text: str, tacit: list[str], cov: dict) -> list[dict]:
        vague = len(tacit) >= 2 or len(expert_text.split()) < 12
        last_target = (self.last_q or {}).get("target", "")
        out = []
        for c in cands:
            t = c.get("type", "")
            if t == "teach_back" and (cov["score"] < TEACH_BACK_AT or self.teach_back_done):
                continue
            if t == "wrap_up" and not self.teach_back_done:
                continue
            base = sum(w * float(c.get(k, 3)) for k, w in WEIGHTS.items())
            pen = REPEAT_PENALTY * self.km.times_asked(c.get("target", ""), c.get("slot", ""))
            bonus = DEPTH_BONUS if (vague and last_target and c.get("target", "").startswith(last_target)) else 0.0
            if t == "teach_back":
                bonus += 1.0  # once allowed, it is the most valuable check
            c = dict(c, score=round(base - pen + bonus, 3), penalty=pen, bonus=bonus)
            out.append(c)
        out.sort(key=lambda c: -c["score"])
        return out

    def _fallback(self) -> dict:
        """If the model gives nothing usable, ask about the most important gap directly."""
        gaps = self.km.gaps()
        order = ["action", "why", "done_cue", "how", "pitfalls", "tools", "assumption", "meaning"] + list(TASK_SLOTS)
        gaps.sort(key=lambda g: (self.km.times_asked(g["target"], g["slot"]), order.index(g["slot"]) if g["slot"] in order else 99))
        if not gaps:
            return {"question": "I think I've got it. Is there anything you always tell new people that we haven't covered?",
                    "type": "wrap_up", "target": "task", "slot": "wrap_up"}
        g = gaps[0]
        templ = {
            "why": "Why do you do '{s}'? What would happen if I skipped it?",
            "done_cue": "When you do '{s}', how do you know it's done right? What do you look for?",
            "how": "For '{s}', how exactly do you do it? Any amounts, settings or timing?",
            "pitfalls": "What usually goes wrong with '{s}', and how do you fix it?",
            "tools": "What do you need to have in hand for '{s}'?",
            "assumption": "Quick check: {s}",
        }
        s = g.get("step") or g["desc"]
        q = templ.get(g["slot"], "Could you tell me more about {s}?").format(s=s)
        return {"question": q, "type": "assumption" if g["slot"] == "assumption" else g["slot"],
                "target": g["target"], "slot": g["slot"], "score": 0}
