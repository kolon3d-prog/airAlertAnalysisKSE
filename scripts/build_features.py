"""Expand alert intervals to the 5-min raion grid per docs/model_spec.md.

Writes data/processed/train.parquet (20:1 downsampled negatives, held-out
windows + 6h buffers excluded) and data/processed/eval_nights.parquet
(both held-out replay windows fully scored, no downsampling).

Threat-event block 7 is optional: --threat-events PATH. Assumed schema of that
parquet (until the real parser lands): ts (UTC), raion_keys (list[str]),
threat_type in {uav,cruise,ballistic,kab,mig31k}, heading ('toward' = heading
toward the listed raion), count, confidence. When absent, block-7 columns are
all zeros (spec: "All zeros when the block is absent").
"""
import argparse
import json

import numpy as np
import pandas as pd

from build_dataset import merge_intervals
from pipe_common import (DATA, HORIZONS, NEG_KEEP_P, NIGHTS, exclusion_window,
                         night_window, scored_raions)

TE_TYPES = ["uav", "cruise", "ballistic", "kab", "mig31k"]
TE_COLS = ([f"te_own_{t}_{w}" for t in TE_TYPES for w in (15, 30, 60)]
           + [f"te_toward_{t}_60" for t in TE_TYPES]
           + ["te_nb_30", "te_nb_60", "te_nat_30", "te_nat_60"])
CAP_ALERT_MIN = 720        # minutes_in_alert cap (12h)
CAP_SINCE_MIN = 7 * 24 * 60  # minutes_since_last_alert_end cap (7d)


def wcount(m, w, T, win):
    """Sum of w over events in (t-win, t] for each t in T. m sorted minutes."""
    if len(m) == 0:
        return np.zeros(len(T), np.float32)
    cs = np.concatenate([[0.0], np.cumsum(w)])
    hi = np.searchsorted(m, T, side="right")
    lo = np.searchsorted(m, T - win, side="right")
    return (cs[hi] - cs[lo]).astype(np.float32)


