"""End-to-end tests using the offline mock brain (no API keys or network needed).

Run:  python -m pytest -q
"""
from __future__ import annotations

import os
import tempfile
import time

os.environ["LLM_PROVIDER"] = "mock"
os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="last-expert-test-")
os.environ["ELEVENLABS_API_KEY"] = ""
os.environ["ANAM_API_KEY"] = ""
os.environ["MONGODB_URI"] = ""

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402

client = TestClient(app)

MESSY_ANSWER = (
    "First I grab the mud balance from the blue cabinet. I just check the cup is clean, then fill it. "
    "I tap it a bit to knock the air out because air makes it read light. If it sounds off when I tap it "
    "I swap to the spare one. Then I read the rider and log it on the BOP sheet."
)


def _new_session() -> dict:
    r = client.post("/api/sessions", json={"expert_name": "Maria", "expert_role": "Mud engineer", "site": "Rig 7"})
    assert r.status_code == 200, r.text
    return r.json()


def _wait(sid: str) -> dict:
    """Wait for the background recording of the last answer to land."""
    for _ in range(200):
        s = client.get(f"/api/sessions/{sid}").json()
        if not s["processing"]:
            return s
        time.sleep(0.05)
    raise AssertionError("background recording never finished")


def _say(sid: str, text: str) -> dict:
    r = client.post(f"/api/sessions/{sid}/message", json={"text": text})
    assert r.status_code == 200, r.text
    return r.json()


def test_health():
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json()["features"]["llm"].startswith("mock")


def test_opening_line_is_first_prompt():
    s = _new_session()
    assert s["phase"] == "opening"
    assert s["transcript"][0]["role"] == "agent"
    assert "what do you want to talk about" in s["transcript"][0]["text"].lower()


def test_full_capture_flow():
    sid = _new_session()["id"]

    # 1. Expert answers the opening question -> plan + first real question
    body = _say(sid, "I want to show you how we check mud weight on the rig.")
    assert body["phase"] == "interview" and body["reply"]
    assert body["session"]["topic"]
    assert len(body["session"]["coverage"]) >= 6
    _wait(sid)

    # 2. An answer full of assumptions -> question comes back fast, aimed at the focus assumption
    body = _say(sid, MESSY_ANSWER)
    q = body["question"]
    assert q["role"] == "agent" and q["target_type"] == "assumption"
    focus = next(a for a in body["session"]["assumptions"] if a["id"] == q["target_ref"])
    assert focus["status"] == "asked"

    # ...and the knowledge base fills in from the background call
    s = _wait(sid)
    assert s["stats"]["knowledge"] >= 3
    kinds = {a["kind"] for a in s["assumptions"]}
    assert {"automatic_action", "site_reference", "vague_term"} & kinds
    assert any(k["not_in_any_manual"] for k in s["knowledge"])
    texts = [a["text"] for a in s["assumptions"]]
    assert len(texts) == len(set(texts)), "focus assumption was recorded twice"

    # 3. Answering the probed assumption resolves it
    _say(sid, "Clean means no dried mud on the lip of the cup, because dried mud adds weight and reads heavy.")
    s = _wait(sid)
    assert next(a for a in s["assumptions"] if a["id"] == focus["id"])["status"] == "resolved"

    # 4. Expert dismisses an assumption
    open_a = next(a for a in s["assumptions"] if a["status"] in ("open", "asked"))
    r = client.post(f"/api/sessions/{sid}/assumptions/{open_a['id']}", json={"status": "dismissed"})
    assert r.status_code == 200
    assert next(a for a in r.json()["assumptions"] if a["id"] == open_a["id"])["status"] == "dismissed"

    # 5. Candidates endpoint explains what it wants to ask next
    r = client.get(f"/api/sessions/{sid}/candidates")
    assert r.status_code == 200 and isinstance(r.json(), list)

    # 6. Expert wants to stop -> closing question -> done
    assert _say(sid, "Okay that's all I have time for, I have to go.")["phase"] == "closing"
    body = _say(sid, "They forget to calibrate the balance with fresh water first, it should read 8.33.")
    assert body["phase"] == "done"
    assert "captured" in body["reply"].lower()
    _wait(sid)


