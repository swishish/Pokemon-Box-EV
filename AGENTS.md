# AGENTS.md

Standing checks for Cursor cloud agents working on this site.

## UI checks (required)

Before marking **UI Ready** or merging any UI change:

- Verify **both mobile and desktop viewports**.
- Confirm there is **no overflow, misalignment, hidden sections, or layout bugs**.
- Prefer checking the **live/preview UI**, not only code review.

Preview: `python3 -m http.server 8765` then open `http://127.0.0.1:8765/` (or the GitHub Pages URL).

## Weekly Chase_Fit snapshot

Public page `chase-fit.html` is a **static** JSON snapshot. Market pages auto-update via `.github/workflows/update-market-en.yml`; **Chase 公允值 and Box EV do not.**

After Gary drops the dated workbook (`pokemon_psa10_regression_YYYY-MM-DD.xlsx`):

```bash
python3 tools/export_chase_fit.py --self-test
python3 tools/export_chase_fit.py /path/to/pokemon_psa10_regression_YYYY-MM-DD.xlsx
```

- Publishes sheet **Chase_Fit** only (not `Chase_Fit_Large`).
- Archives the previous `data/chase-fit/latest.json` to `data/chase-fit/archive/YYYY-MM-DD.json`.
- Diffs Strong Buy labels into `weekly_changes` (新進 / 退出 / 維持) for the 「本週變動」 box.
- Refuses to write if required columns / coefficients / PriceCharting URLs are missing.
- Bump `chase-fit.html` `?v=` if the page HTML also changed.

Vintage_Fit Strong Buys (e.g. 臭泥 Grimer #88) live on the Shortlist **vintage** rows — they are **not** on this page unless we later add Vintage_Fit.

## Weekly Box EV snapshot (manual)

`tools/refresh_box_ev.py` **cannot** run without hand-saved PriceCharting console HTML:

```bash
# TODO: save each set's PC console page first, e.g. /tmp/pc-sets/M6.html
python3 tools/refresh_box_ev.py --root . --pc-dir /tmp/pc-sets
```

If `/tmp/pc-sets` is empty the script **exits without writing** (will not invent prices or bump `as_of`). SNKRDUNK Grade A asks in `data.json` are also not auto-refreshed. `calc.html` turns the freshness banner orange after ≥8 days.
