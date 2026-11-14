"""Thin wrapper around the Claude API: structured JSON output plus plain text.

Models differ in which structured-output features they accept, so call_tool tries, in order:
  1. json_schema  - native structured outputs (output_config.format)
  2. tool_auto    - offer the tool and ask the model to call it
  3. plain_json   - ask for a bare JSON object matching the schema
The first mode that works for a model is remembered, so later calls go straight to it.
"""
from __future__ import annotations

import copy
import json
import logging
import re
import time
from typing import Any, Optional

from .config import settings

log = logging.getLogger("last_expert.llm")

MODES = ("json_schema", "tool_auto", "plain_json")


class LLMError(RuntimeError):
    pass


class _TryNextMode(Exception):
    """This structured-output mode didn't work for this model; try the next one."""


def _sanitize_schema(schema: Any) -> Any:
    """Strip keywords structured outputs may reject, and close every object."""
    if isinstance(schema, dict):
        out = {k: _sanitize_schema(v) for k, v in schema.items() if k not in ("minimum", "maximum")}
        if out.get("type") == "object":
            out["additionalProperties"] = False
        return out
    if isinstance(schema, list):
        return [_sanitize_schema(v) for v in schema]
    return schema


def _parse_json(text: str) -> dict:
    text = (text or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*|\s*```$", "", text)
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            raise _TryNextMode("no JSON object in the response")
        try:
            data = json.loads(text[start : end + 1])
        except json.JSONDecodeError as exc:
            raise _TryNextMode(f"invalid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise _TryNextMode("JSON was not an object")
    return data


class AnthropicLLM:
    name = "anthropic"

    def __init__(self, api_key: str):
        import anthropic

        self._anthropic = anthropic
        self.client = anthropic.Anthropic(api_key=api_key, max_retries=3, timeout=90.0)
        self._mode_for_model: dict[str, str] = {}

    @staticmethod
    def _system_blocks(system: str) -> list[dict]:
        # The system prompt is static, so mark it cacheable to cut latency and cost on every turn.
        return [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]

    def _create(self, **kwargs):
        try:
            return self.client.messages.create(**kwargs)
        except self._anthropic.BadRequestError as exc:
            raise _TryNextMode(f"400: {exc.message}") from exc
        except self._anthropic.APIStatusError as exc:
            raise LLMError(f"Claude API error {exc.status_code}: {exc.message}") from exc
        except self._anthropic.APIConnectionError as exc:
            raise LLMError(f"Could not reach the Claude API: {exc}") from exc

    def _call_mode(self, mode: str, system: str, user_text: str, tool: dict, model: str, max_tokens: int) -> dict:
        schema = tool["input_schema"]
        if mode == "json_schema":
            response = self._create(
                model=model,
                max_tokens=max_tokens,
                system=self._system_blocks(system),
                messages=[{"role": "user", "content": user_text}],
                output_config={"format": {"type": "json_schema", "schema": _sanitize_schema(schema)}},
            )
            return _parse_json("".join(getattr(b, "text", "") for b in response.content))

        if mode == "tool_auto":
            response = self._create(
                model=model,
                max_tokens=max_tokens,
                system=self._system_blocks(
                    system + f"\n\nAlways respond by calling the `{tool['name']}` tool exactly once."
                ),
                messages=[{"role": "user", "content": user_text}],
                tools=[tool],
                tool_choice={"type": "auto"},
            )
            for block in response.content:
                if getattr(block, "type", None) == "tool_use" and isinstance(block.input, dict):
                    return block.input
            return _parse_json("".join(getattr(b, "text", "") for b in response.content))

        # plain_json
        response = self._create(
            model=model,
            max_tokens=max_tokens,
            system=self._system_blocks(system),
            messages=[
                {
                    "role": "user",
                    "content": user_text
                    + "\n\nRespond with ONLY a JSON object, no prose and no code fences, matching this JSON "
                    + f"schema:\n{json.dumps(schema)}",
                }
            ],
        )
        return _parse_json("".join(getattr(b, "text", "") for b in response.content))

    def call_tool(
        self,
        system: str,
        user_text: str,
        tool: dict,
        model: Optional[str] = None,
        max_tokens: int = 2500,
    ) -> dict[str, Any]:
        model = model or settings.llm_model
        cached = self._mode_for_model.get(model)
        modes = [cached] + [m for m in MODES if m != cached] if cached else list(MODES)
        started = time.time()
        problems = []
        for mode in modes:
            try:
                data = self._call_mode(mode, system, user_text, tool, model, max_tokens)
            except _TryNextMode as exc:
                problems.append(f"{mode}: {exc}")
                log.warning("%s via %s didn't work on %s (%s); trying next mode", tool["name"], mode, model, exc)
                continue
            if self._mode_for_model.get(model) != mode:
                log.info("Using %s structured output for %s", mode, model)
            self._mode_for_model[model] = mode
            log.info("%s %s via %s in %.1fs", tool["name"], model, mode, time.time() - started)
            return data
        raise LLMError("Claude could not return structured output. " + " | ".join(problems))

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
