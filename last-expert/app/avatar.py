"""Anam real-time avatar: exchange the API key for a short-lived session token.

The avatar uses our own brain (llmId CUSTOMER_CLIENT_V1): Anam listens to the expert and gives us the
transcript, we decide what to ask, and the browser calls anamClient.talk(question).
"""
from __future__ import annotations

import httpx

from .config import settings


class AvatarError(RuntimeError):
    pass


def enabled() -> bool:
    return bool(settings.anam_api_key)


def session_token() -> str:
    if not enabled():
        raise AvatarError("ANAM_API_KEY is not set.")
    body = {
        "personaConfig": {
            "name": settings.anam_persona_name,
            "avatarId": settings.anam_avatar_id,
            "avatarModel": settings.anam_avatar_model,
            "voiceId": settings.anam_voice_id,
            "llmId": "CUSTOMER_CLIENT_V1",
        }
    }
    try:
        response = httpx.post(
            "https://api.anam.ai/v1/auth/session-token",
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {settings.anam_api_key}"},
            json=body,
            timeout=30,
        )
    except httpx.HTTPError as exc:
        raise AvatarError(f"Could not reach Anam: {exc}") from exc
    if response.status_code >= 300:
        raise AvatarError(f"Anam session token failed ({response.status_code}): {response.text[:300]}")
    token = response.json().get("sessionToken")
    if not token:
        raise AvatarError("Anam did not return a session token.")
    return token
