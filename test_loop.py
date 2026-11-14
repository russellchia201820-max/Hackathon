from expert_agent import Interviewer, KnowledgeMap, tacit_signals
from expert_agent.mock import MockLLM


def test_tacit():
    s = tacit_signals("You just give it a tap until it looks right, obviously.")
    assert {"just", "until it", "looks right", "obviously"} <= set(s)


def test_full_turns():
    iv = Interviewer(MockLLM(), "mud weight")
    assert "walking me through" in iv.start()
    r1 = iv.respond("You fill the cup, just give it a tap, lid on till mud comes out, then balance it and read the rider.")
    assert r1.coverage["steps"] == 4
    assert [s.action for s in iv.km.steps][1] == "Tap the cup"
    assert r1.chosen["type"] != "teach_back"           # gated: coverage too low
    assert r1.chosen["target"] == "S2"                # depth bonus on the vague step
    r2 = iv.respond("Three or four taps. Air makes it read light.")
    assert iv.km.step("S2").why
    assert any(a.status == "resolved" for a in iv.km.assumptions)
    # same target+slot already asked once -> penalised
    assert r2.candidates[0]["target"] != "S2" or r2.candidates[0]["penalty"] == 0


def test_roundtrip_and_markdown():
    iv = Interviewer(MockLLM(), "mud weight"); iv.start()
    iv.respond("fill, tap, lid, balance, read")
    km2 = KnowledgeMap.from_json(iv.km.to_json())
    assert len(km2.steps) == 4 and "## Steps" in km2.to_markdown()
    assert "[S2]" in iv.ask_as_learner("why tap?")


def test_insert_between():
    km = KnowledgeMap("t", "o")
    km.apply_patch({"steps": [{"id": "n1", "action": "a"}, {"id": "n2", "after": "n1", "action": "c"}]}, 1)
    km.apply_patch({"steps": [{"id": "n3", "after": "S1", "action": "b"}]}, 2)
    assert [s.action for s in km.steps] == ["a", "b", "c"]
