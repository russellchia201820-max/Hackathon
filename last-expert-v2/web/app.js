/* Last Expert front-end: polling knowledge panel + LiveKit voice/avatar call. Vanilla JS, no build. */
"use strict";

const SLOTS = {
  goal: "Goal and what good looks like",
  steps: "Steps, end to end",
  decisions: "Decision rules",
  cues: "Cues to look for",
  mistakes: "Common mistakes",
  edge_cases: "Edge cases",
  tools_terms: "Tools and terms",
  quality_check: "How to check your work",
};
const KIND_LABEL = {
  vague_term: "Vague term", undefined_jargon: "Jargon", skipped_step: "Skipped step",
  missing_why: "Missing why", missing_failure_case: "No failure case", missing_cue: "Missing cue",
  contradiction: "Contradiction", missing_slot: "Not covered yet",
};
const PHASE_LABEL = { opening: "Opening", deep_dive: "Deep dive", wrap_up: "Wrapping up", done: "Done" };
const COV_PCT = { empty: 0, partial: 50, good: 100 };
const MAX_OPEN_GAPS = 8;

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

let sessionId = null;
let state = null;
let version = -1;
let seenItems = new Set();
let seenTools = new Set();
let gapStatus = new Map();
let firstRender = true;
let config = {};
let lastErrorTs = 0;
let lastPlaybook = "";
let renderedTurns = -1;

/* ---------------- toasts + api ---------------- */
function toast(msg, kind = "error") {
  const el = document.createElement("div");
  el.className = "toast " + (kind === "error" ? "error" : "");
  el.textContent = msg;
  $("toasts").appendChild(el);
  setTimeout(() => el.remove(), kind === "error" ? 7000 : 3500);
}

async function api(path, opts = {}) {
  const res = await fetch(path, { headers: { "Content-Type": "application/json" }, ...opts, body: opts.body ? JSON.stringify(opts.body) : undefined });
  let data = null;
  try { data = await res.json(); } catch { /* non-json */ }
  if (!res.ok || (data && data.error)) throw new Error((data && (data.error || data.detail)) || `HTTP ${res.status}`);
  return data;
}

/* ---------------- session lifecycle ---------------- */
function setSession(id) {
  sessionId = id;
  version = -1;
  seenItems = new Set();
  seenTools = new Set();
  gapStatus = new Map();
  firstRender = true;
  renderedTurns = -1;
  $("chat").innerHTML = "";
  $("playbook").innerHTML = '<p class="empty">Generate a beginner-ready training manual from everything captured so far. It ends with an honest "Not yet covered" list.</p>';
  $("btnDownload").disabled = true;
  try { localStorage.setItem("lastexpert.session", id); } catch { }
  const u = new URL(location.href);
  u.searchParams.set("s", id);
  history.replaceState(null, "", u);
  poll(true);
}

async function newSession(objective, name) {
  try {
    const s = await api("/api/session", { method: "POST", body: { objective, expert_name: name || "Expert" } });
    await hangUp();
    setSession(s.id);
  } catch (e) { toast("Could not create session: " + e.message); }
}

/* ---------------- polling + render ---------------- */
async function poll(force = false) {
  if (!sessionId) return;
  try {
    const d = await api(`/api/session/${sessionId}/state?since=${force ? -1 : version}`);
    if (d.unchanged) return;
    state = d;
    version = d.version;
    render();
  } catch (e) {
    if (String(e.message).includes("not found")) { sessionId = null; openModal(); }
  }
}
setInterval(poll, 1000);

