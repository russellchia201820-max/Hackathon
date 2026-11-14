# Last Expert

**An AI apprentice that interviews an expert on any topic, questions every assumption, and turns their unwritten know-how into a knowledge base that can teach anyone.**

Built at **Imperial AI Hack 2026** · Track 03: Voice, Vision & Generative Media

---

## The problem

Some knowledge is so specific to one place, one team, one setup or one person that it simply isn't online: how this kitchen runs a Saturday service, how this team ships a release, how this lab's instrument really behaves, how this account manager keeps a difficult client happy. When the expert leaves, it leaves with them.

Ask a standard AI how to do a task and what it tells you will usually be correct, but it will be the general version for the whole field. It can't tell you what only matters at one site:

- **Where things are:** which cabinet the spare kit lives in, which folder has the real version of the spreadsheet, who actually holds the key.
- **The state of the tools:** which machine, system or document has quirks, needs upkeep or can't be trusted ("staging is a week out of date, check the replica").
- **Local problems and fixes:** what keeps going wrong in this particular place or team, and the workarounds people really use.
- **People and unwritten rules:** who actually signs off, who only reads Slack before 10am, what never to do on a Friday.
- **One person's tips:** small habits an experienced person picked up over years that make the work faster, safer or better.

None of this is in any training data, because it was never written down. It's more efficient to capture it directly from the expert, once, and let an agent teach it to everyone else.

## What Last Expert does

The expert just talks to it.

1. **"What do you want to talk about?"** The apprentice opens the conversation and lets the expert choose any topic: a task, a whole role, a craft, a process, a piece of software, a place.
2. **It asks the first question.** From the answer it sets an objective (what a trainee should be able to do by the end) and a coverage plan of what a trainee needs to know.
3. **It hunts for assumptions.** After every answer it works out what the expert did automatically, took for granted, left vague or called by a local name, and asks about the most important one:
   *"You said you 'just check the seal'. What exactly are you looking for?"*
4. **It records everything** in a structured knowledge base: steps, locations, maintenance, problems and fixes, judgement calls, safety, tips. Each item carries the reason behind it and the expert's own words.
5. **It teaches.** Trainees ask questions by voice or text and get answers grounded only in what the expert said. Anything it can't answer becomes a question for the expert's next session.
6. **It exports** a training manual, and an "expert pack" that loads the know-how into any other AI agent.

Inputs can be live conversation (voice or text), or uploaded **text, transcripts or audio**: a manual, a checklist, an SOP, notes or an old recording of the expert.

## How it stays curious without going off track

| Mechanism | What it does |
|---|---|
| **Assumption detector** | Classifies every gap in an answer: unstated assumption, automatic action, vague term ("a bit", "until it looks right"), local reference ("the shared drive", "Dave's spreadsheet"), judgement call, missing step, jargon. Each is rated 1–5 for importance. |
| **Coverage plan** | 6–9 areas a newcomer needs, shaped to the kind of topic: a task (steps, what you need, judgement calls, problems), a role (responsibilities, people, routines, unwritten rules), a skill (signals, common mistakes, practice) or a process (inputs, quirks, failure modes). Scores fill up as the expert talks, so curiosity always serves the objective. |
| **Priority ranking** | Fresh, important assumptions first; then weak coverage areas. Threads deeper than three follow-ups are deprioritised unless something is safety-critical. |
| **Official docs as baseline** | Upload the official manual, checklist or guide and the apprentice never wastes the expert's time on what's already written down. Everything it captures beyond it is marked **★ not in any manual**. |
| **Trainee feedback loop** | Questions trainees ask that the knowledge base can't answer go to the top of the agenda for the expert's next session. |
| **Expert in control** | The expert can dismiss an irrelevant assumption or delete a wrong item with one click. |

## Examples

It works on any topic. Two samples are in [`samples/`](samples): upload the checklist as a *reference document* and the notes as the *expert's own words*.

**Software: running the weekly release.** The checklist says *"make sure CI is green, get product owner approval, monitor dashboards"*. The expert adds:

- ★ Never release on a Friday after 2pm. Thursday morning is the real slot, because the on-call handover is Friday at 5.
- ★ The payments test is flaky: rerun it once. If it fails twice, it's real.
- ★ "Product owner approval" means Sam, who only reads Slack DMs before 10am.
- ★ Ignore the CPU spike after a deploy. Watch checkout success: under 97% in ten minutes means roll back.
- ★ The rollback pipeline doesn't undo migrations. Run `scripts/undo_migration.sh` first.

**Energy: checking drilling-mud weight.** The SOP says *"fill the cup, seat the lid, read the rider"*. The expert adds:

- ★ This rig uses the pressurised balance, because its mud traps gas and the open cup reads light.
- ★ It lives in the grey Pelican case, mud lab, bottom shelf, under the retort.
- ★ The plunger sticks in winter. A gritty feel means the O-ring has gone; spares are taped inside the lid.
- ★ Log it on the whiteboard as well as the report, because the driller reads the board.

None of that is in the official documents, and no chatbot could tell you.

## Architecture

