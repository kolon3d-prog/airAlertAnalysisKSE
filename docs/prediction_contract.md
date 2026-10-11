# Prediction Contract

The interface between the Python pipeline and the static replay page. The page is a dumb renderer; swapping the replay reader for a live stream later means producing the same JSON shape live.

## File: `web/data/predictions_<night>.json`

One file per replay night, e.g. `predictions_2025-12-12.json`.

```json
{
  "night": "2025-12-12",
  "generated_at": "2026-10-11T12:00:00Z",
  "step_minutes": 5,
  "horizons_min": [15, 30, 60],
  "raions": [
    {"key": "куп'янський", "name_ua": "Куп'янський район", "oblast_ua": "Харківська область", "lat": 49.71, "lon": 37.62}
  ],
  "steps": [
    {
      "t": "2025-12-12T18:00:00Z",
      "alert": [3, 17, 42],
      "onset_15": [42],
      "onset_30": [42, 55],
      "onset_60": [42, 55, 71],
      "pred": {
        "15": [0.02, 0.61, "..."],
        "30": ["..."],
        "60": ["..."]
      }
    }
  ]
}
```

## Rules

- `raions` is the canonical ordered list (all scored raions, permanent-alert raions excluded). All per-raion arrays align to this index order. `key` matches NEPTUN `raions.geojson` `properties.key`.
- `steps` covers the replay window (18:00→08:00 UTC default) at `step_minutes` spacing, UTC ISO 8601 with `Z`.
- `alert`: indices of raions under an active alert at step start (ground truth).
- `onset_<h>`: indices of raions whose next alert onset occurs within horizon `h` after this step (ground-truth labels, for evaluation overlay).
- `pred.<h>`: probability per raion (0–1, rounded to 3 decimals) for horizon `h`. Missing raions = not scored (excluded) → rendered neutral.
- A `meta` object may be added per file: `{"model": "...", "pr_auc": {"15": 0.0, "30": 0.0, "60": 0.0}, "top30_hit": {...}}` — the page shows it in the side panel footer if present.

## Geo assets

- `web/data/raions.geojson` — verbatim copy of https://neptun.in.ua/raions.geojson. The page joins on `properties.key`.
