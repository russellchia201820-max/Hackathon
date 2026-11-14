# NOTES — Apprentice build log

## Progress (update after every milestone)
- [x] M0 setup: conda env `ichack` (py3.11), repo, requirements, `.env.example`, `scripts/check_keys.py` — **key check pending: `.env` not yet filled**
- [~] M1 brain + CLI: all code written (models, checklist, llm, analyst, selector, interviewer, engine, CLI `--simulate`, capture score). **Needs ANTHROPIC key to run/tune.**
- [~] M2 server + panel: FastAPI + web UI done; verified with mock session (curl + headless screenshot, no JS errors). Live text turn needs key.
- [~] M3 playbook + ask: code + UI done; untested against API.
- [~] M4 voice: `agent/worker.py` written against installed livekit-agents 1.8.12; turn-hook unit test passes (`tests/test_turn_hook.py`). Needs LiveKit/Deepgram keys.
- [~] M5 avatar: Synthesia plugin wired behind `AVATAR_ENABLED` with timeout + voice-only fallback. Needs keys.
- [ ] M6 polish: README/DEMO written; toasts done.
- [ ] M7 vision: not started.

**Next:** once `.env` exists → `python scripts/check_keys.py` → `python cli.py --simulate --turns 12` → tune prompts → record capture score.

## How to run
```bash
conda activate ichack
cd ~/jblinux/hackathons/apprentice
python scripts/check_keys.py                 # 1. keys OK?
python -m uvicorn server.app:app --port 8000 # terminal 1: web + API
python agent/worker.py dev                   # terminal 2: voice agent (optional)
# open http://localhost:8000 in Chrome (Windows Chrome works: WSL2 forwards localhost)
python cli.py --simulate --turns 12          # simulated expert + capture score
python cli.py                                # you are the expert, in the terminal
python tests/test_turn_hook.py               # offline unit test (no keys)
python scripts/mock_session.py               # mock session for UI work (no keys) -> open /?s=<id>
```
Without activating: prefix with `conda run -n ichack` or use `~/miniconda3/envs/ichack/bin/python`.

## Decisions
- Brain (`apprentice/`) is pure Python; CLI, web text mode and voice worker all go through `apprentice/engine.py`.
- State: `sessions/<id>/state.json`, atomic write (temp + rename), `version` counter bumps each save; panel polls `/state?since=<version>` every 1 s.
- **Who writes state:** text-mode sessions → server process; voice sessions → worker process (server flips `mode` to `voice` when a token is issued and then only reads). Playbook goes to a separate `playbook.md`.
- Analyst output via **structured outputs** (`output_config.format` json_schema) — forced `tool_choice` is rejected by Sonnet 5.5, so tool-use forcing was not used.
- Analyst concurrency: one `AnalystRunner` per session; a trigger while busy sets `dirty` → exactly one rerun on the latest transcript.
- Gap times_asked counted per distinct agent turn (no double counting on reruns). Gaps asked ≥2× → dropped.
- Dedup in code too (difflib ratio ≥0.85 items, ≥0.75 gap questions). Panel caps open gaps at 8.
- Fast model: Haiku 5.5 with thinking disabled + effort low, max_tokens 150–160 (latency). Analyst: Sonnet 5.5, effort medium (`ANALYST_EFFORT` env overrides).
- Voice: preemptive generation disabled (instructions change each turn, so it would be invalidated anyway). Endpointing `min_delay=TURN_END_DELAY_S` (1.2 s) + `inference.TurnDetector()` (cloud, local fallback). Noise cancellation: LiveKit BVC.
- In `on_user_turn_completed` the reply uses a *copy* of the chat ctx made before the hook, so the worker writes fresh instructions into `turn_ctx` directly **and** calls `agent.update_instructions()` for later turns.
- Agent dispatch is explicit: token carries `RoomConfiguration(agents=[RoomAgentDispatch(agent_name="apprentice", metadata={session_id})])`.

## Gotchas
- livekit-agents 1.8: `livekit.plugins.turn_detector` is deprecated → use `livekit.agents.inference.TurnDetector`. `python worker.py dev` still works (prints a deprecation warning; `lk agent dev` is the new way).
- `temperature` must not be sent to current Claude models (400).
- Headless screenshots: Playwright chromium needed `nss nspr alsa-lib` from conda-forge; run with `LD_LIBRARY_PATH=~/miniconda3/envs/ichack/lib python scripts/screenshot.py <sid> out.png`.
- Mic only works on `http://localhost:8000` (not an IP). Use headphones (echo).

## Capture score (simulated expert, tests/fixtures/expert_mineral_id.md, 42 facts)
- (pending keys)

## Cut / fallbacks
- (none yet)

## Human test checklist
- [ ] Try one manual CLI interview: `python cli.py`
- [ ] Type an interview in the browser and watch the panel fill (assumptions feed animates open → asked → resolved ✓)
- [ ] Generate playbook; download .md; ask 2 covered + 1 uncovered question in "Ask the expert"
- [ ] Start call, talk to it with **headphones**, confirm captions + panel fill during a spoken interview
- [ ] Check it doesn't cut you off mid-thought (tune `TURN_END_DELAY_S`, e.g. 1.5)
- [ ] Full interview with the Synthesia avatar (`AVATAR_ENABLED=true`); note avatar lag
- [ ] View the page on the projector from 3 m — legible?
- [ ] **Record a backup demo video** (OBS / Windows Game Bar Win+Alt+R)
