// Last Expert front end. Plain JavaScript, no build step.
const $ = (sel) => document.querySelector(sel);

const state = {
  features: {},
  session: null,
  sessions: [],
  tab: "capture",
  busy: false,
  recorder: null,
  recognition: null,
  audio: null,
  anam: null,
  avatarActive: false,
  seenAnam: new Set(),
  anamQueue: Promise.resolve(),
  typingTimer: null,
  pollToken: 0,
};

const KIND_LABELS = {
  unstated_assumption: "Assumption",
  automatic_action: "Automatic action",
  vague_term: "Vague term",
  site_reference: "Local reference",
  judgement_call: "Judgement call",
  missing_step: "Missing step",
  jargon: "Jargon",
};
const CATEGORY_LABELS = {
  procedure_step: "Steps",
  location: "Where things are",
  tool_equipment: "Tools, systems & materials",
  maintenance: "Upkeep & maintenance",
  problem_fix: "Problems & fixes",
  judgement_rule: "Judgement calls",
  safety: "Risks & safety",
  tip: "Tips",
  quality_check: "Quality checks",
  people_process: "People & process",
  context: "Context",
};
const TARGET_LABELS = {
  opening: "Opening",
  assumption: "Probing an assumption",
  coverage: "Filling a gap in the plan",
  follow_up: "Following up",
  clarify: "Clarifying",
  trainee_question: "A trainee's question",
  wrap_up: "Wrapping up",
};
const THINKING = [
  "Listening for assumptions",
  "Checking what's already known",
  "Spotting what you did automatically",
  "Choosing the next question",
];

// ------------------------------------------------------------------ helpers
const local = {
  get(k, d) { try { const v = localStorage.getItem(k); return v === null ? d : v; } catch { return d; } },
  set(k, v) { try { localStorage.setItem(k, v); } catch { /* storage unavailable */ } },
};

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

async function api(path, opts = {}) {
  const init = { ...opts };
  if (opts.json !== undefined) {
    init.method = init.method || "POST";
    init.headers = { "Content-Type": "application/json" };
    init.body = JSON.stringify(opts.json);
    delete init.json;
  }
  const res = await fetch(path, init);
  const type = res.headers.get("content-type") || "";
  const data = type.includes("application/json") ? await res.json() : await res.text();
  if (!res.ok) throw new Error((data && (data.error || data.detail)) || `Request failed (${res.status})`);
  return data;
}

function toast(msg, isError = false) {
  const t = $("#toast");
  t.textContent = msg;
  t.classList.toggle("error", isError);
  t.hidden = false;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => (t.hidden = true), isError ? 6500 : 3500);
}

function flash(ids) {
  for (const id of new Set(ids || [])) {
    document.querySelectorAll(`[data-id="${CSS.escape(id)}"]`).forEach((el) => {
      el.classList.remove("flash");
      void el.offsetWidth;
      el.classList.add("flash");
    });
  }
}

