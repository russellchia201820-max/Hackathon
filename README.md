# Apprentice — an AI that interviews an expert and captures what they don't know they know

> Imperial AI Hack 2026 · Voice/Vision track

## The problem
Experts are bad at explaining what they do, not because they hide it but because they've stopped noticing it. "You just get a feel for it." "It's basically obvious." Psychologists estimate that most of an expert's working knowledge is **tacit**. When senior people retire or move on, that knowledge leaves with them, and the training documents they leave behind skip exactly the steps a beginner needs.

## What Apprentice does
An expert opens a video call with an AI apprentice and says *"I want to teach you how to do X."* The apprentice interviews them like a curious, sharp trainee who **lets no assumption pass**:

- **Live knowledge panel.** As the expert talks, their knowledge appears as structured cards: steps, decision rules, cues, mistakes, edge cases, tools and terms, and quality checks.
- **"Assumptions spotted" feed.** Every vague word ("usually", "you just know"), undefined bit of jargon, skipped step or missing *why* is flagged with the expert's exact words. The card then moves through open → asked → resolved ✓ as the apprentice follows up.
- **Coverage bar.** Shows how much of a task checklist is covered.
- **Generate playbook.** Turns the interview into a beginner-ready training document. It includes an honest "Not yet covered" section.
- **Ask the expert.** A beginner can question an AI copy of the expert that answers **only** from captured knowledge, cites the items it used, and clearly says when the expert never covered something.

## How it works
```
Browser (index.html)                                Python backend
 ├─ LiveKit room: avatar video, mic, captions   ◄──► FastAPI (server/app.py)
 └─ live panel (polls state every 1 s)                │
        │ WebRTC                                       ▼
        ▼                                         apprentice/  ← the brain (pure Python)
  LiveKit Cloud ◄──► LiveKit Agent worker ───────►  sessions/<id>/state.json
                      Deepgram STT → Claude → Deepgram TTS → Synthesia avatar
```

### Two loops: fast and slow
| | Fast loop: **interviewer** | Slow loop: **analyst** |
|---|---|---|
| When | every reply, in real time | after every expert turn, in the background |
| Model | Claude Haiku 5.5 (no thinking, ~150 tokens) | Claude Sonnet 5.5 (structured JSON output) |
| Job | say one short, natural question | extract atomic knowledge items, spot assumptions, write targeted questions, track gap status and coverage |

A single model can't both reply within about 1 second and do careful knowledge extraction. Splitting the work keeps the conversation snappy while the analysis stays rigorous. The analyst never blocks the call. If the expert speaks again while it's running, it reruns once on the latest transcript.

Between the loops sits a **deterministic selector**, plain code with no LLM. It ranks open gaps by priority, relevance to the current topic and empty checklist slots, and penalises gaps that have already been asked. It hands the interviewer the top 3, each with the expert's own words as an anchor, so the apprentice can bridge back naturally: *"Earlier you said 'you just know'. What exactly are you seeing?"* Gaps that have been asked twice are dropped, so the apprentice doesn't badger the expert. Phases (opening → deep dive → wrap-up → done) are driven by checklist coverage.

### Grounded in Cognitive Task Analysis
The analyst's question patterns come from **Cognitive Task Analysis** and Klein's **Critical Decision Method**, the techniques human knowledge engineers use to draw out expertise:
- *"Tell me about a time this went wrong."*
- *"What's the first thing you notice when…?"*
- *"How do you know when…?"*
- *"What would a beginner miss here?"*

The coverage checklist (goal, steps, decisions, cues, mistakes, edge cases, tools and terms, quality check) mirrors what a CTA practitioner would want captured.

## Evidence: simulated-expert capture score
`python cli.py --simulate --turns 15` runs the apprentice against a simulated expert. That expert is a Claude role-play of a field geologist who holds 42 hidden facts about identifying minerals and answers like a real expert: jargon, skipped steps, "you just know". A separate grader then counts how many hidden facts ended up in the knowledge base.

**Result:** _see NOTES.md → Capture score_

## Setup
```bash
conda create -n ichack python=3.11 -y && conda activate ichack
pip install -r requirements.txt
cp .env.example .env   # fill in keys
python scripts/check_keys.py
```
Run it:
```bash
python -m uvicorn server.app:app --port 8000   # terminal 1
python agent/worker.py dev                     # terminal 2 (voice)
# open http://localhost:8000
```
Text-only mode (no voice keys needed): just run the server and type in the box under the video.

## Tech
Python 3.11 · FastAPI · LiveKit Agents 1.8 (Deepgram STT and Aura TTS, Anthropic plugin, Silero VAD, LiveKit turn detector, BVC noise cancellation) · Synthesia interactive avatar · Anthropic Claude (Haiku 5.5 for speed, Sonnet 5.5 for analysis) · vanilla JS, no build step.

## Roadmap
- **Multi-task roles:** interview someone about a whole job, not just one task.
- **Multiple experts:** merge and reconcile what several experts know, and flag where they disagree.
- **Video understanding:** the expert *shows* the task and the apprentice asks about what it sees ("Show me" mode).
- **Exports:** to an LMS (SCORM), to SOP templates, and to **agent skills**, so captured expertise can drive AI agents directly.
