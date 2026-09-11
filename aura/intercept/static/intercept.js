// AURA Intercept. One SSE stream in, a handful of POSTs out. No framework.
// Voice: declarative, log register. Times in Zulu, bearings to one decimal,
// units always printed. Absence is stated with an em dash, never left blank.
"use strict";
const $ = (id) => document.getElementById(id);
const post = (body) => fetch("/api/control", {method: "POST",
  headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)});
const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;"}[c]));
const zulu = (t) => new Date(t * 1000).toISOString().slice(11, 19) + "Z";
const deg = (v) => (v > 0 ? "+" : v < 0 ? "−" : "") + Math.abs(v).toFixed(1);
const DASH = "—";
const NATO = ["Alpha","Bravo","Charlie","Delta","Echo","Foxtrot","Golf","Hotel","India","Juliett","Kilo","Lima","Mike",
  "November","Oscar","Papa","Quebec","Romeo","Sierra","Tango","Uniform","Victor","Whiskey","X-ray","Yankee","Zulu"];
const phon = (L) => NATO[L.charCodeAt(0) - 65] || L;
const tag = (t) => t.letter ? `${t.letter}·T${t.id}` : `T${t.id}`;
const who = (t) => t.letter ? `Contact ${phon(t.letter).toLowerCase()} (T${t.id})` : `Track T${t.id}`;

let S = null, lastSeq = -1, lastSource = null, selected = null, lastTableKey = "", lastRecordKey = "";

setInterval(() => { $("clock").textContent = new Date().toISOString().slice(11, 19) + "Z"; }, 250);

// --- status vocabulary: a word and a shape -----------------------------------------
const STATUS = {
  TRACKING:    ["Assessing",   "unknown"],
  IFF_QUERY:   ["IFF query",   "unknown"],
  FRIENDLY:    ["Friendly",    "own"],
  HOSTILE:     ["Hostile",     "contact"],
  ENGAGING:    ["Engaging",    "contact"],
  NEUTRALISED: ["Neutralised", "contact"],
};
function status(t) {
  if (!t.fresh && t.state !== "NEUTRALISED") return `<span class="st">${DASH} Lost</span>`;
  const [word, shape] = STATUS[t.state];
  const q = t.state === "HOSTILE" && t.queue ? ` · Q${t.queue}` : "";
  return `<span class="st ${shape === "unknown" ? "" : shape}"><i class="sh ${shape}"></i>${word}${q}</span>`;
}

// --- finding ------------------------------------------------------------------------
function finding(s, lead) {
  const T = lead ? who(lead) : "";
  const at = lead ? `bearing ${deg(lead.bearing)}°, elevation ${deg(lead.elevation)}°` : "";
  switch (s.verdict) {
    case "TRACKING":  return ["Contact under assessment.", `${T} held at ${at}. Confidence ${lead.conf.toFixed(2)}; evidence accumulating.`];
    case "IFF_QUERY": return ["Drone confirmed. Identity pending.", `${T} under IFF and Remote-ID interrogation.`];
    case "FRIENDLY":  return ["Friendly drone. Allowed to pass.", `${T} returned a valid IFF reply. No action taken; track monitored.`];
    case "HOSTILE":   return ["Hostile drone.", s.mode === "AUTO"
      ? `${T} returned no IFF reply. Engagement follows under weapons-free rules.`
      : `${T} returned no IFF reply. Engagement awaits operator authorisation.`];
    case "ENGAGING": {
      const q = s.tracks.filter((t) => t.queue).length;
      return ["Hostile drone. Engagement in progress.", `Soft-kill laid on ${at}.` +
        (q ? ` ${q} further hostile${q > 1 ? "s" : ""} queued; one effector.` : "")];
    }
    case "NEUTRALISED": {
      const e = lead.record.effect;
      const d = lead.record.detect;
      const took = e && d ? `, ${(e[0] - d[0]).toFixed(1)} s from first detection` : "";
      return ["Threat neutralised.", `${T} defeated. Kill assessed at ${e ? zulu(e[0]) : DASH}${took}.`];
    }
    default: return ["Sky clear.", "No aerial contact in sector."];
  }
}

