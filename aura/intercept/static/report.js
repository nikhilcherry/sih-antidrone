// After-action report: one fetch, one render. Same voice as the console:
// declarative, Zulu, units printed, absence stated with an em dash.
"use strict";
const $ = (id) => document.getElementById(id);
const DASH = "—";
const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;"}[c]));
const zulu = (t) => new Date(t * 1000).toISOString().slice(11, 19) + "Z";
const deg = (v) => v == null ? DASH : (v > 0 ? "+" : v < 0 ? "−" : "") + Math.abs(v).toFixed(1);
const n = (k, word) => `${k} ${word}${k === 1 ? "" : "s"}`;
let data = null;

const OUTCOME = {
  NEUTRALISED: `<span class="st contact"><i class="sh contact"></i>Neutralised</span>`,
  PASSED: `<span class="st own"><i class="sh own"></i>Passed</span>`,
  LOST: `<span class="st">${DASH} Lost</span>`,
  OPEN: `<span class="st"><i class="sh unknown"></i>Open</span>`,
};

function render(r) {
  data = r;
  const rows = r.engagements;
  const kills = rows.filter((e) => e.outcome === "NEUTRALISED");
  const passed = rows.filter((e) => e.outcome === "PASSED").length;
  const lost = rows.filter((e) => e.outcome === "LOST").length;
  const d2k = kills.map((e) => e.d2k).filter((v) => v != null);
  const best = d2k.length ? Math.min(...d2k) : null;

  $("r-session").textContent = r.session;
  $("r-period").textContent = `${zulu(r.start)} – ${zulu(r.end)}`;
  $("r-model").textContent = r.model;
  $("r-issued").textContent = new Date().toISOString().slice(0, 10) + " " + zulu(Date.now() / 1000);

  if (!rows.length) {
    $("r-statement").textContent = "No drone confirmed in this session.";
    $("r-body").textContent = `Source at close: ${r.source || DASH}.`;
  } else {
    $("r-statement").textContent = `${n(rows.length, "drone")} confirmed. ${kills.length} neutralised, ${passed} allowed to pass.`;
    $("r-body").textContent = (d2k.length
      ? `Mean detect-to-kill ${r.mean_d2k.toFixed(1)} s over ${n(d2k.length, "kill")}; fastest ${best.toFixed(1)} s. `
      : "No kill assessed. ") + `Rules of engagement at close: ${r.mode === "AUTO" ? "weapons free" : "hold"}. Source at close: ${r.source || DASH}.`;
  }

  $("s-conf").textContent = rows.length;
  $("s-pass").textContent = passed;
  $("s-kill").textContent = kills.length;
  $("s-lost").textContent = lost;
  $("s-mean").textContent = r.mean_d2k != null ? `${r.mean_d2k.toFixed(1)} s` : DASH;
  $("s-best").textContent = best != null ? `${best.toFixed(1)} s` : DASH;

  const t = (e, k) => e.record[k] ? zulu(e.record[k][0]) : DASH;
  const note = (e, k) => e.record[k] ? esc(e.record[k][1]) : DASH;
  $("r-rows").innerHTML = rows.length ? rows.map((e) => `<tr>
      <td>E${String(e.no).padStart(3, "0")}</td><td>T${e.track}</td>
      <td class="ev"><figure>${e.evidence ? `<img src="${esc(e.evidence)}" alt="Evidence frame, engagement ${e.no}">`
        : `<span class="none">${DASH}<br>no frame</span>`}</figure></td>
      <td>${t(e, "detect")}</td><td>${t(e, "confirm")}</td><td>${note(e, "identify")}</td>
      <td>${note(e, "decide")}</td><td>${t(e, "effect")}</td>
      <td class="n">${e.d2k != null ? e.d2k.toFixed(1) : DASH}</td>
      <td class="n">${deg(e.bearing)}</td><td class="n">${deg(e.elevation)}</td>
      <td>${OUTCOME[e.outcome] || esc(e.outcome)}</td></tr>`).join("")
    : `<tr class="empty"><td>${DASH}</td><td colspan="11">No engagement recorded.</td></tr>`;

  $("r-log").innerHTML = r.log.length ? r.log.map((e) =>
    `<tr><td>${zulu(e.t)}</td><td>${e.track != null ? "T" + e.track : DASH}</td>` +
    `<td class="${e.level}">${esc(e.text)}</td></tr>`).join("")
    : `<tr class="empty"><td>${DASH}</td><td>${DASH}</td><td>No entries.</td></tr>`;

  $("r-dir").textContent = `Session files: ${r.dir}`;
}

async function load() {
  render(await (await fetch("/api/report")).json());
}

$("r-json").onclick = () => {
  if (!data) return;
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob([JSON.stringify(data, null, 1)], {type: "application/json"}));
  a.download = `aura-aar-${data.session}.json`;
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
};

load();
