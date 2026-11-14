#!/usr/bin/env python
"""Turn a Synthesia Studio avatar (av_...) into an interactive avatar and save its id to .env.

Usage: python scripts/synthesia_avatar.py av_xxxxxxxxxxxx
Polls POST /api/interactive-avatars/avatars until done (the documented way to poll)."""
import re
import sys
import time
from pathlib import Path

import httpx
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parent.parent
ENV = ROOT / ".env"
URL = "https://developers.synthesia.io/api/interactive-avatars/avatars"


def set_env(key: str, value: str) -> None:
    text = ENV.read_text()
    line = f"{key}={value}"
    text = re.sub(rf"^{key}=.*$", line, text, flags=re.M) if re.search(rf"^{key}=", text, re.M) else text + f"\n{line}\n"
    ENV.write_text(text)


def main(source: str) -> None:
    key = dotenv_values(ENV).get("SYNTHESIA_API_KEY")
    if not key:
        sys.exit("SYNTHESIA_API_KEY missing in .env")
    h = {"Authorization": f"Bearer {key}"}
    t0 = time.time()
    while True:
        r = httpx.post(URL, headers=h, json={"sourceId": source}, timeout=80)
        if r.status_code >= 400:
            sys.exit(f"Synthesia error {r.status_code}: {r.text[:400]}")
        d = r.json()
        print(f"[{time.time() - t0:5.0f}s] status={d.get('status')} id={d.get('id')}", flush=True)
        if d.get("done"):
            break
        time.sleep(10)
    if d.get("status") != "completed":
        sys.exit(f"Avatar generation ended with status {d.get('status')}: this source can't be interactive.")
    set_env("SYNTHESIA_AVATAR_ID", d["id"])
    print(f"OK: SYNTHESIA_AVATAR_ID={d['id']} written to .env")


if __name__ == "__main__":
    if len(sys.argv) != 2 or not sys.argv[1].startswith("av_"):
        sys.exit(__doc__)
    main(sys.argv[1])
