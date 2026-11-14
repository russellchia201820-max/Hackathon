"""Load/save session state atomically: sessions/<id>/state.json."""
from __future__ import annotations

import json
import os
import tempfile
import threading
from pathlib import Path

from . import ROOT
from .models import Session, now

SESSIONS_DIR = Path(os.getenv("SESSIONS_DIR", ROOT / "sessions"))
_lock = threading.Lock()


def session_dir(sid: str) -> Path:
    d = SESSIONS_DIR / sid
    d.mkdir(parents=True, exist_ok=True)
    return d


def save(s: Session) -> None:
    with _lock:
        s.version += 1
        s.updated_at = now()
        d = session_dir(s.id)
        fd, tmp = tempfile.mkstemp(dir=d, prefix=".state-", suffix=".json")
        with os.fdopen(fd, "w") as f:
            f.write(s.model_dump_json(indent=1))
        os.replace(tmp, d / "state.json")


def load(sid: str) -> Session | None:
    p = SESSIONS_DIR / sid / "state.json"
    if not p.exists():
        return None
    return Session.model_validate_json(p.read_text())


def load_raw(sid: str) -> dict | None:
    p = SESSIONS_DIR / sid / "state.json"
    return json.loads(p.read_text()) if p.exists() else None


def list_sessions() -> list[dict]:
    out = []
    if SESSIONS_DIR.exists():
        for p in SESSIONS_DIR.glob("*/state.json"):
            try:
                d = json.loads(p.read_text())
                out.append({"id": d["id"], "objective": d["objective"], "updated_at": d["updated_at"]})
            except Exception:
                pass
    return sorted(out, key=lambda x: -x["updated_at"])