function md(src) {
  const inline = (t) =>
    esc(t)
      .replace(/`([^`]+)`/g, "<code>$1</code>")
      .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
      .replace(/(^|[^*])\*([^*\n]+)\*/g, "$1<em>$2</em>")
      .replace(/(^|\s)_([^_\n]+)_(?=\s|$|[.,;:])/g, "$1<em>$2</em>");
  let html = "";
  let list = null;
  let para = [];
  const flushPara = () => { if (para.length) { html += `<p>${inline(para.join(" "))}</p>`; para = []; } };
  const closeList = () => { if (list) { html += `</${list}>`; list = null; } };
  for (const raw of String(src || "").replace(/\r/g, "").split("\n")) {
    const line = raw.trimEnd();
    let m;
    if (!line.trim()) { flushPara(); closeList(); continue; }
    if ((m = line.match(/^(#{1,4})\s+(.*)$/))) {
      flushPara(); closeList();
      const n = m[1].length;
      html += `<h${n}>${inline(m[2])}</h${n}>`;
      continue;
    }
    if ((m = line.match(/^\s*[-*•]\s+(.*)$/))) {
      flushPara();
      if (list !== "ul") { closeList(); html += "<ul>"; list = "ul"; }
      html += `<li>${inline(m[1])}</li>`;
      continue;
    }
    if ((m = line.match(/^\s*\d+[.)]\s+(.*)$/))) {
      flushPara();
      if (list !== "ol") { closeList(); html += "<ol>"; list = "ol"; }
      html += `<li>${inline(m[1])}</li>`;
      continue;
    }
    if (/^-{3,}$/.test(line.trim())) { flushPara(); closeList(); html += "<hr>"; continue; }
    closeList();
    para.push(line.trim());
  }
  flushPara();
  closeList();
  return html;
}

function lastAgentTurn() {
  const t = state.session?.transcript || [];
  for (let i = t.length - 1; i >= 0; i--) if (t[i].role === "agent") return t[i];
  return null;
}

// ------------------------------------------------------------------ sessions
async function refreshSessions() {
  try {
    state.sessions = await api("/api/sessions");
  } catch (e) {
    return toast(e.message, true);
  }
  const sel = $("#sessionSelect");
  sel.innerHTML = state.sessions
    .map((s) => {
      const when = new Date(s.updated * 1000).toLocaleString([], { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
      const who = s.expert_name ? ` · ${s.expert_name}` : "";
      return `<option value="${esc(s.id)}">${esc(s.topic)}${esc(who)} (${s.knowledge} items, ${when})</option>`;
    })
    .join("");
  if (state.session) sel.value = state.session.id;
}

async function openSession(id) {
  try {
    state.session = await api(`/api/sessions/${id}`);
  } catch (e) {
    return toast(e.message, true);
  }
  local.set("le.session", id);
  $("#sessionSelect").value = id;
  render();
}

async function createSession(profile) {
  try {
    state.session = await api("/api/sessions", { json: profile || {} });
  } catch (e) {
    return toast(e.message, true);
  }
  local.set("le.session", state.session.id);
  await refreshSessions();
  render();
  speak(lastAgentTurn()?.text);
  $("#input").focus();
}

// ------------------------------------------------------------------ rendering
function render() {
  if (!state.session) return;
  renderObjective();
  renderChat();
  renderWhy();
  renderStats();
  renderCoverage();
  renderAssumptions();
  renderKnowledge();
  renderDocuments();
  renderTrainee();
  renderPending();
  renderManual();
  const id = state.session.id;
  $("#dlManual").href = `/api/sessions/${id}/export?format=manual`;
  $("#dlAgent").href = `/api/sessions/${id}/export?format=agent`;
  $("#dlJson").href = `/api/sessions/${id}/export?format=json`;
}

function renderObjective() {
  const s = state.session;
  const who = [s.expert_name, s.expert_role, s.site].filter(Boolean).join(" · ");
  $("#topicText").textContent = (s.topic || "New session") + (who ? `  —  ${who}` : "");
  $("#objectiveText").textContent = s.objective
    ? `Objective: ${s.objective}`
    : "The apprentice will work out the objective from your first answer.";
}

function renderChat() {
  const chat = $("#chat");
  const turns = state.session.transcript || [];
  chat.innerHTML = turns
    .map((t) => {
      if (t.role === "system") return `<div class="msg system">${esc(t.text)}</div>`;
      if (t.role === "expert") return `<div class="msg expert">${esc(t.text)}</div>`;
      const label = TARGET_LABELS[t.target_type] || "";
      const ref = t.target_ref ? ` · <span class="ref">${esc(t.target_ref)}</span>` : "";
      const tag = label && t.target_type !== "opening" ? `<span class="msg-tag">${esc(label)}${ref}</span>` : "";
      return `<div class="msg agent">${tag}${esc(t.text)}</div>`;
    })
    .join("");
  if (state.busy && state.tab === "capture") {
    chat.insertAdjacentHTML("beforeend", `<div class="typing" id="typing">${THINKING[0]}</div>`);
    startThinking();
  }
  chat.scrollTop = chat.scrollHeight;
}

function startThinking() {
  clearInterval(state.typingTimer);
  let i = 0;
  state.typingTimer = setInterval(() => {
    const el = $("#typing");
    if (!el || !state.busy) return clearInterval(state.typingTimer);
    i = (i + 1) % THINKING.length;
    el.textContent = THINKING[i];
  }, 1400);
}

function renderWhy() {
  const t = lastAgentTurn();
  const box = $("#whyBox");
  if (!t || !t.why || t.target_type === "opening") { box.hidden = true; return; }
  let detail = "";
  if (t.target_type === "assumption" && t.target_ref) {
    const a = state.session.assumptions.find((x) => x.id === t.target_ref);
    if (a) detail = ` — ${KIND_LABELS[a.kind] || a.kind}: ${a.text}`;
  } else if (t.target_type === "coverage" && t.target_ref) {
    const d = state.session.coverage.find((x) => x.id === t.target_ref);
    if (d) detail = ` — ${d.name}`;
  }
  $("#whyText").textContent = `${t.why}${detail}`;
  box.hidden = false;
}

function renderStats() {
  const st = state.session.stats || {};
  $("#statKnowledge").textContent = st.knowledge ?? 0;
  $("#statNotInManual").textContent = st.not_in_manual ?? 0;
  $("#statAssumptions").textContent = st.assumptions_caught ?? 0;
  $("#statCoverage").textContent = `${Math.round((st.coverage || 0) * 100)}%`;
}

function renderCoverage() {
  const dims = state.session.coverage || [];
  if (!dims.length) { $("#coverage").innerHTML = `<p class="empty">Appears after the first answer.</p>`; return; }
  $("#coverage").innerHTML = dims
    .map((d) => {
      const pct = Math.round((d.score || 0) * 100);
      return `<div class="cov-row" data-id="${esc(d.id)}" title="${esc(d.description)}${d.note ? " — " + esc(d.note) : ""}">
        <div class="cov-name">${esc(d.name)}<div class="cov-bar"><div class="cov-fill" style="width:${pct}%"></div></div></div>
        <div class="cov-pct">${pct}%</div></div>`;
    })
    .join("");
}

function renderAssumptions() {
  const all = state.session.assumptions || [];
  const order = { open: 0, asked: 1, resolved: 2, dismissed: 3 };
  const sorted = [...all].sort((a, b) => order[a.status] - order[b.status] || b.importance - a.importance);
  const open = all.filter((a) => a.status === "open" || a.status === "asked").length;
  const resolved = all.filter((a) => a.status === "resolved").length;
  $("#assumptionCounts").textContent = all.length ? `${open} open · ${resolved} resolved` : "";
  if (!all.length) {
    $("#assumptions").innerHTML = `<p class="empty">Things the expert does automatically or takes for granted show up here.</p>`;
    return;
  }
  $("#assumptions").innerHTML = sorted
    .map((a) => {
      const dots = "●".repeat(a.importance) + "○".repeat(5 - a.importance);
      const statusText = { open: "open", asked: "asked", resolved: "resolved", dismissed: "dismissed" }[a.status];
      const action =
        a.status === "dismissed"
          ? `<button class="icon-btn" data-action="restore" data-aid="${esc(a.id)}">restore</button>`
          : a.status === "resolved"
          ? ""
          : `<button class="icon-btn" data-action="dismiss" data-aid="${esc(a.id)}" title="Not relevant">dismiss</button>`;
      return `<div class="item ${a.status}" data-id="${esc(a.id)}">
        <div class="item-top"><span class="kind ${esc(a.kind)}">${esc(KIND_LABELS[a.kind] || a.kind)}</span>
          <span class="importance" title="importance">${dots}</span>
          <span class="status">${esc(a.id)} · ${statusText}</span>${action}</div>
        <div class="item-body">${esc(a.text)}</div>
        ${a.evidence_quote ? `<div class="item-meta">“${esc(a.evidence_quote)}”</div>` : ""}
        ${a.status === "resolved" && a.resolution ? `<div class="item-meta">✓ ${esc(a.resolution)}</div>` : ""}
      </div>`;
    })
    .join("");
}

function renderKnowledge() {
  const items = state.session.knowledge || [];
  if (!items.length) { $("#knowledge").innerHTML = `<p class="empty">Nothing captured yet.</p>`; return; }
  let html = "";
  for (const [cat, label] of Object.entries(CATEGORY_LABELS)) {
    const group = items.filter((k) => k.category === cat);
    if (!group.length) continue;
    html += `<div class="cat-head">${esc(label)} (${group.length})</div>`;
    html += group
      .map(
        (k) => `<div class="item ${k.not_in_any_manual ? "star" : ""}" data-id="${esc(k.id)}">
        <div class="item-top"><span class="item-title">${k.not_in_any_manual ? "★ " : ""}${esc(k.title)}</span>
          <span class="status">${esc(k.id)} · ${esc(k.specificity)}</span>
          <button class="icon-btn" data-action="delete-k" data-kid="${esc(k.id)}" title="Remove">✕</button></div>
        <div class="item-body">${esc(k.content)}</div>
        ${k.why ? `<div class="item-meta">Why: ${esc(k.why)}</div>` : ""}
        ${k.conditions ? `<div class="item-meta">When: ${esc(k.conditions)}</div>` : ""}
      </div>`
      )
      .join("");
  }
  $("#knowledge").innerHTML = html;
}

function renderDocuments() {
  const docs = state.session.documents || [];
  $("#docsCard").hidden = !docs.length;
  $("#documents").innerHTML = docs
    .map(
      (d) => `<div class="item"><div class="item-top"><span class="item-title">${esc(d.filename)}</span>
      <span class="status">${d.kind === "reference" ? "reference manual" : "expert's own words"}</span></div>
      <div class="item-meta">${esc(d.summary)}</div></div>`
    )
    .join("");
}

function renderTrainee() {
  const log = state.session.trainee_log || [];
  const kb = Object.fromEntries((state.session.knowledge || []).map((k) => [k.id, k]));
  const box = $("#traineeChat");
  if (!log.length) {
    box.innerHTML = `<div class="msg system">Ask anything a new starter would ask. The apprentice answers only from what the expert taught it.</div>`;
    return;
  }
  box.innerHTML = log
    .map((x) => {
      const q = `<div class="msg expert">${esc(x.question)}</div>`;
      if (x.answer === null || x.answer === undefined) return q + `<div class="typing">Checking what the expert taught me</div>`;
      const label = {
        yes: `<span class="covered-yes">From the expert's knowledge</span>`,
        partial: `<span class="covered-partial">Partly covered · question sent to the expert</span>`,
        no: `<span class="covered-no">Not covered yet · question sent to the expert</span>`,
      }[x.covered] || "";
      const cites = (x.cited_ids || [])
        .map((id) => `<span class="chip" title="${esc(kb[id]?.content || "")}">${esc(id)} ${esc(kb[id]?.title || "")}</span>`)
        .join("");
      return q + `<div class="msg agent"><span class="msg-tag">${label}</span>${esc(x.answer)}${cites ? `<div class="cites">${cites}</div>` : ""}</div>`;
    })
    .join("");
  box.scrollTop = box.scrollHeight;
}

