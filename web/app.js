/* Apprentice front-end: polling knowledge panel + LiveKit voice call. Vanilla JS, no build. */
"use strict";

const SLOTS = {
  goal: { label: "Goal", color: "#5eead4" },
  steps: { label: "Steps", color: "#60a5fa" },
  decisions: { label: "Decisions", color: "#c084fc" },
  cues: { label: "Cues", color: "#f472b6" },
  mistakes: { label: "Mistakes", color: "#f87171" },
  edge_cases: { label: "Edge cases", color: "#fb923c" },
  tools_terms: { label: "Tools & terms", color: "#a3e635" },
  quality_check: { label: "Quality check", color: "#4ade80" },
};
const KIND_LABEL = {
  vague_term: "Vague term", undefined_jargon: "Undefined jargon", skipped_step: "Skipped step",
  missing_why: "Missing why", missing_failure_case: "No failure case", missing_cue: "Missing cue",
  contradiction: "Contradiction", missing_slot: "Not covered yet",
};
const PHASE_LABEL = { opening: "Opening", deep_dive: "Deep dive", wrap_up: "Wrapping up", done: "Done" };
const MAX_OPEN_GAPS = 8;

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

let sessionId = null;
let state = null;
let version = -1;
let seenItems = new Set();
let gapStatus = new Map();
let firstRender = true;
let config = {};
let lastErrorTs = 0;
let lastPlaybook = "";
let liveCaption = { agent: 0, expert: 0 }; // timestamps of live (voice) captions

/* ---------------- toasts ---------------- */
function toast(msg, kind = "error") {
  const el = document.createElement("div");
  el.className = "toast " + kind;
  el.textContent = msg;
  $("toasts").appendChild(el);
  setTimeout(() => el.remove(), kind === "error" ? 7000 : 3500);
}

async function api(path, opts = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...opts,
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  });
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
  $("chat").innerHTML = "";
  $("playbook").innerHTML = '<div class="empty">Generate a beginner-ready playbook from everything captured so far.</div>';
  $("btnDownload").disabled = true;
  try { localStorage.setItem("apprentice.session", id); } catch { }
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
  $("phase").textContent = PHASE_LABEL[s.phase] || s.phase;
  $("busy").classList.toggle("hidden", !s.analyst_busy);

  // coverage
  const pts = { empty: 0, partial: 0.5, good: 1 };
  const covs = Object.entries(s.coverage);
  const pct = Math.round((100 * covs.reduce((a, [, c]) => a + pts[c.status], 0)) / covs.length);
  $("covPct").textContent = pct + "%";
  $("covFill").style.width = pct + "%";
  $("slots").innerHTML = covs.map(([k, c]) =>
    `<div class="slot s-${c.status}" title="${esc(c.note)}">${esc(SLOTS[k]?.label || k)}<div class="sbar"><div class="sfill"></div></div></div>`).join("");
  const resolved = s.gaps.filter((g) => g.status === "resolved").length;
  const nq = s.transcript.filter((t) => t.speaker === "agent").length;
  $("stats").textContent = `${s.items.length} items · ${resolved}/${s.gaps.length} gaps resolved · ${nq} questions`;

  renderTools(s);
  renderVisualRequest(s);
  renderGaps(s);
  renderItems(s);
  renderFrames(s);
  renderCaptions(s);

  // surface new errors as toasts
  for (const ev of s.events || []) {
    if (ev.kind === "error" && ev.ts > lastErrorTs) {
      if (!firstRender) toast(ev.text.slice(0, 220));
      lastErrorTs = ev.ts;
    }
  }
  firstRender = false;
}

function renderGaps(s) {
  const order = { asked: 0, open: 1, resolved: 2, dropped: 3 };
  const live = s.gaps.filter((g) => g.status === "open" || g.status === "asked")
    .sort((a, b) => order[a.status] - order[b.status] || b.priority - a.priority || b.created_turn - a.created_turn)
    .slice(0, MAX_OPEN_GAPS);
  const done = s.gaps.filter((g) => g.status === "resolved" || g.status === "dropped")
    .sort((a, b) => b.updated_at - a.updated_at).slice(0, 6);
  const list = [...live, ...done];
  $("gapCount").textContent = s.gaps.length ? `${live.length} open` : "";
  if (!list.length) {
    $("gaps").innerHTML = '<div class="empty">Listening for vague words, hidden steps and missing whys…</div>';
    return;
  }
  $("gaps").innerHTML = list.map((g) => {
    const prev = gapStatus.get(g.id);
    let anim = "";
    if (!firstRender) {
      if (prev === undefined) anim = "new";
      else if (prev !== g.status && g.status === "resolved") anim = "just-resolved";
      else if (prev !== g.status && g.status === "asked") anim = "just-asked";
    }
    const chip = g.status === "resolved" ? '<span class="check">✓</span>resolved' : g.status;
    return `<div class="gap ${g.status} ${anim}" title="${esc(g.question)}">
      <div class="gap-top"><span class="kind">${esc(KIND_LABEL[g.kind] || g.kind)}</span>
        <span class="prio">${"●".repeat(Math.max(1, Math.min(5, g.priority)))}</span>
        <span class="chip ${g.status}">${chip}</span></div>
      ${g.anchor_quote ? `<div class="anchor">${esc(g.anchor_quote)}</div>` : ""}
      <div class="question">${esc(g.question)}</div></div>`;
  }).join("");
  for (const g of s.gaps) gapStatus.set(g.id, g.status);
}

