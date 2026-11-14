#!/usr/bin/env python
"""Headless screenshot of the UI + JS console errors. Usage: screenshot.py <session_id> <out.png> [tab]"""
import asyncio
import sys

from playwright.async_api import async_playwright


async def main(sid, out, tab=None):
    async with async_playwright() as p:
        b = await p.chromium.launch()
        pg = await b.new_page(viewport={"width": 1600, "height": 900})
        errs = []
        pg.on("console", lambda m: errs.append(m.text) if m.type == "error" else None)
        pg.on("pageerror", lambda e: errs.append(str(e)))
        await pg.goto(f"http://localhost:8000/?s={sid}")
        await pg.wait_for_timeout(2500)
        if tab:
            await pg.click(f'.tab[data-tab="{tab}"]')
            await pg.wait_for_timeout(500)
        await pg.screenshot(path=out)
        print("JS errors:", errs or "none")
        await b.close()

asyncio.run(main(*sys.argv[1:]))