function renderPending() {
  const pend = state.session.pending_questions || [];
  if (!pend.length) { $("#pending").innerHTML = `<p class="empty">None yet. Unanswerable trainee questions appear here and the apprentice asks the expert next time.</p>`; return; }
  $("#pending").innerHTML = pend
    .map(
      (q) => `<div class="item ${q.status === "answered" ? "resolved" : ""}" data-id="${esc(q.id)}">
      <div class="item-top"><span class="item-title">${esc(q.text)}</span><span class="status">${esc(q.id)} · ${q.status}</span></div></div>`
    )
    .join("");
}

function renderManual() {
  const m = state.session.manual_markdown;
  $("#manual").innerHTML = m ? md(m) : `<p class="empty">Generate a manual once the apprentice has learned something.</p>`;
}

function renderBadges() {
  const f = state.features;
  const badges = [
    [`Brain: ${f.llm}`, !String(f.llm).startsWith("mock")],
    [f.elevenlabs ? "Voice: ElevenLabs" : "Voice: browser", f.elevenlabs],
    [f.avatar ? "Avatar: Anam" : "Avatar: off", f.avatar],
    [`Store: ${f.storage}`, f.storage === "mongodb"],
  ];
  $("#badges").innerHTML = badges.map(([t, on]) => `<span class="badge ${on ? "on" : ""}">${esc(t)}</span>`).join("");
}

