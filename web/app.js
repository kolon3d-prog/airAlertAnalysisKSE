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
// resample exterior rings into evenly spaced wall points (~5 km apart, <=80/raion)
function resample(ring, out) {
  const STEP = 6, CAP = 80;
  let next = STEP / 2, d = 0;
  for (let i = 1; i < ring.length && out.length < CAP; i++) {
    const [ax, ay] = ring[i - 1], [bx, by] = ring[i];
    const len = Math.hypot((bx - ax) * 111 * Math.cos(ay * Math.PI / 180), (by - ay) * 111);
    while (d + len >= next && out.length < CAP) {
      const t = (next - d) / len;
      out.push([ax + (bx - ax) * t, ay + (by - ay) * t]);
      next += STEP;
    }
    d += len;
  }
}
feats.forEach(f => {
  const g = f.geometry;
  const rings = g.type === "Polygon" ? [g.coordinates[0]] : g.coordinates.map(p => p[0]);
  f._wall = [];
  for (const r of rings) resample(r, f._wall);
});
const CITIES = [["Kyiv", 30.52, 50.45], ["Kharkiv", 36.23, 49.99], ["Odesa", 30.73, 46.48],
  ["Dnipro", 35.05, 48.46], ["Lviv", 24.03, 49.84]].map(([n, x, y]) => ({ n, c: [x, y] }));

/* ---------- state ---------- */
let horizon = pred.horizons_min[1] ?? pred.horizons_min[0];
let threshold = 0.5, mode = "top30", pos = 0, playing = false, speedIdx = 1, anim = null;
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

  for (const f of feats) { f._p = P[f._i]; f._al = alertSet.has(f._i); }
  let hot;
  if (mode === "top30") { // rank-based: glows == top-30 eval set; intensity vs step max
    const scored = feats.filter(f => f._p != null).sort((a, b) => b._p - a._p);
    const maxP = scored.length ? scored[0]._p : 1;
    hot = scored.slice(0, 30);
    for (const f of hot) f._g = maxP > 0 ? f._p / maxP : 0;
  } else {
    hot = feats.filter(f => f._p != null && f._p >= threshold);
    for (const f of hot) f._g = f._p;
  }

  // walls: alert raions = red-amber (fill + wall), predicted hot = amber wall only
  const walls = [];
  for (const f of feats) if (f._al) for (const p of f._wall) walls.push({ p, g: 1, i: f._i, al: 1 });
  for (const f of hot) if (!f._al) for (const p of f._wall) walls.push({ p, g: f._g, i: f._i, al: 0 });

  return { P, si, step, hot, layers: [
    new deck.GeoJsonLayer({ id: "base", data: feats, stroked: true, filled: true,
      lineWidthMinPixels: 0.6,
      getLineColor: f => f._al ? [255, 120, 80, 110] : [255, 255, 255, 22],
      getFillColor: f => f._al ? [255, 90, 60, 34] : [150, 150, 160, 8] }),
    new deck.ColumnLayer({ id: "walls", data: walls, diskResolution: 10,
      radius: 1100, extruded: true, material: false,
      getPosition: d => d.p,
      getElevation: d => d.al ? 28000 : 14000 + 34000 * d.g,
      getFillColor: d => d.al
        ? [255, 110, 70, 48 * pulse(d.i)]
        : [255, 200, 120, 70 * d.g * pulse(d.i)],
      parameters: { blendColorOperation: "add", blendColorSrcFactor: "src-alpha",
        blendColorDstFactor: "one" } }),
    new deck.ScatterplotLayer({ id: "onsets",
      data: (step[`onset_${horizon}`] || []).map(i => pred.raions[i]),
      stroked: true, filled: false, radiusUnits: "meters", getRadius: 10000,
      getPosition: d => [d.lon, d.lat],
      getLineColor: [255, 255, 255, 210], lineWidthMinPixels: 1.4 }),
    new deck.TextLayer({ id: "cities", data: CITIES,
      getPosition: d => d.c, getText: d => d.n,
      getSize: 11, sizeUnits: "pixels", getColor: [255, 255, 255, 72],
      fontFamily: "system-ui, sans-serif", fontWeight: 600,
      outlineWidth: 2, outlineColor: [0, 0, 0, 130] }),
  ]};
}

/* ---------- panel ---------- */
let lastKey = "";
function updatePanel(P, si, step, hot) {
  const key = `${si}|${horizon}`;
  if (key === lastKey) return;
  lastKey = key;
  const forecast = hot.filter(f => !f._al).length;
  $("#status").textContent =
    `${step.alert.length} raions on alert · ${forecast} predicted in next ${horizon} min`;
  const prev = pred.steps[Math.max(0, si - 1)].pred[horizon];
  const rows = P.map((p, i) => ({ p, i })).filter(r => r.p != null)
    .sort((a, b) => b.p - a.p).slice(0, 8);
  $("#list").innerHTML = rows.map(r => {
    const d = prev[r.i] != null ? r.p - prev[r.i] : 0;
    const cls = d > 0.005 ? "up" : d < -0.005 ? "dn" : "";
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
seg($("#modeSeg"), ["top30", "thr"], mode, v => {
  mode = v;
  $("#thr").disabled = v !== "thr";
});
[...$("#modeSeg").children].forEach((b, i) => b.textContent = ["Top 30", "p ≥"][i]);
$("#thr").disabled = true;
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
  const { P, si, step, hot, layers } = buildLayers(now);
  overlay.setProps({ layers });
  updatePanel(P, si, step, hot);
  if (document.activeElement !== scrub) scrub.value = pos;
  const d = new Date(step.t);
  const clk = `${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())} UTC`;
  if (clk !== lastClock) { lastClock = clk; $("#clock").textContent = clk; }
  requestAnimationFrame(tick);
}
syncPlay();
requestAnimationFrame(tick);
