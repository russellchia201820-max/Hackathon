"""Provider-agnostic LLM wrapper. Two roles: 'fast' and 'analyst'.

Env: FAST_PROVIDER/FAST_MODEL, ANALYST_PROVIDER/ANALYST_MODEL
     (provider = anthropic | openai_compatible; the latter uses
      OPENAI_COMPAT_BASE_URL / OPENAI_COMPAT_API_KEY).
"""
from __future__ import annotations

import json
import logging
import os
import time
from typing import Any, AsyncIterator

log = logging.getLogger("apprentice.llm")

DEFAULTS = {
    "fast": ("anthropic", "claude-haiku-5-5"),
    "analyst": ("anthropic", "claude-sonnet-5-5"),
}
TIMEOUTS = {"fast": 20.0, "analyst": 90.0}


class LLMError(Exception):
    pass


def config(role: str) -> tuple[str, str]:
    p, m = DEFAULTS[role]
    return (os.getenv(f"{role.upper()}_PROVIDER") or p, os.getenv(f"{role.upper()}_MODEL") or m)


_clients: dict[str, Any] = {}


def _anthropic():
    if "anthropic" not in _clients:
        import anthropic
        _clients["anthropic"] = anthropic.AsyncAnthropic(max_retries=2)
    return _clients["anthropic"]


def _openai():
    if "openai" not in _clients:
        import openai
        _clients["openai"] = openai.AsyncOpenAI(
            base_url=os.getenv("OPENAI_COMPAT_BASE_URL"),
            api_key=os.getenv("OPENAI_COMPAT_API_KEY"), max_retries=2)
    return _clients["openai"]


def _anthropic_kwargs(role: str, model: str, effort: str | None) -> dict:
    kw: dict = {}
    eff = effort or os.getenv(f"{role.upper()}_EFFORT") or ("low" if role == "fast" else "medium")
    out_cfg = {"effort": eff}
    if role == "fast" and "haiku" in model and eff in ("low", "medium", "high"):
        kw["thinking"] = {"type": "disabled"}  # lowest latency for spoken replies
    kw["output_config"] = out_cfg
    return kw


async def complete(role: str, system: str, messages: list[dict], max_tokens: int = 1024,
                   schema: dict | None = None, effort: str | None = None) -> Any:
    """Return text, or a parsed dict when `schema` (JSON schema) is given."""
    provider, model = config(role)
    t0 = time.time()
    try:
        if provider == "anthropic":
            client = _anthropic().with_options(timeout=TIMEOUTS[role])
            kw = _anthropic_kwargs(role, model, effort)
            if schema:
                kw["output_config"]["format"] = {"type": "json_schema", "schema": schema}
            resp = await client.messages.create(model=model, max_tokens=max_tokens,
                                                system=system, messages=messages, **kw)
            if resp.stop_reason == "refusal":
                raise LLMError("model refused")
            text = "".join(b.text for b in resp.content if b.type == "text")
        else:
            client = _openai().with_options(timeout=TIMEOUTS[role])
            sys = system
            kw = {}
            if schema:
                sys += "\n\nRespond ONLY with a JSON object matching this schema:\n" + json.dumps(schema)
                kw["response_format"] = {"type": "json_object"}
            resp = await client.chat.completions.create(
                model=model, max_tokens=max_tokens,
                messages=[{"role": "system", "content": sys}] + messages, **kw)
            text = resp.choices[0].message.content or ""
    except LLMError:
        raise
    except Exception as e:  # network, auth, bad model name...
        raise LLMError(f"{role} LLM ({provider}:{model}) failed: {type(e).__name__}: {e}") from e
    log.info("llm %s %s %.2fs", role, model, time.time() - t0)
    if schema:
        try:
            return json.loads(text[text.find("{"): text.rfind("}") + 1])
        except Exception as e:
            raise LLMError(f"bad JSON from {model}: {text[:200]}") from e
    return text.strip()


async def stream(role: str, system: str, messages: list[dict], max_tokens: int = 200) -> AsyncIterator[str]:
    provider, model = config(role)
    if provider == "anthropic":
        kw = _anthropic_kwargs(role, model, None)
        async with _anthropic().with_options(timeout=TIMEOUTS[role]).messages.stream(
                model=model, max_tokens=max_tokens, system=system, messages=messages, **kw) as s:
            async for chunk in s.text_stream:
                yield chunk
    else:
        resp = await _openai().chat.completions.create(
            model=model, max_tokens=max_tokens, stream=True,
            messages=[{"role": "system", "content": system}] + messages)
        async for ev in resp:
            if ev.choices and ev.choices[0].delta.content:
                yield ev.choices[0].delta.content


def to_chat(transcript, opener: str = "(The expert has joined the call.)") -> list[dict]:
    """Map transcript turns to user/assistant messages (expert=user)."""
    msgs: list[dict] = []
    for t in transcript:
        if t.speaker == "agent":
            role, text = "assistant", t.text
        elif t.speaker == "observation":
            role, text = "user", f"[Camera observation: {t.text}]"
        else:
            role, text = "user", t.text
        if msgs and msgs[-1]["role"] == role:
            msgs[-1]["content"] += "\n" + text
        else:
            msgs.append({"role": role, "content": text})
    if not msgs or msgs[0]["role"] != "user":
        msgs.insert(0, {"role": "user", "content": opener})
    if msgs[-1]["role"] == "assistant":
        msgs.append({"role": "user", "content": "(silence — continue)"})
    return msgs