// ------------------------------------------------------------------ background recording
// The next question arrives in seconds; the full record of the answer lands a moment later.
function snapshot(s) {
  if (!s) return { k: new Map(), a: new Map(), d: new Map(), q: new Map() };
  return {
    k: new Map((s.knowledge || []).map((x) => [x.id, x.content])),
    a: new Map((s.assumptions || []).map((x) => [x.id, x.status])),
    d: new Map((s.coverage || []).map((x) => [x.id, x.score])),
    q: new Map((s.pending_questions || []).map((x) => [x.id, x.status])),
  };
}

function changedIds(before, s) {
  const ids = [];
  for (const x of s.knowledge || []) if (before.k.get(x.id) !== x.content) ids.push(x.id);
  for (const x of s.assumptions || []) if (before.a.get(x.id) !== x.status) ids.push(x.id);
  for (const x of s.coverage || []) if (before.d.get(x.id) !== x.score) ids.push(x.id);
  for (const x of s.pending_questions || []) if (before.q.get(x.id) !== x.status) ids.push(x.id);
  return ids;
}

function setRecording(on) {
  $("#recordingBadge").hidden = !on;
}

async function pollUntilRecorded(before) {
  const token = ++state.pollToken;
  const id = state.session.id;
  setRecording(true);
  try {
    for (let i = 0; i < 90; i++) {
      await new Promise((r) => setTimeout(r, 900));
      if (token !== state.pollToken || !state.session || state.session.id !== id) return;
      let s;
      try { s = await api(`/api/sessions/${id}`); } catch { continue; }
      if (s.processing) continue;
      if (state.busy || token !== state.pollToken) return; // a newer turn will render its own state
      state.session = s;
      render();
      flash(changedIds(before, s));
      if (s.last_error) toast(s.last_error, true);
      return;
    }
  } finally {
    if (token === state.pollToken) setRecording(false);
  }
}