let seenTools = new Set();
function renderTools(s) {
  const tools = (s.events || []).filter((e) => e.kind === "tool").slice(-4);
  $("toolChips").innerHTML = tools.map((e) => {
    const isNew = !firstRender && !seenTools.has(e.ts);
    return `<span class="toolchip ${isNew ? "new" : ""}">${esc(e.text)}</span>`;
  }).join("");
  tools.forEach((e) => seenTools.add(e.ts));
}

let dismissedVisual = "";
function renderVisualRequest(s) {
  const show = s.visual_request && s.visual_request !== dismissedVisual && !(room && room.localParticipant.isCameraEnabled);
  $("visualReq").classList.toggle("hidden", !show);
  if (show) $("visualReqText").textContent = "The apprentice wants to see: " + s.visual_request;
  $("btnCam").classList.toggle("pulse", !!show);
}

function renderItems(s) {
  $("itemCount").textContent = s.items.length || "";
  if (!s.items.length) {
    $("items").innerHTML = '<div class="empty">Knowledge will appear here as the expert talks.</div>';
    return;
  }
  const html = Object.keys(SLOTS).map((slot) => {
    const its = s.items.filter((i) => i.slot === slot);
    if (!its.length) return "";
    return `<div class="slot-group"><h3 style="color:${SLOTS[slot].color}">${SLOTS[slot].label}</h3>` +
      its.map((i) => {
        const isNew = !firstRender && !seenItems.has(i.id);
        return `<div class="item ${i.confidence} ${isNew ? "new" : ""} ${i.pinned ? "pinned" : ""}" style="border-left:4px solid ${SLOTS[slot].color}">
          ${i.pinned ? '<span class="pin">★ KEY INSIGHT</span>' : ""}${esc(i.content)}${i.source_quote ? `<div class="meta"><span class="iid">${i.id}</span>“${esc(i.source_quote)}”</div>` : ""}</div>`;
      }).join("") + "</div>";
  }).join("");
  $("items").innerHTML = html;
  const newest = s.items.find((i) => !seenItems.has(i.id));
  s.items.forEach((i) => seenItems.add(i.id));
  if (newest && !firstRender) {
    const el = $("items").querySelector(".item.new");
    if (el) el.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }
}

function renderFrames(s) {
  $("frames").innerHTML = (s.frames || []).slice(-4).map((f) =>
    `<div class="frame"><img src="/api/session/${s.id}/frame/${f.id}" alt="frame"><div>${esc(f.description.slice(0, 80))}</div></div>`).join("");
}

function renderCaptions(s) {
  const now = Date.now();
  const lastOf = (who) => [...s.transcript].reverse().find((t) => t.speaker === who);
  const a = lastOf("agent"), e = lastOf("expert");
  if (now - liveCaption.agent > 4000) $("capAgent").textContent = a ? a.text : "…";
  if (now - liveCaption.expert > 4000) $("capExpert").textContent = e ? e.text : "…";
  if (s.expert_name) document.querySelector(".cap-expert .who").textContent = s.expert_name;
}

/* ---------------- text mode ---------------- */
$("textForm").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const text = $("textInput").value.trim();
  if (!text || !sessionId) return;
  $("textInput").value = "";
  $("capExpert").textContent = text;
  $("capAgent").textContent = "…";
  setOrb("thinking", "Thinking…");
  $("btnSend").disabled = true;
  try {
    const r = await api(`/api/session/${sessionId}/turn`, { method: "POST", body: { text } });
    $("capAgent").textContent = r.reply;
    poll(true);
  } catch (e) { toast("Turn failed: " + e.message); }
  finally { $("btnSend").disabled = false; if (!room) setOrb("", "Ready"); $("textInput").focus(); }
});

/* ---------------- tabs ---------------- */
document.querySelectorAll(".tab").forEach((t) => t.addEventListener("click", () => {
  document.querySelectorAll(".tab").forEach((x) => x.classList.toggle("active", x === t));
  document.querySelectorAll(".tabpane").forEach((p) => p.classList.toggle("active", p.id === "tab-" + t.dataset.tab));
}));