function render() {
  const s = state;
  $("objective").textContent = s.objective || "Untitled session";
  $("objectiveGoal").textContent = `Objective: a beginner should be able to ${s.objective ? s.objective.replace(/^how to /i, "").replace(/\.$/, "").toLowerCase() : "do this task"} unsupervised. Teaching: ${s.expert_name || "Expert"}.`;
  $("phase").textContent = PHASE_LABEL[s.phase] || s.phase;
  $("busy").classList.toggle("hidden", !s.analyst_busy);
  renderBadges(s);

  const covs = Object.entries(s.coverage);
  const pct = Math.round(covs.reduce((a, [, c]) => a + COV_PCT[c.status], 0) / covs.length);
  $("covPct").textContent = pct + "%";
  $("statItems").textContent = s.items.length;
  $("statPinned").textContent = s.items.filter((i) => i.pinned).length;
  $("statGaps").textContent = s.gaps.length;
  $("slots").innerHTML = covs.map(([k, c]) => `
    <div class="cov-row" title="${esc(c.note)}"><div class="cov-name">${esc(SLOTS[k] || k)}
      <div class="cov-bar"><div class="cov-fill ${c.status}" style="width:${COV_PCT[c.status]}%"></div></div></div>
      <div class="cov-pct">${COV_PCT[c.status]}%</div></div>`).join("");

  renderTools(s);
  renderVisualRequest(s);
  renderGaps(s);
  renderItems(s);
  renderFrames(s);
  renderTranscript(s);

  for (const ev of s.events || []) {
    if (ev.kind === "error" && ev.ts > lastErrorTs) {
      if (!firstRender) toast(ev.text.slice(0, 220));
      lastErrorTs = ev.ts;
    }
  }
  firstRender = false;
}

function renderBadges(s) {
  const avatar = !config.avatar_enabled ? "off" : s.avatar_disabled ? "voice-only" : (config.avatar_provider || "on");
  const badges = [
    ["Brain: Claude", true],
    ["Voice: " + (config.voice_available ? "ElevenLabs" : "text only"), config.voice_available],
    ["Avatar: " + avatar[0].toUpperCase() + avatar.slice(1), config.avatar_enabled && !s.avatar_disabled],
    ["Store: json files", false],
  ];
  $("badges").innerHTML = badges.map(([t, on]) => `<span class="badge ${on ? "on" : ""}">${esc(t)}</span>`).join("");
}

function renderTools(s) {
  const tools = (s.events || []).filter((e) => e.kind === "tool").slice(-4);
  $("toolChips").innerHTML = tools.map((e) => `<span class="toolchip ${!firstRender && !seenTools.has(e.ts) ? "new" : ""}">${esc(e.text)}</span>`).join("");
  tools.forEach((e) => seenTools.add(e.ts));
}

let dismissedVisual = "";
function renderVisualRequest(s) {
  const show = s.visual_request && s.visual_request !== dismissedVisual && !(room && room.localParticipant.isCameraEnabled);
  $("visualReq").classList.toggle("hidden", !show);
  if (show) $("visualReqText").textContent = "The apprentice wants to see: " + s.visual_request;
  $("btnCam").classList.toggle("pulse", !!show);
}

function renderGaps(s) {
  const order = { asked: 0, open: 1, resolved: 2, dropped: 3 };
  const live = s.gaps.filter((g) => g.status === "open" || g.status === "asked")
    .sort((a, b) => order[a.status] - order[b.status] || b.priority - a.priority || b.created_turn - a.created_turn)
    .slice(0, MAX_OPEN_GAPS);
  const done = s.gaps.filter((g) => g.status === "resolved" || g.status === "dropped").sort((a, b) => b.updated_at - a.updated_at).slice(0, 6);
  const list = [...live, ...done];
  const nRes = s.gaps.filter((g) => g.status === "resolved").length;
  $("gapCount").textContent = s.gaps.length ? `${live.length} open · ${nRes} resolved` : "";
  if (!list.length) { $("gaps").innerHTML = '<p class="empty">Listening for vague words, hidden steps and missing whys…</p>'; return; }
  $("gaps").innerHTML = list.map((g) => {
    const prev = gapStatus.get(g.id);
    let anim = "";
    if (!firstRender) {
      if (prev === undefined) anim = "new";
      else if (prev !== g.status && g.status === "resolved") anim = "just-resolved";
      else if (prev !== g.status && g.status === "asked") anim = "just-asked";
    }
    const dots = "●".repeat(g.priority) + "○".repeat(5 - g.priority);
    const status = g.status === "resolved" ? "✓ resolved" : g.status;
    return `<div class="item ${g.status} ${anim}">
      <div class="item-top"><span class="kind ${g.kind}">${esc(KIND_LABEL[g.kind] || g.kind)}</span>
        <span class="importance">${dots}</span><span class="status ${g.status}">${g.id} · ${status}</span></div>
      <div class="item-body">${esc(g.question)}</div>
      ${g.anchor_quote ? `<div class="item-meta"><span class="anchor">${esc(g.anchor_quote)}</span></div>` : ""}</div>`;
  }).join("");
  for (const g of s.gaps) gapStatus.set(g.id, g.status);
}

