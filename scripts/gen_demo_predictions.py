"""Generate web/data/predictions_demo.json per docs/prediction_contract.md.

Synthetic night 2025-12-12, 18:00->08:00 UTC, 5-min steps.
Alert wave sweeps west->east; probabilities = smoothed distance to wavefront.
"""
import json, math, random
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
GEO = ROOT / "web" / "data" / "raions.geojson"
OUT = ROOT / "web" / "data" / "predictions_demo.json"
NIGHT, STEP_MIN, HORIZONS = "2025-12-12", 5, [15, 30, 60]
rng = random.Random(12)

geo = json.loads(GEO.read_text(encoding="utf-8"))


def centroid(geom):
    polys = geom["coordinates"] if geom["type"] == "MultiPolygon" else [geom["coordinates"]]
    ring = max((p[0] for p in polys), key=len)  # largest ring of largest-ish poly
    ring = max((p[0] for p in polys), key=lambda r: (max(c[0] for c in r) - min(c[0] for c in r)))
    xs, ys = zip(*ring)
    return sum(ys) / len(ys), sum(xs) / len(xs)


raions = []
for f in geo["features"]:
    lat, lon = centroid(f["geometry"])
    raions.append({"key": f["properties"]["key"], "name_ua": f["properties"]["rayon"],
                   "oblast_ua": "", "lat": round(lat, 4), "lon": round(lon, 4)})
raions.sort(key=lambda r: r["key"])
N = len(raions)
lons = np.array([r["lon"] for r in raions])

n_steps = 14 * (60 // STEP_MIN) + 1  # 169, inclusive
t0 = np.datetime64(f"{NIGHT}T18:00:00")
u = np.linspace(0, 1, n_steps)
wave = 22.6 + 17.0 * u**1.25 + 0.5 * np.sin(u * 9.0)  # wavefront longitude over time

# tau_i: step index when the wavefront crosses raion i's longitude
tau = np.argmin(np.abs(wave[None, :] - lons[:, None]), axis=1).astype(float)
tau += np.random.default_rng(7).normal(0, 3, N)  # per-raion jitter
dur = rng.choices(range(24, 72), k=N)  # alert duration in steps (2-6h)
unscored = set(rng.sample(range(N), 3))  # exercise "not scored -> neutral"

steps = []
for t in range(n_steps):
    ts = str(t0 + np.timedelta64(t * STEP_MIN, "m")) + "Z"
    alert = [i for i in range(N) if tau[i] <= t <= tau[i] + dur[i]]
    step = {"t": ts, "alert": alert, "pred": {}}
    for h in HORIZONS:
        hs = h // STEP_MIN
        step[f"onset_{h}"] = [i for i in range(N) if t < tau[i] <= t + hs and i not in unscored]
        dt = (tau - t) * STEP_MIN  # minutes until onset
        p = 1 / (1 + np.exp(-(h * 0.6 - dt) / (h * 0.15)))
        p *= np.where(dt < 0, np.exp(dt / (2 * h)), 1.0)  # decay after onset
        p += np.random.default_rng(t * 31 + h).normal(0, 0.03, N)
        pl = np.clip(p, 0.005, 0.995).round(3).tolist()
        for i in unscored:
            pl[i] = None
        step["pred"][str(h)] = pl
    steps.append(step)

doc = {"night": NIGHT, "generated_at": "2026-10-11T12:00:00Z", "step_minutes": STEP_MIN,
       "horizons_min": HORIZONS, "raions": raions, "steps": steps,
       "meta": {"model": "demo-wave-v0", "pr_auc": {"15": 0.71, "30": 0.78, "60": 0.83},
                "top30_hit": {"15": 0.64, "30": 0.72, "60": 0.80}}}
OUT.write_text(json.dumps(doc, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
print(f"{OUT}  {OUT.stat().st_size/1e6:.2f} MB, {N} raions, {n_steps} steps")