function alertFor(s, lead) {
  if (s.source_error) return ["Signal lost", `Source unavailable: ${s.source_error}. Reselect the source.`];
  if (s.verdict === "ENGAGING") return ["Engagement", `Track T${lead.id} engaged on bearing ${deg(lead.bearing)}°. Effector modelled; no emission.`];
  if (s.verdict === "HOSTILE" && s.hitl && lead.letter) {
    const n = phon(lead.letter).toLowerCase();
    return ["Orders required", `${who(lead)} hostile. Say “destroy ${n}” or “let ${n} go”.`];
  }
  if (s.verdict === "HOSTILE" && s.mode === "HOLD") return ["Authorisation required", `Track T${lead.id} classified hostile. Select it and authorise engagement.`];
  return null;
}

// --- FIG. 2: angular plot -------------------------------------------------------------
const PW = 312, PH = 208;
function plot(s) {
  const hb = s.hfov / 2, he = s.vfov / 2;
  const X = (b) => ((b + hb) / s.hfov) * PW, Y = (e) => ((he - e) / s.vfov) * PH;
  let g = "";
  g += `<path d="M${PW / 2 - 7} ${PH / 2}H${PW / 2 + 7}M${PW / 2} ${PH / 2 - 7}V${PH / 2 + 7}" stroke="var(--ink)" stroke-width="1"/>`;
  const t9 = `font-family="IBM Plex Mono, monospace" font-size="9" fill="var(--ink-faint)"`;
  g += `<text x="6" y="14" ${t9}>${deg(-hb)}°</text><text x="${PW - 6}" y="14" text-anchor="end" ${t9}>${deg(hb)}°</text>`;
  g += `<text x="${PW / 2 + 6}" y="14" ${t9}>+${he.toFixed(1)}° EL</text>`;
  for (const t of s.tracks) {
    if (!t.trail.length) continue;
    const pts = t.trail.map(([b, e]) => `${X(b).toFixed(1)},${Y(e).toFixed(1)}`).join(" ");
    const [ob, oe] = t.trail[0];
    g += `<polyline points="${pts}" fill="none" stroke="var(--green)" stroke-width="1"/>`;
    g += `<circle cx="${X(ob)}" cy="${Y(oe)}" r="3.5" fill="var(--paper-raised)" stroke="var(--green)" stroke-width="1"/>`;
    const x = X(t.bearing), y = Y(t.elevation);
    const shape = STATUS[t.state][1];
    if (shape === "contact") g += `<rect x="${x - 4.5}" y="${y - 4.5}" width="9" height="9" fill="var(--oxide)"/>`;
    else if (shape === "own") g += `<circle cx="${x}" cy="${y}" r="4.5" fill="var(--paper-raised)" stroke="var(--green)" stroke-width="1"/>`;
    else g += `<rect x="${x - 4.5}" y="${y - 4.5}" width="9" height="9" fill="var(--paper-raised)" stroke="var(--ink)" stroke-width="1"/>`;
    const col = shape === "contact" ? "var(--oxide)" : shape === "own" ? "var(--green)" : "var(--ink)";
    g += `<text x="${x}" y="${y - 14.5}" text-anchor="middle" font-family="IBM Plex Mono, monospace" font-size="10" fill="${col}">${t.letter || "T" + t.id}</text>`;
  }
  $("fig2").innerHTML = g;
}

// --- tables ---------------------------------------------------------------------------
const STAGES = ["detect", "confirm", "identify", "decide", "effect"];
function record(s, lead) {
  const key = lead ? JSON.stringify([lead.id, lead.record]) : "none";
  if (key === lastRecordKey) return;
  lastRecordKey = key;
  const rows = $("record").tBodies[0].rows;
  STAGES.forEach((k, i) => {
    const r = lead && lead.record[k];
    rows[0].cells[i + 1].textContent = r ? zulu(r[0]) : DASH;
    rows[1].cells[i + 1].textContent = r ? r[1] : DASH;
  });
  $("record-cap").textContent = lead
    ? `TABLE 1 — Process record, track T${lead.id}. Dash: stage not reached.`
    : "TABLE 1 — Process record. No lead track.";
}

