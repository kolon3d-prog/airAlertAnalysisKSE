const $ = s => document.querySelector(s);
const clamp = (v, a, b) => Math.min(b, Math.max(a, v));

const night = new URLSearchParams(location.search).get("night") || "demo";
const DATA_URL = night === "demo" ? "data/predictions_demo.json" : `data/predictions_${night}.json`;

let pred, geo, rib;
try {
  [pred, geo, rib] = await Promise.all([
    fetch(DATA_URL).then(r => { if (!r.ok) throw 0; return r.json(); }),
    fetch("data/raions.geojson").then(r => r.json()),
    fetch("data/ribbons.geojson").then(r => r.json()),
  ]);
} catch {
  $("#err").hidden = false;
  $("#err").textContent = `Could not load ${DATA_URL} — run the pipeline for that night, or open ?night=demo.`;
  throw new Error("data load failed");
}
// optional sidecars (hover card); page works without them
const [adj, sig] = await Promise.all([
  fetch("data/adjacency.json").then(r => r.ok ? r.json() : null).catch(() => null),
  fetch(night === "demo" ? "data/signals_demo.json" : `data/signals_${night}.json`)
    .then(r => r.ok ? r.json() : null).catch(() => null),
]);
const sigOK = sig && Array.isArray(sig.steps) && sig.steps.length === pred.steps.length;

const NS = pred.steps.length, N = pred.raions.length;
const key2i = new Map(pred.raions.map((r, i) => [r.key, i]));
const feats = geo.features.filter(f => key2i.has(f.properties.key));
feats.forEach(f => {
  f._i = key2i.get(f.properties.key);
  const r = pred.raions[f._i];
  f._c = [r.lon, r.lat];
});
const byI = new Map(feats.map(f => [f._i, f]));
const ribFeats = rib.features.filter(f => key2i.has(f.properties.key));
ribFeats.forEach(f => { f._f = byI.get(key2i.get(f.properties.key)); });
// raions excluded from scoring: rendered as neutral hoverable outlines
const extraFeats = geo.features.filter(f => !key2i.has(f.properties.key));
extraFeats.forEach(f => { f._i = -1; });
// flatten (Multi)Polygons into plain ring-arrays for SolidPolygonLayer
const flatRows = features => features.flatMap(f => {
  const g = f.geometry;
  const polys = g.type === "Polygon" ? [g.coordinates] : g.coordinates;
  return polys.map(rings => ({ rings, _f: f._f ?? f }));
});
const allRows = flatRows(feats).concat(flatRows(extraFeats));
const ribRows = flatRows(ribFeats);
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
  pixelRatio: Math.min(devicePixelRatio || 1, 1.75),
  attributionControl: { compact: true },
});
let swapped = false;
map.on("error", () => {
  if (!swapped && !map.isStyleLoaded()) {
    swapped = true;
    map.setStyle("https://basemaps.cartocdn.com/gl/dark_matter-gl-style/style.json");
  }
});
// near-black veil over the basemap, under all deck polygons
function addDim() {
  if (!map.isStyleLoaded() || map.getLayer("dim")) return;
  if (!map.getSource("dim")) map.addSource("dim", { type: "geojson", data: geo });
  map.addLayer({ id: "dim", type: "fill", source: "dim",
    paint: { "fill-color": "#0a0a0c", "fill-opacity": 0.35 } });
}
map.on("load", addDim);
map.on("styledata", addDim);
map.fitBounds([[22.1, 44.2], [40.3, 52.4]], {
  padding: { top: 80, right: 300, bottom: 130, left: 50 },
  pitch: 50, bearing: -8, duration: reduced ? 0 : 1800,
});
const overlay = new deck.MapboxOverlay({ interleaved: true, layers: [], onHover });
map.addControl(overlay);
window.__map = map; // debug/test hooks
window.__feats = feats;

/* ---------- visuals ---------- */
// preallocated interpolation buffer (NaN = not scored)
const Pbuf = new Float32Array(N);
function probsAt(h, x) { // linear interpolation between steps
  const i = clamp(Math.floor(x), 0, NS - 2), f = x - i;
  const a = pred.steps[i].pred[h], b = pred.steps[i + 1].pred[h];
  for (let k = 0; k < N; k++) {
    const v = a[k], w = b[k];
    Pbuf[k] = v == null ? NaN : w == null ? v : v + (w - v) * f;
  }
  return Pbuf;
}