// ------------------------------------------------------------------ actions
async function sendExpert(text) {
  text = (text || "").trim();
  if (!text || state.busy || !state.session) return;
  const s = state.session;
  state.busy = true;
  stopSpeaking();
  s.transcript.push({ role: "expert", text });
  $("#input").value = "";
  renderChat();
  try {
    const before = snapshot(state.session);
    const res = await api(`/api/sessions/${s.id}/message`, { json: { text } });
    state.session = res.session;
    state.busy = false;
    render();
    flash(changedIds(before, state.session));
    speak(res.reply);
    refreshSessions();
    if (res.session.processing) pollUntilRecorded(before);
  } catch (e) {
    s.transcript.pop();
    state.busy = false;
    renderChat();
    $("#input").value = text;
    toast(e.message, true);
  }
}

async function askTrainee(question) {
  question = (question || "").trim();
  if (!question || state.busy || !state.session) return;
  state.busy = true;
  stopSpeaking();
  const log = state.session.trainee_log;
  log.push({ question, answer: null });
  $("#traineeInput").value = "";
  renderTrainee();
  try {
    const res = await api(`/api/sessions/${state.session.id}/ask`, { json: { question } });
    state.session = res.session;
    state.busy = false;
    render();
    if (res.pending_question) flash([res.pending_question.id]);
    speak(res.answer);
  } catch (e) {
    log.pop();
    state.busy = false;
    renderTrainee();
    $("#traineeInput").value = question;
    toast(e.message, true);
  }
}

