"""Settings, read once from environment variables (and .env if present)."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


def _env(name: str, default: str = "") -> str:
    value = os.getenv(name, default)
    return value.strip() if isinstance(value, str) else default


@dataclass(frozen=True)
class Settings:
    anthropic_api_key: str = _env("ANTHROPIC_API_KEY")
    llm_model: str = _env("LLM_MODEL", "claude-sonnet-5-5")
    llm_fast_model: str = _env("LLM_FAST_MODEL", "claude-haiku-5-5")
    llm_provider: str = _env("LLM_PROVIDER", "auto").lower()

    elevenlabs_api_key: str = _env("ELEVENLABS_API_KEY")
    elevenlabs_voice_id: str = _env("ELEVENLABS_VOICE_ID", "JBFqnCBsd6RMkjVDRZzb")
    elevenlabs_tts_model: str = _env("ELEVENLABS_TTS_MODEL", "eleven_flash_v2_5")
    elevenlabs_stt_model: str = _env("ELEVENLABS_STT_MODEL", "scribe_v2")

    anam_api_key: str = _env("ANAM_API_KEY")
    anam_avatar_id: str = _env("ANAM_AVATAR_ID", "071b0286-4cce-4808-bee2-e642f1062de3")
    anam_voice_id: str = _env("ANAM_VOICE_ID", "de23e340-1416-4dd8-977d-065a7ca11697")
    anam_avatar_model: str = _env("ANAM_AVATAR_MODEL", "cara-4")
    anam_persona_name: str = _env("ANAM_PERSONA_NAME", "Apprentice")

    mongodb_uri: str = _env("MONGODB_URI")
    mongodb_db: str = _env("MONGODB_DB", "last_expert")
    data_dir: Path = ROOT / _env("DATA_DIR", "data")

    opening_line: str = _env(
        "OPENING_LINE", "Hi, I'm here to learn from you. What do you want to talk about?"
    )
    max_turns: int = int(_env("MAX_TURNS", "30") or 30)

    @property
    def use_mock_llm(self) -> bool:
        if self.llm_provider == "mock":
            return True
        if self.llm_provider == "anthropic":
            return False
        return not self.anthropic_api_key

    @property
    def features(self) -> dict:
        return {
            "llm": "mock (offline)" if self.use_mock_llm else self.llm_model,
            "elevenlabs": bool(self.elevenlabs_api_key),
            "avatar": bool(self.anam_api_key),
            "storage": "mongodb" if self.mongodb_uri else "json files",
        }


settings = Settings()