function contacts(s) {
  const key = JSON.stringify([s.tracks.map((t) => [t.id, t.state, t.fresh, t.conf.toFixed(2), t.bearing, t.elevation, t.range, t.queue, t.letter]), selected]);
  if (key === lastTableKey) return;
  lastTableKey = key;
  $("contacts").innerHTML = s.tracks.length ? s.tracks.map((t) =>
    `<tr data-id="${t.id}" class="${t.id === selected ? "sel" : ""}${t.fresh || t.state === "NEUTRALISED" ? "" : " stale"}">
      <td>${tag(t)}</td><td>${status(t)}</td><td class="n">${t.conf.toFixed(2)}</td>
      <td class="n">${deg(t.bearing)}</td><td class="n">${deg(t.elevation)}</td>
      <td class="n">${t.range != null ? t.range : DASH}</td></tr>`).join("")
    : `<tr class="empty"><td>${DASH}</td><td colspan="5">No contact.</td></tr>`;
  const withheld = s.tracks.some((t) => t.range == null);
  $("contacts-cap").textContent = "TABLE 2 — Contacts in sector." +
    (withheld ? " Rng dash: range withheld, lens unknown for recorded footage." : "");
}

function actions(s) {
  let t = s.tracks.find((x) => x.id === selected);
  if (!t) { selected = null; t = s.tracks.find((x) => x.id === s.lead); }
  $("sel").textContent = t ? `T${t.id}` : DASH;
  $("sel").dataset.id = t ? t.id : "";
  if (t && t.letter) $("sel").textContent = `${t.letter} · T${t.id}`;
  const done = !t || t.state === "NEUTRALISED" || t.state === "ENGAGING";
  $("b-friendly").disabled = done || t.state === "FRIENDLY";
  $("b-hostile").disabled = done || t.state === "HOSTILE";
  $("b-engage").disabled = !t || t.state !== "HOSTILE" || s.mode !== "HOLD";
  return t;
}
$("contacts").addEventListener("click", (e) => {
  const tr = e.target.closest("tr[data-id]");
  if (tr) { selected = +tr.dataset.id; if (S) render(S); }
});
const target = () => +$("sel").dataset.id;
$("b-friendly").onclick = () => post({action: "override", track: target(), verdict: "friendly"});
$("b-hostile").onclick = () => post({action: "override", track: target(), verdict: "hostile"});
$("b-engage").onclick = () => post({action: "engage", track: target()});

// --- controls ---------------------------------------------------------------------------
async function loadSources(select) {
  const s = await (await fetch("/api/sources")).json();
  const sel = $("source");
  sel.innerHTML = "";
  const add = (value, label, group) => {
    const o = document.createElement("option");
    o.value = value; o.textContent = label; group.appendChild(o);
  };
  const live = document.createElement("optgroup"); live.label = "Live";
  s.cameras.forEach((c) => add(String(c), `Camera ${c}`, live));
  add(s.pi, "EO node, Pi stream", live);
  sel.appendChild(live);
  if (s.clips.length) {
    const rec = document.createElement("optgroup"); rec.label = "Recorded";
    s.clips.forEach((c) => add(c.path, c.name, rec));
    sel.appendChild(rec);
  }
  if (select != null) sel.value = select;
}
$("source").addEventListener("change", (e) => post({action: "source", source: e.target.value}));

$("upload").addEventListener("change", async (e) => {
  const f = e.target.files[0];
  if (!f) return;
  const btn = $("upload-btn");
  btn.firstChild.textContent = "Uploading";
  try {
    const j = await (await fetch("/api/upload", {method: "POST", headers: {"X-Filename": f.name}, body: f})).json();
    if (j.path) loadSources(j.path);
  } finally {
    btn.firstChild.textContent = "Upload footage";
    e.target.value = "";
  }
});
$("upload-btn").addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") $("upload").click(); });

