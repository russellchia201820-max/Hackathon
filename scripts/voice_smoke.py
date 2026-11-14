#!/usr/bin/env python
"""End-to-end voice smoke test without a human: a fake 'expert' joins the LiveKit room,
speaks ElevenLabs-generated audio, and we check the worker transcribed it and replied.
Needs: server on :8000 and `python agent/worker.py dev` running.
Usage: python scripts/voice_smoke.py ["what the expert says" ...]"""
import asyncio
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import apprentice  # noqa: F401,E402  (loads .env)
import httpx  # noqa: E402
from livekit import rtc  # noqa: E402

API = "http://localhost:8000"
SR = 16000
LINES = sys.argv[1:] or [
    "Sure. Basically you look at the lustre first, then you do a hardness test with your knife. "
    "After that it's streak and cleavage, and honestly you just get a feel for it after a while.",
    "The key thing is you always test a fresh surface, because weathered surfaces lie about colour.",
]


async def tts_pcm(h: httpx.AsyncClient, text: str) -> bytes:
    r = await h.post("https://api.elevenlabs.io/v1/text-to-speech/JBFqnCBsd6RMkjVDRZzb?output_format=pcm_16000",
                     headers={"xi-api-key": os.environ["ELEVENLABS_API_KEY"]},
                     json={"text": text, "model_id": "eleven_flash_v2_5"}, timeout=60)
    r.raise_for_status()
    return r.content


async def speak(source: rtc.AudioSource, pcm: bytes, tail_s: float = 2.5) -> None:
    pcm += b"\x00\x00" * int(SR * tail_s)   # trailing silence so the turn ends
    n = SR // 100  # 10 ms frames
    for i in range(0, len(pcm) // 2, n):
        chunk = pcm[i * 2:(i + n) * 2]
        if len(chunk) < n * 2:
            chunk += b"\x00" * (n * 2 - len(chunk))
        await source.capture_frame(rtc.AudioFrame(chunk, SR, 1, n))


async def silence(source: rtc.AudioSource, seconds: float) -> None:
    await speak(source, b"", tail_s=seconds)


async def wait_agent_turns(h, sid, n_agent, timeout=40):
    t0 = time.time()
    while time.time() - t0 < timeout:
        st = (await h.get(f"{API}/api/session/{sid}/state")).json()
        if sum(t["speaker"] == "agent" for t in st["transcript"]) >= n_agent:
            return st, time.time() - t0
        await asyncio.sleep(0.5)
    return st, None


async def main():
    async with httpx.AsyncClient(timeout=30) as h:
        s = (await h.post(f"{API}/api/session", json={"objective": "How to identify minerals in a hand sample",
                                                       "expert_name": "Sam"})).json()
        sid = s["id"]
        tok = (await h.get(f"{API}/api/token", params={"session_id": sid, "name": "Sam"})).json()
        print("session", sid, "room", tok["room"])
        pcms = [await tts_pcm(h, line) for line in LINES]

        room = rtc.Room()
        got_audio = asyncio.Event()

        @room.on("track_subscribed")
        def _on(track, pub, p):
            print(f"  subscribed {track.kind} from {p.identity}")
            if track.kind == rtc.TrackKind.KIND_AUDIO:
                got_audio.set()

        await room.connect(tok["url"], tok["token"])
        source = rtc.AudioSource(SR, 1)
        await room.local_participant.publish_track(
            rtc.LocalAudioTrack.create_audio_track("mic", source),
            rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE))
        feeder = asyncio.create_task(silence(source, 12))  # keep the track alive while the agent greets
        try:
            await asyncio.wait_for(got_audio.wait(), 30)
            print("OK agent joined and is publishing audio")
        except asyncio.TimeoutError:
            print("FAIL agent never published audio (is the worker running?)")
        await feeder
        ok = True
        for k, pcm in enumerate(pcms, start=1):
            print(f"  expert speaks line {k} ({len(pcm) / 2 / SR:.1f}s)")
            await speak(source, pcm)
            feeder = asyncio.create_task(silence(source, 15))
            st, dt = await wait_agent_turns(h, sid, k + 1)
            experts = [t["text"] for t in st["transcript"] if t["speaker"] == "expert"]
            agents = [t["text"] for t in st["transcript"] if t["speaker"] == "agent"]
            if dt is None:
                print(f"FAIL no agent reply to line {k}. expert turns heard: {experts}")
                ok = False
                feeder.cancel()
                break
            print(f"OK heard: {experts[-1]!r}")
            print(f"OK reply (+{dt:.1f}s after turn end incl. silence): {agents[-1]!r}")
            await feeder
        st = (await h.get(f"{API}/api/session/{sid}/state")).json()
        await asyncio.sleep(1)
        st = (await h.get(f"{API}/api/session/{sid}/state")).json()
        print(f"state: {len(st['items'])} items, {len(st['gaps'])} gaps, pinned={sum(i.get('pinned', False) for i in st['items'])}, "
              f"tool events={[e['text'] for e in st['events'] if e['kind'] == 'tool']}")
        errs = [e["text"] for e in st["events"] if e["kind"] == "error"]
        if errs:
            print("errors:", errs)
        await room.disconnect()
        print("PASS" if ok else "FAIL")


asyncio.run(main())
