# Market, Chase_Fit, and Box EV runbook

Procedures for this site. `AGENTS.md` is the short index (UI Ready, product boundaries, secret-env hook). Follow this file when refreshing data or changing these pages. The boundaries in `AGENTS.md` still apply; this is the how-to, relocated so it is not loaded on every turn.

## UI checks (required)

Before marking **UI Ready** or merging any UI change:

- Verify **both mobile and desktop viewports**.
- Confirm there is **no overflow, misalignment, hidden sections, or layout bugs**.
- Prefer checking the **live/preview UI**, not only code review.

Preview: `python3 -m http.server 8765` then open `http://127.0.0.1:8765/` (or the GitHub Pages URL).

Market pages: EN / vintage / mid-era via `.github/workflows/update-market-en.yml` (daily). JP PSA10 **broad benchmark** (`market-jp-psa10-bench.html`) via `update-market-jp-psa10-bench.yml` (daily weights, quarterly membership) is the JP PSA10 product: 復古／中世代／現行 plus sibling baskets. EN Base daily indexes stay on that same page as 監察／旁路 (`#en-1st` and siblings), not a second product page. English Base PriceCharting monthly indexes refresh on their own schedule via `update-market-vintage-pc.yml`. `market-jp-psa10.html` is a bookmark redirect to the benchmark. The ~60-card SV chase sleeve nightly is gone (`update-market-jp-psa10.yml` removed). `data/market-jp-psa10/` is frozen history, not a public page, and nothing should commit new files there. Do not bring that sleeve back as a parallel thermometer, and do not fold its 60-card membership into JP 基準.

English Base **daily** indexes (1st Edition, Shadowless, Unlimited, Base Set 2) live in `data/market-vintage/` (TCGPlayer market via TickerMint + TCGCSV). Anchor is the first retained day, **2024-02-08 = 1000** when that day is kept. That chain is not the PriceCharting monthly chain. The bench page’s EN Base row (`#en-1st`, `#en-shadowless`, `#en-unlimited`, `#en-base2`) draws this daily tape as the primary series and may overlay PriceCharting. Do not source them from SNKRDUNK, and do not replace the JP PSA10 series with them.

English Base **PriceCharting** indexes stay in `data/market-vintage-pc/` and refresh via `update-market-vintage-pc.yml`. They are ungraded `used` prices, monthly, rebased so 2023-01-01 = 1000. Open them at `market-vintage.html#pc/1st` (also shadowless / unlimited / base2). Keep that data and workflow. Do not mix the two anchors.

## Base Set PSA POP

`market-base-psa-pop.html` reads `data/base-psa-pop/latest.json`. Population counts and indicative prices both come from public PriceCharting HTML (no API key). A parse or POP fetch error exits non-zero and does not write. A failed price fetch keeps the previous `pc_prices` (it does not invent a price).

GitHub Action **Update Base PSA pop** (`.github/workflows/update-base-psa-pop.yml`):

- **POP census, monthly:** `17 11 1 * *` (11:17 UTC on the 1st, 19:17 HKT). Runs `python3 tools/refresh_base_psa_pop.py --root .` (POP pages and game-page prices). PSA population lags, so a monthly cross-check is enough. Do not put this census back on a weekly cron.
- **Prices, weekly:** `17 11 * * 1` (Monday 11:17 UTC, 19:17 HKT). Runs `python3 tools/refresh_base_psa_pop.py --root . --prices-only`. That patches `pc_prices` onto the existing snapshot, does not re-fetch POP, and does not move `asOfDate`.
- **Manual:** workflow_dispatch `mode=full` (default) or `mode=prices-only`.
- **Pull requests:** `--self-test` only. No network write.

Stay on PriceCharting HTML. Do not add GemRate or PokeInvest in this job. Base Set 2 remains skippable unless the catalog marks that card `required` (a required card's Base Set 2 miss still fails the job, with no invented pops).

```bash
python3 tools/refresh_base_psa_pop.py --self-test
python3 tools/refresh_base_psa_pop.py --root .                 # monthly POP + prices
python3 tools/refresh_base_psa_pop.py --root . --prices-only   # weekly prices
```

## Weekly Chase_Fit snapshot

Public page `chase-fit.html` is a **static** JSON snapshot. Market pages auto-update via `.github/workflows/update-market-en.yml`; **Chase 公允值 and Box EV do not.**

After the maintainer drops the dated workbook (`pokemon_psa10_regression_YYYY-MM-DD.xlsx`):

```bash
python3 tools/export_chase_fit.py --self-test
python3 tools/build_chase_thumbs.py --self-test
python3 tools/export_chase_fit.py /path/to/pokemon_psa10_regression_YYYY-MM-DD.xlsx
```

- Publishes sheet **Chase_Fit** only (not `Chase_Fit_Large`).
- Archives the previous `data/chase-fit/latest.json` to `data/chase-fit/archive/YYYY-MM-DD.json`.
- Diffs Strong Buy labels into `weekly_changes` (新進 / 退出 / 維持) for the 「本週變動」 box.
- The live page places the regression scatter (and POP chart + detail) under the filters, then the 「便宜過模型」 buy list (`data/names_zh.json`, `data/snkrdunk_ids.json`). Do not invent Chinese names or SNKRDUNK apparel IDs.
- After a successful write, rebuilds `data/chase-fit/thumbs.json` (`tools/build_chase_thumbs.py`). JP = local calc webps (PC URL or set+number) then already-stored / public SNKRDUNK `upload_bg_removed` thumbs; EN = TCGPlayer CDN already on market-en/midera/vintage, then verified pokemontcg.io / TCGdex images. Missing → page placeholder. Do not invent SNKR/PC image hashes.
- Refuses to write if required columns / coefficients / PriceCharting URLs are missing.
- Bump `chase-fit.html` `?v=` if the page HTML also changed.

Vintage_Fit Strong Buys (e.g. 臭泥 Grimer #88) live on the Shortlist **vintage** rows — they are **not** on this page unless we later add Vintage_Fit.

## Weekly Box EV snapshot (manual)

Sealed **box denoms** (有膜／無膜 landed yen) can be refreshed from public SNKRDUNK apparel JSON + PriceCharting booster-box HTML — view-only, no login:

```bash
python3 tools/refresh_sealed_boxes.py --self-test
python3 tools/refresh_sealed_boxes.py --root .
```

Or GitHub → Actions → **Refresh sealed box prices** → Run workflow. Missing fetches keep the previous snapshot (will not invent apparel IDs or prices). This does **not** refresh card Grade A / pity rates.

`tools/refresh_box_ev.py` **cannot** run without hand-saved PriceCharting console HTML:

```bash
# TODO: save each set's PC console page first, e.g. /tmp/pc-sets/M6.html
python3 tools/refresh_box_ev.py --root . --pc-dir /tmp/pc-sets
```

If `/tmp/pc-sets` is empty the script **exits without writing** (will not invent prices or bump `as_of`). SNKRDUNK Grade A **singles** in `data.json` are also not auto-refreshed. `calc.html` turns the freshness banner orange after ≥8 days (card snapshot `as_of`).
