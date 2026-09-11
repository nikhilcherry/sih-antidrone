// AURA ops console client. One WebSocket, one shared environment: whatever a
// slider does here, every other screen on the bus does too.

const $ = (id) => document.getElementById(id);
const NS = "http://www.w3.org/2000/svg";
const TARGET_M = 0.3;          // mirrors model.TARGET_SIZE_M; also sent in env.target

const S = {
  env: null, key: null, atm: null, health: [], endurance: null, eo: null,
  core: null, off: null, ablation: null, sweep: null, status: null,
  scenarios: [], limits: null,
};
const DRAG = new Set();        // keys under the user's hand: ignore their echoes

const CONTROLS = [
  { k: "altitude_m", label: "Altitude", unit: "m", fmt: (v) => Math.round(v).toLocaleString("en-US"),
    ticks: [[0, "SL"], [3500, "Leh"], [5500, "Siachen"]] },
  { k: "temp_offset_K", label: "ISA departure", unit: "K", fmt: signed },
  { k: "wind_ms", label: "Mean wind", unit: "m/s", fmt: (v) => v.toFixed(1) },
  { k: "visibility_km", label: "Visibility", unit: "km", fmt: (v) => v.toFixed(1) },
  { k: "range_m", label: "Engagement range", unit: "m", fmt: (v) => Math.round(v).toLocaleString("en-US") },
];
const TECH = {
  boresight_cal: "boresight calibration", cable_ff: "cable torque feedforward",
  gain_schedule: "gain scheduling", wind_observer: "wind disturbance observer",
  gyro_bias: "gyro bias estimation",
};
const STATUS = { ok: ["✓", "NOMINAL"], degraded: ["▲", "DEGRADED"], critical: ["✕", "CRITICAL"] };

function signed(v) { return v > 0 ? `+${v}` : v < 0 ? `−${Math.abs(v)}` : "0"; }
function urad(v) { return `${Math.round(v)}`; }
function niceCeil(v) {
  if (!(v > 0)) return 1;
  const p = 10 ** Math.floor(Math.log10(v));
  for (const m of [1, 2, 2.5, 5, 10]) if (v <= m * p) return m * p;
  return 10 * p;
}
const halfAngle = () => (TARGET_M / 2) / S.env.range_m * 1e6;       // urad
const activeKey = () => (S.env && S.env.compensate ? "comp" : "uncomp");
const coreFresh = () => S.core && S.env && sameKey(S.core.key, [S.env.altitude_m, S.env.temp_offset_K, S.env.wind_ms]);
function sameKey(a, b) { return a && b && a.length === b.length && a.every((v, i) => Math.abs(v - b[i]) < 1e-6); }
function el(tag, attrs = {}, parent) {
  const e = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v);
  if (parent) parent.appendChild(e);
  return e;
}

// --- bus ---------------------------------------------------------------------

let ws = null, retry = 0, pending = {}, flush = null;