function renderItems(s) {
  $("itemCount").textContent = s.items.length ? `${s.items.length} items` : "";
  if (!s.items.length) { $("items").innerHTML = '<p class="empty">Knowledge will appear here as the expert talks.</p>'; return; }
  $("items").innerHTML = Object.keys(SLOTS).map((slot) => {
    const its = s.items.filter((i) => i.slot === slot);
    if (!its.length) return "";
    return `<div class="cat-head">${esc(SLOTS[slot])}</div>` + its.map((i) => {
      const isNew = !firstRender && !seenItems.has(i.id);
      return `<div class="item ${i.pinned ? "star" : ""} ${i.confidence === "low" ? "low" : ""} ${isNew ? "new" : ""}">
        ${i.pinned ? '<div class="kind missing_slot" style="display:inline-block;margin-bottom:3px">★ Key insight</div>' : ""}
        <div class="item-body">${esc(i.content)}</div>
        ${i.source_quote ? `<div class="item-meta">${i.id} · “${esc(i.source_quote)}”</div>` : ""}</div>`;
    }).join("");
  }).join("");
  const fresh = !firstRender && s.items.some((i) => !seenItems.has(i.id));
  s.items.forEach((i) => seenItems.add(i.id));
  if (fresh) $("items").querySelector(".item.new")?.scrollIntoView({ behavior: "smooth", block: "nearest" });
}

function renderFrames(s) {
  $("frames").innerHTML = (s.frames || []).slice(-4).map((f) =>
    `<div class="frame"><img src="/api/session/${s.id}/frame/${f.id}" alt="camera frame"><div>${esc(f.description.slice(0, 80))}</div></div>`).join("");
}

function agentTag(s, t, nAgentBefore) {
  const g = s.gaps.find((x) => (x.asked_turns || []).includes(t.index));
  if (g) return `Probing an assumption · ${g.id}`;
  if (nAgentBefore === 0) return "Opening";
  if (s.phase === "wrap_up" || s.phase === "done") return "Wrapping up";
  return "Following up";
}

function renderTranscript(s) {
  if (s.transcript.length !== renderedTurns || firstRender) {
    let nAgent = 0;
    $("transcript").innerHTML = s.transcript.map((t) => {
      if (t.speaker === "observation") return `<div class="msg observation">📷 Seen on camera: ${esc(t.text)}</div>`;
      if (t.speaker === "agent") {
        const tag = agentTag(s, t, nAgent++);
        return `<div class="msg agent"><span class="msg-tag">${esc(tag)}</span>${esc(t.text)}</div>`;
      }
      return `<div class="msg expert">${esc(t.text)}</div>`;
    }).join("") + '<div class="msg live hidden" id="liveBubble"></div>';
    renderedTurns = s.transcript.length;
    $("transcript").scrollTop = $("transcript").scrollHeight;
  } else {
    // asked_turns can arrive after the turn itself (analyst is async): refresh tags in place
    let nAgent = 0;
    const els = $("transcript").querySelectorAll(".msg.agent .msg-tag");
    s.transcript.filter((t) => t.speaker === "agent").forEach((t, i) => { if (els[i]) els[i].textContent = agentTag(s, t, nAgent++); });
  }
  renderWhy(s);
}

