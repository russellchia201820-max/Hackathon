"""Scripted stand-in for the model so the loop can be tested without an API key."""


class MockLLM:
    def __init__(self):
        self.n = 0

    def call_tool(self, system, user, tool, retries=0):
        self.n += 1
        if tool["name"] == "update_knowledge_map":
            if "S1" not in user.split("EXPERT'S LATEST ANSWER")[0]:
                return {"summary_of_answer": "Overview of the mud weight check.",
                        "task": {"goal": "Measure drilling mud density accurately"},
                        "steps": [{"id": "new1", "action": "Fill the cup with fresh mud", "quote": "fill the cup"},
                                  {"id": "new2", "after": "new1", "action": "Tap the cup", "quote": "give it a tap"},
                                  {"id": "new3", "after": "new2", "action": "Put the lid on until mud comes out the hole"},
                                  {"id": "new4", "after": "new3", "action": "Balance the arm and read the rider"}],
                        "concepts": [{"term": "rider"}],
                        "new_assumptions": [{"text": "How many taps, and why tap at all?", "about": "new2"}]}
            return {"summary_of_answer": "Explained tapping.",
                    "steps": [{"id": "S2", "why": "Knocks out trapped air so it doesn't read light",
                               "how": "Three or four taps on the side", "quote": "air makes it read light"}],
                    "resolved_assumptions": [{"id": "A5", "resolution": "3-4 taps to release air"}]}
        return {"sequence_check": "Fill -> tap -> lid -> balance -> read. Missing: calibration before, cleaning after.",
                "novice_simulation": "A beginner would not know when the cup is 'full' or which edge of the rider to read.",
                "autopilot_hunt": "Probably wipes the outside of the cup and checks calibration with water.",
                "candidates": [
                    {"question": "You said give it a tap. How many taps, and what's the tap for?", "type": "why",
                     "target": "S2", "slot": "why", "beginner_blocking": 4, "uncertainty": 5, "flow_fit": 5},
                    {"question": "Before you start, do you check the balance is reading right?", "type": "missing_step",
                     "target": "task", "slot": "preconditions", "beginner_blocking": 5, "uncertainty": 4, "flow_fit": 2},
                    {"question": "Let me explain it back.", "type": "teach_back", "target": "task", "slot": "teach_back",
                     "beginner_blocking": 5, "uncertainty": 3, "flow_fit": 3}]}

    def text(self, system, user):
        return "Tap the cup three or four times to release trapped air, otherwise it reads light. [S2]"