def load_threat(path, kidx, t0):
    """-> (by (raion,type): (m, count, toward), by raion: (m, count), national (m, count))."""
    if path is None:
        return {}, {}, None
    df = pd.read_parquet(path)
    df["m"] = (pd.to_datetime(df["ts"], utc=True) - t0).dt.total_seconds() / 60
    ev = df.explode("raion_keys")
    ev = ev[ev["raion_keys"].isin(kidx)].copy()
    ev["ri"] = ev["raion_keys"].map(kidx)
    ev["toward"] = (ev["heading"] == "toward").astype(np.float64)
    by_rt, by_r = {}, {}
    for (ri, ty), g in ev.groupby(["ri", "threat_type"]):
        g = g.sort_values("m")
        by_rt[(ri, ty)] = (g["m"].to_numpy(), g["count"].to_numpy(float), g["toward"].to_numpy())
    for ri, g in ev.groupby("ri"):
        g = g.sort_values("m")
        by_r[ri] = (g["m"].to_numpy(), g["count"].to_numpy(float))
    nat = df.sort_values("m")
    return by_rt, by_r, (nat["m"].to_numpy(), nat["count"].to_numpy(float))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--threat-events", default=None)
    ap.add_argument("--intervals", nargs="+", default=[str(DATA / "alert_intervals.parquet")],
                    help="interval parquets; concatenated + re-merged per raion (dedup overlap)")
    ap.add_argument("--eval-only", action="store_true",
                    help="write only eval_nights.parquet; leave train.parquet untouched")
    args = ap.parse_args()

    raions = scored_raions()
    keys = raions["key"].tolist()
    kidx = {k: i for i, k in enumerate(keys)}
    with open(DATA / "adjacency.json", encoding="utf-8") as f:
        adj_raw = json.load(f)
    # neighbor features over scored raions only (excluded = permanent-alert raions
    # would saturate nb_in_alert / min_hops for every frontier raion)
    adj = {k: [kidx[n] for n in adj_raw.get(k, []) if n in kidx] for k in keys}

    iv = pd.concat([pd.read_parquet(p) for p in args.intervals], ignore_index=True)
    iv = iv[iv["raion_key"].isin(kidx)]
    iv = pd.DataFrame([(k, s, e) for k, g in iv.groupby("raion_key")
                       for s, e in merge_intervals(list(zip(g["started_at"], g["finished_at"])))],
                      columns=["raion_key", "started_at", "finished_at"])
    t0 = pd.Timestamp("2025-12-01", tz="UTC")
    t_end = max(iv["started_at"].max(), iv["finished_at"].max()).floor("5min")
    n = int((t_end - t0).total_seconds() // 300) + 1
    T = 5.0 * np.arange(n)  # step-start minutes since t0
    R = len(keys)
    grid = t0 + pd.to_timedelta(T, unit="m")
    local = grid.tz_convert("Europe/Kyiv")
    hour = np.asarray(local.hour) + np.asarray(local.minute) / 60
    gtemp = {
        "hour_sin": np.sin(2 * np.pi * hour / 24).astype(np.float32),
        "hour_cos": np.cos(2 * np.pi * hour / 24).astype(np.float32),
        "dow": np.asarray(local.dayofweek, dtype=np.float32),
        "is_night": ((local.hour >= 22) | (local.hour < 6)).astype(np.float32),
    }

    by_rt, by_r, nat_ev = load_threat(args.threat_events, kidx, t0)

    # pass 1: per-raion interval spans + grid-wide state/onset matrices
    A = np.zeros((R, n), bool)          # in alert at step start
    ON30 = np.zeros((R, n), np.int16)   # own onsets in (t-30, t]
    ON60 = np.zeros((R, n), np.int16)
    TE30 = np.zeros((R, n), np.float32)  # own threat events (any type) in (t-30, t]
    TE60 = np.zeros((R, n), np.float32)
    spans = {}
    for k, g in iv.groupby("raion_key"):
        i = kidx[k]
        g = g.sort_values("started_at")
        s = (g["started_at"] - t0).dt.total_seconds().to_numpy() / 60
        e = (g["finished_at"] - t0).dt.total_seconds().to_numpy() / 60
        idx = np.searchsorted(s, T, side="right") - 1
        A[i] = (idx >= 0) & (T < e[np.clip(idx, 0, None)])
        hi = np.searchsorted(s, T, side="right")
        ON30[i] = hi - np.searchsorted(s, T - 30, side="right")
        ON60[i] = hi - np.searchsorted(s, T - 60, side="right")
        spans[i] = (s, e)
    for i, (m, w) in by_r.items():
        TE30[i] = wcount(m, w, T, 30)
        TE60[i] = wcount(m, w, T, 60)

    nat_in = A.sum(0).astype(np.float32)
    gtemp["nat_in_alert"] = nat_in
    gtemp["nat_onsets_30"] = ON30.sum(0).astype(np.float32)
    gtemp["attack_night"] = (nat_in > 20).astype(np.float32)
    for c, win in (("te_nat_30", 30), ("te_nat_60", 60)):
        gtemp[c] = wcount(*nat_ev, T, win) if nat_ev is not None else np.zeros(n, np.float32)

    rng = np.random.default_rng(42)
    excl = np.zeros(n, bool)
    eval_mask = np.zeros(n, bool)
    for night in NIGHTS:
        s, e = exclusion_window(night)
        excl |= (grid >= s) & (grid < e)
        ws, we = night_window(night)
        eval_mask |= (grid >= ws) & (grid < we)

    oblast_dt = pd.CategoricalDtype(sorted(raions["oblast_ua"].unique()))
    train_parts, eval_parts = [], []
    for i in range(R):
        s, e = spans.get(i, (np.zeros(1), np.zeros(1)))
        in_alert = A[i]

        idx = np.searchsorted(s, T, side="right") - 1
        min_in = np.where(in_alert, np.minimum(T - s[np.clip(idx, 0, None)], CAP_ALERT_MIN), 0)
        j = np.searchsorted(e, T, side="right") - 1
        min_since = np.where(j >= 0, np.minimum(T - e[np.clip(j, 0, None)], CAP_SINCE_MIN), CAP_SINCE_MIN)

        hi = np.searchsorted(s, T, side="right")
        y = {h: (np.searchsorted(s, T + h, side="right") - hi) > 0 for h in HORIZONS}

        nb = adj[keys[i]]
        if nb:
            nb_cnt = A[nb].sum(0).astype(np.float32)
            nb_frac = nb_cnt / len(nb)
            nb_on30 = ON30[nb].sum(0).astype(np.float32)
            nb_on60 = ON60[nb].sum(0).astype(np.float32)
            te_nb30 = TE30[nb].sum(0).astype(np.float32)
            te_nb60 = TE60[nb].sum(0).astype(np.float32)
        else:
            nb_cnt = nb_frac = nb_on30 = nb_on60 = te_nb30 = te_nb60 = np.zeros(n, np.float32)
        hops = np.where(in_alert, 0, np.where(nb_cnt > 0, 1, 2)).astype(np.float32)

        cols = {
            "in_alert_now": in_alert.astype(np.float32),
            "minutes_in_alert": min_in.astype(np.float32),
            "minutes_since_last_end": min_since.astype(np.float32),
            "own_onsets_30": (hi - np.searchsorted(s, T - 30, side="right")).astype(np.float32),
            "own_onsets_60": (hi - np.searchsorted(s, T - 60, side="right")).astype(np.float32),
            "own_onsets_180": (hi - np.searchsorted(s, T - 180, side="right")).astype(np.float32),
            "own_active_alerts": in_alert.astype(np.float32),
            "nb_in_alert_count": nb_cnt,
            "nb_in_alert_frac": nb_frac.astype(np.float32),
            "nb_onsets_30": nb_on30,
            "nb_onsets_60": nb_on60,
            "min_hops_active": hops,
            "lat": np.float32(raions.at[i, "centroid_lat"]),
            "lon": np.float32(raions.at[i, "centroid_lon"]),
            "te_nb_30": te_nb30,
            "te_nb_60": te_nb60,
        }
        cols.update(gtemp)
        for ty in TE_TYPES:
            m, w, tw = by_rt.get((i, ty), (np.zeros(0), None, None))
            for win in (15, 30, 60):
                cols[f"te_own_{ty}_{win}"] = wcount(m, w, T, win)
            cols[f"te_toward_{ty}_60"] = (wcount(m, tw, T, 60) > 0).astype(np.float32)

        keep = {h: y[h] | (rng.random(n) < NEG_KEEP_P) for h in HORIZONS}
        sel_tr = np.flatnonzero((keep[15] | keep[30] | keep[60]) & ~excl)
        sel_ev = np.flatnonzero(eval_mask)

        def frame(sel, with_keep):
            d = {c: (np.full(len(sel), v, np.float32) if np.isscalar(v) else v[sel])
                 for c, v in cols.items()}
            d["oblast_ua"] = pd.Categorical([raions.at[i, "oblast_ua"]] * len(sel), dtype=oblast_dt)
            d["t"] = grid[sel]
            d["raion_idx"] = np.int16(i)
            for h in HORIZONS:
                d[f"y_{h}"] = y[h][sel].astype(np.uint8)
                if with_keep:
                    d[f"keep_{h}"] = keep[h][sel]
            return pd.DataFrame(d)

        if not args.eval_only:
            train_parts.append(frame(sel_tr, True))
        eval_parts.append(frame(sel_ev, False))

    ev = pd.concat(eval_parts, ignore_index=True)
    ev.to_parquet(DATA / "eval_nights.parquet", index=False)
    print(f"grid: {n} steps x {R} raions = {n * R:,} raion-steps; grid end {t_end}")
    if not args.eval_only:
        train = pd.concat(train_parts, ignore_index=True)
        train.to_parquet(DATA / "train.parquet", index=False)
        for h in HORIZONS:
            sub = train[train[f"keep_{h}"]]
            pos = int(sub[f"y_{h}"].sum())
            print(f"h={h}: train rows kept {len(sub):,}  pos {pos:,}  neg:pos {(len(sub) - pos) / max(pos, 1):.1f}")
        print(f"train.parquet rows {len(train):,} (union of keep masks); eval_nights rows {len(ev):,}")
    else:
        print(f"eval-only: train.parquet untouched; eval_nights rows {len(ev):,}")


if __name__ == "__main__":
    main()
