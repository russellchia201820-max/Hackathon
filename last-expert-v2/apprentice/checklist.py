"""The coverage checklist: slots any task needs covered."""

CHECKLIST: dict[str, dict] = {
    "specifics": {"label": "Specifics", "desc": "The concrete facts needed to actually do it: places (start, destination, where things are), names, routes or numbers, distances, durations, times, quantities, who is involved"},
    "goal": {"label": "Goal", "desc": "What the task achieves and what 'done well' looks like"},
    "steps": {"label": "Steps", "desc": "The sequence, end to end"},
    "decisions": {"label": "Decisions", "desc": "Decision points and rules ('if X then Y')"},
    "cues": {"label": "Cues", "desc": "What the expert looks at, listens for or feels to judge the situation"},
    "mistakes": {"label": "Mistakes", "desc": "What beginners get wrong, and the warning signs"},
    "edge_cases": {"label": "Edge cases", "desc": "Exceptions, and when the normal rules break"},
    "tools_terms": {"label": "Tools & terms", "desc": "Tools, materials, jargon definitions, prerequisites"},
    "quality_check": {"label": "Quality check", "desc": "How you know you did it right"},
}
SLOTS = list(CHECKLIST)


def describe() -> str:
    return "\n".join(f"- {k}: {v['desc']}" for k, v in CHECKLIST.items())