/* ---------------- playbook ---------------- */
$("btnPlaybook").addEventListener("click", async () => {
  if (!sessionId) return;
  const b = $("btnPlaybook");
  b.disabled = true; b.textContent = "Writing playbook…";
  $("playbook").innerHTML = '<div class="empty">Turning captured knowledge into a beginner-ready playbook…</div>';
  try {
    const r = await api(`/api/session/${sessionId}/playbook`, { method: "POST" });
    lastPlaybook = r.markdown;
    $("playbook").innerHTML = DOMPurify.sanitize(marked.parse(r.markdown));
    $("btnDownload").disabled = false;
  } catch (e) {
    toast("Playbook failed: " + e.message);
    $("playbook").innerHTML = '<div class="empty">Playbook generation failed — try again.</div>';
  } finally { b.disabled = false; b.textContent = "Regenerate playbook"; }
});
$("btnDownload").addEventListener("click", () => {
  const blob = new Blob([lastPlaybook], { type: "text/markdown" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = `playbook-${(state?.objective || "task").toLowerCase().replace(/[^a-z0-9]+/g, "-").slice(0, 40)}.md`;
  a.click();
});

/* ---------------- ask the expert ---------------- */
$("askForm").addEventListener("submit", async (ev) => {
  ev.preventDefault();
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
      (r.item_ids.length ? `<div class="cites">${r.item_ids.map((id) =>
        `<span class="cite" title="${esc(items.get(id)?.content || "")}">${id}</span>`).join("")}</div>` : "");
  } catch (e) {
    pending.className = "msg a uncovered"; pending.textContent = "Sorry — that failed: " + e.message;
  }
  chat.scrollTop = chat.scrollHeight;
});

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

/* ---------------- voice: LiveKit ---------------- */
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
  try {
    const t = await api(`/api/token?session_id=${sessionId}&name=${encodeURIComponent(state?.expert_name || "Expert")}`);
    room = new LK.Room({ adaptiveStream: true, dynacast: true });

    room.on(LK.RoomEvent.TrackSubscribed, (track, pub, participant) => {
      if (track.kind === "audio") {
        const el = track.attach();
        el.dataset.lk = "1";
        document.body.appendChild(el);
      } else if (track.kind === "video") {
        const el = track.attach();
        $("videoSlot").innerHTML = "";
        $("videoSlot").appendChild(el);
        $("orbWrap").classList.add("hidden");
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
    // the agent left without us hanging up (e.g. the avatar dropped): reconnect automatically,
    // the worker resumes the same session (voice-only if the avatar was the problem)
    room.on(LK.RoomEvent.ParticipantDisconnected, (p) => {
      if (!p.identity.startsWith("agent-") || userHangup) return;
      const now = Date.now();
      reconnects = reconnects.filter((t) => now - t < 60000);
      if (reconnects.length >= 3) { toast("The apprentice keeps dropping. Click Start call to retry, or use text mode."); hangUp(); return; }
      reconnects.push(now);
      toast("The apprentice dropped. Reconnecting…", "info");
      hangUp().then(() => setTimeout(startCall, 800));
    });

    room.registerTextStreamHandler("lk.transcription", async (reader, info) => {
      const trackId = reader.info.attributes?.["lk.transcribed_track_id"];
      const isExpert = isLocalTrack(trackId) || (info.identity === room.localParticipant.identity);
      const el = isExpert ? $("capExpert") : $("capAgent");
      let text = "";
      for await (const chunk of reader) {
        text += chunk;
        el.textContent = text;
        liveCaption[isExpert ? "expert" : "agent"] = Date.now();
      }
    });

    await room.connect(t.url, t.token);
    await room.localParticipant.setMicrophoneEnabled(true, {
      echoCancellation: true, noiseSuppression: true, autoGainControl: true,
    });
    await room.startAudio();
    btn.textContent = "■ End call"; btn.classList.add("danger"); btn.classList.remove("primary");
    $("btnMute").classList.remove("hidden");
    $("btnCam").classList.remove("hidden");
    setOrb("listening", "Waiting for apprentice…");
    toast("Connected. Use headphones to avoid echo.", "info");
  } catch (e) {
    toast("Call failed: " + e.message + " — falling back to text mode.");
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
  if (!config.voice_available) $("btnCall").title = "Voice not configured — text mode only";
  const fromUrl = new URL(location.href).searchParams.get("s");
  let saved = null;
  try { saved = localStorage.getItem("apprentice.session"); } catch { }
  const id = fromUrl || saved;
  if (id) {
    sessionId = id;
    await poll(true);
    if (sessionId) setSession(id); else openModal();
  } else openModal();
})();
