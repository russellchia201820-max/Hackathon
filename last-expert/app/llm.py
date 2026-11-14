"""Thin wrapper around the Claude API: forced tool calls for structured output, plus plain text."""
from __future__ import annotations

import logging
import time
from typing import Any, Optional

from .config import settings

log = logging.getLogger("last_expert.llm")


class LLMError(RuntimeError):
    pass


class AnthropicLLM:
    name = "anthropic"

    def __init__(self, api_key: str):
        import anthropic

        self._anthropic = anthropic
        self.client = anthropic.Anthropic(api_key=api_key, max_retries=3, timeout=90.0)

    @staticmethod
    def _system_blocks(system: str) -> list[dict]:
        # The system prompt is static, so mark it cacheable to cut latency and cost on every turn.
        return [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]

    def call_tool(
        self,
        system: str,
        user_text: str,
        tool: dict,
        model: Optional[str] = None,
        max_tokens: int = 2500,
    ) -> dict[str, Any]:
        model = model or settings.llm_model
        started = time.time()
        try:
            response = self.client.messages.create(
                model=model,
                max_tokens=max_tokens,
                system=self._system_blocks(system),
                messages=[{"role": "user", "content": user_text}],
                tools=[tool],
                tool_choice={"type": "tool", "name": tool["name"]},
            )
        except self._anthropic.APIStatusError as exc:
            raise LLMError(f"Claude API error {exc.status_code}: {exc.message}") from exc
        except self._anthropic.APIConnectionError as exc:
            raise LLMError(f"Could not reach the Claude API: {exc}") from exc

        log.info("%s %s in %.1fs (stop=%s)", tool["name"], model, time.time() - started, response.stop_reason)
        for block in response.content:
            if getattr(block, "type", None) == "tool_use":
                data = block.input
                if isinstance(data, dict):
                    return data
        raise LLMError(f"Claude did not return structured output (stop_reason={response.stop_reason}).")

    def complete_text(
        self,
        system: str,
        user_text: str,
        model: Optional[str] = None,
        max_tokens: int = 4000,
    ) -> str:
        model = model or settings.llm_model
        try:
            response = self.client.messages.create(
                model=model,
                max_tokens=max_tokens,
                system=self._system_blocks(system),
                messages=[{"role": "user", "content": user_text}],
            )
        except self._anthropic.APIStatusError as exc:
            raise LLMError(f"Claude API error {exc.status_code}: {exc.message}") from exc
        except self._anthropic.APIConnectionError as exc:
            raise LLMError(f"Could not reach the Claude API: {exc}") from exc
        return "".join(getattr(b, "text", "") for b in response.content).strip()


def make_llm():
    if settings.use_mock_llm:
        from .mock_llm import MockLLM

        log.warning("No ANTHROPIC_API_KEY set: running with the offline mock brain.")
        return MockLLM()
    return AnthropicLLM(settings.anthropic_api_key)