const CUR_H = 38000;                 // max curtain height, m
const ADD = { blendColorOperation: "add", blendColorSrcFactor: "src-alpha",
  blendColorDstFactor: "one" };
const ADD_FLAT = { ...ADD, depthTest: false };
const NO_DEPTH = { depthTest: false };
const RGBA0 = [0, 0, 0, 0];
const COL_B_AL = [255, 204, 51, 255], COL_B_PR = [255, 214, 10, 204], COL_B_IN = [44, 44, 46, 255];
const COL_PIN = [255, 226, 90, 255], COL_PIN_GLOW = [255, 214, 10, 42];
const EASE_OUT = t => 1 - Math.pow(1 - t, 3);
// per-raion smoothing: dt-corrected exponential chase (~0.28s time constant)
const TAU = 0.28;
let chasing = true;                  // any curtain height/color still settling

// memoized "on alert since" lookup: walk steps back while raion stays in alert
const sinceCache = new Map();
function alertSince(i, si) {
  const key = i * NS + si;
  let s = sinceCache.get(key);
  if (s === undefined) {
    s = si;
    while (s > 0 && pred.steps[s - 1].alert.includes(i)) s--;
    if (sinceCache.size > 4000) sinceCache.clear();
    sinceCache.set(key, s);
  }
  return s;
}

// mutable anim state read by stable accessors
const A = { now: 0, ph: 0, dtB: 0, lastB: 0 };
const alertSet = new Set();
let curAlertKeys = new Set();

// SolidPolygonLayer with a per-fragment vertical gradient: alpha fades with
// extrusion fraction vH (0 at ground -> 1 at top), caps (vH==1) vanish.
class CurtainLayer extends deck.SolidPolygonLayer {
  getShaders(type) {
    const s = super.getShaders(type);
    return { ...s, inject: {
      "vs:#decl": "varying float vH;",
      "vs:#main-end": "vH = vertexPositions.y;",
      "fs:#decl": "varying float vH;",
      "fs:#main-end": "gl_FragColor.a *= pow(max(1.0 - vH, 0.0), 2.5);",
    }};
  }
}
CurtainLayer.layerName = "CurtainLayer";

/* stable accessors (identity-stable; per-frame values via A + feature fields) */
const getRings = d => d.rings;       // stable identity: keeps tessellation cached
const pulseF = i => reduced ? 1 : 1 + 0.12 * Math.sin(A.ph + i * 1.7);
const haloFill = d => [255, 69, 58, 13 * pulseF(d._f._i)];
const haloLine = d => [255, 69, 58, 17 * pulseF(d._f._i)];
const glowFill = d => d._f._al ? [255, 69, 58, 32 * pulseF(d._f._i)] : RGBA0;
const curElev = d => CUR_H * (d._f._hs || 0);
const curCol = d => { // gold -> red-orange mix by smoothed alertness
  const a = d._f._as || 0;
  return [255, 214 - 124 * a, 10 + 38 * a, 78 + 22 * a];
};
const bordCol = d => d._f._al ? COL_B_AL : d._f._hot ? COL_B_PR : COL_B_IN;
const bordW = d => (d._f._al || d._f._hot) ? 1.5 : 1;
const ripPhase = o => ((A.now + o) % 2000) / 2000;
const ripRadius = d => 12000 * ripPhase(d.o);
const ripColor = d => [255, 214, 10, 180 * Math.pow(1 - ripPhase(d.o), 1.3)];
const getRipPos = d => [d.r.lon, d.r.lat];
const getOnsetPos = d => [d.lon, d.lat];
const getCityPos = d => d.c;
const getCityText = d => d.n;

const citiesLayer = new deck.TextLayer({ id: "cities", data: CITIES, parameters: NO_DEPTH,
  getPosition: getCityPos, getText: getCityText,
  getSize: 11, sizeUnits: "pixels", getColor: [199, 199, 204, 235],
  fontFamily: "system-ui, sans-serif", fontWeight: 600,
  outlineWidth: 1, outlineColor: [10, 10, 12, 210] });

