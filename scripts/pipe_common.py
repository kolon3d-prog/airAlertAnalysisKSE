"""Shared constants/helpers for the alert-onset model pipeline (docs/model_spec.md)."""
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data" / "processed"
MODELS = ROOT / "models"
REPORTS = ROOT / "reports"
WEB = ROOT / "web" / "data"

NIGHTS = ["2025-12-12", "2026-06-20"]
HORIZONS = (15, 30, 60)
NEG_KEEP_P = 0.05  # 20:1 negative downsampling
META_COLS = ["t", "raion_idx", "y_15", "y_30", "y_60", "keep_15", "keep_30", "keep_60"]


def night_window(night):
    """Replay window 18:00 -> 08:00 UTC next day (end exclusive)."""
    s = pd.Timestamp(night, tz="UTC") + pd.Timedelta(hours=18)
    return s, s + pd.Timedelta(hours=14)


def exclusion_window(night):
    """Held-out window plus 6h buffer before it: dropped from training."""
    s, e = night_window(night)
    return s - pd.Timedelta(hours=6), e


def scored_raions():
    r = pd.read_csv(DATA / "raions.csv")
    return r[~r["excluded"].astype(bool)].reset_index(drop=True)


def feature_cols(df):
    return [c for c in df.columns if c not in META_COLS]