document.querySelectorAll("[data-mode]").forEach((b) => b.onclick = () => post({action: "mode", mode: b.dataset.mode}));
document.querySelectorAll("[data-beacon]").forEach((b) => b.onclick = () => post({action: "beacon", on: b.dataset.beacon === "1"}));
document.querySelectorAll("[data-gate]").forEach((b) => b.onclick = () => post({action: "gate", on: b.dataset.gate === "1"}));
$("reset").onclick = () => post({action: "reset"});
$("p-pause").onclick = () => post({action: "pause", on: !S.paused});
$("p-restart").onclick = () => post({action: "restart"});
$("rec").onclick = () => post({action: "record", on: !S.recording});
$("report").onclick = () => window.open("/report", "_blank");

// Presenter keys: F beacon, A weapons free, H hold, C clear, N night inversion.
document.addEventListener("keydown", (e) => {
  if (e.target.matches("input,select,textarea") || e.ctrlKey || e.metaKey || e.altKey || !S) return;
  const k = e.key.toLowerCase();
  if (k === "f") post({action: "beacon", on: !S.iff_beacon});
  else if (k === "a") post({action: "mode", mode: "AUTO"});
  else if (k === "h") post({action: "mode", mode: "HOLD"});
  else if (k === "c") post({action: "reset"});
  else if (k === " " && !S.live) { e.preventDefault(); post({action: "pause", on: !S.paused}); }
  else if (k === "r" && !S.live) post({action: "restart"});
  else if (k === "n") document.documentElement.toggleAttribute("data-night");
  else if (k === "l") post({action: "hitl", on: !S.hitl});
  else if (k === "t" && S.hitl && !e.repeat) talkStart();
});

// --- human in the loop ----------------------------------------------------------------------
// Speech out: one queue, so a callout never talks over a reply.
let voiceCfg = null, lastCallout = null, sayChain = Promise.resolve();
function playAudio(src) {
  return new Promise((res) => { const a = new Audio(src); a.onended = a.onerror = res; a.play().catch(res); });
}
function browserSay(text) {
  return new Promise((res) => {
    if (!window.speechSynthesis) return res();
    const u = new SpeechSynthesisUtterance(text); u.rate = 1.05; u.onend = u.onerror = res;
    speechSynthesis.speak(u);
  });
}
function say(text, b64) {
  sayChain = sayChain.then(async () => {
    if (b64) return playAudio("data:audio/mpeg;base64," + b64);
    const r = await fetch("/api/tts?text=" + encodeURIComponent(text)).catch(() => null);
    if (r && r.status === 200) {
      const url = URL.createObjectURL(await r.blob());
      await playAudio(url); URL.revokeObjectURL(url);
    } else await browserSay(text);
  });
}

async function loadVoiceCfg() {
  voiceCfg = await (await fetch("/api/voice/config")).json();
  $("voice-cap").textContent = `Speech: ${voiceCfg.stt === "elevenlabs" ? "ElevenLabs" : "browser"} · ` +
    `Intent: ${voiceCfg.brain.startsWith("gemini") ? voiceCfg.brain.replace("gemini", "Gemini") : "rule parser, offline"}` +
    (voiceCfg.last_error ? ` · last fault: ${voiceCfg.last_error}` : "");
}

const exchange = [];
function showExchange(opr, aura) {
  exchange.unshift([opr, aura]);
  exchange.length = Math.min(exchange.length, 3);
  $("exchange").innerHTML = exchange.map(([o, a]) =>
    `<tr><td>OPR</td><td>${esc(o)}</td></tr><tr class="aura"><td>AURA</td><td>${esc(a)}</td></tr>`).join("");
}
function handleReply(j, heard) {
  if (j.error) { showExchange(heard || DASH, `Speech service fault. Type the order.`); $("voice-cap").textContent = j.error; return; }
  showExchange(j.transcript || heard, j.reply);
  say(j.reply, j.audio);
  if (j.brain) $("voice-cap").textContent = $("voice-cap").textContent.replace(/ · via .*$/, "") + ` · via ${j.brain}`;
}
async function sendText(text, via) {
  if (!text.trim()) return;
  $("talk").firstChild.textContent = "Thinking ";
  try {
    const j = await (await fetch("/api/command", {method: "POST",
      headers: {"Content-Type": "application/json", "X-Via": via}, body: JSON.stringify({text})})).json();
    handleReply(j, text);
  } finally { $("talk").firstChild.textContent = "Hold to speak "; }
}
$("cmd").addEventListener("keydown", (e) => {
  if (e.key === "Enter") { sendText(e.target.value, "typed"); e.target.value = ""; }
});

