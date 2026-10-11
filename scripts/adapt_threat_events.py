"""Adapt parser JSONL threat events to the parquet schema build_features.py expects.

In:  data/processed/threat_events_<channel>.jsonl  (parser output)
Out: data/processed/threat_events.parquet with columns
     ts (UTC), raion_keys (list[str]), threat_type, heading, count, confidence

Mapping decisions:
- heading: E's loader flags heading=='toward'. Parser headings are 8-wind enums
  meaning "threat on stated course" (i.e. moving/transiting). We map any non-null
  heading -> 'toward', null -> None. Documented approximation: the toward flag
  means "threat with known course near this raion", not geometric ray-casting.
- dedup: reply/quote duplicates share text; drop by sha1(normalized text).
- events with empty raion_keys are kept (national-rate features use them).
"""
import glob, json, sys

import pandas as pd

rows, seen = [], set()
for path in glob.glob("data/processed/threat_events_*.jsonl"):
    if "sample" in path:
        continue
    for line in open(path, encoding="utf-8"):
        r = json.loads(line)
        # no text field in parser output; dedup reply/quote dupes by a 60s-bucket signature
        ts = pd.Timestamp(r["ts"])
        sig = (r["channel"], int(ts.timestamp()) // 60, r.get("threat_type"),
               tuple(sorted(r.get("raion_keys") or [])), r.get("heading"), r.get("count"))
        if sig in seen:
            continue
        seen.add(sig)
        rows.append({
            "ts": r["ts"],
            "raion_keys": r.get("raion_keys") or [],
            "threat_type": {"cruise_missile": "cruise"}.get(r.get("threat_type"), r.get("threat_type")),
            "heading": "toward" if r.get("heading") else None,
            "count": r.get("count") or 1,
            "confidence": r.get("confidence") or "regex",
        })

df = pd.DataFrame(rows)
df["ts"] = pd.to_datetime(df["ts"], utc=True)
df = df.sort_values("ts").reset_index(drop=True)
df.to_parquet("data/processed/threat_events.parquet", index=False)
print(f"{len(df)} events -> data/processed/threat_events.parquet")
print(df["threat_type"].value_counts().to_string())
print("with raion:", df["raion_keys"].map(bool).mean().round(3), "| with heading:", df["heading"].notna().mean().round(3))