function renderWhy(s) {
  const last = [...s.transcript].reverse().find((t) => t.speaker === "agent");
  let why = "";
  if (last) {
    const g = s.gaps.find((x) => (x.asked_turns || []).includes(last.index));
    const nAgent = s.transcript.filter((t) => t.speaker === "agent").length;
    if (g) why = `${KIND_LABEL[g.kind] || g.kind}${g.anchor_quote ? ` in “${g.anchor_quote}”` : ""}. ${SLOTS[g.slot] || g.slot}, importance ${g.priority}/5.`;
    else if (nAgent === 1) why = "Opening: establish the goal before probing any details.";
    else if (s.phase === "opening") why = "Getting the end-to-end picture first, then digging into details.";
    else if (s.phase === "wrap_up") why = "Wrapping up: checking nothing important is missing.";
  }
  $("whyBox").classList.toggle("hidden", !why);
  $("whyText").textContent = why;
}

function showLive(isExpert, text) {
  const el = $("liveBubble");
  if (!el) return;
  el.className = "msg live " + (isExpert ? "expert" : "agent");
  el.textContent = text;
  $("transcript").scrollTop = $("transcript").scrollHeight;
}

/* ---------------- text mode ---------------- */
async function sendText() {
  const text = $("textInput").value.trim();
  if (!text || !sessionId) return;
  $("textInput").value = "";
  showLive(true, text);
  setOrb("thinking", "Thinking…");
  $("btnSend").disabled = true;
  try {
    await api(`/api/session/${sessionId}/turn`, { method: "POST", body: { text } });
    poll(true);
  } catch (e) { toast("Turn failed: " + e.message); }
  finally { $("btnSend").disabled = false; if (!room) setOrb("", "Ready"); $("textInput").focus(); }
}
$("textForm").addEventListener("submit", (ev) => { ev.preventDefault(); sendText(); });
$("textInput").addEventListener("keydown", (ev) => { if (ev.key === "Enter" && !ev.shiftKey) { ev.preventDefault(); sendText(); } });

/* ---------------- tabs ---------------- */
document.querySelectorAll(".tab-btn").forEach((t) => t.addEventListener("click", () => {
  document.querySelectorAll(".tab-btn").forEach((x) => x.classList.toggle("active", x === t));
  document.querySelectorAll(".tab").forEach((p) => p.classList.toggle("active", p.id === "tab-" + t.dataset.tab));
}));

/* ---------------- manual ---------------- */
$("btnPlaybook").addEventListener("click", async () => {
  if (!sessionId) return;
  const b = $("btnPlaybook");
  b.disabled = true; b.textContent = "Writing manual…";
  $("playbook").innerHTML = '<p class="empty">Turning captured knowledge into a beginner-ready training manual…</p>';
  try {
    const r = await api(`/api/session/${sessionId}/playbook`, { method: "POST" });
    lastPlaybook = r.markdown;
    $("playbook").innerHTML = DOMPurify.sanitize(marked.parse(r.markdown));
    $("btnDownload").disabled = false;
  } catch (e) {
    toast("Manual failed: " + e.message);
    $("playbook").innerHTML = '<p class="empty">Generation failed. Try again.</p>';
  } finally { b.disabled = false; b.textContent = "Regenerate training manual"; }
});
$("btnDownload").addEventListener("click", () => {
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob([lastPlaybook], { type: "text/markdown" }));
  a.download = `manual-${(state?.objective || "task").toLowerCase().replace(/[^a-z0-9]+/g, "-").slice(0, 40)}.md`;
  a.click();
});

/* ---------------- ask as a trainee ---------------- */
async function sendAsk() {
  const q = $("askInput").value.trim();
  if (!q || !sessionId) return;
  $("askInput").value = "";
  const chat = $("chat");
  chat.insertAdjacentHTML("beforeend", `<div class="msg q">${esc(q)}</div>`);
  const pending = document.createElement("div");
  pending.className = "msg a pending"; pending.textContent = "Checking what the expert said…";
  chat.appendChild(pending); chat.scrollTop = chat.scrollHeight;
  try {
    const r = await api(`/api/session/${sessionId}/ask`, { method: "POST", body: { question: q } });
    const items = new Map((state?.items || []).map((i) => [i.id, i]));
    pending.className = "msg a" + (r.covered ? "" : " uncovered");
    pending.innerHTML = (r.covered ? "" : '<div class="flag">⚠ Not covered by the expert</div>') + esc(r.answer) +
      (r.item_ids.length ? `<div class="cites">${r.item_ids.map((id) => `<span class="cite" title="${esc(items.get(id)?.content || "")}">${id}</span>`).join("")}</div>` : "");
  } catch (e) { pending.className = "msg a uncovered"; pending.textContent = "Sorry, that failed: " + e.message; }
  chat.scrollTop = chat.scrollHeight;
}
$("askForm").addEventListener("submit", (ev) => { ev.preventDefault(); sendAsk(); });
$("askInput").addEventListener("keydown", (ev) => { if (ev.key === "Enter" && !ev.shiftKey) { ev.preventDefault(); sendAsk(); } });

