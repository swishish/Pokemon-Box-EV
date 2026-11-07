# Poke Social Weather

Static shards for `social-weather.html`. Kept separate from sealed Box EV (`data.json`), JP PSA10, and the EN pulse.

| File | Role |
| --- | --- |
| `daily.json` | One row per Asia/Shanghai calendar day: composite `score` (−100…+100), label, four axes, external TCGCharts F&G (0–100), `n_posts` |
| `segments.json` | Same days, five segments: seller% / buyer% / balance tag |

The page reads these files only. It does not recompute the composite and it does not render `read_zh`, `sources`, keyword detail, notable comments, buy buttons, or private digest Target-hit lines.

## Cadence

Tue / Thu / Sat, **Asia/Shanghai**. Append workbook sheets **Daily** and **SegmentDaily**, then overwrite these two JSON files in a follow-up commit. There is no GitHub Action for this yet.

```bash
python3 tools/export_social_weather.py --self-test
```

`--xlsx` is intentionally unwired: the script refuses to write so a half-finished export cannot invent scores. Column mapping lives in that script’s docstring.

## Score

Published `daily[].score` is the source of truth on the page. The weights are X 40 / Reddit 25 / YouTube 15 / TCGCharts F&G 10 / JP shop 10. Before weighting, map the 0–100 F&G print with `(F&G − 50) × 2`, then round the weighted sum to an integer. `tools/export_social_weather.py --self-test` checks that this reproduces the committed seed (−15 / −14 / −15 / −25).

Dates are ISO `YYYY-MM-DD` (Shanghai calendar date). Excel serials convert with the 1899-12-30 epoch (`46326` → `2026-10-31`). Do not leave serials in the JSON.

## Period buttons

5D / 10D / 30D are inclusive calendar windows ending on the latest day (5D = latest − 4 days). A window stays disabled until the oldest point is on or before the window start. While the whole tape is still inside 5 calendar days and has at least two points, **5D** and **All** are both on. **YTD** stays off until a point exists on or before 1 January of the latest year. **All** draws every retained point (the line needs two).
