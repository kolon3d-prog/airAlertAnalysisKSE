"""Train one LightGBM binary classifier per horizon per docs/model_spec.md.

Early stopping on the last-14-days validation tail (average precision),
isotonic calibration on the same tail with negatives re-weighted to undo the
20:1 downsampling. Saves models/<h>min.txt, models/<h>min_iso.pkl,
models/<h>min_importance.json; writes reports/training.json.
"""
import json
import pickle
import time

import lightgbm as lgb
import numpy as np
import pandas as pd
from sklearn.isotonic import IsotonicRegression
from sklearn.linear_model import LogisticRegression

from pipe_common import DATA, HORIZONS, MODELS, NEG_KEEP_P, REPORTS, feature_cols

PARAMS = dict(objective="binary", metric="average_precision", learning_rate=0.05,
              num_leaves=63, min_child_samples=100, feature_fraction=0.9,
              bagging_fraction=0.9, bagging_freq=1, verbose=-1)


def main():
    MODELS.mkdir(exist_ok=True)
    REPORTS.mkdir(exist_ok=True)
    df = pd.read_parquet(DATA / "train.parquet")
    feats = feature_cols(df)
    info = {}
    for h in HORIZONS:
        t0 = time.time()
        d = df[df[f"keep_{h}"]]
        cutoff = d["t"].max() - pd.Timedelta(days=14)
        tr, va = d[d["t"] < cutoff], d[d["t"] >= cutoff]
        dtr = lgb.Dataset(tr[feats], tr[f"y_{h}"], categorical_feature=["oblast_ua"])
        dva = lgb.Dataset(va[feats], va[f"y_{h}"], reference=dtr, categorical_feature=["oblast_ua"])
        m = lgb.train(PARAMS, dtr, num_boost_round=5000, valid_sets=[dva],
                      callbacks=[lgb.early_stopping(200), lgb.log_evaluation(200)])

        p_raw = m.predict(va[feats], num_iteration=m.best_iteration)
        yv = va[f"y_{h}"].to_numpy()
        w = np.where(yv == 1, 1.0, 1.0 / NEG_KEEP_P)  # undo downsampling for calibration
        iso = IsotonicRegression(out_of_bounds="clip").fit(p_raw, yv, sample_weight=w)
        eps = 1e-6
        logit = np.log(np.clip(p_raw, eps, 1 - eps) / (1 - np.clip(p_raw, eps, 1 - eps))).reshape(-1, 1)
        slope = float(LogisticRegression().fit(logit, yv, sample_weight=w).coef_[0][0])

        m.save_model(str(MODELS / f"{h}min.txt"), num_iteration=m.best_iteration)
        with open(MODELS / f"{h}min_iso.pkl", "wb") as f:
            pickle.dump(iso, f)
        imp = sorted(zip(m.feature_name(), m.feature_importance("gain")), key=lambda x: -x[1])
        with open(MODELS / f"{h}min_importance.json", "w") as f:
            json.dump([{"feature": a, "gain": float(b)} for a, b in imp], f, indent=1)

        info[str(h)] = {"best_iteration": m.best_iteration,
                        "val_pr_auc": m.best_score["valid_0"]["average_precision"],
                        "calibration_slope": slope,
                        "train_rows": len(tr), "val_rows": len(va),
                        "val_pos_rate": float(yv.mean()),
                        "train_seconds": round(time.time() - t0, 1)}
        print(f"h={h}: {info[str(h)]}")
    with open(REPORTS / "training.json", "w") as f:
        json.dump(info, f, indent=1)


if __name__ == "__main__":
    main()
