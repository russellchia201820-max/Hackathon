"""Thin wrapper around the Anthropic API that forces structured (tool) output."""
from __future__ import annotations

import os
import time


class LLM:
    def __init__(self, model: str | None = None, max_tokens: int = 4000):
        import anthropic  # imported here so the mock works without the package
        self.client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY
        self.model = model or os.getenv("EXPERT_MODEL", "claude-sonnet-5-5")
        self.max_tokens = max_tokens

    def call_tool(self, system: str, user: str, tool: dict, retries: int = 2) -> dict:
        last_err = None
        for attempt in range(retries + 1):
            try:
                resp = self.client.messages.create(
                    model=self.model,
                    max_tokens=self.max_tokens,
                    system=system,
                    messages=[{"role": "user", "content": user}],
                    tools=[tool],
                    tool_choice={"type": "tool", "name": tool["name"]},
                )
                for block in resp.content:
                    if block.type == "tool_use":
                        return block.input
                raise RuntimeError("model returned no tool call")
            except Exception as e:  # network blips, overload: retry briefly
                last_err = e
                time.sleep(1.5 * (attempt + 1))
        raise RuntimeError(f"LLM call failed: {last_err}")

    def text(self, system: str, user: str) -> str:
        resp = self.client.messages.create(
            model=self.model, max_tokens=1200, system=system,
            messages=[{"role": "user", "content": user}],
        )
        return "".join(b.text for b in resp.content if b.type == "text")
