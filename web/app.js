const $ = s => document.querySelector(s);
const clamp = (v, a, b) => Math.min(b, Math.max(a, v));
const lerp = (a, b, t) => a + (b - a) * t;

const night = new URLSearchParams(location.search).get("night") || "demo";
const DATA_URL = night === "demo" ? "data/predictions_demo.json" : `data/predictions_${night}.json`;

let pred, geo;
try {
  [pred, geo] = await Promise.all([
    fetch(DATA_URL).then(r => { if (!r.ok) throw 0; return r.json(); }),
    fetch("data/raions.geojson").then(r => r.json()),
  ]);
} catch {
  $("#err").hidden = false;
  $("#err").textContent = `Could not load ${DATA_URL} — run the pipeline for that night, or open ?night=demo.`;
  throw new Error("data load failed");
}

const NS = pred.steps.length, N = pred.raions.length;
const key2i = new Map(pred.raions.map((r, i) => [r.key, i]));
const feats = geo.features.filter(f => key2i.has(f.properties.key));
feats.forEach(f => {
  f._i = key2i.get(f.properties.key);
  const r = pred.raions[f._i];
  f._c = [r.lon, r.lat];
});

/* ---------- state ---------- */
let horizon = pred.horizons_min[1] ?? pred.horizons_min[0];
let threshold = 0.5, pos = 0, playing = false, speedIdx = 1, anim = null;
const SPEEDS = [10, 60, 300];
const reduced = matchMedia("(prefers-reduced-motion: reduce)").matches;

/* ---------- map ---------- */
const map = new maplibregl.Map({
  container: "map",
  style: "https://tiles.openfreemap.org/styles/dark",
  center: [31, 48.8], zoom: 5,
  attributionControl: { compact: true },
});
let swapped = false;
map.on("error", () => {
  if (!swapped && !map.isStyleLoaded()) {
    swapped = true;
    map.setStyle("https://basemaps.cartocdn.com/gl/dark_matter-gl-style/style.json");
  }
});
map.fitBounds([[22.1, 44.2], [40.3, 52.4]], { padding: 24, duration: 0 });
const overlay = new deck.MapboxOverlay({ interleaved: true, layers: [] });
map.addControl(overlay);
window.__map = map; // debug/test hook

/* ---------- visuals ---------- */
function probRGB(p) { // amber -> red
  const t = Math.pow(clamp(p, 0, 1), 0.75);
  return [lerp(255, 255, t) | 0, lerp(196, 94, t) | 0, lerp(110, 58, t) | 0];
}
function probsAt(h, x) { // linear interpolation between steps
  const i = clamp(Math.floor(x), 0, NS - 2), f = x - i;
  const a = pred.steps[i].pred[h], b = pred.steps[i + 1].pred[h];
  return a.map((v, k) => v == null ? null : b[k] == null ? v : v + (b[k] - v) * f);
}

function buildLayers(now) {
  const P = probsAt(horizon, pos);
  const si = clamp(Math.round(pos), 0, NS - 1);
  const step = pred.steps[si];
  const alertSet = new Set(step.alert);
  const ph = (now * 2 * Math.PI) / 3200;
  const pulse = i => reduced ? 1 : 0.82 + 0.18 * Math.sin(ph + i * 1.7);

  const hot = [];
  for (const f of feats) {
    f._p = P[f._i]; f._al = alertSet.has(f._i);
    if (f._p != null && f._p >= threshold) hot.push(f);
  }

  return { P, si, step, layers: [
    new deck.GeoJsonLayer({ id: "base", data: feats, stroked: true, filled: true,
      lineWidthMinPixels: 0.6,
      getLineColor: f => f._al ? [255, 255, 255, 80] : [255, 255, 255, 22],
      getFillColor: f => f._p == null ? [150, 150, 160, 12]
        : [...probRGB(f._p), 6 + f._p * 66] }),
    new deck.GeoJsonLayer({ id: "glowF", data: hot, stroked: false, filled: true,
      getFillColor: f => [...probRGB(f._p), 34 * f._p * pulse(f._i)] }),
    new deck.GeoJsonLayer({ id: "glowL", data: hot, stroked: true, filled: false,
      lineWidthMinPixels: 3,
      getLineColor: f => [...probRGB(f._p), 52 * f._p * pulse(f._i)] }),
    new deck.ColumnLayer({ id: "cols", data: hot, diskResolution: 16, radius: 6500,
      extruded: true, material: false,
      getPosition: f => f._c,
      getElevation: f => 25000 + 90000 * f._p,
      getFillColor: f => [255, 238, 205, 24 * f._p * pulse(f._i)],
      parameters: { blendColorOperation: "add", blendColorSrcFactor: "src-alpha",
        blendColorDstFactor: "one" } }),
    new deck.ScatterplotLayer({ id: "onsets",
      data: (step[`onset_${horizon}`] || []).map(i => pred.raions[i]),
      stroked: true, filled: false, radiusUnits: "meters", getRadius: 10000,
      getPosition: d => [d.lon, d.lat],
      getLineColor: [255, 255, 255, 210], lineWidthMinPixels: 1.4 }),
  ]};
}

