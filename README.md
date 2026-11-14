# Last Expert: interviewer reasoning core

An agent that learns a task from an expert by interviewing them like a curious apprentice,
and builds a structured knowledge map a beginner could follow.

## Run
```bash
pip install -r requirements.txt
echo "ANTHROPIC_API_KEY=sk-..." > .env
python cli.py "the task you want to learn"        # live interview (text)
python cli.py "anything" --mock                    # no API key, scripted model
python cli.py --learn session.json                 # a learner questions the trained agent
pytest -q tests
```
In the interview, type `/map` to see the knowledge map, `/think` to hide/show reasoning, `/done` to save.

## How the agent thinks (every turn)
1. **Tacit scan (code):** flags "just", "until it looks right", "obviously", "a bit"... each hides an assumption.
2. **Analyse (model):** turns the answer into a patch: steps in order (inserting missed steps between known ones),
   why / how / done-cue / pitfalls / tools / conditions per step, jargon, and the assumptions the expert made.
3. **Coverage (code):** every step needs 6 slots, the task needs 5. Unfilled slots + open assumptions = the gap list.
4. **Plan (model):** sequence check (jumps between steps), novice simulation (a beginner doing it tomorrow from the
   map alone: where do they get stuck?), autopilot hunt (what the expert does without thinking), then 3-6 scored questions.
5. **Select (code):** score = 0.45 blocking + 0.30 uncertainty + 0.25 flow, minus 1.5 per repeat, +0.6 for drilling into
   a vague answer. Teach-back unlocks at 75% coverage; wrap-up only after teach-back.

## Files
- `expert_agent/knowledge.py`: knowledge map, slots, patching, coverage, markdown/json export
- `expert_agent/prompts.py`: the two reasoning prompts and their output schemas (tune here)
- `expert_agent/interviewer.py`: the loop and question selection
- `expert_agent/llm.py`: Anthropic wrapper (model via `EXPERT_MODEL`, default `claude-sonnet-5-5`)
- `cli.py`: text interface; the voice front end calls `Interviewer.start()` / `.respond(text)` the same way
