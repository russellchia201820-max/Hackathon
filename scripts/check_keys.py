#!/usr/bin/env python
"""Ping every configured API and report OK / FAIL."""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import apprentice  # noqa: E402,F401  (loads .env)
import httpx  # noqa: E402

results: list[tuple[str, bool, str]] = []


def rep(name, ok, msg=""):
    results.append((name, ok, msg))
    tag = "\033[92mOK  \033[0m" if ok else "\033[91mFAIL\033[0m"
    print(f"{tag} {name:<22} {msg}", flush=True)


def need(*keys):
    missing = [k for k in keys if not os.getenv(k)]
    return missing


async def check_anthropic():
    if m := need("ANTHROPIC_API_KEY"):
        return rep("anthropic", False, f"missing {m}")
    import anthropic
    c = anthropic.AsyncAnthropic()
    for role in ("FAST", "ANALYST"):
        prov = os.getenv(f"{role}_PROVIDER", "anthropic")
        if prov != "anthropic":
            continue
        model = os.getenv(f"{role}_MODEL") or ("claude-haiku-5-5" if role == "FAST" else "claude-sonnet-5-5")
        try:
            r = await c.messages.create(model=model, max_tokens=5, messages=[{"role": "user", "content": "Say OK"}])
            rep(f"anthropic {role.lower()}", True, f"{model} -> {r.content[0].text if r.content else ''!r}")
        except Exception as e:
            avail = ""
            try:
                avail = ", ".join([m.id async for m in c.models.list()][:15])
            except Exception:
                pass
            rep(f"anthropic {role.lower()}", False, f"{model}: {str(e)[:150]}  available: {avail}")


async def check_openai_compat():
    if "openai_compatible" not in (os.getenv("FAST_PROVIDER", ""), os.getenv("ANALYST_PROVIDER", "")):
        return
    if m := need("OPENAI_COMPAT_BASE_URL", "OPENAI_COMPAT_API_KEY"):
        return rep("openai_compatible", False, f"missing {m}")
    import openai
    try:
        ms = await openai.AsyncOpenAI(base_url=os.getenv("OPENAI_COMPAT_BASE_URL"),
                                      api_key=os.getenv("OPENAI_COMPAT_API_KEY")).models.list()
        rep("openai_compatible", True, f"{len(ms.data)} models")
    except Exception as e:
        rep("openai_compatible", False, str(e)[:150])


async def check_livekit():
    if m := need("LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET"):
        return rep("livekit", False, f"missing {m}")
    from livekit import api
    url = os.getenv("LIVEKIT_URL").replace("wss://", "https://").replace("ws://", "http://")
    lk = api.LiveKitAPI(url, os.getenv("LIVEKIT_API_KEY"), os.getenv("LIVEKIT_API_SECRET"))
    try:
        rooms = await lk.room.list_rooms(api.ListRoomsRequest())
        rep("livekit", True, f"{len(rooms.rooms)} active rooms")
    except Exception as e:
        rep("livekit", False, str(e)[:150])
    finally:
        await lk.aclose()


async def check_synthesia(h: httpx.AsyncClient):
    if not os.getenv("SYNTHESIA_API_KEY"):
        return rep("synthesia", os.getenv("AVATAR_PROVIDER") != "synthesia", "no SYNTHESIA_API_KEY (optional)")
    r = await h.get("https://api.synthesia.io/v2/videos?limit=1",
                    headers={"Authorization": os.getenv("SYNTHESIA_API_KEY")})
    rep("synthesia (api key)", r.status_code == 200, f"HTTP {r.status_code} {r.text[:120] if r.status_code != 200 else ''}")
    if not os.getenv("SYNTHESIA_AVATAR_ID"):
        rep("synthesia avatar id", False, "SYNTHESIA_AVATAR_ID not set")


async def check_anam(h: httpx.AsyncClient):
    if not os.getenv("ANAM_API_KEY"):
        return rep("anam", os.getenv("AVATAR_PROVIDER") != "anam", "no ANAM_API_KEY yet (optional; 'coming soon')")
    r = await h.get("https://api.anam.ai/v1/avatars", headers={"Authorization": f"Bearer {os.getenv('ANAM_API_KEY')}"})
    rep("anam (api key)", r.status_code == 200, f"HTTP {r.status_code} {r.text[:120] if r.status_code != 200 else ''}")
    if not os.getenv("ANAM_AVATAR_ID"):
        rep("anam avatar id", False, "ANAM_AVATAR_ID not set")


async def check_elevenlabs(h: httpx.AsyncClient):
    if m := need("ELEVENLABS_API_KEY"):
        return rep("elevenlabs", False, f"missing {m}")
    hd = {"xi-api-key": os.getenv("ELEVENLABS_API_KEY")}
    r = await h.get("https://api.elevenlabs.io/v1/models", headers=hd)
    rep("elevenlabs (key)", r.status_code == 200, f"HTTP {r.status_code} {r.text[:120] if r.status_code != 200 else ''}")
    # real TTS call (tiny) proves TTS permission + credits
    vid = os.getenv("ELEVENLABS_VOICE_ID") or "hpp4J3VqNfWAUOO0d1Us"
    r = await h.post(f"https://api.elevenlabs.io/v1/text-to-speech/{vid}", headers=hd,
                     json={"text": "OK", "model_id": "eleven_flash_v2_5"})
    rep("elevenlabs TTS", r.status_code == 200, f"voice {vid}: HTTP {r.status_code} {r.text[:120] if r.status_code != 200 else ''}")


async def main():
    async with httpx.AsyncClient(timeout=20) as h:
        for coro in (check_anthropic(), check_openai_compat(), check_elevenlabs(h), check_livekit(),
                     check_synthesia(h), check_anam(h)):
            try:
                await coro
            except Exception as e:
                rep(getattr(coro, "__name__", "check"), False, f"{type(e).__name__}: {e}"[:150])
    bad = [r for r in results if not r[1]]
    print(f"\n{len(results) - len(bad)}/{len(results)} OK")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    asyncio.run(main())
