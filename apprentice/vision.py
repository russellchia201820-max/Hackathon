"""M7 'Show me': describe a camera frame from the expert and suggest a question."""
from __future__ import annotations

import base64
import re

from . import llm, store
from .models import Frame, Session

TRIGGER = re.compile(
    r"\b(look at (this|that|it|these|here)|see (this|that|here)|show you|take a look|can you see|"
    r"have a look|here'?s (one|an example|what)|watch (this|me)|like this)\b", re.I)

SCHEMA = {
    "type": "object",
    "properties": {"description": {"type": "string"}, "question": {"type": "string"}},
    "required": ["description", "question"], "additionalProperties": False,
}
PROMPT = """You are the eyes of an AI apprentice on a video call with an expert who is teaching a task.
The expert just pointed their camera at something. Describe concretely and briefly (1-2 sentences) what is
visible that matters for the task (objects, colours, textures, shapes, what their hands are doing).
Do not identify or describe people's faces. Then write ONE short spoken question the apprentice could ask
about what it sees, to draw out the expert's tacit knowledge (e.g. what cue they're reading in it)."""


def wants_to_show(text: str) -> bool:
    return bool(TRIGGER.search(text or ""))


async def describe(s: Session, jpeg: bytes, said: str) -> dict:
    content = [
        {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                     "data": base64.standard_b64encode(jpeg).decode()}},
        {"type": "text", "text": f"Task: {s.objective}\nThe expert just said: \"{said}\""},
    ]
    return await llm.complete("analyst", PROMPT, [{"role": "user", "content": content}],
                              max_tokens=400, schema=SCHEMA, effort="low")


def record(s: Session, jpeg: bytes, description: str) -> Frame:
    fid = f"f{len(s.frames) + 1}"
    d = store.session_dir(s.id) / "frames"
    d.mkdir(exist_ok=True)
    (d / f"{fid}.jpg").write_bytes(jpeg)
    t = s.add_turn("observation", description)
    f = Frame(id=fid, path=str(d / f"{fid}.jpg"), description=description, turn_index=t.index)
    s.frames.append(f)
    return f