let lastSetKey = "", curtainSet = [], alertRows = [];

function buildLayers(si, step, key) {
  const P = probsAt(horizon, pos);
  alertSet.clear();
  for (const i of step.alert) alertSet.add(i);
  curAlertKeys = new Set(step.alert.map(i => pred.raions[i].key));

  for (const f of feats) { f._p = P[f._i]; f._al = alertSet.has(f._i); f._hot = false; }
  let hot;
  if (mode === "top30") { // rank-based: curtains == top-30 eval set; intensity vs step max
    const scored = feats.filter(f => f._p === f._p).sort((a, b) => b._p - a._p);
    const maxP = scored.length ? scored[0]._p : 1;
    hot = scored.slice(0, 30);
    for (const f of hot) f._g = maxP > 0 ? f._p / maxP : 0;
  } else {
    hot = feats.filter(f => f._p === f._p && f._p >= threshold);
    for (const f of hot) f._g = f._p;
  }
  for (const f of hot) f._hot = true;

  // smoothed per-raion curtain state: height factor + alertness (ease-out chase)
  const k = reduced ? 1 : 1 - Math.exp(-A.dtB / TAU);
  let rem = 0;
  for (const f of feats) {
    const hT = f._al ? 1.3 : f._hot ? 0.7 + 0.6 * f._g : 0;
    f._hs = f._hs == null ? hT : f._hs + (hT - f._hs) * k;
    const aT = f._al ? 1 : 0;
    f._as = f._as == null ? aT : f._as + (aT - f._as) * k;
    const dH = Math.abs(hT - f._hs), dA = Math.abs(aT - f._as);
    if (dH > rem) rem = dH;
    if (dA > rem) rem = dA;
  }
  chasing = rem > 0.002;

  // cached sets: stable array identity between step changes; raions whose
  // curtain is still draining stay in the set so departures sink smoothly
  const setKey = `${si}|${horizon}|${mode}|${mode === "thr" ? threshold : ""}`;
  if (setKey !== lastSetKey) {
    lastSetKey = setKey;
    curtainSet = ribRows.filter(d => d._f._al || d._f._hot || (d._f._hs ?? 0) > 0.02);
    alertRows = allRows.filter(d => d._f._al);
  }

  const onsets = (step[`onset_${horizon}`] || []).map(i => pred.raions[i]);
  const rip = reduced ? [] : onsets.flatMap(r => [{ r, o: 0 }, { r, o: 1000 }]);
  const bKey = lastSetKey; // borders change only with state, not per frame

  const layers = [
    // ground halo (fake bloom bleed) + pulsing glow fill for alert raions
    new deck.PolygonLayer({ id: "halo", data: alertRows, getPolygon: getRings,
      filled: true, stroked: true, parameters: ADD,
      getFillColor: haloFill, getLineColor: haloLine,
      getLineWidth: 9, lineWidthUnits: "pixels",
      updateTriggers: { getFillColor: key, getLineColor: key } }),
    new deck.PolygonLayer({ id: "glow", data: allRows, getPolygon: getRings,
      filled: true, stroked: false, pickable: true,
      getFillColor: glowFill, updateTriggers: { getFillColor: key } }),
    // volumetric curtain over the border ribbons of hot raions
    new CurtainLayer({ id: "curtain", data: curtainSet, getPolygon: getRings,
      filled: true, stroked: false, extruded: true, material: false,
      parameters: ADD_FLAT, getElevation: curElev, getFillColor: curCol,
      updateTriggers: { getElevation: key, getFillColor: key } }),
    // crisp borders
    new deck.PolygonLayer({ id: "borders", data: allRows, getPolygon: getRings,
      filled: false, stroked: true, parameters: NO_DEPTH, lineWidthUnits: "pixels",
      getLineColor: bordCol, getLineWidth: bordW,
      updateTriggers: { getLineColor: bKey, getLineWidth: bKey } }),
    // radar pins at onset centroids
    new deck.ScatterplotLayer({ id: "ripples", data: rip, stroked: true, filled: false,
      parameters: NO_DEPTH, radiusUnits: "meters", lineWidthMinPixels: 1.25,
      getPosition: getRipPos, getRadius: ripRadius, getLineColor: ripColor,
      updateTriggers: { getRadius: key, getLineColor: key } }),
    new deck.ScatterplotLayer({ id: "pinGlow", data: onsets, filled: true, stroked: false,
      parameters: ADD_FLAT, radiusUnits: "pixels",
      getPosition: getOnsetPos, getRadius: 11, getFillColor: COL_PIN_GLOW }),
    new deck.ScatterplotLayer({ id: "pins", data: onsets, filled: true, stroked: false,
      parameters: NO_DEPTH, radiusUnits: "pixels",
      getPosition: getOnsetPos, getRadius: 4, getFillColor: COL_PIN }),
    citiesLayer,
  ];
  return { P, hot, layers };
}

