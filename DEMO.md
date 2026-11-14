# DEMO checklist

## 30 minutes before
1. `conda activate ichack && cd ~/jblinux/hackathons/apprentice`
2. `python scripts/check_keys.py` → everything OK (Synthesia minutes left?)
3. **Terminal 1:** `python -m uvicorn server.app:app --port 8000`
4. **Terminal 2:** `python agent/worker.py dev` → wait for "registered worker"
5. Chrome → **http://localhost:8000** (never an IP; the mic needs localhost)
6. Plug in a **headset**. Allow mic access. Volume up.
7. Do a 2-minute dry run, then click **New session** for a clean one.

## Script (about 4 min)
1. The problem in 20 s: "experts don't know what they know".
2. New session → "How to identify minerals in a hand sample" (or the presenter's own skill).
3. Start call → the apprentice greets → answer vaguely on purpose ("you just get a feel for it").
4. Point at the **Assumptions spotted** feed: the vague phrase appears, gets asked, then turns ✓ resolved.
5. Coverage bar and knowledge cards fill up.
6. **Playbook** tab → Generate → show "Not yet covered".
7. **Ask the expert** → ask one covered question (it cites items) and one uncovered question (it is flagged ⚠).
8. Evidence: the simulated-expert capture score (README).

## Fallback ladder (move down one rung if something breaks)
1. **Avatar** (`AVATAR_ENABLED=true`)
2. **Voice-only with the orb.** Set `AVATAR_ENABLED=false` in `.env` and restart terminal 2. (This happens automatically if the avatar fails to start.)
3. **Browser text mode.** Don't start a call; type answers in the box under the video. Only terminal 1 is needed.
4. **Backup video.** Play the recorded demo.

## Quick fixes
- Agent cuts you off → raise `TURN_END_DELAY_S` (e.g. 1.6) in `.env` and restart the worker.
- Agent hears itself → use headphones.
- Windows Chrome can't reach localhost:8000 → run the server with `--host 0.0.0.0`, or run `wsl --shutdown` and restart.
- The panel is stuck → refresh the page (the session id is in the URL).