/* ---------------- modal ---------------- */
function openModal() { $("modal").classList.remove("hidden"); $("newObjective").focus(); }
$("btnNew").addEventListener("click", openModal);
$("btnCancel").addEventListener("click", () => { if (sessionId) $("modal").classList.add("hidden"); });
$("newForm").addEventListener("submit", (ev) => {
  ev.preventDefault();
  $("modal").classList.add("hidden");
  newSession($("newObjective").value.trim(), $("newName").value.trim());
});
$("btnReset").addEventListener("click", async () => {
  if (!sessionId || !confirm("Restart this interview from scratch (same objective)?")) return;
  try {
    await hangUp();
    const s = await api(`/api/session/${sessionId}/reset`, { method: "POST" });
    setSession(s.id);
    toast("Interview reset", "info");
  } catch (e) { toast("Reset failed: " + e.message); }
});

/* ---------------- voice + avatar: LiveKit ---------------- */
let room = null;
let userHangup = false;
let reconnects = [];
const LK = window.LivekitClient;

function setOrb(mode, label) {
  const orb = $("orb");
  orb.classList.remove("speaking", "thinking", "listening");
  if (mode) orb.classList.add(mode);
  $("orbLabel").textContent = label;
}

function isLocalTrack(sid) {
  if (!room || !sid) return false;
  return [...room.localParticipant.trackPublications.values()].some((p) => p.trackSid === sid);
}

async function startCall() {
  if (!LK) return toast("LiveKit client failed to load (CDN). Use text mode.");
  if (!config.voice_available) return toast("Voice isn't configured (LiveKit/ElevenLabs keys missing in .env). Use text mode.");
  if (!sessionId) return openModal();
  const btn = $("btnCall");
  btn.disabled = true; btn.textContent = "Connecting…";
  $("avatarStatus").textContent = "Connecting…";
  try {
    const t = await api(`/api/token?session_id=${sessionId}&name=${encodeURIComponent(state?.expert_name || "Expert")}`);
    room = new LK.Room({ adaptiveStream: true, dynacast: true });

    room.on(LK.RoomEvent.TrackSubscribed, (track) => {
      if (track.kind === "audio") {
        const el = track.attach(); el.dataset.lk = "1"; document.body.appendChild(el);
      } else if (track.kind === "video") {
        const el = track.attach();
        $("videoSlot").innerHTML = ""; $("videoSlot").appendChild(el);
        $("orbWrap").classList.add("hidden");
        $("avatarStatus").textContent = "Live: just talk. The apprentice is listening.";
      }
    });
    room.on(LK.RoomEvent.TrackUnsubscribed, (track) => {
      track.detach().forEach((el) => el.remove());
      if (track.kind === "video") { $("videoSlot").innerHTML = ""; $("orbWrap").classList.remove("hidden"); }
    });
    room.on(LK.RoomEvent.ParticipantAttributesChanged, (changed, participant) => {
      const st = participant.attributes?.["lk.agent.state"];
      if (st) setOrb({ speaking: "speaking", thinking: "thinking", listening: "listening" }[st] || "",
        { speaking: "Speaking", thinking: "Thinking…", listening: "Listening", initializing: "Joining…" }[st] || st);
    });
    room.on(LK.RoomEvent.Disconnected, () => onDisconnected());
    // the agent left without us hanging up (e.g. the avatar dropped): reconnect automatically;
    // the worker resumes the same session (voice-only if the avatar was the problem)
    room.on(LK.RoomEvent.ParticipantDisconnected, (p) => {
      if (!p.identity.startsWith("agent-") || userHangup) return;
      const now = Date.now();
      reconnects = reconnects.filter((x) => now - x < 60000);
      if (reconnects.length >= 3) { toast("The apprentice keeps dropping. Click Start call to retry, or use text mode."); hangUp(); return; }
      reconnects.push(now);
      toast("The apprentice dropped. Reconnecting…", "info");
      hangUp().then(() => setTimeout(startCall, 800));
    });
    room.registerTextStreamHandler("lk.transcription", async (reader, info) => {
      const trackId = reader.info.attributes?.["lk.transcribed_track_id"];
      const isExpert = isLocalTrack(trackId) || info.identity === room.localParticipant.identity;
      let text = "";
      for await (const chunk of reader) { text += chunk; showLive(isExpert, text); }
    });

    await room.connect(t.url, t.token);
    await room.localParticipant.setMicrophoneEnabled(true, { echoCancellation: true, noiseSuppression: true, autoGainControl: true });
    await room.startAudio();
    btn.textContent = "■ End call"; btn.classList.add("danger"); btn.classList.remove("primary");
    $("btnMute").classList.remove("hidden"); $("btnCam").classList.remove("hidden");
    setOrb("listening", "Waiting for the apprentice…");
    $("avatarStatus").textContent = "Connected. Use headphones to avoid echo.";
  } catch (e) {
    toast("Call failed: " + e.message + ". Falling back to text mode.");
    await hangUp();
  } finally { btn.disabled = false; }
}

