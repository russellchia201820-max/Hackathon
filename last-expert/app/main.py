"""HTTP API and static web app.  Run with:  uvicorn app.main:app --reload"""
from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import avatar, voice
from .config import ROOT, settings
from .documents import UnsupportedFile, extract_text
from .interviewer import Interviewer, NotFound
from .llm import LLMError, make_llm
from .store import make_store

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("last_expert")

app = FastAPI(title="Last Expert", version="1.0")
engine = Interviewer(make_llm(), make_store())
STATIC = ROOT / "static"
MAX_UPLOAD_BYTES = 25 * 1024 * 1024


# ---------------------------------------------------------------- errors
@app.exception_handler(NotFound)
def _not_found(_: Request, exc: NotFound):
    return JSONResponse({"error": str(exc)}, status_code=404)


@app.exception_handler(ValueError)
def _bad_request(_: Request, exc: ValueError):
    return JSONResponse({"error": str(exc)}, status_code=400)


@app.exception_handler(LLMError)
def _llm_error(_: Request, exc: LLMError):
    log.error("LLM error: %s", exc)
    return JSONResponse({"error": str(exc)}, status_code=502)


@app.exception_handler(voice.VoiceError)
def _voice_error(_: Request, exc: voice.VoiceError):
    return JSONResponse({"error": str(exc)}, status_code=502)


@app.exception_handler(avatar.AvatarError)
def _avatar_error(_: Request, exc: avatar.AvatarError):
    return JSONResponse({"error": str(exc)}, status_code=502)


# ---------------------------------------------------------------- schemas
class NewSession(BaseModel):
    expert_name: str = ""
    expert_role: str = ""
    site: str = ""


class Message(BaseModel):
    text: str


class Question(BaseModel):
    question: str


class StatusUpdate(BaseModel):
    status: str


class SpeakRequest(BaseModel):
    text: str


# ---------------------------------------------------------------- pages
@app.get("/", include_in_schema=False)
def index():
    return FileResponse(STATIC / "index.html")


app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.get("/api/health")
def health():
    return {"ok": True, "features": settings.features, "opening_line": settings.opening_line}


# ---------------------------------------------------------------- sessions
@app.post("/api/sessions")
def create_session(body: NewSession):
    return engine.public(engine.create_session(body.expert_name, body.expert_role, body.site))


@app.get("/api/sessions")
def list_sessions():
    return engine.store.list()


@app.get("/api/sessions/{sid}")
def get_session(sid: str):
    return engine.get_public(sid)


@app.delete("/api/sessions/{sid}")
def delete_session(sid: str):
    if not engine.store.delete(sid):
        raise NotFound(f"Session {sid} not found")
    return {"deleted": sid}


# ---------------------------------------------------------------- capture
@app.post("/api/sessions/{sid}/message")
def expert_message(sid: str, body: Message):
    return engine.handle_expert_message(sid, body.text)


@app.post("/api/sessions/{sid}/documents")
async def upload_document(sid: str, file: UploadFile = File(...), kind: str = Form("reference")):
    data = await file.read()
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, "File too large (25 MB max).")
    try:
        text = extract_text(file.filename or "", data, file.content_type or "")
    except UnsupportedFile as exc:
        return JSONResponse({"error": str(exc)}, status_code=415)
    # The LLM call is blocking, so run it off the event loop.
    from starlette.concurrency import run_in_threadpool

    return await run_in_threadpool(engine.add_document, sid, file.filename or "document", kind, text)


@app.post("/api/sessions/{sid}/assumptions/{aid}")
def update_assumption(sid: str, aid: str, body: StatusUpdate):
    return engine.set_assumption_status(sid, aid, body.status)


@app.delete("/api/sessions/{sid}/knowledge/{kid}")
def delete_knowledge(sid: str, kid: str):
    return engine.delete_knowledge(sid, kid)


@app.get("/api/sessions/{sid}/candidates")
def candidates(sid: str):
    """What the apprentice would like to ask about next, and why (for transparency in the UI)."""
    return engine.rank_candidates(engine.load(sid))


# ---------------------------------------------------------------- trainee + outputs
@app.post("/api/sessions/{sid}/ask")
def trainee_ask(sid: str, body: Question):
    return engine.ask(sid, body.question)


@app.post("/api/sessions/{sid}/manual")
def make_manual(sid: str):
    return {"markdown": engine.generate_manual(sid)}


@app.get("/api/sessions/{sid}/export")
def export(sid: str, format: str = "json"):
    engine.wait_idle(sid)
    s = engine.load(sid)
    slug = "".join(c if c.isalnum() else "-" for c in (s.topic or "session").lower()).strip("-")[:40] or "session"
    if format == "json":
        return JSONResponse(
            engine.public(s), headers={"Content-Disposition": f'attachment; filename="{slug}-knowledge.json"'}
        )
    if format == "agent":
        return PlainTextResponse(
            engine.agent_pack(sid), headers={"Content-Disposition": f'attachment; filename="{slug}-expert-pack.md"'}
        )
    if format == "manual":
        markdown = s.manual_markdown or engine.generate_manual(sid)
        return PlainTextResponse(markdown, headers={"Content-Disposition": f'attachment; filename="{slug}-manual.md"'})
    raise ValueError("format must be json, agent or manual")


# ---------------------------------------------------------------- voice + avatar
@app.post("/api/transcribe")
async def transcribe(file: UploadFile = File(...)):
    if not voice.enabled():
        return JSONResponse({"error": "ElevenLabs is not configured; use browser speech."}, status_code=400)
    data = await file.read()
    from starlette.concurrency import run_in_threadpool

    text = await run_in_threadpool(
        voice.transcribe, data, file.filename or "speech.webm", file.content_type or "audio/webm"
    )
    return {"text": text}


@app.post("/api/tts")
def tts(body: SpeakRequest):
    if not voice.enabled():
        return JSONResponse({"error": "ElevenLabs is not configured; use browser speech."}, status_code=400)
    return Response(content=voice.speak(body.text), media_type="audio/mpeg")


@app.post("/api/avatar/session")
def avatar_session():
    if not avatar.enabled():
        return JSONResponse({"error": "ANAM_API_KEY is not set."}, status_code=400)
    return {"sessionToken": avatar.session_token()}