// Speech in: ElevenLabs if the server has a key, else the browser's recogniser.
let rec = null, chunks = [], stream = null, talking = false, pressAt = 0, recog = null, recogText = "";
async function talkStart() {
  if (talking || !S || !S.hitl) return;
  talking = true; pressAt = Date.now();
  $("talk").classList.add("live"); $("talk").firstChild.textContent = "Listening, release to send ";
  if (voiceCfg && voiceCfg.stt === "elevenlabs") {
    try {
      stream = stream || await navigator.mediaDevices.getUserMedia({audio: {echoCancellation: true, noiseSuppression: true}});
      chunks = [];
      rec = new MediaRecorder(stream, MediaRecorder.isTypeSupported("audio/webm;codecs=opus") ? {mimeType: "audio/webm;codecs=opus"} : {});
      rec.ondataavailable = (e) => e.data.size && chunks.push(e.data);
      rec.start();
    } catch (err) { talking = false; talkIdle(); $("voice-cap").textContent = "Microphone unavailable. Type the order."; }
  } else {
    const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!SR) { talking = false; talkIdle(); $("voice-cap").textContent = "No speech recogniser in this browser. Type the order."; return; }
    recog = new SR(); recog.lang = "en-IN"; recog.interimResults = false; recogText = "";
    recog.onresult = (e) => { recogText = [...e.results].map((r) => r[0].transcript).join(" "); };
    recog.start();
  }
}
function talkIdle() { $("talk").classList.remove("live"); $("talk").firstChild.textContent = "Hold to speak "; }
async function talkStop() {
  if (!talking) return;
  talking = false;
  const short = Date.now() - pressAt < 300;
  if (rec) {
    const r = rec; rec = null;
    const done = new Promise((res) => (r.onstop = res));
    r.stop(); await done;
    if (short) return talkIdle();
    $("talk").classList.remove("live"); $("talk").firstChild.textContent = "Transcribing ";
    const blob = new Blob(chunks, {type: r.mimeType || "audio/webm"});
    try {
      const resp = await fetch("/api/voice", {method: "POST", headers: {"Content-Type": blob.type}, body: blob});
      handleReply(await resp.json());
    } catch { $("voice-cap").textContent = "Speech service unreachable. Type the order."; }
    talkIdle();
  } else if (recog) {
    const r = recog; recog = null;
    const done = new Promise((res) => (r.onend = res));
    r.stop(); await done; talkIdle();
    if (!short && recogText) sendText(recogText, "voice, browser");
  } else talkIdle();
}
$("talk").addEventListener("pointerdown", (e) => { e.preventDefault(); talkStart(); });
["pointerup", "pointerleave", "pointercancel"].forEach((ev) => $("talk").addEventListener(ev, talkStop));
document.addEventListener("keyup", (e) => { if (e.key.toLowerCase() === "t") talkStop(); });
document.querySelectorAll("[data-hitl]").forEach((b) => b.onclick = () => post({action: "hitl", on: b.dataset.hitl === "1"}));

function renderHitl(s) {
  press("h-on", s.hitl); press("h-off", !s.hitl);
  const was = !$("voice").hidden;
  $("voice").hidden = !s.hitl;
  if (s.hitl && !was) loadVoiceCfg();
  // Spoken callouts: only new ones, only with a human in the loop.
  const top = s.callouts.length ? s.callouts[s.callouts.length - 1].seq : 0;
  if (lastCallout === null) lastCallout = top;
  if (s.hitl) for (const c of s.callouts) if (c.seq > lastCallout) say(c.text);
  lastCallout = top;
}

