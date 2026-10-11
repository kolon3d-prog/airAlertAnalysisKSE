"""Emit web/data/signals_<night>.json — OSINT (telegram) report counts.

For each step of web/data/predictions_<night>.json, count threat_events rows
mentioning each raion in the trailing 60 min before the step. Output is
index-aligned to the prediction steps; only nonzero raion entries are kept.
"""
import json

import numpy as np
import pandas as pd

from pipe_common import DATA, NIGHTS, WEB

WINDOW_NS = pd.Timedelta(minutes=60).value


def main():
    ev = pd.read_parquet(DATA / "threat_events.parquet", columns=["ts", "raion_keys"])
    ev = ev.explode("raion_keys").dropna(subset=["raion_keys"])
    print(f"events {ev['ts'].min()} .. {ev['ts'].max()}, rows w/ raion: {len(ev)}")

    for night in NIGHTS:
        pred = json.loads((WEB / f"predictions_{night}.json").read_text(encoding="utf-8"))
        keys = [r["key"] for r in pred["raions"]]
        key2i = {k: i for i, k in enumerate(keys)}
        ts = pd.to_datetime([s["t"] for s in pred["steps"]], utc=True).asi8
        tg = [{} for _ in ts]
        sub = ev[ev["raion_keys"].isin(key2i)]
        for key, g in sub.groupby("raion_keys"):
            et = np.sort(g["ts"].astype("int64").to_numpy())
            cnt = (np.searchsorted(et, ts, "left")
                   - np.searchsorted(et, ts - WINDOW_NS, "left"))
            i = key2i[key]
            for s in np.flatnonzero(cnt):
                tg[s][str(i)] = int(cnt[s])
        steps = [{"t": s["t"], "tg": tg[i]} for i, s in enumerate(pred["steps"])]
        out = WEB / f"signals_{night}.json"
        out.write_text(json.dumps({"steps": steps}, ensure_ascii=False,
                                  separators=(",", ":")), encoding="utf-8")
        print(f"wrote {out} ({out.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