async function uploadFile(file) {
  if (!file || !state.session) return;
  const kind = $("#docKind").value;
  const form = new FormData();
  form.append("file", file);
  form.append("kind", kind);
  $("#uploadBtn").disabled = true;
  $("#uploadBtn").textContent = "Reading…";
  try {
    const res = await api(`/api/sessions/${state.session.id}/documents`, { method: "POST", body: form });
    state.session = res.session;
    render();
    flash([...(res.delta?.knowledge || []), ...(res.delta?.assumptions || [])]);
    toast(`${file.name}: ${res.document.summary || "added"}`);
  } catch (e) {
    toast(e.message, true);
  } finally {
    $("#uploadBtn").disabled = false;
    $("#uploadBtn").textContent = "Upload document";
    $("#fileInput").value = "";
  }
}

async function setAssumption(aid, status) {
  try {
    state.session = await api(`/api/sessions/${state.session.id}/assumptions/${aid}`, { json: { status } });
    render();
  } catch (e) {
    toast(e.message, true);
  }
}

async function deleteKnowledge(kid) {
  try {
    state.session = await api(`/api/sessions/${state.session.id}/knowledge/${kid}`, { method: "DELETE" });
    render();
  } catch (e) {
    toast(e.message, true);
  }
}

async function generateManual() {
  const btn = $("#manualBtn");
  btn.disabled = true;
  btn.textContent = "Writing the manual…";
  try {
    const { markdown } = await api(`/api/sessions/${state.session.id}/manual`, { method: "POST" });
    state.session.manual_markdown = markdown;
    renderManual();
  } catch (e) {
    toast(e.message, true);
  } finally {
    btn.disabled = false;
    btn.textContent = "Generate training manual";
  }
}

// ------------------------------------------------------------------ voice out
async function speak(text) {
  if (!text) return;
  if (state.avatarActive && state.anam) {
    try { await state.anam.talk(text); } catch (e) { console.warn("Avatar talk failed", e); }
    return;
  }
  if (!$("#speakToggle").checked) return;
  stopSpeaking();
  if (state.features.elevenlabs) {
    try {
      const res = await fetch("/api/tts", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ text }) });
      if (!res.ok) throw new Error(`TTS ${res.status}`);
      const url = URL.createObjectURL(await res.blob());
      const audio = new Audio(url);
      state.audio = audio;
      audio.onended = () => URL.revokeObjectURL(url);
      await audio.play();
      return;
    } catch (e) {
      console.warn("ElevenLabs voice failed, using browser voice", e);
    }
  }
  if ("speechSynthesis" in window) {
    const u = new SpeechSynthesisUtterance(text);
    u.lang = "en-GB";
    u.rate = 1.03;
    window.speechSynthesis.speak(u);
  }
}

function stopSpeaking() {
  if (state.audio) { state.audio.pause(); state.audio = null; }
  if ("speechSynthesis" in window) window.speechSynthesis.cancel();
}

// ------------------------------------------------------------------ voice in
function micButton(target) { return target === "capture" ? $("#micBtn") : $("#traineeMicBtn"); }
function inputBox(target) { return target === "capture" ? $("#input") : $("#traineeInput"); }

function setMicUI(target, on) {
  micButton(target).classList.toggle("recording", on);
  inputBox(target).placeholder = on ? "Listening… click the mic again when you're done." : target === "capture"
    ? "Answer here, or press the mic and talk…"
    : "e.g. What should I check first if it doesn't work?";
}

function deliverSpeech(target, text) {
  text = (text || "").trim();
  if (!text) return toast("Didn't catch that. Try again.");
  if (target === "capture") sendExpert(text);
  else askTrainee(text);
}

