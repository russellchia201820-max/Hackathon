"""Turn uploaded files (manuals, transcripts, notes, recordings) into plain text."""
from __future__ import annotations

import io
from pathlib import Path

from . import voice

TEXT_EXT = {".txt", ".md", ".markdown", ".csv", ".json", ".log", ".srt", ".vtt"}
AUDIO_EXT = {".mp3", ".wav", ".m4a", ".mp4", ".webm", ".ogg", ".oga", ".flac", ".aac"}


class UnsupportedFile(ValueError):
    pass


def extract_text(filename: str, data: bytes, content_type: str = "") -> str:
    ext = Path(filename or "").suffix.lower()

    if ext in TEXT_EXT or content_type.startswith("text/"):
        return data.decode("utf-8", errors="replace")

    if ext == ".pdf" or content_type == "application/pdf":
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(data))
        return "\n\n".join((page.extract_text() or "") for page in reader.pages)

    if ext == ".docx":
        import docx

        document = docx.Document(io.BytesIO(data))
        parts = [p.text for p in document.paragraphs if p.text.strip()]
        for table in document.tables:
            for row in table.rows:
                parts.append(" | ".join(cell.text.strip() for cell in row.cells))
        return "\n".join(parts)

    if ext in AUDIO_EXT or content_type.startswith("audio/") or content_type.startswith("video/"):
        if not voice.enabled():
            raise UnsupportedFile("Audio files need an ELEVENLABS_API_KEY to be transcribed.")
        return voice.transcribe(data, filename or "audio.webm", content_type or "application/octet-stream")

    raise UnsupportedFile(f"Unsupported file type '{ext or content_type}'. Use .txt, .md, .pdf, .docx or audio.")
