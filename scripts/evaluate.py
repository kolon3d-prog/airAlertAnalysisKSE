"""Evaluate the three horizon models on the held-out nights vs both baselines.

Per night x horizon: macro PR-AUC (mean per-raion AP over raions with >=1
positive), micro PR-AUC, top-30 hit rate per 5-min step. Baselines per spec:
(a) persistence rule: 0.9 if in alert, else neighbor in-alert fraction;
(b) diurnal climatology: per-raion onset rate by Kyiv hour, fit on the
training window only (held-out windows + buffers excluded).

Writes reports/eval_<night>.md and reports/metrics.json.
"""
import json
import pickle

import lightgbm as lgb
import numpy as np
import pandas as pd
from sklearn.metrics import average_precision_score

from pipe_common import (DATA, HORIZONS, MODELS, NIGHTS, REPORTS,
                         exclusion_window, feature_cols, night_window,
                         scored_raions)

TOPK = 30

# per-night caveats prepended to the eval report
NOTES = {
    "2026-10-08": (
        "> **Caveat — history gap.** Training/history alert intervals end 2026-09-07; "
        "Oct 8–9 intervals were appended from an external export "
        "(data/raw/alerts_2026-10-09.csv). The 2026-09-07 → 2026-10-08 period is unobserved, "
        "so state features saturate (minutes_since_last_end capped at 7d) and trailing-window "
        "intensity features under-read for the first hours of the night. Telegram threat-event "
        "features come from fresh eRadarRuA + kpszsu Oct scrapes, parsed regex-only like the "
        "training data. Models were NOT retrained; climatology baseline is fit on the original "
        "training window only."
    ),
}


def climatology():
    """-> rate[raion_idx, kyiv_hour], onsets/hour, smoothed, training window only."""
    iv = pd.read_parquet(DATA / "alert_intervals.parquet")
    t_end = max(iv["started_at"].max(), iv["finished_at"].max()).floor("5min")
    raions = scored_raions()
    kidx = {k: i for i, k in enumerate(raions["key"])}
    grid = pd.date_range(pd.Timestamp("2025-12-01", tz="UTC"), t_end, freq="5min")
    gmask = np.ones(len(grid), bool)
    iv = iv[iv["raion_key"].isin(kidx)]
    imask = np.ones(len(iv), bool)
    for night in NIGHTS:
        s, e = exclusion_window(night)
        gmask &= ~((grid >= s) & (grid < e))
        imask &= ~((iv["started_at"] >= s) & (iv["started_at"] < e))
    hours_obs = np.bincount(grid[gmask].tz_convert("Europe/Kyiv").hour, minlength=24) / 12
    cnt = np.zeros((len(kidx), 24))
    iv = iv[imask]
    hr = iv["started_at"].dt.tz_convert("Europe/Kyiv").dt.hour.to_numpy()
    ri = iv["raion_key"].map(kidx).to_numpy()
    np.add.at(cnt, (ri, hr), 1)
    return (cnt + 1.0) / (hours_obs + 24.0)  # prior: 1 onset/day uniform


def pr_auc_macro(y, s, ris):
    aps = []
    for ri in np.unique(ris):
        m = ris == ri
        if y[m].sum() > 0:
            aps.append(average_precision_score(y[m], s[m]))
    return float(np.mean(aps))


def topk_hit(y, s, ts, k=TOPK):
    d = pd.DataFrame({"t": ts, "y": y, "s": s})
    hits = []
    for _, g in d.groupby("t"):
        on = np.flatnonzero(g["y"].to_numpy())
        if len(on) == 0:
            continue
        top = set(np.argsort(-g["s"].to_numpy(), kind="stable")[:k])
        hits.append(sum(o in top for o in on) / len(on))
    return float(np.mean(hits)) if hits else float("nan")


def all_metrics(y, s, ts, ris):
    return {"macro_pr_auc": pr_auc_macro(y, s, ris),
            "micro_pr_auc": float(average_precision_score(y, s)),
            "top30_hit": topk_hit(y, s, ts)}


def main():
    ev = pd.read_parquet(DATA / "eval_nights.parquet")
    feats = feature_cols(ev)
    clim = climatology()
    kyiv_hour = ev["t"].dt.tz_convert("Europe/Kyiv").dt.hour.to_numpy()
    ris = ev["raion_idx"].to_numpy()
    ts = ev["t"].to_numpy()
    base_persist = np.where(ev["in_alert_now"].to_numpy() > 0, 0.9,
                            ev["nb_in_alert_frac"].to_numpy())
    base_clim = clim[ris, kyiv_hour]

    metrics = {}
    for night in NIGHTS:
        ws, we = night_window(night)
        sel = (ev["t"] >= ws) & (ev["t"] < we)
        metrics[night] = {}
        lines = [f"# Eval {night} ({ws:%Y-%m-%d %H:%M} -> {we:%Y-%m-%d %H:%M} UTC, held out)", ""]
        if night in NOTES:
            lines += [NOTES[night], ""]
        for h in HORIZONS:
            y = ev.loc[sel, f"y_{h}"].to_numpy()
            t_s, r_s = ts[sel], ris[sel]
            booster = lgb.Booster(model_file=str(MODELS / f"{h}min.txt"))
            with open(MODELS / f"{h}min_iso.pkl", "rb") as f:
                iso = pickle.load(f)
            p = iso.predict(booster.predict(ev.loc[sel, feats]))
            res = {"model": all_metrics(y, p, t_s, r_s),
                   "persistence": all_metrics(y, base_persist[sel], t_s, r_s),
                   "climatology": all_metrics(y, base_clim[sel], t_s, r_s)}
            metrics[night][str(h)] = res
            lines += [f"## h = {h} min  (positive rate {y.mean():.3f})", "",
                      "| scorer | macro PR-AUC | micro PR-AUC | top-30 hit |",
                      "|---|---|---|---|"]
            for name, m in res.items():
                lines.append(f"| {name} | {m['macro_pr_auc']:.4f} | {m['micro_pr_auc']:.4f} | {m['top30_hit']:.4f} |")
            lines.append("")
        out = REPORTS / f"eval_{night}.md"
        out.write_text("\n".join(lines), encoding="utf-8")
        print(f"wrote {out}")
    mpath = REPORTS / "metrics.json"
    merged = json.loads(mpath.read_text(encoding="utf-8")) if mpath.exists() else {}
    merged.update(metrics)
    with open(mpath, "w", encoding="utf-8") as f:
        json.dump(merged, f, indent=1)
    print(json.dumps(metrics, indent=1))


if __name__ == "__main__":
    main()