/* ---------- panel ---------- */
let lastKey = "";
function updatePanel(P, si) {
  const key = `${si}|${horizon}`;
  if (key === lastKey) return;
  lastKey = key;
  const prev = pred.steps[Math.max(0, si - 1)].pred[horizon];
  const rows = P.map((p, i) => ({ p, i })).filter(r => r.p != null)
    .sort((a, b) => b.p - a.p).slice(0, 8);
  $("#list").innerHTML = rows.map(r => {
    const d = prev[r.i] != null ? r.p - prev[r.i] : 0;
    const cls = d > 0.0005 ? "up" : d < -0.0005 ? "dn" : "";
    const txt = cls ? (d > 0 ? "+" : "−") + Math.abs(d).toFixed(2) : "·";
    return `<div class="row"><div class="nm">${pred.raions[r.i].name_ua}</div>` +
      `<div class="bar"><i style="width:${(r.p * 100).toFixed(1)}%"></i></div>` +
      `<div class="val">${r.p.toFixed(2)}</div><div class="dl ${cls}">${txt}</div></div>`;
  }).join("");
}

/* ---------- controls ---------- */
const ICONS = {
  play: '<svg viewBox="0 0 16 16"><path d="M4 2.5v11l9-5.5z"/></svg>',
  pause: '<svg viewBox="0 0 16 16"><path d="M3.5 2.5h3v11h-3zM9.5 2.5h3v11h-3z"/></svg>',
};
function syncPlay() { $("#play").innerHTML = playing ? ICONS.pause : ICONS.play; }
function setPlaying(v) {
  playing = v && pos < NS - 1;
  if (playing) anim = null;
  syncPlay();
}
function stepTo(n) {
  anim = { from: pos, to: clamp(Math.round(n), 0, NS - 1), t0: performance.now() };
  setPlaying(false);
}
function seg(el, items, cur, cb) {
  el.innerHTML = "";
  items.forEach(v => {
    const b = document.createElement("button");
    b.textContent = v.label ?? v;
    if (v === cur) b.classList.add("on");
    b.onclick = () => { cb(v); [...el.children].forEach(c => c.classList.toggle("on", c === b)); };
    el.appendChild(b);
  });
}
seg($("#horSeg"), pred.horizons_min, horizon, v => { horizon = v; });
seg($("#spdSeg"), SPEEDS, SPEEDS[speedIdx], v => { speedIdx = SPEEDS.indexOf(v); });
[...$("#spdSeg").children].forEach((b, i) => b.textContent = SPEEDS[i] + "×");

$("#play").onclick = () => setPlaying(!playing);
$("#thr").oninput = e => { threshold = +e.target.value; $("#thrV").textContent = threshold.toFixed(2); };
const scrub = $("#scrub");
scrub.max = NS - 1;
scrub.oninput = () => { pos = +scrub.value; anim = null; };

addEventListener("keydown", e => {
  if (e.code === "Space") { e.preventDefault(); setPlaying(!playing); }
  else if (e.code === "ArrowRight") { e.preventDefault(); stepTo(Math.round(pos) + 1); }
  else if (e.code === "ArrowLeft") { e.preventDefault(); stepTo(Math.round(pos) - 1); }
});

$("#nightLbl").textContent = night === "demo" ? "demo night 2025-12-12" : night;
if (pred.meta) {
  const m = pred.meta, f = o => m[o] ? pred.horizons_min.map(h => (m[o][h] ?? 0).toFixed(2)).join(" / ") : "—";
  $("#meta").textContent = `MODEL  ${m.model ?? "—"}\nPR-AUC 15/30/60  ${f("pr_auc")}\nTOP-30 HIT  ${f("top30_hit")}`;
} else $("#meta").hidden = true;

/* ---------- main loop ---------- */
const pad = n => String(n).padStart(2, "0");
let lastT = performance.now(), lastClock = "";
function tick(now) {
  const dt = (now - lastT) / 1000; lastT = now;
  if (playing) {
    pos += dt * SPEEDS[speedIdx] / (pred.step_minutes * 60);
    if (pos >= NS - 1) { pos = NS - 1; setPlaying(false); }
  } else if (anim) { // 300ms ease-out toward target step
    const k = clamp((now - anim.t0) / 300, 0, 1);
    pos = anim.from + (anim.to - anim.from) * (1 - Math.pow(1 - k, 3));
    if (k >= 1) anim = null;
  }
  const { P, si, step, layers } = buildLayers(now);
  overlay.setProps({ layers });
  updatePanel(P, si);
  if (document.activeElement !== scrub) scrub.value = pos;
  const d = new Date(step.t);
  const clk = `${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())} UTC`;
  if (clk !== lastClock) { lastClock = clk; $("#clock").textContent = clk; }
  requestAnimationFrame(tick);
}
syncPlay();
requestAnimationFrame(tick);