async function toggleMic(target) {
  if (state.avatarActive) return toast("The avatar is listening: just talk.");
  if (state.recorder || state.recognition) return stopMic();
  stopSpeaking();

  if (state.features.elevenlabs && navigator.mediaDevices?.getUserMedia && window.MediaRecorder) {
    let stream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch (e) {
      return toast("Microphone unavailable: " + e.message, true);
    }
    const mime = ["audio/webm;codecs=opus", "audio/webm", "audio/mp4"].find((m) => MediaRecorder.isTypeSupported(m)) || "";
    const rec = new MediaRecorder(stream, mime ? { mimeType: mime } : undefined);
    const chunks = [];
    rec.ondataavailable = (e) => e.data.size && chunks.push(e.data);
    rec.onstop = async () => {
      stream.getTracks().forEach((t) => t.stop());
      setMicUI(target, false);
      const blob = new Blob(chunks, { type: rec.mimeType || "audio/webm" });
      if (blob.size < 1500) return toast("Didn't catch that. Try again.");
      const ext = (rec.mimeType || "").includes("mp4") ? "m4a" : "webm";
      const form = new FormData();
      form.append("file", blob, `speech.${ext}`);
      inputBox(target).placeholder = "Transcribing…";
      try {
        const { text } = await api("/api/transcribe", { method: "POST", body: form });
        deliverSpeech(target, text);
      } catch (e) {
        toast(e.message, true);
      } finally {
        setMicUI(target, false);
      }
    };
    rec.start();
    state.recorder = rec;
    setMicUI(target, true);
    return;
  }

  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!SR) return toast("Voice input needs Chrome or an ElevenLabs key. You can type instead.", true);
  const rec = new SR();
  rec.lang = "en-GB";
  rec.interimResults = true;
  rec.continuous = true;
  let finalText = "";
  const box = inputBox(target);
  rec.onresult = (ev) => {
    let interim = "";
    for (let i = ev.resultIndex; i < ev.results.length; i++) {
      const r = ev.results[i];
      if (r.isFinal) finalText += r[0].transcript + " ";
      else interim += r[0].transcript;
    }
    box.value = (finalText + interim).trim();
  };
  rec.onerror = (ev) => {
    if (ev.error !== "no-speech" && ev.error !== "aborted") toast("Speech recognition error: " + ev.error, true);
  };
  rec.onend = () => {
    state.recognition = null;
    setMicUI(target, false);
    deliverSpeech(target, box.value);
  };
  rec.start();
  state.recognition = rec;
  setMicUI(target, true);
}

function stopMic() {
  if (state.recorder) { state.recorder.stop(); state.recorder = null; }
  if (state.recognition) state.recognition.stop();
}

// ------------------------------------------------------------------ avatar (Anam)
async function toggleAvatar() {
  const btn = $("#avatarBtn");
  if (state.avatarActive) {
    try { await state.anam.stopStreaming(); } catch { /* already closed */ }
    state.avatarActive = false;
    state.anam = null;
    btn.textContent = "Start face-to-face";
    $("#avatarStatus").textContent = "Avatar stopped. Voice and text still work.";
    return;
  }
  btn.disabled = true;
  $("#avatarStatus").textContent = "Connecting…";
  try {
    const { sessionToken } = await api("/api/avatar/session", { method: "POST" });
    const sdk = await import("https://esm.sh/@anam-ai/js-sdk@latest");
    let AnamEvent = sdk.AnamEvent;
    if (!AnamEvent) {
      try { AnamEvent = (await import("https://esm.sh/@anam-ai/js-sdk@latest/dist/module/types")).AnamEvent; } catch { /* use string names */ }
    }
    const anam = sdk.createClient(sessionToken);
    anam.addListener(AnamEvent?.MESSAGE_HISTORY_UPDATED ?? "MESSAGE_HISTORY_UPDATED", onAnamHistory);
    if (AnamEvent?.CONNECTION_CLOSED) {
      anam.addListener(AnamEvent.CONNECTION_CLOSED, () => {
        state.avatarActive = false;
        btn.textContent = "Start face-to-face";
        $("#avatarStatus").textContent = "Connection closed.";
      });
    }
    await anam.streamToVideoElement("avatarVideo");
    state.anam = anam;
    state.avatarActive = true;
    btn.textContent = "Stop";
    $("#avatarStatus").textContent = "Live: just talk. The apprentice is listening.";
    stopSpeaking();
    const last = lastAgentTurn();
    if (last) anam.talk(last.text);
  } catch (e) {
    toast("Avatar failed: " + e.message, true);
    $("#avatarStatus").textContent = "Avatar unavailable. Voice and text still work.";
  } finally {
    btn.disabled = false;
  }
}