function connect() {
  ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws`);
  ws.onopen = () => { retry = 0; chip("chip-bus", "on", "live"); };
  ws.onclose = () => {
    chip("chip-bus", "warn", "reconnecting");
    setTimeout(connect, Math.min(4000, 300 * 2 ** retry++));
  };
  ws.onmessage = (e) => handle(JSON.parse(e.data));
}

function send(patch) {
  Object.assign(pending, patch);
  if (flush) return;
  flush = setTimeout(() => {
    flush = null;
    if (ws && ws.readyState === 1) ws.send(JSON.stringify({ type: "set", env: pending }));
    pending = {};
  }, 40);
}

function handle(msg) {
  switch (msg.type) {
    case "env": {
      const local = S.env || {};
      S.env = { ...msg.env };
      for (const k of DRAG) if (k in local) S.env[k] = local[k];
      Object.assign(S, { key: msg.key, atm: msg.atm, health: msg.health, endurance: msg.endurance, eo: msg.eo });
      renderEnv(); renderHealth(); renderEO(); renderPoint(); renderSweep(); renderAblation();
      break;
    }
    case "core":
      S.core = msg;
      S.off = { uncomp: Float32Array.from(msg.uncomp.trace, (v) => -v),
                comp: Float32Array.from(msg.comp.trace, (v) => -v) };
      S.p98 = Math.max(p98(S.off.uncomp), p98(S.off.comp));
      renderPoint(); renderSweep();
      break;
    case "ablation": S.ablation = msg; renderAblation(); break;
    case "sweep": S.sweep = msg; renderSweep(); break;
    case "status": S.status = msg; renderStatus(); renderEO(); break;
  }
}

function chip(id, cls, text) {
  const c = $(id);
  c.classList.remove("on", "off", "warn");
  c.classList.add(cls);
  c.querySelector("b").textContent = text;
}

// --- environment ----------------------------------------------------------------

function buildControls() {
  const box = $("controls");
  for (const c of CONTROLS) {
    const lim = S.limits[c.k];
    const wrap = document.createElement("div");
    wrap.className = "ctl";
    wrap.innerHTML = `<div class="ctl-top"><label for="in-${c.k}">${c.label}</label>
      <output id="out-${c.k}" for="in-${c.k}"></output></div>
      <input type="range" id="in-${c.k}" min="${lim.min}" max="${lim.max}" step="${lim.step}">`;
    if (c.ticks) {
      const t = document.createElement("div");
      t.className = "ticks";
      for (const [v, name] of c.ticks) {
        const s = document.createElement("span");
        s.style.left = `${((v - lim.min) / (lim.max - lim.min)) * 100}%`;
        s.textContent = name;
        t.appendChild(s);
      }
      wrap.appendChild(t);
    }
    box.appendChild(wrap);
    const input = wrap.querySelector("input");
    input.addEventListener("pointerdown", () => DRAG.add(c.k));
    const release = () => setTimeout(() => DRAG.delete(c.k), 250);
    input.addEventListener("pointerup", release);
    input.addEventListener("pointercancel", release);
    input.addEventListener("input", () => {
      const v = parseFloat(input.value);
      S.env[c.k] = v;
      renderEnv(); renderPoint(); renderSweep(); renderEO();
      send({ [c.k]: v });
    });
  }
  const pbox = $("presets");
  S.scenarios.forEach((s, i) => {
    const b = document.createElement("button");
    b.className = "preset";
    b.dataset.key = s.key;
    b.setAttribute("aria-pressed", "false");
    b.innerHTML = `<span class="pn">${s.name.split(",")[0]}</span>
      <span class="pa">${s.altitude_m.toLocaleString("en-US")} m &middot; ${signed(s.temp_offset_K)} K &middot; ${s.wind_ms} m/s</span><kbd>${i + 1}</kbd>`;
    b.addEventListener("click", () => preset(s.key));
    pbox.appendChild(b);
  });
}

function preset(key) { if (ws && ws.readyState === 1) ws.send(JSON.stringify({ type: "preset", key })); }
function setCompensate(on) {
  if (!S.env) return;
  S.env.compensate = on;
  renderPoint(); renderHealth();
  send({ compensate: on });
}

function renderEnv() {
  if (!S.env || !S.limits) return;
  for (const c of CONTROLS) {
    const input = $(`in-${c.k}`), lim = S.limits[c.k], v = S.env[c.k];
    if (!DRAG.has(c.k)) input.value = v;
    input.style.setProperty("--p", `${((v - lim.min) / (lim.max - lim.min)) * 100}%`);
    input.setAttribute("aria-valuetext", `${c.fmt(v)} ${c.unit}`);
    $(`out-${c.k}`).innerHTML = `${c.fmt(v)}<small>${c.unit}</small>`;
  }
  for (const b of $("presets").children) {
    const s = S.scenarios.find((x) => x.key === b.dataset.key);
    const on = s && s.altitude_m === S.env.altitude_m && s.temp_offset_K === S.env.temp_offset_K && s.wind_ms === S.env.wind_ms;
    b.setAttribute("aria-pressed", String(!!on));
  }
  if (S.atm) {
    $("a-t").textContent = `${S.atm.temp_C.toFixed(1)} °C`;
    $("a-p").textContent = `${S.atm.pressure_kPa.toFixed(1)} kPa`;
    $("a-r").textContent = S.atm.density_ratio.toFixed(3);
  }
  $("b-unc").setAttribute("aria-pressed", String(!S.env.compensate));
  $("b-cmp").setAttribute("aria-pressed", String(!!S.env.compensate));
}

// --- health + endurance ------------------------------------------------------------

function renderHealth() {
  if (!S.env) return;
  $("health").classList.toggle("comp-on", !!S.env.compensate);
  const ul = $("health-list");
  ul.replaceChildren(...S.health.map((r) => {
    const li = document.createElement("li");
    const [ico, word] = STATUS[r.status];
    li.className = `s-${r.status}`;
    li.innerHTML = `<span class="ico" aria-hidden="true">${ico}</span>
      <span class="lbl">${r.label}</span><span class="val">${r.text}</span>
      <span class="sub"><span>${r.note}</span><span class="st">${word}</span></span>
      ${r.comp ? `<span class="comp">↺ compensated &middot; ${TECH[r.comp]}${r.residual ? ` &middot; ${r.residual}` : ""}</span>` : ""}`;
    return li;
  }));
  const e = S.endurance;
  if (e) {
    $("en-v").innerHTML = `${Math.round(e.minutes)}<small>min of ${e.rated_min}</small>`;
    $("en-bar").style.width = `${Math.min(100, e.fraction * 100)}%`;
    $("en-s").textContent = `cold-soaked Li-ion at ${S.atm.temp_C.toFixed(0)} °C: ${Math.round(e.fraction * 100)}% capacity`;
  }
}

// --- links + EO ----------------------------------------------------------------------

function renderStatus() {
  const st = S.status;
  if (!st) return;
  if (!st.eo.url) chip("chip-eo", "off", "probe off");
  else chip("chip-eo", st.eo.online ? "on" : "off", st.eo.online ? "live" : "offline");
  chip("chip-sdr", st.sdr.present ? "on" : "off", st.sdr.present ? `rx ${st.sdr.id}` : "not attached");
  $("chip-view").querySelector("b").textContent = st.viewers;
  $("chip-view").lastChild.textContent = st.viewers === 1 ? " VIEWER" : " VIEWERS";
}

let feedUrl = null;
function renderEO() {
  if (S.eo && S.env) {
    $("eo-r").textContent = `${Math.round(S.eo.range_m).toLocaleString("en-US")} m`;
    $("eo-rs").textContent = `of ${S.eo.clear_m.toLocaleString("en-US")} m clear air`;
    $("eo-v").textContent = `${S.env.visibility_km.toFixed(1)} km`;
  }
  const st = S.status, feed = $("feed");
  const url = st && st.eo.online ? st.eo.url : null;
  if (url === feedUrl && feed.childElementCount) return;
  feedUrl = url;
  if (url) {
    feed.innerHTML = `<img alt="Live EO feed from the Pi"><span class="live"><i></i>LIVE</span>`;
    feed.querySelector("img").src = url;
  } else {
    const why = st && !st.eo.url ? "EO probe disabled (--no-eo)" : "Pi not reachable on the USB link";
    feed.innerHTML = `<div class="off"><b>EO NODE OFFLINE</b><span class="note" style="margin:0">${why}</span>
      <code>python3 -m aura.eo.sender --http</code>${st && st.eo.url ? `<code>${st.eo.url}</code>` : ""}</div>`;
  }
}

// --- pointing: stats + trace --------------------------------------------------------

function onTarget(off, half) {
  let n = 0;
  for (const v of off) if (Math.abs(v) <= half) n++;
  return n / off.length;
}

function renderPoint() {
  if (!S.env) return;
  const fresh = coreFresh();
  $("point").classList.toggle("stale", !fresh);
  $("busy-core").classList.toggle("show", !fresh);
  if (!S.core) return;
  const half = halfAngle(), act = activeKey(), other = act === "comp" ? "uncomp" : "comp";
  const R = S.env.range_m, u = S.core.uncomp.rms, c = S.core.comp.rms;
  const tot = onTarget(S.off[act], half), totOther = onTarget(S.off[other], half);
  $("k-tot").textContent = `On target · ${Math.round(R)} m`;
  $("v-tot").innerHTML = `${Math.round(tot * 100)}<small>% of pass</small>`;
  $("s-tot").textContent = `${other === "comp" ? "compensated" : "uncompensated"}: ${Math.round(totOther * 100)}% · 0.3 m drone`;
  $("v-unc").innerHTML = `${urad(u)}<small>µrad</small>`;
  $("v-cmp").innerHTML = `${urad(c)}<small>µrad</small>`;
  $("s-gain").textContent = c < u ? `${(u / c).toFixed(1)}× lower` : `${(c / u).toFixed(1)}× higher`;
  const miss = (act === "comp" ? c : u) * 1e-6 * R * 100;
  $("k-miss").textContent = `RMS miss · ${Math.round(R)} m`;
  $("v-miss").innerHTML = `${miss.toFixed(1)}<small>cm</small>`;
  $("s-miss").textContent = `drone half-width ${(TARGET_M * 50).toFixed(0)} cm`;
  $("band-lbl").textContent = `on target: 0.3 m drone at ${Math.round(R)} m (±${Math.round(half)} µrad)`;
  drawTrace();
}

function drawTrace() {
  const svg = $("trace");
  const W = svg.parentElement.clientWidth || 600, H = W < 520 ? 116 : 124;
  const L = 44, Rm = 8, T = 18, B = 22, iw = W - L - Rm, ih = H - T - B;
  svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
  svg.replaceChildren();
  if (!S.core) return;
  const half = halfAngle(), dt = S.core.uncomp.dt, n = S.off.uncomp.length, dur = n * dt;
  let m = half * 1.3;
  for (const k of ["uncomp", "comp"]) for (const v of S.off[k]) m = Math.max(m, Math.abs(v));
  const Y = niceCeil(m);
  const x = (t) => L + (t / dur) * iw, y = (v) => T + ih / 2 - (v / Y) * (ih / 2);
  for (const v of [-Y, -Y / 2, 0, Y / 2, Y]) {
    el("line", { x1: L, x2: L + iw, y1: y(v), y2: y(v), stroke: v === 0 ? "#3A5268" : "#1C2833", "stroke-width": 1 }, svg);
    el("text", { x: L - 6, y: y(v) + 3.5, "text-anchor": "end" }, svg).textContent = v === 0 ? "0" : `${v > 0 ? "+" : "−"}${Math.abs(v)}`;
  }
  el("text", { x: L, y: T - 8, "text-anchor": "start" }, svg).textContent = "aim offset, µrad";
  for (let t = 0; t <= dur + 1e-9; t += 5) el("text", { x: x(t), y: H - 6, "text-anchor": "middle" }, svg).textContent = `${t}s`;
  el("rect", { x: L, width: iw, y: y(half), height: Math.max(1, y(-half) - y(half)), fill: "rgba(157,176,192,.10)" }, svg);
  for (const v of [half, -half]) el("line", { x1: L, x2: L + iw, y1: y(v), y2: y(v), stroke: "#3A5268", "stroke-dasharray": "3 3" }, svg);
  const act = activeKey();
  for (const k of act === "comp" ? ["uncomp", "comp"] : ["comp", "uncomp"]) {
    const off = S.off[k];
    let d = "";
    for (let i = 0; i < n; i++) d += `${i ? "L" : "M"}${x(i * dt).toFixed(1)} ${y(off[i]).toFixed(1)}`;
    el("path", { d, fill: "none", stroke: k === "comp" ? "#2A9BCB" : "#D9772F", "stroke-width": 2,
      "stroke-linejoin": "round", opacity: k === act ? 1 : 0.35 }, svg);
  }
  const ph = el("line", { id: "trace-ph", y1: T, y2: T + ih, stroke: "#9DB0C0", "stroke-width": 1, opacity: 0.7 }, svg);
  const cx = el("line", { y1: T, y2: T + ih, stroke: "#62778A", "stroke-dasharray": "2 3", opacity: 0 }, svg);
  const hit = el("rect", { x: L, y: T, width: iw, height: ih, fill: "transparent" }, svg);
  const tip = $("trace-tip");
  const move = (ev) => {
    const r = svg.getBoundingClientRect(), px = ((ev.clientX - r.left) / r.width) * W;
    const i = Math.max(0, Math.min(n - 1, Math.round(((px - L) / iw) * n)));
    cx.setAttribute("x1", x(i * dt)); cx.setAttribute("x2", x(i * dt)); cx.setAttribute("opacity", 1);
    tip.innerHTML = `<div class="th">t = ${(i * dt).toFixed(2)} s · aim offset</div>
      <div class="tr"><i style="background:var(--unc)"></i>Uncompensated<b>${urad(S.off.uncomp[i])} µrad</b></div>
      <div class="tr"><i style="background:var(--cmp)"></i>Compensated<b>${urad(S.off.comp[i])} µrad</b></div>`;
    tip.style.opacity = 1;
    const fx = (x(i * dt) / W) * r.width;
    tip.style.left = `${Math.min(Math.max(fx + 12, 0), r.width - 230)}px`;
    tip.style.top = "26px";
  };
  hit.addEventListener("pointermove", move);
  hit.addEventListener("pointerleave", () => { tip.style.opacity = 0; cx.setAttribute("opacity", 0); });
  drawTrace.ph = { el: ph, x, dur };
}

// --- sweep ------------------------------------------------------------------------

function renderSweep() {
  const svg = $("sweep");
  const sw = S.sweep;
  const fresh = sw && S.env && sameKey(sw.key, [S.env.temp_offset_K, S.env.wind_ms]);
  $("busy-sweep").classList.toggle("show", !!S.env && !fresh);
  if (S.env) $("sweep-sub").textContent = `ISA ${signed(S.env.temp_offset_K)} K · wind ${S.env.wind_ms.toFixed(1)} m/s`;
  const W = svg.parentElement.clientWidth || 600, H = W < 520 ? 160 : 168;
  const L = 44, Rm = 64, T = 22, B = 26, iw = W - L - Rm, ih = H - T - B;
  svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
  svg.replaceChildren();
  if (!sw) return;
  svg.style.opacity = fresh ? 1 : 0.45;
  const A = sw.altitudes, amax = A[A.length - 1];
  const Y = niceCeil(Math.max(...sw.uncomp, ...sw.comp) * 1.08);
  const x = (a) => L + (a / amax) * iw, y = (v) => T + ih - (v / Y) * ih;
  for (let k = 0; k <= 4; k++) {
    const v = (Y * k) / 4;
    el("line", { x1: L, x2: L + iw, y1: y(v), y2: y(v), stroke: k ? "#1C2833" : "#3A5268" }, svg);
    el("text", { x: L - 6, y: y(v) + 3.5, "text-anchor": "end" }, svg).textContent = Math.round(v);
  }
  el("text", { x: L, y: T - 10, "text-anchor": "start" }, svg).textContent = "RMS pointing error, µrad";
  for (let a = 0; a <= amax; a += 1000) el("text", { x: x(a), y: H - 8, "text-anchor": "middle" }, svg).textContent = a ? `${a / 1000}k m` : "0";
  if (S.env) {
    const ax = x(S.env.altitude_m);
    el("line", { x1: ax, x2: ax, y1: T, y2: T + ih, stroke: "#9DB0C0", "stroke-dasharray": "3 3", opacity: 0.6 }, svg);
    el("text", { x: ax + 4, y: T + 10, fill: "#9DB0C0" }, svg).textContent = `SITE ${Math.round(S.env.altitude_m).toLocaleString("en-US")} m`;
  }
  for (const [k, col] of [["uncomp", "#D9772F"], ["comp", "#2A9BCB"]]) {
    const vals = sw[k];
    el("path", { d: vals.map((v, i) => `${i ? "L" : "M"}${x(A[i]).toFixed(1)} ${y(v).toFixed(1)}`).join(""),
      fill: "none", stroke: col, "stroke-width": 2, "stroke-linejoin": "round" }, svg);
    vals.forEach((v, i) => el("circle", { cx: x(A[i]), cy: y(v), r: 3.5, fill: col, stroke: "#0D141B", "stroke-width": 2 }, svg));
    const lv = vals[vals.length - 1];
    el("text", { x: x(amax) + 9, y: y(lv) + 3.5, fill: "#9DB0C0" }, svg).textContent =
      `${k === "comp" ? "COMP" : "UNCOMP"} ${Math.round(lv)}`;
  }
  if (coreFresh() && fresh) {
    for (const [k, col] of [["uncomp", "#D9772F"], ["comp", "#2A9BCB"]])
      el("circle", { cx: x(S.env.altitude_m), cy: y(S.core[k].rms), r: 5.5, fill: "#0D141B", stroke: col, "stroke-width": 2.5 }, svg);
  }
  const cx = el("line", { y1: T, y2: T + ih, stroke: "#62778A", "stroke-dasharray": "2 3", opacity: 0 }, svg);
  const hit = el("rect", { x: L, y: T, width: iw, height: ih, fill: "transparent" }, svg);
  const tip = $("sweep-tip");
  hit.addEventListener("pointermove", (ev) => {
    const r = svg.getBoundingClientRect(), px = ((ev.clientX - r.left) / r.width) * W;
    let i = 0;
    A.forEach((a, j) => { if (Math.abs(x(a) - px) < Math.abs(x(A[i]) - px)) i = j; });
    cx.setAttribute("x1", x(A[i])); cx.setAttribute("x2", x(A[i])); cx.setAttribute("opacity", 1);
    tip.innerHTML = `<div class="th">${A[i].toLocaleString("en-US")} m</div>
      <div class="tr"><i style="background:var(--unc)"></i>Uncompensated<b>${urad(sw.uncomp[i])} µrad</b></div>
      <div class="tr"><i style="background:var(--cmp)"></i>Compensated<b>${urad(sw.comp[i])} µrad</b></div>`;
    tip.style.opacity = 1;
    const fx = (x(A[i]) / W) * r.width;
    tip.style.left = `${Math.min(Math.max(fx + 12, 0), r.width - 230)}px`;
    tip.style.top = "30px";
  });
  hit.addEventListener("pointerleave", () => { tip.style.opacity = 0; cx.setAttribute("opacity", 0); });
  $("sweep-table").innerHTML = `<tr><th>altitude</th><th>uncomp µrad</th><th>comp µrad</th></tr>` +
    A.map((a, i) => `<tr><td>${a} m</td><td>${urad(sw.uncomp[i])}</td><td>${urad(sw.comp[i])}</td></tr>`).join("");
}

// --- ablation ------------------------------------------------------------------------

function renderAblation() {
  const ab = S.ablation;
  const fresh = ab && S.env && sameKey(ab.key, [S.env.altitude_m, S.env.temp_offset_K, S.env.wind_ms]);
  $("busy-abl").classList.toggle("show", !!S.env && !fresh);
  const box = $("abl");
  box.style.opacity = fresh ? 1 : 0.45;
  if (!ab) return;
  $("abl-sum").innerHTML = `All five at this site: <b>${urad(ab.uncomp)} → ${urad(ab.full)} µrad</b>`;
  const M = niceCeil(Math.max(1, ...ab.items.map((it) => Math.abs(it.cost))));
  box.replaceChildren(...ab.items.map((it) => {
    const row = document.createElement("div");
    row.className = "row";
    const c = it.cost, w = (Math.abs(c) / M) * 50;
    const flat = Math.abs(c) < 1;
    const why = flat ? "no measurable effect at this site"
      : c > 0 ? `removing it adds ${urad(c)} µrad` : `removing it cuts ${urad(-c)} µrad — it hurts here`;
    row.innerHTML = `<div class="top"><span>${it.label}</span><b>${c >= 0 ? "+" : "−"}${urad(Math.abs(c))} µrad</b></div>
      <div class="track" title="${it.label}: RMS ${urad(it.rms_without)} µrad without it, ${urad(ab.full)} with all five">
        <span class="fill ${c >= 0 ? "help" : "hurt"}" style="left:${c >= 0 ? 50 : 50 - w}%;width:${Math.max(flat ? 0 : 0.6, w)}%"></span></div>
      <div class="why">${why}</div>`;
    return row;
  }));
}

// --- scope ------------------------------------------------------------------------

const scope = { t: 0, playing: true, last: 0, S: null };

function frame(now) {
  const dt = scope.last ? (now - scope.last) / 1000 : 0;
  scope.last = now;
  if (S.core && S.off) {
    const dur = S.off.uncomp.length * S.core.uncomp.dt;
    if (scope.playing) scope.t = (scope.t + dt) % dur;
    const ph = drawTrace.ph;
    if (ph && ph.el.isConnected) { const px = ph.x(scope.t); ph.el.setAttribute("x1", px); ph.el.setAttribute("x2", px); }
  }
  drawScope();
  requestAnimationFrame(frame);
}

function drawScope() {
  const cv = $("scope"), dpr = Math.min(window.devicePixelRatio || 1, 2);
  const w = Math.round(cv.clientWidth * dpr), h = Math.round(cv.clientHeight * dpr);
  if (!w || !h) return;
  if (cv.width !== w || cv.height !== h) { cv.width = w; cv.height = h; }
  const g = cv.getContext("2d");
  const cx = w / 2, cy = h / 2, R = Math.min(w, h) / 2 - 20 * dpr, u = dpr;
  g.clearRect(0, 0, w, h);

  const bg = g.createRadialGradient(cx, cy, R * 0.1, cx, cy, R);
  bg.addColorStop(0, "#0E1720"); bg.addColorStop(1, "#080D12");
  g.fillStyle = bg; g.beginPath(); g.arc(cx, cy, R, 0, Math.PI * 2); g.fill();

  // bezel: 5-degree ticks, heavier every 45
  g.strokeStyle = "#26384A";
  for (let d = 0; d < 360; d += 5) {
    const a = (d * Math.PI) / 180, long = d % 45 === 0;
    g.lineWidth = long ? 1.5 * u : 1 * u;
    g.beginPath();
    g.moveTo(cx + Math.cos(a) * (R + 2 * u), cy + Math.sin(a) * (R + 2 * u));
    g.lineTo(cx + Math.cos(a) * (R + (long ? 12 : 6) * u), cy + Math.sin(a) * (R + (long ? 12 : 6) * u));
    g.stroke();
  }

  if (!S.core || !S.off || !S.env) {
    ring(g, cx, cy, R, u);
    g.fillStyle = "#62778A"; g.font = `${12 * u}px "IBM Plex Mono", monospace`; g.textAlign = "center";
    g.fillText("AWAITING SIM", cx, cy + 4 * u);
    return;
  }

  const half = halfAngle(), act = activeKey(), other = act === "comp" ? "uncomp" : "comp";
  const dt = S.core.uncomp.dt, n = S.off.uncomp.length;
  const i = Math.min(n - 1, Math.floor(scope.t / dt));
  const goal = niceCeil(Math.max(S.p98 * 1.15, half * 2.4));
  scope.S = scope.S ? scope.S + (goal - scope.S) * 0.12 : goal;
  const Sc = scope.S, px = (v) => (v / Sc) * R;

  ring(g, cx, cy, R, u);
  // ring labels along the 3 o'clock axis
  g.fillStyle = "#62778A"; g.font = `${10 * u}px "IBM Plex Mono", monospace`; g.textAlign = "center";
  for (const k of [2, 4]) g.fillText(`${Math.round((Sc * k) / 4)}`, cx + (R * k) / 4 - 14 * u, cy - 5 * u);
  g.fillText("µrad", cx + R - 14 * u, cy + 14 * u);

  // target footprint + drone glyph
  const rh = Math.max(px(half), 2 * u);
  g.save(); g.beginPath(); g.arc(cx, cy, R, 0, Math.PI * 2); g.clip();
  g.fillStyle = "rgba(157,176,192,.09)"; g.strokeStyle = "#3A5268"; g.setLineDash([4 * u, 3 * u]); g.lineWidth = 1 * u;
  g.beginPath(); g.arc(cx, cy, rh, 0, Math.PI * 2); g.fill(); g.stroke(); g.setLineDash([]);
  drone(g, cx, cy, Math.max(rh, 12 * u), u);

  // inactive series: ghost ring
  const xo = cx + clampR(px(S.off[other][i]), R);
  g.strokeStyle = other === "comp" ? "rgba(42,155,203,.55)" : "rgba(217,119,47,.55)"; g.lineWidth = 2 * u;
  g.beginPath(); g.arc(xo, cy, 6 * u, 0, Math.PI * 2); g.stroke();

  // active series: trail falling away over the last 2 s, then the aim mark
  const col = act === "comp" ? [42, 155, 203] : [217, 119, 47], off = S.off[act];
  const span = Math.min(i, Math.round(2 / dt));
  for (let j = span; j > 0; j -= 1) {
    const a = 1 - j / span;
    g.strokeStyle = `rgba(${col},${(a * 0.75).toFixed(3)})`; g.lineWidth = (1 + a * 1.5) * u;
    g.beginPath();
    g.moveTo(cx + clampR(px(off[i - j]), R), cy + (j / span) * R * 0.85);
    g.lineTo(cx + clampR(px(off[i - j + 1]), R), cy + ((j - 1) / span) * R * 0.85);
    g.stroke();
  }
  g.restore();
  const xa = cx + clampR(px(off[i]), R);
  g.fillStyle = `rgb(${col})`; g.strokeStyle = "#080D12"; g.lineWidth = 2 * u;
  g.beginPath(); g.arc(xa, cy, 7 * u, 0, Math.PI * 2); g.fill(); g.stroke();
  g.strokeStyle = `rgb(${col})`; g.lineWidth = 1.5 * u;
  g.beginPath(); g.moveTo(xa, cy - 18 * u); g.lineTo(xa, cy - 10 * u); g.moveTo(xa, cy + 10 * u); g.lineTo(xa, cy + 18 * u); g.stroke();

  // HUD
  const on = Math.abs(off[i]) <= half;
  const pad = 14 * u, big = `600 ${15 * u}px "IBM Plex Mono", monospace`, small = `${10 * u}px "IBM Plex Mono", monospace`;
  g.textAlign = "left"; g.fillStyle = "#62778A"; g.font = small; g.fillText("AIM OFFSET", pad, pad + 8 * u);
  g.fillStyle = "#E4EDF3"; g.font = big;
  g.fillText(`${off[i] >= 0 ? "+" : "−"}${Math.abs(Math.round(off[i]))} µrad`, pad, pad + 27 * u);
  g.textAlign = "right"; g.fillStyle = "#62778A"; g.font = small; g.fillText("PASS", w - pad, pad + 8 * u);
  g.fillStyle = "#E4EDF3"; g.font = big; g.fillText(`${scope.t.toFixed(1)} / ${(n * dt).toFixed(0)} s`, w - pad, pad + 27 * u);
  g.textAlign = "left"; g.font = big; g.fillStyle = on ? "#5DB98A" : "#E35757";
  g.fillText(on ? "✓ ON TARGET" : "✕ OFF TARGET", pad, h - pad);
  g.textAlign = "right"; g.fillStyle = "#62778A"; g.font = small;
  g.fillText(`0.3 m drone @ ${Math.round(S.env.range_m)} m`, w - pad, h - pad);
  if (!scope.playing) { g.textAlign = "center"; g.fillStyle = "#9DB0C0"; g.fillText("PAUSED", cx, pad + 8 * u); }
}

function ring(g, cx, cy, R, u) {
  g.strokeStyle = "#1C2833"; g.lineWidth = 1 * u;
  for (const k of [1, 2, 3]) { g.beginPath(); g.arc(cx, cy, (R * k) / 4, 0, Math.PI * 2); g.stroke(); }
  g.strokeStyle = "#3A5268"; g.lineWidth = 1.5 * u;
  g.beginPath(); g.arc(cx, cy, R, 0, Math.PI * 2); g.stroke();
  g.strokeStyle = "#26384A"; g.lineWidth = 1 * u;
  g.beginPath(); g.moveTo(cx - R, cy); g.lineTo(cx + R, cy); g.moveTo(cx, cy - R); g.lineTo(cx, cy + R); g.stroke();
  for (let k = -8; k <= 8; k++) {
    if (!k) continue;
    const x = cx + (R * k) / 8, l = (k % 2 ? 3 : 6) * u;
    g.beginPath(); g.moveTo(x, cy - l); g.lineTo(x, cy + l); g.stroke();
  }
}

function drone(g, cx, cy, s, u) {
  // s = footprint radius; arm tip + rotor reach 0.93 s on the diagonal, so the glyph sits inside it
  const arm = s * 0.5, rot = s * 0.22;
  g.strokeStyle = "#C9D6E0"; g.lineWidth = Math.max(1, 1.4 * u);
  g.beginPath();
  g.moveTo(cx - arm, cy - arm); g.lineTo(cx + arm, cy + arm);
  g.moveTo(cx + arm, cy - arm); g.lineTo(cx - arm, cy + arm);
  g.stroke();
  for (const [dx, dy] of [[-1, -1], [1, -1], [1, 1], [-1, 1]]) {
    g.beginPath(); g.arc(cx + dx * arm, cy + dy * arm, rot, 0, Math.PI * 2); g.stroke();
  }
  g.fillStyle = "#C9D6E0"; g.fillRect(cx - s * 0.12, cy - s * 0.12, s * 0.24, s * 0.24);
}

function clampR(v, R) { return Math.max(-R, Math.min(R, v)); }
function p98(arr) {
  const a = Array.from(arr, Math.abs).sort((p, q) => p - q);
  return a[Math.floor(a.length * 0.98)] || 0;
}

// --- terrain: contour lines of a seeded noise field, the Himalaya as texture ---

function drawTerrain() {
  const cv = $("terrain"), dpr = Math.min(window.devicePixelRatio || 1, 2);
  const W = window.innerWidth, H = window.innerHeight;
  cv.width = W * dpr; cv.height = H * dpr;
  const g = cv.getContext("2d");
  g.setTransform(dpr, 0, 0, dpr, 0, 0);
  let seed = 5808;
  const rnd = () => ((seed = (seed * 1664525 + 1013904223) >>> 0) / 4294967296);
  const N = 64, lat = Float32Array.from({ length: N * N }, rnd);
  const sm = (t) => t * t * (3 - 2 * t);
  const noise = (x, y) => {
    const xi = Math.floor(x), yi = Math.floor(y), xf = sm(x - xi), yf = sm(y - yi);
    const at = (i, j) => lat[((j & (N - 1)) * N) + (i & (N - 1))];
    const a = at(xi, yi), b = at(xi + 1, yi), c = at(xi, yi + 1), d = at(xi + 1, yi + 1);
    return a + (b - a) * xf + (c - a) * yf + (a - b - c + d) * xf * yf;
  };
  const field = (x, y) => {
    let v = 0, amp = 0.55, f = 1 / 420;
    for (let o = 0; o < 4; o++) { v += amp * noise(x * f, y * f); amp *= 0.5; f *= 2.1; }
    return v + (1 - y / H) * 0.18;                      // higher ground toward the top
  };
  const cell = 12, nx = Math.ceil(W / cell) + 1, ny = Math.ceil(H / cell) + 1;
  const F = new Float32Array(nx * ny);
  for (let j = 0; j < ny; j++) for (let i = 0; i < nx; i++) F[j * nx + i] = field(i * cell, j * cell);
  const lerp = (a, b, lv) => (lv - a) / (b - a || 1e-9);
  for (let L = 0, lv = 0.2; lv < 1.0; lv += 0.035, L++) {
    g.strokeStyle = L % 5 === 0 ? "rgba(110,150,180,.11)" : "rgba(110,150,180,.05)";
    g.lineWidth = L % 5 === 0 ? 1.1 : 0.8;
    g.beginPath();
    for (let j = 0; j < ny - 1; j++) for (let i = 0; i < nx - 1; i++) {
      const a = F[j * nx + i], b = F[j * nx + i + 1], c = F[(j + 1) * nx + i + 1], d = F[(j + 1) * nx + i];
      const idx = (a > lv) | ((b > lv) << 1) | ((c > lv) << 2) | ((d > lv) << 3);
      if (idx === 0 || idx === 15) continue;
      const x0 = i * cell, y0 = j * cell;
      const e = [
        [x0 + lerp(a, b, lv) * cell, y0], [x0 + cell, y0 + lerp(b, c, lv) * cell],
        [x0 + lerp(d, c, lv) * cell, y0 + cell], [x0, y0 + lerp(a, d, lv) * cell],
      ];
      const segs = { 1: [[3, 0]], 2: [[0, 1]], 3: [[3, 1]], 4: [[1, 2]], 5: [[3, 0], [1, 2]], 6: [[0, 2]], 7: [[3, 2]],
        8: [[2, 3]], 9: [[0, 2]], 10: [[0, 1], [2, 3]], 11: [[1, 2]], 12: [[1, 3]], 13: [[0, 1]], 14: [[0, 3]] }[idx];
      for (const [p, q] of segs) { g.moveTo(e[p][0], e[p][1]); g.lineTo(e[q][0], e[q][1]); }
    }
    g.stroke();
  }
}

// --- boot ---------------------------------------------------------------------------

function keys(e) {
  if (e.ctrlKey || e.metaKey || e.altKey) return;
  if (e.key === "c" || e.key === "C") setCompensate(!(S.env && S.env.compensate));
  else if (e.key >= "1" && e.key <= "9" && S.scenarios[+e.key - 1]) preset(S.scenarios[+e.key - 1].key);
  else if (e.key === " " && !(e.target instanceof HTMLButtonElement)) { e.preventDefault(); scope.playing = !scope.playing; }
}

async function boot() {
  [S.limits, S.scenarios] = await Promise.all([
    fetch("/api/limits").then((r) => r.json()), fetch("/api/scenarios").then((r) => r.json()),
  ]);
  buildControls();
  $("b-unc").addEventListener("click", () => setCompensate(false));
  $("b-cmp").addEventListener("click", () => setCompensate(true));
  document.addEventListener("keydown", keys);
  const tick = () => { $("clock").textContent = new Date().toLocaleTimeString("en-GB"); };
  tick(); setInterval(tick, 1000);
  drawTerrain();
  let rz;
  window.addEventListener("resize", () => { clearTimeout(rz); rz = setTimeout(drawTerrain, 200); });
  new ResizeObserver(() => { drawTrace(); renderSweep(); }).observe($("point"));
  connect();
  requestAnimationFrame(frame);
}

boot();
