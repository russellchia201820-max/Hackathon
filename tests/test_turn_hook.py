"""Unit test of the voice turn hook + analyst merge, with the LLM stubbed out (no keys needed).

Run: python tests/test_turn_hook.py
"""
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "agent"))

import apprentice.analyst as analyst_mod  # noqa: E402
from apprentice import engine, store  # noqa: E402

CANNED = {
    "new_items": [{"slot": "steps", "content": "Check lustre first.", "source_quote": "lustre first",
                   "turn_index": 1, "confidence": "high"}],
    "new_gaps": [{"kind": "vague_term", "anchor_quote": "you just know", "question": "What exactly tells you?",
                  "slot": "cues", "priority": 5, "topic": "feel"}],
    "asked_gap_ids": [], "resolved": [], "dropped_gap_ids": [],
    "coverage_updates": [{"slot": "steps", "status": "partial", "note": "only first step"}],
    "current_topic": "lustre", "ready_to_wrap_up": False,
}


async def fake_analyse(s):
    await asyncio.sleep(0.2)
    return CANNED


async def main():
    analyst_mod.analyse = fake_analyse
    import worker
    from livekit.agents import llm

    s = engine.new_session("Identify minerals", "Sam", mode="voice")
    agent = worker.Apprentice(s)
    ctx = llm.ChatContext.empty()
    ctx.add_message(role="assistant", content=s.transcript[0].text)
    ctx.add_message(role="user", content="Lustre first, then you just know.")
    t0 = asyncio.get_event_loop().time()
    await worker.handle_expert_turn(agent, ctx, "Lustre first, then you just know.")
    hook_ms = 1000 * (asyncio.get_event_loop().time() - t0)
    assert hook_ms < 100, f"hook blocked for {hook_ms:.0f}ms"
    assert s.transcript[-1].speaker == "expert"
    instr = [i for i in ctx.items if getattr(i, "role", None) in ("system", "developer")]
    assert instr and "Identify minerals" in instr[0].text_content, "instructions not injected into turn ctx"
    # second turn while analyst busy -> should mark dirty, not stack
    await worker.handle_expert_turn(agent, ctx, "Also streak.")
    assert agent.runner.dirty
    await agent.runner.wait_idle()
    s2 = store.load(s.id)
    assert len(s2.items) == 1 and len(s2.gaps) == 1, (len(s2.items), len(s2.gaps))  # dedup on rerun
    assert s2.coverage["steps"].status == "partial"
    # second-turn instructions include the new gap as a candidate
    assert "you just know" in __import__("apprentice.interviewer").interviewer.build_instructions(s)
    print(f"PASS turn hook ({hook_ms:.1f}ms, non-blocking, dirty-rerun, dedup, instructions refreshed)")


asyncio.run(main())
