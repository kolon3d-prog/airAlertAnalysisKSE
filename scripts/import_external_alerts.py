#!/usr/bin/env python3
"""Import the external Telegram-export alert CSV (alerts_2026-10-09.csv) to intervals.

Input : data/raw/alerts_2026-10-09.csv — Ukrainian headers
        "Оголошено о","Закінчено о","Район/Громада/Місто","Регіон","Країна","Тип","Повідомлення";
        timestamps "DD.MM.YYYY, HH:MM:SS" in Kyiv local time (Oct 2026 = EEST, UTC+3).
Output: data/processed/alert_intervals_oct.parquet — same schema as
        alert_intervals.parquet (raion_key = NEPTUN key, times UTC).

Keeps only rows starting 2026-10-08+ Kyiv-local (older rows are permanent
alerts), only type "Повітряна тривога". Oblast-level rows (empty raion) are
dropped (logged). City rows map to their raion via CITY2RAION; unmapped names
are logged. Alerts still open get finished_at = latest timestamp in the file
(lower bound on the true end; the eval window ends 2026-10-09 10:00 UTC, well
before that horizon). Overlapping intervals are merged per raion. Rerunnable.
"""
import json
import re

import pandas as pd

from build_dataset import merge_intervals, norm_apostrophe, raion_key
from pipe_common import DATA, ROOT, scored_raions

SRC = ROOT / "data" / "raw" / "alerts_2026-10-09.csv"
OUT = DATA / "alert_intervals_oct.parquet"
KYIV = "Europe/Kyiv"
NEW_SINCE = pd.Timestamp("2026-10-08", tz=KYIV)
TS_FMT = "%d.%m.%Y, %H:%M:%S"

CITY2RAION = {  # normalized "м. <name>" -> NEPTUN raion key (only what's needed)
    "м. нікополь": "нікопольський",
    "м. марганець": "нікопольський",
    "м. світловодськ": "олександрійський",
    "м. запоріжжя": "запорізький",
    "м. кривий ріг": "криворізький",
    "м. харків": "харківський",
}


# renamings newer than the NEPTUN geojson, beyond build_dataset.KEY_ALIAS
EXTRA_ALIAS = {"берестинський": "красноградський", "володимирський": "володимир-волинський"}


def to_raion_key(name):
    """'Бучанський район' -> 'бучанський'; 'м. Нікополь' -> 'нікопольський'."""
    n = norm_apostrophe(str(name).strip()).lower()
    if n.startswith("м."):
        return CITY2RAION.get(n)
    k = raion_key(name)
    return EXTRA_ALIAS.get(k, k)


def main():
    df = pd.read_csv(SRC)
    df.columns = ["started_at", "finished_at", "raion", "oblast", "country", "type", "msg"]
    for c in ("started_at", "finished_at"):
        df[c] = (pd.to_datetime(df[c], format=TS_FMT, errors="coerce")
                 .dt.tz_localize(KYIV).dt.tz_convert("UTC"))
    print(f"rows total: {len(df)}")

    df = df[df["started_at"] >= NEW_SINCE].copy()
    print(f"rows starting {NEW_SINCE.date()}+ Kyiv: {len(df)}")

    dropped = df.loc[df["type"] != "Повітряна тривога", "type"].value_counts()
    df = df[df["type"] == "Повітряна тривога"].copy()
    print(f"dropped non-air-alert rows: {dropped.to_dict()}")

    n_oblast = int(df["raion"].isna().sum())
    print(f"dropped oblast-level rows (empty raion): {n_oblast}")
    df = df[df["raion"].notna()].copy()

    df["raion_key"] = df["raion"].map(to_raion_key)
    unmapped = sorted(df.loc[df["raion_key"].isna(), "raion"].unique())
    if unmapped:
        print(f"UNMAPPED raion/city names (dropped): {unmapped}")
    df = df[df["raion_key"].notna()].copy()

    geo = json.load(open(ROOT / "data" / "raw" / "raions.geojson", encoding="utf-8"))
    geo_keys = {norm_apostrophe(f["properties"]["key"]) for f in geo["features"]}
    bad = sorted(set(df["raion_key"]) - geo_keys)
    if bad:
        print(f"WARNING keys not in NEPTUN geojson (dropped): {bad}")
        df = df[df["raion_key"].isin(geo_keys)].copy()

    horizon = max(df["started_at"].max(), df["finished_at"].max())
    n_open = int(df["finished_at"].isna().sum())
    df["finished_at"] = df["finished_at"].fillna(horizon)
    print(f"open alerts (no end) -> finished_at := {horizon} (file horizon): {n_open}")

    n_bad = int((df["finished_at"] < df["started_at"]).sum())
    if n_bad:
        print(f"WARNING finished<started dropped: {n_bad}")
        df = df[df["finished_at"] >= df["started_at"]]

    intervals = []
    for key, g in df.groupby("raion_key"):
        intervals += [(key, s, e) for s, e in
                      merge_intervals(list(zip(g["started_at"], g["finished_at"])))]
    iv = pd.DataFrame(intervals, columns=["raion_key", "started_at", "finished_at"])
    iv.to_parquet(OUT, index=False)
    print(f"merged intervals: {len(iv)} (from {len(df)} rows, "
          f"{df['raion_key'].nunique()} distinct raions) -> {OUT}")

    scored = scored_raions()
    onsets = iv[iv["started_at"] >= NEW_SINCE.tz_convert("UTC")]
    have = set(onsets["raion_key"]) & set(scored["key"])
    missing = sorted(set(scored["key"]) - set(onsets["raion_key"]))
    print(f"scored raions with onsets Oct 8-9: {len(have)}/{len(scored)}")
    print(f"scored raions WITHOUT onsets: {missing}")


if __name__ == "__main__":
    main()
