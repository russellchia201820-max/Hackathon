"""Playbook generator + ask-the-expert. Built ONLY from captured items."""
from __future__ import annotations

from pathlib import Path

from . import llm, store
from .models import Session

P = Path(__file__).parent / "prompts"
PLAYBOOK_PROMPT = (P / "playbook.md").read_text()
ASK_PROMPT = (P / "ask.md").read_text()

ASK_SCHEMA = {
    "type": "object",
    "properties": {"answer": {"type": "string"}, "item_ids": {"type": "array", "items": {"type": "string"}},
                   "covered": {"type": "boolean"}},
    "required": ["answer", "item_ids", "covered"], "additionalProperties": False,
}


def items_text(s: Session) -> str:
    return "\n".join(f"{i.id} [{i.slot}] {i.content} (expert said: \"{i.source_quote}\")" for i in s.items)


async def generate_playbook(s: Session) -> str:
    gaps = "\n".join(f"- {g.question}" for g in s.gaps if g.status in ("open", "asked", "dropped")) or "(none)"
    user = (f"Task: {s.objective}\nExpert: {s.expert_name}\n\n# Knowledge items\n{items_text(s) or '(none)'}\n\n"
            f"# Not yet covered (open or dropped questions)\n{gaps}")
    md = await llm.complete("analyst", PLAYBOOK_PROMPT, [{"role": "user", "content": user}],
                            max_tokens=6000, effort="low")
    (store.session_dir(s.id) / "playbook.md").write_text(md)
    return md


async def ask(s: Session, question: str) -> dict:
    if not s.items:
        return {"answer": "The expert hasn't taught me anything yet, so I can't answer that.",
                "item_ids": [], "covered": False}
    user = f"Task: {s.objective}\n\n# Knowledge items\n{items_text(s)}\n\n# Beginner's question\n{question}"
    out = await llm.complete("analyst", ASK_PROMPT, [{"role": "user", "content": user}],
                             max_tokens=800, schema=ASK_SCHEMA, effort="low")
    valid = {i.id for i in s.items}
    out["item_ids"] = [i for i in out.get("item_ids", []) if i in valid]
    return out
