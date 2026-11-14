#!/usr/bin/env python
"""End-to-end voice smoke test without a human: a fake 'expert' joins the LiveKit room,
speaks ElevenLabs-generated audio, and we check the worker transcribed it and replied.
Needs: server on :8000 and `python agent/worker.py dev` running.
Usage: python scripts/voice_smoke.py ["what the expert says" ...]
       python scripts/voice_smoke.py --video   # also publish a camera feed (M7 'Show me' test)
       python scripts/voice_smoke.py --drop-avatar  # kill the avatar mid-call, reconnect like the browser,
                                                    # check the call resumes voice-only"""
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
VIDEO = "--video" in sys.argv
DROP = "--drop-avatar" in sys.argv
ARGS = [a for a in sys.argv[1:] if not a.startswith("--")]
LINES = ARGS or ([
    "Let me show you, look at this one here. See the colour and how it breaks?",
] if VIDEO else [
    "Sure. Basically you look at the lustre first, then you do a hardness test with your knife. "
    "After that it's streak and cleavage, and honestly you just get a feel for it after a while.",
    "The key thing is you always test a fresh surface, because weathered surfaces lie about colour.",
])


def rock_image(w=640, h=480) -> bytes:
    """A synthetic 'pink feldspar crystal' on a bench, as RGBA bytes."""
    from PIL import Image, ImageDraw
    img = Image.new("RGBA", (w, h), (90, 70, 50, 255))
    d = ImageDraw.Draw(img)
    d.polygon([(200, 150), (420, 120), (470, 260), (380, 360), (210, 330), (170, 240)], fill=(226, 150, 140, 255))
    for x in range(220, 440, 18):  # flat, stepped cleavage faces
        d.line([(x, 160), (x + 30, 330)], fill=(245, 190, 180, 255), width=3)
    d.ellipse([280, 200, 320, 230], fill=(255, 255, 255, 255))
    return img.tobytes()


async def camera(source, stop: asyncio.Event) -> None:
    frame = rtc.VideoFrame(640, 480, rtc.VideoBufferType.RGBA, rock_image())
    while not stop.is_set():
        source.capture_frame(frame)
        await asyncio.sleep(0.2)


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


async def drop_and_reconnect(h, sid, room):
    """Remove the avatar participant, expect the agent to leave, rejoin like the browser does."""
    from livekit import api
    lk = api.LiveKitAPI(os.environ["LIVEKIT_URL"].replace("wss://", "https://"),
                        os.environ["LIVEKIT_API_KEY"], os.environ["LIVEKIT_API_SECRET"])
    av = [p for p in room.remote_participants.values() if "avatar" in p.identity]
    if not av:
        print("SKIP no avatar in room (AVATAR_ENABLED=false?)")
        await lk.aclose()
        return room, None
    await lk.room.remove_participant(api.RoomParticipantIdentity(room=room.name, identity=av[0].identity))
    await lk.aclose()
    print(f"  removed {av[0].identity}; waiting for the agent to leave…")
    t0 = time.time()
    while time.time() - t0 < 20 and any(p.identity.startswith("agent-") for p in room.remote_participants.values()):
        await asyncio.sleep(0.3)
    left = not any(p.identity.startswith("agent-") for p in room.remote_participants.values())
    print(("OK" if left else "FAIL") + f" agent left after {time.time() - t0:.1f}s")
    st = (await h.get(f"{API}/api/session/{sid}/state")).json()
    print(("OK" if st.get("avatar_disabled") else "FAIL") + " session marked avatar_disabled")
    await room.disconnect()
    tok = (await h.get(f"{API}/api/token", params={"session_id": sid, "name": "Sam"})).json()
    room = rtc.Room()
    await room.connect(tok["url"], tok["token"])
    source = rtc.AudioSource(SR, 1)
    await room.local_participant.publish_track(
        rtc.LocalAudioTrack.create_audio_track("mic", source),
        rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE))
    n_agent = sum(t["speaker"] == "agent" for t in st["transcript"])
    feeder = asyncio.create_task(silence(source, 14))
    st2, dt = await wait_agent_turns(h, sid, n_agent + 1, timeout=30)
    await feeder
    if dt is None:
        print("FAIL no welcome-back after reconnect")
    else:
        print(f"OK reconnected voice-only, apprentice said: {st2['transcript'][-1]['text']!r}")
    print("  avatar in new room:", any("avatar" in p.identity for p in room.remote_participants.values()))
    return room, source


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
        stop_cam = asyncio.Event()
        if VIDEO:
            vsrc = rtc.VideoSource(640, 480)
            await room.local_participant.publish_track(
                rtc.LocalVideoTrack.create_video_track("cam", vsrc),
                rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_CAMERA))
            asyncio.create_task(camera(vsrc, stop_cam))
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
            before = sum(t["speaker"] == "agent" for t in (await h.get(f"{API}/api/session/{sid}/state")).json()["transcript"])
            await speak(source, pcm)
            feeder = asyncio.create_task(silence(source, 15))
            st, dt = await wait_agent_turns(h, sid, before + 1)
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
            if DROP and k == 1:
                room, source = await drop_and_reconnect(h, sid, room)
        st = (await h.get(f"{API}/api/session/{sid}/state")).json()
        await asyncio.sleep(1)
        st = (await h.get(f"{API}/api/session/{sid}/state")).json()
        print(f"state: {len(st['items'])} items, {len(st['gaps'])} gaps, pinned={sum(i.get('pinned', False) for i in st['items'])}, "
              f"tool events={[e['text'] for e in st['events'] if e['kind'] == 'tool']}")
        errs = [e["text"] for e in st["events"] if e["kind"] == "error"]
        if errs:
            print("errors:", errs)
        if VIDEO:
            print(f"frames captured: {[f['description'][:120] for f in st.get('frames', [])]}")
            stop_cam.set()
        await room.disconnect()
        print("PASS" if ok else "FAIL")


asyncio.run(main())