/* ---------- hover card ---------- */
const hoverEl = $("#hover");
let hoverF = null, hoverPt = [0, 0], hoverRaf = 0, hoverKey = "";
let dragging = false, scrubbing = false, curSi = 0;

function onHover(info) {
  hoverF = info.object ? info.object._f : null;
  hoverPt = [info.x, info.y];
  if (!hoverRaf) hoverRaf = requestAnimationFrame(() => { hoverRaf = 0; renderHover(); });
}
map.on("dragstart", () => { dragging = true; renderHover(); });
map.on("dragend", () => { dragging = false; renderHover(); });

function bandText(p15, p30, p60) {
  if (p15 == null && p30 == null && p60 == null) return null;
  if ((p15 ?? 0) >= 0.5) return "likely within ~15 min";
  if ((p30 ?? 0) >= 0.5) return "likely within 15–30 min";
  if ((p60 ?? 0) >= 0.5) return "likely within 30–60 min";
  if (Math.max(p15 ?? 0, p30 ?? 0, p60 ?? 0) >= 0.15) return "possible within 60 min";
  return "low risk";
}

function renderHover() {
  const f = hoverF;
  if (!f || dragging || scrubbing) { hoverEl.classList.remove("on"); hoverKey = ""; return; }
  const i = f._i;
  const key = `${i}|${curSi}|${horizon}|${mode}`;
  if (key !== hoverKey) {
    hoverKey = key;
    const r = i >= 0 ? pred.raions[i] : null;
    const p15 = r ? pred.steps[curSi].pred["15"][i] : null;
    const p30 = r ? pred.steps[curSi].pred["30"][i] : null;
    const p60 = r ? pred.steps[curSi].pred["60"][i] : null;
    const [chip, cls] = f._al ? ["On alert now", "al"]
      : (!r || p30 == null) ? ["Not scored", "na"]
      : f._hot ? ["Predicted", "pr"] : ["Quiet", "qu"];
    let band = r ? bandText(p15, p30, p60) : null;
    let bars = r && p30 != null ? [15, 30, 60].map(h => {
      const p = pred.steps[curSi].pred[String(h)][i];
      return `<div class="brow"><span>${h}</span><span class="bbar"><i style="width:` +
        `${p == null ? 0 : (p * 100).toFixed(0)}%"></i></span>` +
        `<span class="bval">${p == null ? "—" : p.toFixed(2)}</span></div>`;
    }).join("") : "";
    if (f._al && r) { // onset probabilities are meaningless for active alerts
      const d0 = new Date(pred.steps[alertSince(i, curSi)].t);
      const mins = Math.max(0, Math.round((new Date(pred.steps[curSi].t) - d0) / 60000));
      const dur = mins >= 60 ? `${Math.floor(mins / 60)}h ${mins % 60}m` : `${mins}m`;
      band = `On alert since ${pad(d0.getUTCHours())}:${pad(d0.getUTCMinutes())} UTC (${dur})`;
      bars = "";
    }
    let sigRow = "";
    if (adj || (sigOK && r)) {
      const n = adj ? (adj[f.properties.key] || [])
        .filter(k => curAlertKeys.has(k)).length : null;
      const m = sigOK && r ? (sig.steps[curSi].tg[String(i)] ?? 0) : null;
      if (n === 0 && m === 0) sigRow = "no active signals";
      else {
        const parts = [];
        if (n !== null) parts.push(`neighbors in alert: ${n}`);
        if (m !== null) parts.push(`OSINT reports (60 min): ${m}`);
        let drv = "";
        if (n !== null && m !== null) {
          if (n > 0 && m === 0) drv = " · driver: cascade";
          else if (n === 0 && m > 0) drv = " · driver: OSINT";
          else if (n === 0 && m === 0) drv = "";
        }
        sigRow = parts.join(" · ") + drv;
      }
      sigRow = `<div class="sigcap">Live signals</div><div class="sig">${sigRow}</div>`;
    }
    hoverEl.innerHTML =
      `<div class="ttl"><span class="nm">${r ? r.name_ua : f.properties.rayon}</span>` +
      `<span class="chip ${cls}">${chip}</span></div>` +
      (r && r.oblast_ua ? `<div class="obl">${r.oblast_ua}</div>` : "") +
      (band ? `<div class="band">${band}</div>` : "") +
      (bars ? `<div class="bars">${bars}</div>` : "") + sigRow;
  }
  const w = hoverEl.offsetWidth, h = hoverEl.offsetHeight;
  const x = clamp(hoverPt[0] + 14, 8, innerWidth - w - 8);
  const y = clamp(hoverPt[1] + 14, 8, innerHeight - h - 8);
  hoverEl.style.transform = `translate(${x}px, ${y}px)`;
  hoverEl.classList.add("on");
}