function onDisconnected() {
  room = null;
  document.querySelectorAll("[data-lk]").forEach((el) => el.remove());
  $("videoSlot").innerHTML = ""; $("orbWrap").classList.remove("hidden");
  $("selfView").classList.add("hidden");
  const btn = $("btnCall");
  btn.textContent = "🎙 Start call"; btn.classList.remove("danger"); btn.classList.add("primary");
  $("btnMute").classList.add("hidden"); $("btnCam").classList.add("hidden");
  $("avatarStatus").textContent = "Call ended. Voice and text still work.";
  setOrb("", "Ready");
}

async function hangUp() {
  if (room) { try { await room.disconnect(); } catch { } }
  onDisconnected();
}

$("btnCall").addEventListener("click", async () => {
  if (room) { userHangup = true; await hangUp(); userHangup = false; } else startCall();
});
$("btnMute").addEventListener("click", async () => {
  if (!room) return;
  const on = room.localParticipant.isMicrophoneEnabled;
  await room.localParticipant.setMicrophoneEnabled(!on);
  $("btnMute").textContent = on ? "Unmute" : "Mute";
});
$("btnCam").addEventListener("click", async () => {
  if (!room) return;
  const on = room.localParticipant.isCameraEnabled;
  try {
    await room.localParticipant.setCameraEnabled(!on, { resolution: { width: 1280, height: 720 } });
    const pub = room.localParticipant.getTrackPublication(LK.Track.Source.Camera);
    if (!on && pub?.track) { pub.track.attach($("selfView")); $("selfView").classList.remove("hidden"); }
    else $("selfView").classList.add("hidden");
    $("btnCam").textContent = on ? "📷 Show me" : "📷 Camera off";
    if (!on) { dismissedVisual = state?.visual_request || ""; $("visualReq").classList.add("hidden"); $("btnCam").classList.remove("pulse"); }
  } catch (e) { toast("Camera failed: " + e.message); }
});

/* ---------------- boot ---------------- */
(async function boot() {
  try { config = await api("/api/config"); } catch { config = {}; }
  if (!config.voice_available) $("btnCall").title = "Voice not configured: text mode only";
  const fromUrl = new URL(location.href).searchParams.get("s");
  let saved = null;
  try { saved = localStorage.getItem("lastexpert.session") || localStorage.getItem("apprentice.session"); } catch { }
  const id = fromUrl || saved;
  if (id) {
    sessionId = id;
    await poll(true);
    if (sessionId) setSession(id); else openModal();
  } else openModal();
})();
