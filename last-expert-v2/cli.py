#!/usr/bin/env python
"""Text interview in the terminal.

  python cli.py                       # you are the expert
  python cli.py --simulate --turns 12 # a simulated expert answers; prints a capture score
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import time
from pathlib import Path

from apprentice import engine, llm, store
from apprentice.models import Session

ROOT = Path(__file__).parent
FIXTURE = ROOT / "tests" / "fixtures" / "expert_mineral_id.md"


def fixture_meta(text: str) -> tuple[str, str]:
    obj = next((l.split(":", 1)[1].strip() for l in text.splitlines() if l.startswith("Objective:")),
               "How to identify common minerals in a hand sample")
    name = next((l.split(":", 1)[1].strip() for l in text.splitlines() if l.startswith("Name:")), "Sam")
    return obj, name

C = {"agent": "\033[96m", "expert": "\033[93m", "log": "\033[90m", "end": "\033[0m", "bold": "\033[1m"}

SIM_SYSTEM = """You are role-playing a real human expert being interviewed by an apprentice on a voice call.
Your hidden knowledge is below. Answer like a REAL expert, not a textbook:
- Speak naturally and casually, 2-4 short sentences (under 70 words), as if talking out loud. No lists or markdown.
- Be imperfect like a real expert: use jargon without defining it (streak, conchoidal, Mohs, habit), skip steps that feel obvious to you, and use vague phrases like "you just know", "basically", "usually", "it depends", "you get a feel for it" in most answers.
- Answer only the question asked, and only partly: give the gist, not every detail. Reveal at most 1-2 hidden facts per answer, and only facts the question invites. Precise follow-up questions get precise answers.
- If asked a precise, well-targeted question, answer it honestly and concretely using your hidden knowledge.
- Never invent facts beyond your hidden knowledge; if asked something you don't cover, give a vague practical answer or say it doesn't come up much.

# Your hidden knowledge
"""

JUDGE_SCHEMA = {
    "type": "object",
    "properties": {"captured": {"type": "array", "items": {"type": "integer"}},
                   "partial": {"type": "array", "items": {"type": "integer"}}},
    "required": ["captured", "partial"], "additionalProperties": False,
}


def p(kind: str, text: str) -> None:
    label = {"agent": "APPRENTICE", "expert": "EXPERT"}.get(kind, "")
    print(f"{C.get(kind, '')}{C['bold'] if label else ''}{label + ': ' if label else ''}{C['end'] if label else ''}"
          f"{C.get(kind, '')}{text}{C['end']}", flush=True)


async def sim_expert_reply(knowledge: str, s: Session) -> str:
    # flip roles: the apprentice is the "user" for the simulated expert
    msgs = []
    for t in s.transcript:
        role = "user" if t.speaker == "agent" else "assistant"
        if msgs and msgs[-1]["role"] == role:
            msgs[-1]["content"] += "\n" + t.text
        else:
            msgs.append({"role": role, "content": t.text})
    return await llm.complete("analyst", SIM_SYSTEM + knowledge, msgs, max_tokens=450, effort="low")


async def capture_score(knowledge: str, s: Session) -> dict:
    items = "\n".join(f"{i.id}: {i.content}" for i in s.items)
    prompt = ("Below are numbered hidden FACTS an expert knows, and the knowledge ITEMS an interviewer captured.\n"
              "For each fact, decide if it is captured (the items convey the fact's substance), partially captured "
              "(only part of it), or not. Return fact numbers.\n\n# FACTS\n" + knowledge.split("## Facts", 1)[-1] +
              "\n\n# ITEMS\n" + (items or "(none)"))
    out = await llm.complete("analyst", "You are a strict, fair grader.", [{"role": "user", "content": prompt}],
                             max_tokens=1500, schema=JUDGE_SCHEMA, effort="low")
    total = sum(1 for line in knowledge.splitlines() if line[:3].strip().rstrip(".").isdigit())
    cap, part = set(out["captured"]), set(out["partial"]) - set(out["captured"])
    score = (len(cap) + 0.5 * len(part)) / total
    return {"total": total, "captured": len(cap), "partial": len(part), "score": round(100 * score, 1)}


async def run(args) -> None:
    if args.simulate:
        knowledge = Path(args.fixture).read_text() if args.fixture else FIXTURE.read_text()
        s = engine.new_session(*fixture_meta(knowledge))
    else:
        obj = input("What will you teach today? > ").strip() or "How to make a great cup of coffee"
        name = input("Your name? > ").strip() or "Expert"
        s = engine.new_session(obj, name)
    print(f"{C['log']}session {s.id} -> sessions/{s.id}/state.json{C['end']}")
    p("agent", s.transcript[-1].text)
    on_log = lambda line: p("log", f"   [analyst] {line}")
    r = engine.runner(s, on_log)
    timings = []
    for turn in range(args.turns):
        if args.simulate:
            answer = await sim_expert_reply(knowledge, s)
            p("expert", answer)
        else:
            answer = (await asyncio.to_thread(input, f"{C['expert']}YOU: {C['end']}")).strip()
            if answer.lower() in ("quit", "exit", "q"):
                break
            if not answer:
                continue
        t0 = time.time()
        reply = await engine.text_turn(s, answer, on_log)
        timings.append(time.time() - t0)
        p("agent", reply)
        if args.simulate:
            await r.wait_idle()  # keep the simulated run deterministic
        if s.phase == "done":
            break
    await r.wait_idle()
    print(f"\n{C['bold']}Items: {len(s.items)}  Gaps: {len(s.gaps)} "
          f"(resolved {sum(g.status == 'resolved' for g in s.gaps)})  Coverage: {s.coverage_pct()}%  "
          f"Phase: {s.phase}{C['end']}")
    if timings:
        print(f"Interviewer reply latency: avg {sum(timings)/len(timings):.2f}s, max {max(timings):.2f}s")
    if args.simulate:
        sc = await capture_score(knowledge, s)
        n_q = sum(1 for t in s.transcript if t.speaker == "agent") - 1
        print(f"{C['bold']}CAPTURE SCORE: {sc['score']}% ({sc['captured']} full + {sc['partial']} partial "
              f"of {sc['total']} hidden facts) in {n_q} questions{C['end']}")
        (store.session_dir(s.id) / "capture_score.json").write_text(json.dumps(sc))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--simulate", action="store_true")
    ap.add_argument("--turns", type=int, default=30)
    ap.add_argument("--fixture", help="hidden-knowledge file for --simulate (default: minerals)")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING)
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