```
  Expert speaks ──► ElevenLabs speech-to-text ─┐        (or Anam avatar listens; or typing)
                                              ▼
                  ┌──────────────────────── Claude ─────────────────────────┐
                  │ 1. ask_next        (fast, in the request)                │
                  │    focus assumption + the single best next question      │
                  │ 2. record_knowledge (background)                         │
                  │    knowledge items, other assumptions, resolutions,      │
                  │    coverage scores, answered trainee questions           │
                  └───────────────┬──────────────────────────┬───────────────┘
                                  ▼                          ▼
              ElevenLabs voice / Anam avatar         Knowledge base (JSON or MongoDB)
              speaks the question                     ├─ Trainee Q&A (grounded answers)
                                                      ├─ Training manual (Markdown)
                                                      └─ Expert pack for other agents
```

Each expert turn makes two Claude calls. The short `ask_next` call returns the next question in a couple of seconds, so the conversation feels natural. The longer `record_knowledge` call runs in the background and the UI updates when it lands. The next turn waits for it, so the apprentice never works from stale context.

## Tech stack, APIs and models

| Layer | Tool |
|---|---|
| Interviewing, assumption detection, extraction, manuals | **Anthropic Claude API** (`claude-sonnet-5-5`; trainee answers on `claude-haiku-5-5`), structured output via forced tool calls, prompt caching |
| Speech-to-text | **ElevenLabs** Scribe (`scribe_v2`) |
| Text-to-speech | **ElevenLabs** (`eleven_flash_v2_5`) |
| Face-to-face avatar | **Anam** JS SDK, with our own brain (`CUSTOMER_CLIENT_V1`) |
| Backend | Python, FastAPI |
| Frontend | Plain HTML/CSS/JavaScript (no build step) |
| Storage | JSON files, or MongoDB Atlas if `MONGODB_URI` is set |
| Document reading | pypdf, python-docx |
| Datasets | None. Demo content was written and recorded by the team during the hackathon. |

Without keys, voice falls back to the browser's built-in speech, and the brain falls back to an offline heuristic mock, so anyone can run the app.

## Getting started

```bash
git clone https://github.com/<your-org>/last-expert.git
cd last-expert
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env                                  # add your keys
uvicorn app.main:app --reload
```

Open **http://localhost:8000** in Chrome.

| Key in `.env` | Needed for |
|---|---|
| `ANTHROPIC_API_KEY` | The real brain (required for the demo) |
| `ELEVENLABS_API_KEY` | High-quality voice in and out, and audio uploads |
| `ANAM_API_KEY` | The face-to-face avatar |
| `MONGODB_URI` | Optional: store sessions in MongoDB instead of `./data` |

Run the tests (offline, no keys needed):

```bash
python -m pytest -q
```

To work on the UI without spending credits, leave `ANTHROPIC_API_KEY` empty. `MOCK_RECORD_DELAY=2` simulates Claude's latency.

## Demo script (4 minutes)

1. **Pick a topic a judge knows**, or use a sample. Optionally upload the official version first (`samples/release_checklist.txt` as "Reference document").
2. **Talk to it.** Say what you want to teach. Watch it set an objective and a coverage plan, then ask the first question.
3. **Answer with natural shortcuts:** "I just check it's fine and ping Sam." Watch the assumptions appear and the next question probe one of them, with the reason shown under the chat.
4. **Show the ★ counter:** know-how captured that is in no manual.
5. **Switch to Trainee.** Ask a newcomer's question and get the expert's answer. Ask something it doesn't know and show it queued for the expert.
6. **Generate the manual** and download the expert pack.

Best live demo: interview a judge about something only they know, such as how their team does something, or a quirk of their own kitchen or commute.

## Project structure

```
app/
  main.py          HTTP API and static file server
  interviewer.py   the engine: conversation flow, ranking, merging, trainee Q&A, outputs
  prompts.py       every prompt and structured-output schema (tune behaviour here)
  models.py        session, knowledge item, assumption and coverage data models
  llm.py           Claude client (forced tool calls, prompt caching)
  mock_llm.py      offline heuristic brain for development and tests
  store.py         JSON or MongoDB storage
  documents.py     text from .txt / .md / .pdf / .docx / audio
  voice.py         ElevenLabs speech-to-text and text-to-speech
  avatar.py        Anam session tokens
static/            the web app (index.html, app.js, style.css)
samples/           reference documents and expert notes from two different fields
tests/             end-to-end tests
```

## API

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/api/sessions` | Start a session; the apprentice opens with "What do you want to talk about?" |
| `POST` | `/api/sessions/{id}/message` | Expert speaks; returns the next question |
| `POST` | `/api/sessions/{id}/documents` | Upload an official document (`kind=reference`) or the expert's own words (`kind=expert`) |
| `POST` | `/api/sessions/{id}/ask` | Trainee question, answered from the knowledge base |
| `POST` | `/api/sessions/{id}/manual` | Generate the training manual |
| `GET` | `/api/sessions/{id}/export?format=json\|agent\|manual` | Download the knowledge base, expert pack or manual |
| `GET` | `/api/sessions/{id}/candidates` | What the apprentice wants to ask next, and why |
| `POST` | `/api/transcribe`, `/api/tts`, `/api/avatar/session` | Voice and avatar helpers |

## Roadmap

- Multi-session capture that builds one expert profile over weeks
- Disagreement detection when two experts do the same task differently
- Export to existing training and maintenance systems
- Pilots where knowledge loss hurts most: retiring engineers, parental-leave handovers, high-turnover kitchens and labs

## Team

- Name — role
- Name — role
- Name — role
- Name — role

## License

MIT
