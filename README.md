# Air Alert Analysis — Raion-Level Alert-Onset Prediction

Analytical research prototype (KSE Agentic AI School). Predicts which Ukrainian **raions** will transition to an air-raid alert state within the next **15 / 30 / 60 minutes**, and replays a historical mass-attack night on an interactive map with predictions shown against ground truth.

**This is not a warning system.** Alerts are a policy-driven proxy for threats, not a measurement; see *Limitations*.

## Quick start

```bash
pip install -r requirements.txt

# data pipeline (downloads Vadimkin dataset + NEPTUN boundaries)
python scripts/build_dataset.py

# optional: Telegram threat-event features (see "Telegram pipeline")
python scripts/scrape_tme.py eRadarrua 2025-12-01T00:00:00 2026-09-07T23:59:59 data/telegram/eradarrua.jsonl
python scripts/scrape_tme.py kpszsu    2025-12-01T00:00:00 2026-09-07T23:59:59 data/telegram/kpszsu.jsonl
python scripts/parse_telegram.py data/telegram/eradarrua.jsonl data/processed/threat_events_eradarrua.jsonl
python scripts/parse_telegram.py data/telegram/kpszsu.jsonl    data/processed/threat_events_kpszsu.jsonl
python scripts/adapt_threat_events.py

# model chain (add --threat-events data/processed/threat_events.parquet for Model B)
python scripts/build_features.py
python scripts/train_model.py
python scripts/evaluate.py
python scripts/export_predictions.py

# demo
python -m http.server 8765 --directory web
# open http://localhost:8765/?night=2025-12-12   (held-out demo night)
#      http://localhost:8765/?night=2026-06-20   (held-out validation night)
```

Offline demo insurance: [reports/demo_replay.mp4](reports/demo_replay.mp4) (67 s, peak of the Dec 12→13 night). Screenshots: [docs/screenshots/](docs/screenshots/).

## Approach

- **Target**: per-raion binary label — alert onset within horizon *h* ∈ {15, 30, 60} min — on a 5-min UTC grid. Official alerts are raion-level since late 2025; the modeling window is 2025-12-01 → 2026-09-07 (Vadimkin `official_data`).
- **Model**: one pooled global LightGBM classifier per horizon over 95 scored raions (23 near-permanent-alert frontline raions excluded). Features: own/neighbor-ring alert state and momentum (ADM2 adjacency graph), centroids, rolling intensity, diurnal/weekly cycles, national attack level. 5% negative downsampling; isotonic calibration with negatives re-weighted ×20.
- **Telegram block (Model B)**: 62,531 messages backfilled from @eRadarrua + @kpszsu public web previews (no account; `scripts/scrape_tme.py`), parsed by a hybrid regex+gazetteer parser (`scripts/parse_telegram.py`, optional qwen-flash fallback via DashScope) into 52,004 structured threat events → per-raion/5-min feature block.
- **Evaluation**: two held-out attack nights — 2025-12-12→13 (demo) and 2026-06-20→21 (validation) — macro/micro PR-AUC + top-30 hit rate per 5-min step, against persistence and diurnal-climatology baselines.

## Results

Model A (history-only, shipped) vs baselines on the demo night — top-30 hit rate / micro PR-AUC:

| horizon | Model A | persistence | climatology |
|---|---|---|---|
| 15 min | **0.830** / 0.131 | 0.436 / 0.042 | 0.627 / 0.061 |
| 30 min | **0.805** / 0.200 | 0.417 / 0.096 | 0.601 / 0.129 |
| 60 min | **0.802** / 0.343 | 0.364 / 0.185 | 0.592 / 0.261 |

Validation night (2026-06-20) shows the same ordering (top-30: 0.861 / 0.777 / 0.657).

**Latest period — 2026-10-08→09** (fully out-of-sample; training data ends 2026-09-07; [eval report](reports/eval_2026-10-08.md)):

| horizon | Model A | persistence | climatology |
|---|---|---|---|
| 15 min | **0.870** / 0.165 | 0.420 / 0.042 | 0.502 / 0.047 |
| 30 min | **0.810** / 0.248 | 0.424 / 0.074 | 0.517 / 0.092 |
| 60 min | **0.673** / 0.340 | 0.447 / 0.148 | 0.492 / 0.175 |

(top-30 hit / micro PR-AUC). Caveat: the 2026-09-07 → 10-08 history gap means trailing intensity features under-read early in the night. Replay it at `?night=2026-10-08`.

**Ablation — Telegram features (Model B)**: no lift. Validation PR-AUC 0.747/0.825/0.889 vs 0.750/0.833/0.895 history-only; held-out nights comparable or slightly worse ([reports/metrics_telegram_ablation.json](reports/metrics_telegram_ablation.json)). National threat-event rate ranks top-10 by gain, but per-raion Telegram features (28% raion-resolved) add noise. Model A ships; the parsing pipeline remains for future work.

## Frontend

Static page ([web/](web/), no build step): MapLibre + OpenFreeMap dark basemap + deck.gl interleaved overlay over NEPTUN raion boundaries. Replay clock with speed control and scrubber; raions glow with soft pulsing beacon columns when in the model's top-30 for the selected horizon (rank-based, matching the evaluation metric); white rings mark actual ground-truth onsets; side panel ranks raions with per-step deltas. Data contract: [docs/prediction_contract.md](docs/prediction_contract.md) — the page is a dumb renderer; swapping the replay JSON for a live stream producer requires no page changes.

## Repo map

```
scripts/build_dataset.py        # intervals, raions.csv, adjacency.json
scripts/scrape_tme.py           # t.me/s backfill (bisect + resume)
scripts/parse_telegram.py       # regex+gazetteer -> threat events (qwen-flash fallback)
scripts/adapt_threat_events.py  # events JSONL -> parquet for feature builder
scripts/build_features.py       # grid expansion, labels, features
scripts/train_model.py          # LightGBM x3 horizons + isotonic calibration
scripts/evaluate.py             # held-out nights vs baselines
scripts/export_predictions.py   # -> web/data/predictions_<night>.json
docs/model_spec.md              # frozen training design
docs/prediction_contract.md     # pipeline <-> frontend interface
GLOSSARY.md                     # domain language
```

## Data sources

- [Vadimkin/ukrainian-air-raid-sirens-dataset](https://github.com/Vadimkin/ukrainian-air-raid-sirens-dataset) — official alert intervals (CC: dataset license)
- [neptun.in.ua](https://neptun.in.ua) — raion boundaries ([terms](https://neptun.in.ua/api-terms))
- Telegram public web previews: @eRadarrua, @kpszsu (Air Force Command)
- Basemap: OpenFreeMap / CARTO (attribution in page)

## Limitations

- Alert onsets are a **delayed, policy-driven proxy** for threat presence — not per-drone tracks. All evaluation is against this proxy; cross-checks use Air Force morning summaries.
- Macro PR-AUC at long horizons is still beaten by diurnal climatology on quiet nights — honest signal that per-raion timing is dominated by routine.
- Telegram features showed no lift under this encoding; raion-resolution of free-text OSINT remains the bottleneck.
