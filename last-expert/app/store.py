"""Knowledge-base storage. JSON files by default, MongoDB if MONGODB_URI is set."""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Optional

from .config import settings
from .models import Session, now


class JsonStore:
    def __init__(self, folder: Path):
        self.folder = folder / "sessions"
        self.folder.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def _path(self, sid: str) -> Path:
        safe = "".join(c for c in sid if c.isalnum())
        return self.folder / f"{safe}.json"

    def get(self, sid: str) -> Optional[Session]:
        path = self._path(sid)
        if not path.exists():
            return None
        return Session.model_validate_json(path.read_text(encoding="utf-8"))

    def save(self, session: Session) -> None:
        session.updated = now()
        path = self._path(session.id)
        tmp = path.with_suffix(".tmp")
        with self._lock:
            tmp.write_text(session.model_dump_json(indent=2), encoding="utf-8")
            os.replace(tmp, path)

    def list(self) -> list[dict]:
        out = []
        for path in self.folder.glob("*.json"):
            try:
                out.append(Session.model_validate_json(path.read_text(encoding="utf-8")).summary())
            except Exception:  # a corrupt file should not break the list
                continue
        return sorted(out, key=lambda s: s["updated"], reverse=True)

    def delete(self, sid: str) -> bool:
        path = self._path(sid)
        if path.exists():
            path.unlink()
            return True
        return False


class MongoStore:
    def __init__(self, uri: str, db: str):
        from pymongo import MongoClient

        self.col = MongoClient(uri, serverSelectionTimeoutMS=5000)[db]["sessions"]

    def get(self, sid: str) -> Optional[Session]:
        doc = self.col.find_one({"_id": sid})
        if not doc:
            return None
        doc.pop("_id", None)
        return Session.model_validate(doc)

    def save(self, session: Session) -> None:
        session.updated = now()
        doc = json.loads(session.model_dump_json())
        doc["_id"] = session.id
        self.col.replace_one({"_id": session.id}, doc, upsert=True)

    def list(self) -> list[dict]:
        out = []
        for doc in self.col.find({}).sort("updated", -1).limit(200):
            doc.pop("_id", None)
            try:
                out.append(Session.model_validate(doc).summary())
            except Exception:
                continue
        return out

    def delete(self, sid: str) -> bool:
        return self.col.delete_one({"_id": sid}).deleted_count > 0


def make_store():
    if settings.mongodb_uri:
        return MongoStore(settings.mongodb_uri, settings.mongodb_db)
    return JsonStore(settings.data_dir)
