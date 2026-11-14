"""Text-mode interview for testing the reasoning loop.

  python cli.py "measuring mud weight with a mud balance"          # real model
  python cli.py "measuring mud weight" --mock                       # no API key needed
  python cli.py --learn session.json                                # ask a trained agent questions
Type /map to see the knowledge map, /think to toggle the agent's reasoning, /done to finish.
"""
import argparse
import json
import pathlib
import textwrap

from dotenv import load_dotenv

from expert_agent import Interviewer, KnowledgeMap


def wrap(s, ind="    "):
    return textwrap.fill(str(s), 100, initial_indent=ind, subsequent_indent=ind)


def main():
    load_dotenv()
    p = argparse.ArgumentParser()
    p.add_argument("topic", nargs="?", default="")
    p.add_argument("--objective", default="")
    p.add_argument("--mock", action="store_true")
    p.add_argument("--learn", help="path to a saved session json")
    p.add_argument("--out", default="session")
    a = p.parse_args()

    if a.mock:
        from expert_agent.mock import MockLLM
        llm = MockLLM()
    else:
        from expert_agent.llm import LLM
        llm = LLM()

    if a.learn:
        iv = Interviewer(llm, "loaded")
        iv.km = KnowledgeMap.from_json(pathlib.Path(a.learn).read_text())
        print(f"Learner mode for: {iv.km.topic}. Ask anything; empty line to quit.")
        while (q := input("\nYOU: ").strip()):
            print(wrap(iv.ask_as_learner(q), "AGENT: "))
        return

    iv = Interviewer(llm, a.topic or input("Task to learn: "), a.objective)
    show = True
    print("\nAGENT:", iv.start())
    while True:
        ans = input("\nEXPERT: ").strip()
        if ans == "/map":
            print(iv.km.outline()); continue
        if ans == "/think":
            show = not show; print("thinking", "on" if show else "off"); continue
        if ans in ("/done", ""):
            break
        r = iv.respond(ans)
        if show:
            print("\n--- agent thinking ---")
            print(wrap("heard: " + r.reasoning.get("summary_of_answer", "")))
            if r.tacit: print(wrap("tacit signals: " + ", ".join(r.tacit)))
            for c in r.changes: print(wrap("+ " + c))
            print(wrap("sequence: " + r.reasoning["sequence_check"]))
            print(wrap("beginner would get stuck: " + r.reasoning["novice_simulation"]))
            print(wrap("autopilot: " + r.reasoning["autopilot_hunt"]))
            for c in r.candidates:
                mark = ">>" if c is r.candidates[0] else "  "
                print(wrap(f"{mark} [{c['score']}] ({c['type']} {c['target']}/{c['slot']}) {c['question']}"))
            print(wrap(f"coverage {r.coverage['score']:.0%} | {r.coverage['steps']} steps | "
                       f"{r.coverage['open_assumptions']} open assumptions"))
            print("----------------------")
        print("\nAGENT:", r.question)
        if r.done:
            break

    pathlib.Path(a.out + ".json").write_text(iv.km.to_json())
    pathlib.Path(a.out + ".md").write_text(iv.km.to_markdown())
    pathlib.Path(a.out + "_transcript.json").write_text(json.dumps(iv.transcript, indent=2))
    print(f"\nSaved {a.out}.json, {a.out}.md, {a.out}_transcript.json")


if __name__ == "__main__":
    main()
