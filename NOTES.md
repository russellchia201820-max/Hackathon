# NOTES — Apprentice build log

## Progress (update after every milestone)
**Spec change (Sat eve):** hackathon services only. ElevenLabs is used for STT and TTS, there is no Deepgram, and the avatar is switchable through `AVATAR_PROVIDER=anam|synthesia`. Anam keys are "coming soon", so Synthesia is the first target.
- [x] M0 setup: key check 7/9 OK on Sat; avatar IDs then filled: Anam = Mia studio `edf6fdcb-acab-44b8-b974-ded72665ee26`, voice = ElevenLabs Jessica `cgSgspJ2msm6clMCkdW9`. **SYNTHESIA_AVATAR_ID still blank**: Synthesia has no listing API, so copy one from app.synthesia.io → Avatars (human).
- [x] M1 brain + CLI: 3 tuning rounds done, capture score **78.6% in 15 questions** (table below).
- [x] M2 server + panel: live text interview via API (turn returns in ~2.9 s, analyst runs in background, panel updates; headless screenshot, no JS errors). Gap dedup by anchor quote.
- [x] M3 playbook + ask: playbook 13 s (all sections incl. Not yet covered); ask covered → cited items, pyrite-vs-gold → covered=false with closest info.
- [~] M4 voice: `agent/worker.py` uses ElevenLabs Scribe v2 realtime STT and ElevenLabs TTS (flash v2.5), with Claude Haiku plus the tools `flag_important`, `request_visual` and `end_interview`. Tool effects are in `apprentice/tools.py`, and the panel shows them as 🔧 chips. The unit test passes. Needs keys.
- [~] M5 avatar: `agent/avatar.py` switches between Anam and Synthesia, with an automatic fallback to the other provider and then to voice-only. Needs keys.
- [~] M6 polish: README (with Built with and Bounties sections), DEVPOST.md and DEMO.md are written. Still to do: insert the capture score, then make the GitHub repo public.
- [~] M7 vision: written (a "look at this" trigger or `request_visual` → frame → Claude vision → observation and thumbnail). Untested live.

**Next:** once `.env` exists → `python scripts/check_keys.py` → `python cli.py --simulate --turns 12` → tune the prompts → record the capture score in NOTES, README and DEVPOST → live-test the voice and avatar.

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
- Voice STT/TTS = ElevenLabs only (hackathon services). The LiveKit plugin reads `ELEVEN_API_KEY`; worker copies `ELEVENLABS_API_KEY` into it. STT model `scribe_v2_realtime` (env `ELEVENLABS_STT_MODEL`), TTS `eleven_flash_v2_5` (env `ELEVENLABS_TTS_MODEL`), voice `ELEVENLABS_VOICE_ID`.
- Avatar: `agent/avatar.py` — `anam.AvatarSession(persona_config=anam.PersonaConfig(name, avatarId))`, `synthesia.AvatarSession(synthesia.AvatarConfig(avatar_ids=[id]))`, both `.start(session, room=room)` before `session.start()`, 25 s timeout, then try the other, then voice-only. Demo with Anam if both work (bounty).
- Tools: function tools on the `Apprentice` Agent; effects in `apprentice/tools.py` (pinned item, `visual_request` → UI banner + pulsing Show me button, phase=done). Tool instructions only added in voice mode (`build_instructions(s, voice=True)`).
- Voice: preemptive generation disabled (instructions change each turn, so it would be invalidated anyway). Endpointing `min_delay=TURN_END_DELAY_S` (1.2 s) + `inference.TurnDetector()` (cloud, local fallback). Noise cancellation: LiveKit BVC.
- In `on_user_turn_completed` the reply uses a *copy* of the chat ctx made before the hook, so the worker writes fresh instructions into `turn_ctx` directly **and** calls `agent.update_instructions()` for later turns.
- Agent dispatch is explicit: token carries `RoomConfiguration(agents=[RoomAgentDispatch(agent_name="apprentice", metadata={session_id})])`.

## Gotchas
- livekit-agents 1.8: `livekit.plugins.turn_detector` is deprecated → use `livekit.agents.inference.TurnDetector`. `python worker.py dev` still works (prints a deprecation warning; `lk agent dev` is the new way).
- `temperature` must not be sent to current Claude models (400).
- Headless screenshots: Playwright chromium needed `nss nspr alsa-lib` from conda-forge; run with `LD_LIBRARY_PATH=~/miniconda3/envs/ichack/lib python scripts/screenshot.py <sid> out.png`.
- Mic only works on `http://localhost:8000` (not an IP). Use headphones (echo).

## Capture score (simulated expert, tests/fixtures/expert_mineral_id.md, 42 facts)
| Run | Turns | Score | Notes |
|---|---|---|---|
| baseline | 6 | 28.6% | sim expert too chatty; double questions; early rabbit holes |
| round 1 (one question, breadth, opening walkthrough, terser sim expert) | 12 | 48.8% | still drilled the streak test for 4 Qs |
| round 2 (selector: empty-slot +3, topic +1; "still thin" list) | 12 | 65.5% | recaps + occasional double questions |
| **round 3 (hard clip after first '?', no recaps)** | **15** | **78.6%** (24 full + 18 partial / 42) | 52 items, 19 gaps (12 resolved), coverage 88%, reply latency avg 1.32 s |

## Cut / fallbacks
- (none yet)

## Human test checklist
- [ ] Try one manual CLI interview: `python cli.py`
- [ ] Type an interview in the browser and watch the panel fill (assumptions feed animates open → asked → resolved ✓)
- [ ] Generate playbook; download .md; ask 2 covered + 1 uncovered question in "Ask the expert"
- [ ] Start call, talk to it with **headphones**, confirm captions + panel fill during a spoken interview
- [ ] Check it doesn't cut you off mid-thought (tune `TURN_END_DELAY_S`, e.g. 1.5)
- [ ] Full interview with the Synthesia avatar (`AVATAR_ENABLED=true`, `AVATAR_PROVIDER=synthesia`); note avatar lag
- [ ] Same with Anam once keys arrive (`AVATAR_PROVIDER=anam`) — demo with Anam if it works
- [ ] Say "the key thing is…" → 🔧 chip + ★ pinned card; let it ask to see something → Show me → thumbnail
- [ ] Pick a warm ElevenLabs voice and set `ELEVENLABS_VOICE_ID`
- [ ] View the page on the projector from 3 m — legible?
- [ ] Make GitHub repo public + push (commands in handoff)
- [ ] **Record a backup demo video** (judging is live; safety net only) (OBS / Windows Game Bar Win+Alt+R)
