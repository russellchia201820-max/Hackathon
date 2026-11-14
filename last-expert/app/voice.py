"""ElevenLabs speech-to-text (Scribe) and text-to-speech."""
from __future__ import annotations

import httpx

from .config import settings

API = "https://api.elevenlabs.io/v1"


class VoiceError(RuntimeError):
    pass


def enabled() -> bool:
    return bool(settings.elevenlabs_api_key)


def _headers() -> dict:
    return {"xi-api-key": settings.elevenlabs_api_key}


def transcribe(audio: bytes, filename: str = "audio.webm", content_type: str = "audio/webm") -> str:
    if not enabled():
        raise VoiceError("ELEVENLABS_API_KEY is not set.")
    try:
        response = httpx.post(
            f"{API}/speech-to-text",
            headers=_headers(),
            data={"model_id": settings.elevenlabs_stt_model},
            files={"file": (filename, audio, content_type)},
            timeout=120,
        )
    except httpx.HTTPError as exc:
        raise VoiceError(f"Could not reach ElevenLabs: {exc}") from exc
    if response.status_code != 200:
        raise VoiceError(f"ElevenLabs transcription failed ({response.status_code}): {response.text[:300]}")
    return (response.json().get("text") or "").strip()


def speak(text: str) -> bytes:
    if not enabled():
        raise VoiceError("ELEVENLABS_API_KEY is not set.")
    try:
        response = httpx.post(
            f"{API}/text-to-speech/{settings.elevenlabs_voice_id}",
            params={"output_format": "mp3_44100_128"},
            headers={**_headers(), "Content-Type": "application/json", "Accept": "audio/mpeg"},
            json={"text": text[:2500], "model_id": settings.elevenlabs_tts_model},
            timeout=60,
        )
    except httpx.HTTPError as exc:
        raise VoiceError(f"Could not reach ElevenLabs: {exc}") from exc
    if response.status_code != 200:
        raise VoiceError(f"ElevenLabs speech failed ({response.status_code}): {response.text[:300]}")
    return response.content
