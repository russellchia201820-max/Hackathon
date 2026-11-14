"""FastAPI server: session API, LiveKit tokens, static web UI.

Run:  python -m uvicorn server.app:app --port 8000
"""
from __future__ import annotations

import json
import logging
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from fastapi import FastAPI, HTTPException  # noqa: E402
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402
from pydantic import BaseModel  # noqa: E402

from apprentice import engine, outputs, store  # noqa: E402
from apprentice.models import Session  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("server")
app = FastAPI(title="Apprentice")
_cache: dict[str, Session] = {}   # text-mode sessions live in memory (+ disk)


def get_session(sid: str) -> Session:
    s = _cache.get(sid)
    if s is not None and s.mode == "text":
        return s
    s = store.load(sid)
    if s is None:
        raise HTTPException(404, f"session {sid} not found")
    if s.mode == "text":
        _cache[sid] = s
    return s


class NewSession(BaseModel):
    objective: str = ""
    expert_name: str = "Expert"
    mode: str = "text"


class TurnIn(BaseModel):
    text: str


class AskIn(BaseModel):
    question: str


@app.get("/api/config")
def config():
    voice = all(os.getenv(k) for k in ("LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET", "DEEPGRAM_API_KEY"))
    return {"voice_available": voice, "avatar_enabled": os.getenv("AVATAR_ENABLED", "false").lower() == "true",
            "livekit_url": os.getenv("LIVEKIT_URL", "")}


@app.get("/api/sessions")
def sessions():
    return store.list_sessions()[:20]


@app.post("/api/session")
def create(body: NewSession):
    s = engine.new_session(body.objective, body.expert_name, "voice" if body.mode == "voice" else "text")
    _cache[s.id] = s
    return json.loads(s.model_dump_json())


@app.get("/api/session/{sid}/state")
def state(sid: str, since: int = -1):
    raw = store.load_raw(sid)
    if raw is None:
        raise HTTPException(404, "not found")
    if raw.get("version", 0) == since:
        return {"unchanged": True, "version": since}
    return raw


@app.post("/api/session/{sid}/turn")
async def turn(sid: str, body: TurnIn):
    s = get_session(sid)
    if not body.text.strip():
        raise HTTPException(400, "empty turn")
    if s.mode == "voice":
        s.mode = "text"   # typing into a voice session: take it over in text mode
        _cache[sid] = s
    reply = await engine.text_turn(s, body.text)
    return {"reply": reply, "version": s.version, "phase": s.phase}


@app.post("/api/session/{sid}/reset")
def reset(sid: str):
    old = get_session(sid)
    s = engine.new_session(old.objective, old.expert_name, old.mode)
    _cache[s.id] = s
    return json.loads(s.model_dump_json())


@app.post("/api/session/{sid}/playbook")
async def playbook(sid: str):
    s = get_session(sid)
    try:
        md = await outputs.generate_playbook(s)
    except Exception as e:
        log.exception("playbook failed")
        return JSONResponse({"error": str(e)}, status_code=502)
    return {"markdown": md}


@app.get("/api/session/{sid}/playbook")
def get_playbook(sid: str):
    p = store.session_dir(sid) / "playbook.md"
    if not p.exists():
        raise HTTPException(404, "no playbook yet")
    return PlainTextResponse(p.read_text(), media_type="text/markdown")


@app.post("/api/session/{sid}/ask")
async def ask(sid: str, body: AskIn):
    s = get_session(sid)
    try:
        return await outputs.ask(s, body.question)
    except Exception as e:
        log.exception("ask failed")
        return JSONResponse({"error": str(e)}, status_code=502)


@app.get("/api/session/{sid}/frame/{fid}")
def frame(sid: str, fid: str):
    p = store.session_dir(sid) / "frames" / f"{fid}.jpg"
    if not p.exists():
        raise HTTPException(404)
    return FileResponse(p)


@app.get("/api/token")
def token(session_id: str, name: str = "Expert"):
    from livekit import api
    key, secret, url = os.getenv("LIVEKIT_API_KEY"), os.getenv("LIVEKIT_API_SECRET"), os.getenv("LIVEKIT_URL")
    if not (key and secret and url):
        raise HTTPException(503, "LiveKit not configured (.env)")
    s = get_session(session_id)
    if s.mode != "voice":
        s.mode = "voice"           # the worker owns this session from now on
        store.save(s)
        _cache.pop(s.id, None)
    room = f"apprentice-{s.id}-{int(time.time())}"
    meta = json.dumps({"session_id": s.id})
    jwt = (api.AccessToken(key, secret)
           .with_identity(f"expert-{s.id}-{int(time.time())}")
           .with_name(name or s.expert_name)
           .with_metadata(meta)
           .with_grants(api.VideoGrants(room_join=True, room=room, can_publish=True, can_subscribe=True))
           .with_room_config(api.RoomConfiguration(
               agents=[api.RoomAgentDispatch(agent_name="apprentice", metadata=meta)]))
           .to_jwt())
    return {"token": jwt, "url": url, "room": room}


WEB = ROOT / "web"


@app.get("/")
def index():
    return FileResponse(WEB / "index.html")


app.mount("/", StaticFiles(directory=WEB), name="web")