def test_documents_trainee_manual_and_exports():
    sid = _new_session()["id"]
    _say(sid, "How we check mud weight on the rig")
    _wait(sid)

    manual = b"Mud balance procedure. Fill the cup with fresh mud sample. Place the lid on the cup firmly. Read the density at the rider."
    r = client.post(f"/api/sessions/{sid}/documents", files={"file": ("sop.txt", manual, "text/plain")}, data={"kind": "reference"})
    assert r.status_code == 200, r.text
    assert r.json()["session"]["reference_points"]

    notes = b"Honestly I just give the cup a quick wipe. The old one in the cage reads high so I knock a bit off. Our spare balance lives in the back drawer of the lab."
    r = client.post(f"/api/sessions/{sid}/documents", files={"file": ("maria_notes.txt", notes, "text/plain")}, data={"kind": "expert"})
    assert r.status_code == 200, r.text
    s = r.json()["session"]
    assert s["stats"]["knowledge"] >= 2 and s["stats"]["assumptions_caught"] >= 2

    # Trainee mode: covered question
    r = client.post(f"/api/sessions/{sid}/ask", json={"question": "Where is the spare balance kept?"})
    assert r.status_code == 200, r.text
    assert r.json()["covered"] in ("yes", "partial")

    # Trainee mode: uncovered question becomes the top priority for the expert's next session
    r = client.post(f"/api/sessions/{sid}/ask", json={"question": "Who signs off the permit to work?"})
    body = r.json()
    assert body["covered"] == "no" and body["pending_question"]
    qid = body["pending_question"]["id"]
    assert client.get(f"/api/sessions/{sid}/candidates").json()[0]["type"] == "trainee_question"
    reply = _say(sid, "The shift supervisor signs it.")
    assert reply["question"]["target_type"] in ("trainee_question", "coverage", "assumption")
    s = _wait(sid)
    assert next(q for q in s["pending_questions"] if q["id"] == qid)["status"] == "answered"

    # Manual + exports
    r = client.post(f"/api/sessions/{sid}/manual")
    assert r.status_code == 200 and r.json()["markdown"].startswith("#")
    for fmt in ("json", "agent", "manual"):
        r = client.get(f"/api/sessions/{sid}/export", params={"format": fmt})
        assert r.status_code == 200, (fmt, r.text)
    assert "Expert pack" in client.get(f"/api/sessions/{sid}/export", params={"format": "agent"}).text


def test_errors_are_clean():
    assert client.get("/api/sessions/doesnotexist").status_code == 404
    s = _new_session()
    assert client.post(f"/api/sessions/{s['id']}/message", json={"text": "   "}).status_code == 400
    r = client.post(
        f"/api/sessions/{s['id']}/documents",
        files={"file": ("x.exe", b"MZ", "application/octet-stream")},
        data={"kind": "reference"},
    )
    assert r.status_code == 415
    assert client.post("/api/tts", json={"text": "hi"}).status_code == 400
    assert client.post("/api/avatar/session").status_code == 400


def test_failed_turn_is_rolled_back():
    from app.main import engine

    sid = _new_session()["id"]
    _say(sid, "How we check mud weight")
    _wait(sid)
    before = len(client.get(f"/api/sessions/{sid}").json()["transcript"])
    original = engine.llm.call_tool

    def boom(*a, **k):
        from app.llm import LLMError

        raise LLMError("simulated outage")

    engine.llm.call_tool = boom
    try:
        r = client.post(f"/api/sessions/{sid}/message", json={"text": "some answer"})
        assert r.status_code == 502 and "simulated outage" in r.json()["error"]
    finally:
        engine.llm.call_tool = original
    assert len(client.get(f"/api/sessions/{sid}").json()["transcript"]) == before


def test_pdf_and_docx_extraction():
    import io

    import docx
    from pypdf import PdfWriter

    from app.documents import extract_text

    d = docx.Document()
    d.add_paragraph("Check the seal on pump two every Monday.")
    buf = io.BytesIO()
    d.save(buf)
    assert "seal on pump two" in extract_text("sop.docx", buf.getvalue())

    w = PdfWriter()
    w.add_blank_page(width=200, height=200)
    pbuf = io.BytesIO()
    w.write(pbuf)
    assert isinstance(extract_text("blank.pdf", pbuf.getvalue()), str)


def test_real_claude_client_code_path():
    """Runs the engine through the real AnthropicLLM class with a fake transport returning SDK objects."""
    import jsonschema
    from anthropic.types import Message, TextBlock, ToolUseBlock, Usage

    from app.interviewer import Interviewer
    from app.llm import AnthropicLLM
    from app.mock_llm import MockLLM
    from app.store import JsonStore

    mock, calls = MockLLM(), []

    def fake_create(**kw):
        calls.append(kw)
        user = kw["messages"][0]["content"]
        if "tools" in kw:
            tool = kw["tools"][0]
            jsonschema.Draft202012Validator.check_schema(tool["input_schema"])
            data = mock.call_tool("", user, tool)
            jsonschema.validate(data, tool["input_schema"])
            content = [ToolUseBlock(id="toolu_1", type="tool_use", name=tool["name"], input=data)]
            stop = "tool_use"
        else:
            content = [TextBlock(type="text", text=mock.complete_text("", user))]
            stop = "end_turn"
        return Message(id="m", type="message", role="assistant", model=kw["model"], content=content,
                       stop_reason=stop, stop_sequence=None, usage=Usage(input_tokens=1, output_tokens=1))

    llm = AnthropicLLM("sk-test")
    llm.client.messages.create = fake_create
    eng = Interviewer(llm, JsonStore(__import__("pathlib").Path(tempfile.mkdtemp())), background=False)
    s = eng.create_session("Maria")
    eng.handle_expert_message(s.id, "I want to show you how we check mud weight on the rig.")
    r = eng.handle_expert_message(s.id, MESSY_ANSWER)
    assert r["session"]["stats"]["knowledge"] >= 3
    assert eng.ask(s.id, "Where is the blue cabinet balance kept?")["answer"]
    assert eng.generate_manual(s.id).startswith("#")
    first = calls[0]
    assert first["tool_choice"] == {"type": "tool", "name": "plan_session"}
    assert first["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert {c["tools"][0]["name"] for c in calls if "tools" in c} >= {"plan_session", "ask_next", "record_knowledge"}
