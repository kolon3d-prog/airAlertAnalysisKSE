"""Export per-night replay JSON per docs/prediction_contract.md.

raions = scored raions in raions.csv order (excluded raions omitted entirely,
so no nulls in pred arrays). alert/onsets from ground truth (eval_nights
labels), pred from the calibrated models. Reads metrics from
reports/metrics.json (run evaluate.py first) into meta.
"""
import json
import pickle

import lightgbm as lgb
import numpy as np
import pandas as pd

from pipe_common import (DATA, HORIZONS, MODELS, NIGHTS, REPORTS, WEB,
                         feature_cols, night_window, scored_raions)


def main():
    ev = pd.read_parquet(DATA / "eval_nights.parquet")
    raions = scored_raions()
    feats = feature_cols(ev)
    with open(REPORTS / "metrics.json", encoding="utf-8") as f:
        metrics = json.load(f)
    models = {}
    for h in HORIZONS:
        with open(MODELS / f"{h}min_iso.pkl", "rb") as f:
            models[h] = (lgb.Booster(model_file=str(MODELS / f"{h}min.txt")), pickle.load(f))
    WEB.mkdir(parents=True, exist_ok=True)

    for night in NIGHTS:
        ws, we = night_window(night)
        d = ev[(ev["t"] >= ws) & (ev["t"] < we)].sort_values(["t", "raion_idx"])
        steps = []
        for t, g in d.groupby("t", sort=True):
            assert (g["raion_idx"].to_numpy() == np.arange(len(raions))).all()
            step = {"t": t.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "alert": np.flatnonzero(g["in_alert_now"].to_numpy()).tolist(),
                    "pred": {}}
            for h in HORIZONS:
                step[f"onset_{h}"] = np.flatnonzero(g[f"y_{h}"].to_numpy()).tolist()
                booster, iso = models[h]
                p = iso.predict(booster.predict(g[feats]))
                step["pred"][str(h)] = [round(float(x), 3) for x in p]
            steps.append(step)
        out = {
            "night": night,
            "generated_at": pd.Timestamp.now("UTC").strftime("%Y-%m-%dT%H:%M:%SZ"),
            "step_minutes": 5,
            "horizons_min": list(HORIZONS),
            "raions": [{"key": r.key, "name_ua": r.name_ua, "oblast_ua": r.oblast_ua,
                        "lat": r.centroid_lat, "lon": r.centroid_lon}
                       for r in raions.itertuples()],
            "steps": steps,
            "meta": {"model": "lightgbm-binary+isotonic",
                     "pr_auc": {str(h): metrics[night][str(h)]["model"]["micro_pr_auc"] for h in HORIZONS},
                     "top30_hit": {str(h): metrics[night][str(h)]["model"]["top30_hit"] for h in HORIZONS}},
        }
        path = WEB / f"predictions_{night}.json"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False)
        print(f"wrote {path} ({len(steps)} steps)")


if __name__ == "__main__":
    main()