function onAnamHistory(messages) {
  if (!Array.isArray(messages) || !messages.length) return;
  const last = messages[messages.length - 1];
  if (String(last.role || "").toLowerCase() !== "user") return;
  const key = last.id || `${messages.length}:${last.content}`;
  if (state.seenAnam.has(key)) return;
  state.seenAnam.add(key);
  const text = (last.content || "").trim();
  if (!text) return;
  // Queue utterances so they're processed in order.
  state.anamQueue = state.anamQueue.then(() => (state.tab === "trainee" ? askTrainee(text) : sendExpert(text)));
}

// ------------------------------------------------------------------ wiring
function bindUI() {
  document.querySelectorAll(".tab-btn").forEach((b) =>
    b.addEventListener("click", () => {
      state.tab = b.dataset.tab;
      document.querySelectorAll(".tab-btn").forEach((x) => x.classList.toggle("active", x === b));
      document.querySelectorAll(".tab").forEach((t) => t.classList.toggle("active", t.id === `tab-${state.tab}`));
    })
  );

  $("#sendBtn").addEventListener("click", () => sendExpert($("#input").value));
  $("#input").addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); sendExpert($("#input").value); }
  });
  $("#askBtn").addEventListener("click", () => askTrainee($("#traineeInput").value));
  $("#traineeInput").addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); askTrainee($("#traineeInput").value); }
  });
  $("#micBtn").addEventListener("click", () => toggleMic("capture"));
  $("#traineeMicBtn").addEventListener("click", () => toggleMic("trainee"));

  $("#uploadBtn").addEventListener("click", () => $("#fileInput").click());
  $("#fileInput").addEventListener("change", (e) => uploadFile(e.target.files[0]));
  $("#speakToggle").addEventListener("change", (e) => {
    local.set("le.speak", e.target.checked ? "1" : "0");
    if (!e.target.checked) stopSpeaking();
  });

  $("#sessionSelect").addEventListener("change", (e) => openSession(e.target.value));
  $("#newSessionBtn").addEventListener("click", () => {
    $("#newSessionForm").reset();
    $("#newSessionDialog").showModal();
  });
  $("#newSessionDialog").addEventListener("close", () => {
    if ($("#newSessionDialog").returnValue !== "ok") return;
    const form = new FormData($("#newSessionForm"));
    createSession(Object.fromEntries(form.entries()));
  });

  $("#avatarBtn").addEventListener("click", toggleAvatar);
  $("#manualBtn").addEventListener("click", generateManual);

  $("#assumptions").addEventListener("click", (e) => {
    const btn = e.target.closest("[data-action]");
    if (!btn) return;
    setAssumption(btn.dataset.aid, btn.dataset.action === "dismiss" ? "dismissed" : "open");
  });
  $("#knowledge").addEventListener("click", (e) => {
    const btn = e.target.closest("[data-action='delete-k']");
    if (btn && confirm("Remove this item from the knowledge base?")) deleteKnowledge(btn.dataset.kid);
  });
}

async function init() {
  bindUI();
  $("#speakToggle").checked = local.get("le.speak", "1") === "1";
  try {
    const health = await api("/api/health");
    state.features = health.features;
    renderBadges();
    $("#avatarCard").hidden = !state.features.avatar;
  } catch (e) {
    return toast("Can't reach the server: " + e.message, true);
  }
  await refreshSessions();
  const last = local.get("le.session", "");
  if (last && state.sessions.some((s) => s.id === last)) await openSession(last);
  else if (state.sessions.length) await openSession(state.sessions[0].id);
  else await createSession({});
}

init();
