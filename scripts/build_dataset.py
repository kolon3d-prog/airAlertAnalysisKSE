#!/usr/bin/env python3
"""Build raion-level data foundation: alert intervals, raion metadata, adjacency.

Inputs:  data/raw/official_data_uk.csv, data/raw/raions.geojson
Outputs: data/processed/alert_intervals.parquet, data/processed/raions.csv,
         data/processed/adjacency.json
All times UTC. Rerunnable.
"""
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

import pandas as pd
from shapely.geometry import shape

RAW = Path("data/raw")
OUT = Path("data/processed")
WINDOW_START = pd.Timestamp("2025-12-01", tz="UTC")
EXCLUDE_DUTY = 0.50
TOUCH_TOL_DEG = 1e-4  # ~10 m, for float-noisy shared boundaries
# dataset key -> NEPTUN geojson key for raions renamed after the geojson was built
KEY_ALIAS = {"звягельський": "новоград-волинський", "самарівський": "новомосковський"}

if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # Ukrainian names on cp1252 consoles


def norm_apostrophe(s: str) -> str:
    return s.replace("’", "'").replace("ʼ", "'")


def raion_key(name: str) -> str:
    """'Куп’янський район' -> "куп'янський" (matches NEPTUN properties.key)."""
    k = re.sub(r"\s*район\s*$", "", norm_apostrophe(str(name).strip()), flags=re.IGNORECASE).lower()
    return KEY_ALIAS.get(k, k)


def merge_intervals(iv):
    """Merge overlapping/touching [start, end] pairs."""
    iv = sorted(iv)
    out = []
    for s, e in iv:
        if out and s <= out[-1][1]:
            if e > out[-1][1]:
                out[-1][1] = e
        else:
            out.append([s, e])
    return out


def main():
    OUT.mkdir(parents=True, exist_ok=True)

    # --- geojson ---
    geo = json.load(open(RAW / "raions.geojson", encoding="utf-8"))
    geoms, names = {}, {}
    for f in geo["features"]:
        k = norm_apostrophe(f["properties"]["key"])
        geoms[k] = shape(f["geometry"])
        names[k] = f["properties"].get("rayon", k)
    print(f"geojson polygons: {len(geoms)}")

    # --- alerts ---
    df = pd.read_csv(RAW / "official_data_uk.csv")
    for c in ("started_at", "finished_at"):
        df[c] = pd.to_datetime(df[c], utc=True)
    df = df[df["started_at"] >= WINDOW_START].copy()
    print(f"rows since {WINDOW_START.date()}: {len(df)}")

    n_oblast = int((df["level"] == "oblast").sum())
    df = df[df["level"] != "oblast"]
    print(f"dropped post-switch oblast-level rows: {n_oblast}")

    # hromada rows: parent raion taken from the dataset's own raion column
    unmapped_hromada = df[(df["level"] == "hromada") & df["raion"].isna()]["hromada"].unique()
    if len(unmapped_hromada):
        print(f"UNMAPPED hromadas (no parent raion): {list(unmapped_hromada)}")
    df = df[df["raion"].notna()].copy()
    df["raion_key"] = df["raion"].map(raion_key)

    bad = sorted(set(df["raion_key"]) - set(geoms))
    if bad:
        print(f"WARNING raion keys not in geojson: {bad}")
    df = df[df["raion_key"].isin(geoms)]

    bad_rows = int((df["finished_at"] < df["started_at"]).sum())
    if bad_rows:
        print(f"WARNING rows with finished<started dropped: {bad_rows}")
        df = df[df["finished_at"] >= df["started_at"]]

    window_end = df["finished_at"].max()
    window_span = window_end - WINDOW_START
    print(f"window: {WINDOW_START} .. {window_end} ({window_span.days} days)")

    # --- merge intervals per raion, duty fraction ---
    intervals, duty = [], {}
    for key, g in df.groupby("raion_key"):
        merged = merge_intervals(list(zip(g["started_at"], g["finished_at"])))
        duty[key] = sum((e - s for s, e in merged), pd.Timedelta(0)) / window_span
        intervals += [(key, s, e) for s, e in merged]
    iv = pd.DataFrame(intervals, columns=["raion_key", "started_at", "finished_at"])
    iv.to_parquet(OUT / "alert_intervals.parquet", index=False)
    print(f"merged intervals: {len(iv)} (from {len(df)} rows)")

    # --- raions.csv ---
    oblast_of = df.groupby("raion_key")["oblast"].agg(lambda s: s.mode().iloc[0])
    rows = []
    for key in sorted(duty):
        c = geoms[key].centroid
        rows.append({
            "key": key,
            "name_ua": names[key],
            "oblast_ua": oblast_of[key],
            "centroid_lat": round(c.y, 5),
            "centroid_lon": round(c.x, 5),
            "excluded": bool(duty[key] > EXCLUDE_DUTY),
        })
    raions = pd.DataFrame(rows)
    raions.to_csv(OUT / "raions.csv", index=False)
    excl = raions[raions["excluded"]]
    print(f"scored raions: {len(raions)} | excluded (duty>{EXCLUDE_DUTY:.0%}): {len(excl)}")
    print("excluded:", ", ".join(f"{r.key} ({r.oblast_ua})" for r in excl.itertuples()))

    # --- adjacency (all geojson polygons) ---
    keys = sorted(geoms)
    adj = defaultdict(list)
    for i, a in enumerate(keys):
        for b in keys[i + 1:]:
            if geoms[a].distance(geoms[b]) < TOUCH_TOL_DEG:
                adj[a].append(b)
                adj[b].append(a)
    json.dump({k: adj.get(k, []) for k in keys},
              open(OUT / "adjacency.json", "w", encoding="utf-8"),
              ensure_ascii=False, indent=0)
    print(f"adjacency: {sum(len(v) for v in adj.values()) // 2} edges over {len(keys)} raions")

    # --- sanity stats ---
    onsets = iv.groupby("raion_key").size().sort_values(ascending=False)
    dur = (iv["finished_at"] - iv["started_at"]).dt.total_seconds() / 3600
    print("\n--- SANITY ---")
    print(f"join hit-rate: {raions['key'].isin(geoms).sum()}/{len(raions)}")
    print("top-5 raions by onset count:")
    print(onsets.head(5).to_string())
    print(f"avg alert duration: {dur.mean():.2f} h | median {dur.median():.2f} h")
    kupiansk = "куп'янський"
    print(f"'{kupiansk}' present: {kupiansk in set(raions['key'])}")


if __name__ == "__main__":
    main()
