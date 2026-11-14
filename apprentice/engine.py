"""Session orchestration shared by CLI, web text mode and voice agent."""
from __future__ import annotations

from . import interviewer, selector, store
from .analyst import AnalystRunner
from .models import Session

_runners: dict[str, AnalystRunner] = {}


def new_session(objective: str, expert_name: str = "Expert", mode: str = "text") -> Session:
    s = Session(objective=objective.strip(), expert_name=(expert_name or "Expert").strip(), mode=mode)
    s.add_turn("agent", interviewer.opening_line(s))
    store.save(s)
    return s


def runner(s: Session, on_log=None) -> AnalystRunner:
    r = _runners.get(s.id)
    if r is None or r.s is not s:
        r = AnalystRunner(s, store.save, on_log)
        _runners[s.id] = r
    return r


def add_expert_turn(s: Session, text: str) -> None:
    s.add_turn("expert", text)
    selector.on_expert_turn(s)
    store.save(s)


def add_agent_turn(s: Session, text: str) -> None:
    s.add_turn("agent", text)
    store.save(s)


async def text_turn(s: Session, text: str, on_log=None) -> str:
    """Text mode: expert says something -> agent replies -> analyst runs in background."""
    add_expert_turn(s, text)
    try:
        reply = await interviewer.reply(s)
    except Exception as e:
        s.log(f"interviewer error: {e}", "error")
        reply = "Sorry, I lost my train of thought for a second. Could you say a bit more about that?"
    add_agent_turn(s, reply)
    runner(s, on_log).trigger()
    return reply
