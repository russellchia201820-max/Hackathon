# Apprentice — an AI that interviews an expert and captures what they don't know they know

> Imperial AI Hack 2026 · Voice, Vision & Generative Media track

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
                      ElevenLabs Scribe STT → Claude (+ tools) → ElevenLabs TTS → Anam / Synthesia avatar
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

**Result:** Apprentice captured **78.6% of the expert's hidden knowledge in 15 questions** (24 facts fully and 18 partially, out of 42). Interviewer replies averaged **1.3 s**. Three prompt-tuning rounds took the score from 28.6% to 78.6% (see NOTES.md).

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

### An agent that acts as well as talks
The voice agent has real **function tools** that it calls during the interview. Each call appears live on the panel as a 🔧 chip:
- `flag_important(summary)`: when the expert stresses something ("the key thing is…"), the agent pins it as a ★ key-insight card.
- `request_visual(reason)`: the agent asks to *see* something. A "Show me" prompt pops up and the expert turns on their camera. The frame goes to Claude vision, and what the agent sees becomes part of the interview.
- `end_interview()`: called when coverage is complete. It closes the interview and readies the playbook.

## Built with
| What | Used for |
|---|---|
| **Anthropic Claude Haiku 5.5** (`claude-haiku-5-5`) | Fast loop: the interviewer's spoken replies and function-tool calls (through the LiveKit Anthropic plugin) |
| **Anthropic Claude Sonnet 5.5** (`claude-sonnet-5-5`) | Slow loop: the analyst (structured JSON outputs), the playbook generator, Ask-the-expert, "Show me" vision, and the simulated expert and grader in our test harness |
| **ElevenLabs Scribe v2 Realtime** | Streaming speech-to-text of the expert |
| **ElevenLabs TTS** (Flash v2.5) | The apprentice's voice |
| **LiveKit Cloud + LiveKit Agents 1.8** | WebRTC room, agent orchestration, turn detection (LiveKit turn detector + Silero VAD), noise cancellation (BVC), live transcriptions |
| **Anam / Synthesia** interactive avatars (LiveKit plugins) | The apprentice's face. Set `AVATAR_PROVIDER`; it falls back automatically to the other provider, then to voice-only |
| FastAPI, vanilla JS, marked.js, DOMPurify, livekit-client (CDN) | Server and UI, with no build step |
| Dataset | None. Our test fixture (`tests/fixtures/expert_mineral_id.md`, 42 facts about identifying minerals) was written by us |

## Bounties
- **Best ElevenLabs project.** A real-time voice agent that goes well beyond text-to-speech. ElevenLabs Scribe realtime transcribes the expert and ElevenLabs TTS voices the apprentice. Underneath sits a two-loop agent that analyses every answer as it arrives, decides what to ask next, and calls tools that change shared state the audience can see. We tuned turn-taking to be patient (experts pause mid-thought), and the interaction is designed around one short question per turn.
- **Best use of Anam.** An Anam avatar is the apprentice's face. It is driven by our own LLM, STT and TTS stack, with tool calling (pinning insights, requesting visuals, ending the interview) and multi-modal input: the expert can *show* things on camera, and the avatar asks about what it sees.

## Roadmap
- **Multi-task roles:** interview someone about a whole job, not just one task.
- **Multiple experts:** merge and reconcile what several experts know, and flag where they disagree.
- **Video understanding:** the expert *shows* the task and the apprentice asks about what it sees ("Show me" mode).
- **Exports:** to an LMS (SCORM), to SOP templates, and to **agent skills**, so captured expertise can drive AI agents directly.