/* ---------- panel ---------- */
let lastPanelKey = "";
function updatePanel(P, si, step, hot) {
  const pkey = `${si}|${horizon}|${mode}|${threshold}`;
  if (pkey === lastPanelKey) return;
  lastPanelKey = pkey;
  const forecast = hot.filter(f => !f._al).length;
  $("#status").textContent =
    `${step.alert.length} raions on alert · ${forecast} predicted in next ${horizon} min`;
  const prev = pred.steps[Math.max(0, si - 1)].pred[horizon];
  const rows = [];
  for (let i = 0; i < N; i++) { const p = P[i]; if (p === p) rows.push({ p, i }); }
  rows.sort((a, b) => b.p - a.p);
  rows.length = Math.min(rows.length, 8);
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
scrub.addEventListener("pointerdown", () => { scrubbing = true; renderHover(); });
addEventListener("pointerup", () => { if (scrubbing) { scrubbing = false; renderHover(); } });

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

/* ---------- main loop (memoized: rebuild layers only on state/bucket change) ---------- */
const pad = n => String(n).padStart(2, "0");
let lastT = performance.now(), lastClock = "", lastKey = "";
function tick(now) {
  const dt = (now - lastT) / 1000; lastT = now;
  if (playing) {
    pos += dt * SPEEDS[speedIdx] / (pred.step_minutes * 60);
    if (pos >= NS - 1) { pos = NS - 1; setPlaying(false); }
  } else if (anim) { // 300ms ease-out toward target step
    const k = clamp((now - anim.t0) / 300, 0, 1);
    pos = anim.from + (anim.to - anim.from) * EASE_OUT(k);
    if (k >= 1) anim = null;
  }
  A.now = now;
  A.ph = (now * 2 * Math.PI) / 3500;
  const si = clamp(Math.round(pos), 0, NS - 1);
  curSi = si;
  // adaptive cadence: every frame while playing / stepping / curtain chase
  // unsettled; otherwise a 100ms bucket (idle sleep under reduced motion)
  const stateKey = `${si}|${horizon}|${mode}|${mode === "thr" ? threshold : ""}`;
  const key = stateKey + "|" + (
    (playing || anim || chasing) ? "f" + now
    : reduced ? "p" + Math.round(pos * 8)
    : "b" + Math.floor(now / 100));
  if (key !== lastKey) {
    lastKey = key;
    A.dtB = Math.min(0.5, (now - (A.lastB || now)) / 1000);
    A.lastB = now;
    const { P, hot, layers } = buildLayers(si, pred.steps[si], key);
    overlay.setProps({ layers });
    updatePanel(P, si, pred.steps[si], hot);
    if (hoverF) renderHover();
  }
  if (document.activeElement !== scrub) scrub.value = pos;
  const d = new Date(pred.steps[si].t);
  const clk = `${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())} UTC`;
  if (clk !== lastClock) { lastClock = clk; $("#clock").textContent = clk; }
  requestAnimationFrame(tick);
}
syncPlay();
requestAnimationFrame(tick);
