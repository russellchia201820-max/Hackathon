#!/usr/bin/env python
"""Create a mock session with sample items/gaps (UI testing without API keys)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from apprentice import engine, store  # noqa: E402
from apprentice.models import Gap, KnowledgeItem, SlotCoverage  # noqa: E402

s = engine.new_session("How to identify common minerals in a hand sample", "Sam")
s.add_turn("expert", "Basically you look at lustre first, then you do hardness with your knife, streak, and you just get a feel for it.")
s.add_turn("agent", "When you say you 'just get a feel for it', what are you actually noticing?")
s.add_turn("expert", "Well, mostly whether it scratches glass. Quartz will, calcite won't, and calcite fizzes in acid.")
s.items = [
    KnowledgeItem(id="k1", slot="steps", content="Test lustre first, then hardness with a steel knife, then streak.", source_quote="you look at lustre first, then you do hardness", turn_index=1, confidence="high"),
    KnowledgeItem(id="k2", slot="decisions", content="If the sample scratches glass, it is harder than about 5.5 (e.g. quartz); calcite does not scratch glass.", source_quote="Quartz will, calcite won't", turn_index=3),
    KnowledgeItem(id="k3", slot="cues", content="Calcite fizzes when dilute hydrochloric acid is dropped on it.", source_quote="calcite fizzes in acid", turn_index=3, confidence="high"),
    KnowledgeItem(id="k4", slot="tools_terms", content="Tools needed: a steel knife and a glass plate for hardness testing.", source_quote="hardness with your knife", turn_index=1, confidence="medium"),
]
s.gaps = [
    Gap(id="g1", kind="vague_term", anchor_quote="you just get a feel for it", question="What exactly are you noticing when you 'get a feel' for it?", slot="cues", priority=5, topic="feel", status="resolved", resolved_by_item_ids=["k2"]),
    Gap(id="g2", kind="undefined_jargon", anchor_quote="lustre", question="What does lustre mean, and what kinds are there?", slot="tools_terms", priority=4, topic="lustre", status="asked", times_asked=1),
    Gap(id="g3", kind="missing_failure_case", anchor_quote="do hardness with your knife", question="What goes wrong when beginners test hardness?", slot="mistakes", priority=4, topic="hardness"),
    Gap(id="g4", kind="skipped_step", anchor_quote="then you do hardness with your knife, streak", question="How do you actually do a streak test?", slot="steps", priority=3, topic="streak"),
    Gap(id="g5", kind="missing_slot", anchor_quote="", question="How do you know your identification is right?", slot="quality_check", priority=3, topic="quality check"),
]
s.coverage.update({"steps": SlotCoverage(status="partial"), "decisions": SlotCoverage(status="partial"),
                   "cues": SlotCoverage(status="partial"), "tools_terms": SlotCoverage(status="good")})
s.phase = "deep_dive"
s.current_topic = "hardness"
store.save(s)
print(s.id)
