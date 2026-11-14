"""Avatar switch: AVATAR_PROVIDER = anam | synthesia. The plugin only renders the face;
our own STT/LLM/TTS stay in charge. Any failure -> voice-only (the demo never dies)."""
from __future__ import annotations

import asyncio
import logging
import os
import time

log = logging.getLogger("apprentice.avatar")


def _enabled() -> bool:
    return os.getenv("AVATAR_ENABLED", "false").lower() == "true"


def _anam():
    from livekit.plugins import anam
    avatar_id = os.getenv("ANAM_AVATAR_ID")
    if not (os.getenv("ANAM_API_KEY") and avatar_id):
        raise RuntimeError("ANAM_API_KEY / ANAM_AVATAR_ID missing")
    return anam.AvatarSession(persona_config=anam.PersonaConfig(name="Apprentice", avatarId=avatar_id))


def _synthesia():
    from livekit.plugins import synthesia
    avatar_id = os.getenv("SYNTHESIA_AVATAR_ID")
    if not (os.getenv("SYNTHESIA_API_KEY") and avatar_id):
        raise RuntimeError("SYNTHESIA_API_KEY / SYNTHESIA_AVATAR_ID missing")
    return synthesia.AvatarSession(synthesia.AvatarConfig(avatar_ids=[avatar_id]))


PROVIDERS = {"anam": _anam, "synthesia": _synthesia}


async def start_avatar(session, room) -> str | None:
    """Start the configured avatar. Returns the provider name, or None for voice-only.
    Must be called before AgentSession.start()."""
    if not _enabled():
        log.info("avatar disabled (AVATAR_ENABLED=false) -> voice-only")
        return None
    provider = os.getenv("AVATAR_PROVIDER", "anam").lower()
    order = [provider] + [p for p in PROVIDERS if p != provider]  # try the other one as a fallback
    for name in order:
        t0 = time.time()
        try:
            av = PROVIDERS[name]()
            await asyncio.wait_for(av.start(session, room=room), timeout=float(os.getenv("AVATAR_TIMEOUT_S", "25")))
            log.info("%s avatar started in %.1fs", name, time.time() - t0)
            return name
        except Exception as e:
            log.warning("AVATAR %s FAILED (%s: %s)", name, type(e).__name__, e)
    log.warning("no avatar available -> falling back to voice-only")
    return None
