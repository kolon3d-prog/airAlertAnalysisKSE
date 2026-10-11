# Model Spec (frozen at design confirmation)

## Task
Per-raion binary classification: P(alert onset within horizon h) on a 5-min UTC grid, h ∈ {15, 30, 60}. One pooled global LightGBM per horizon over all scored raions.

## Inputs
- `data/processed/alert_intervals.parquet` — merged [raion_key, started_at, finished_at], window 2025-12-01 → end.
- `data/processed/raions.csv` — key, name_ua, oblast_ua, centroid_lat/lon, excluded.
- `data/processed/adjacency.json` — raion_key → neighbor keys.
- `data/processed/threat_events.parquet` (when parser lands) — ts, raion_keys[], oblasts_ua[], threat_type, heading, count, confidence.

## Labels
For raion r, grid time t (step start): y_h = 1 iff an alert interval for r starts in (t, t+h]. Raions already in alert at t are still scored (onset = next onset after current alert ends) — but excluded raions (excluded=True) are dropped from training and scoring entirely.

## Feature blocks (per raion×step row)
1. State: in_alert_now, minutes_in_alert_so_far (capped 720), minutes_since_last_alert_end (capped 7d).
2. Momentum: onsets in own raion last 30/60/180 min; alerts active now count.
3. Neighbor ring (adjacency.json): neighbors in alert now (count, fraction), neighbor onsets last 30/60 min, min distance-in-hops to any active alert (0,1,2+).
4. Spatial: centroid_lat, centroid_lon, oblast one-hot.
5. Temporal: hour-of-day (sin/cos), day-of-week, is_night (22–06 local).
6. National: total raions in alert now, onsets nationwide last 30 min, attack_night_in_progress flag (>20 raions in alert).
7. Telegram (additive when available): threat events in own raion last 15/30/60 min by type (uav/cruise/ballistic/kab/mig31k counts), heading-toward flags, neighbor-ring event counts, national event rate. All zeros when the block is absent.

## Sampling
- Grid: every 5 min from 2025-12-01 00:00 UTC to data end.
- Negatives: downsample 20:1 per horizon (keep all positives).
- Held-out nights (excluded from train): 2025-12-12 18:00 → 2025-12-13 08:00 UTC and 2026-06-20 18:00 → 2026-06-21 08:00 UTC, plus a 6-hour buffer before each window.
- Validation for early stopping: last 14 days of the training window (time-based, no shuffle).

## Training
- LightGBM, binary logloss, early stopping on validation PR-AUC, class imbalance via the downsampling (no scale_pos_weight).
- Calibration: isotonic or Platt on the validation tail — report calibration slope; the UI threshold (0.5) assumes calibrated probabilities.

## Evaluation (held-out nights, per horizon)
- Macro PR-AUC (average over raions of per-raion AP), micro PR-AUC.
- Top-30 hit rate per 5-min step: fraction of actual onsets in the next h minutes whose raion is in the model's top-30 probabilities at that step (steps with zero onsets excluded).
- Baselines to beat: (a) persistence — P = f(neighbor activity, current state) via a hand rule: score = 0.9 if in alert, else neighbor fraction; (b) diurnal climatology — per-raion onset rate by hour-of-day.
- Output: reports/eval_<night>.md + reports/metrics.json; per-night predictions exported per docs/prediction_contract.md → web/data/predictions_<night>.json (alert/onset arrays from ground truth; excluded raions omitted from pred arrays as nulls).