// --- render -------------------------------------------------------------------------------
const press = (id, on) => $(id).setAttribute("aria-pressed", on);

function render(s) {
  S = s;
  document.body.dataset.verdict = s.verdict;
  const lead = s.tracks.find((t) => t.id === s.lead) || null;

  const [statement, body] = finding(s, lead);
  $("statement").textContent = statement;
  $("finding-body").textContent = body;
  const stamp = {HOSTILE: "Hostile", ENGAGING: "Engaging", NEUTRALISED: "Neutralised"}[s.verdict];
  $("stamp").hidden = !stamp;
  if (stamp) $("stamp").textContent = stamp;

  const al = alertFor(s, lead);
  $("alert").classList.toggle("on", !!al);
  $("alert").setAttribute("aria-hidden", !al);
  $("alert-label").textContent = al ? al[0] : "";
  $("alert-body").textContent = al ? al[1] : "";

  $("m-src").textContent = s.source_error ? "Unavailable" : (s.live ? "Live" : s.paused ? "Recorded · held" : "Recorded");
  $("m-det").textContent = s.status === "ready" ? `p2_s · ${s.fps.toFixed(0)} fps` : s.status;
  const res = s.frame[0] ? `${s.frame[0]} × ${s.frame[1]} px · ${s.fps.toFixed(0)} fps` : DASH;
  const gate = s.gate ? `gate on, ${s.target_m} m at ${s.hfov.toFixed(1)}°, ${s.gate_rejected} rejected` : "gate off";
  $("fig1-cap").textContent = `FIG. 1 — EO sensor feed · ${s.source_label || DASH} · ${res} · ${gate}` +
    (s.paused ? " · playback held" : "") + (s.recording ? " · recording" : "");

  $("n-det").textContent = s.counts.detected;
  $("n-ok").textContent = s.counts.allowed;
  $("n-kill").textContent = s.counts.neutralised;
  $("n-d2k").textContent = s.mean_d2k != null ? `${s.mean_d2k.toFixed(1)} s mean` : DASH;
  $("session").textContent = s.session;
  $("p-pause").disabled = $("p-restart").disabled = s.live || !!s.source_error;
  $("p-pause").textContent = s.paused ? "Resume playback" : "Hold playback";
  $("p-pause").classList.toggle("primary", s.paused);
  $("rec").textContent = s.recording ? "Stop recording" : "Start recording";
  $("rec").classList.toggle("primary", s.recording);
  $("rec-cap").textContent = s.recording ? `Recording to ${s.rec_file.split("/").pop()}.`
    : s.rec_file ? `Last file: ${s.rec_file.split("/").slice(-2).join("/")}.` : "Not recording.";

  press("m-auto", s.mode === "AUTO"); press("m-hold", s.mode === "HOLD");
  press("bc-on", s.iff_beacon); press("bc-off", !s.iff_beacon);
  press("g-on", s.gate); press("g-off", !s.gate);

  renderHitl(s);
  plot(s);
  record(s, lead);
  contacts(s);
  actions(s);

  const top = s.events[0]?.seq ?? 0;
  if (top !== lastSeq) {
    lastSeq = top;
    $("log").innerHTML = s.events.length ? s.events.map((e) =>
      `<tr><td>${zulu(e.t)}</td><td>${e.track != null ? "T" + e.track : DASH}</td>` +
      `<td class="${e.level}">${esc(e.text)}</td></tr>`).join("")
      : `<tr class="empty"><td>${DASH}</td><td>${DASH}</td><td>No entries.</td></tr>`;
  }

  if (s.source !== lastSource) {
    lastSource = s.source;
    loadSources(s.source);
  }
}

function connect() {
  const es = new EventSource("/events");
  es.onmessage = (m) => render(JSON.parse(m.data));
  es.onerror = () => {
    $("m-det").textContent = "Offline";
    es.close();
    setTimeout(() => {
      $("video").src = "/stream.mjpg?" + Date.now();   // the MJPEG died with the server
      connect();
    }, 1500);
  };
}
connect();
