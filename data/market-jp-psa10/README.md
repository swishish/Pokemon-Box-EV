# JP PSA10 chase sleeve (frozen)

Retired. `market-jp-psa10.html` redirects to `market-jp-psa10-bench.html` (JP 基準). This folder is **not** a live index and **not** a second thermometer.

The nightly GitHub Action **Update JP PSA10 index** (`.github/workflows/update-market-jp-psa10.yml`) and `tools/refresh_market_jp_psa10.py` are removed. Nothing in Actions fetches SNK or commits into this folder. Last written snapshot: **2026-10-29** (`latest.json` `asOfDate`). Membership in `baskets.json` was last rebuilt **2026-10-19**.

Do not point nav or the homepage at this sleeve. Do not fold these ~60 cards into the JP 基準 baskets — membership rules are different. JP 基準 keeps its own daily job (`update-market-jp-psa10-bench.yml`).

## Files (historical)

Three chained-return series from **one frozen basket** of liquid Japanese Pokémon TCG **PSA10** slabs on SNKRDUNK. Kept so the tape is not thrown away.

| File | Role |
| --- | --- |
| `baskets.json` | Frozen apparel / catalog IDs (membership as of 2026-10-19) |
| `latest.json` / `latest.js` | Last snapshot (2026-10-29): three index levels, breadth, movers, constituent weights |
| `history.json` / `history.js` | Daily points through that date: `equal`, `liquidity`, `price`, `volume` |
| `series-cache.json` | Per-card SNK PSA10 chart prints + last-20 trades, frozen with the last fetch |

`baskets.json` is still **read** as a list of already-known SNK apparel IDs:

- JP 基準 discovery (`tools/refresh_market_jp_psa10_bench.py`) may seed its search universe from these IDs. That does not copy this basket’s membership rules into the benchmark.
- Chase_Fit thumbnails (`tools/build_chase_thumbs.py`) may reuse image URLs already stored here.

Neither path writes this folder.

## What the series was

Public SNKRDUNK trading-history only (`condition_code=trading_card_single_psa10`). Chart timestamps are **Asia/Tokyo** midnight. Missing days used last observation carried forward, never ¥0.

The basket was apparel IDs already in `data.json` (this site’s JP Box EV chase cards). Filters: last PSA10 print ≥ ¥3,000 and ≥ 12 chart-print days in 90 days; freeze top 60 by 30-day activity. The daily job did not re-rank.

Sparse prints: a print whose previous actual print was **> 14 calendar days** ago was a re-entry (0 return). Remaining card-day returns were winsorized at **±25%**. Index base is 1,000 on the first Tokyo calendar day with **≥ 40** names that have a LOCF price.

## Weights

- **Equal**: 1/n of names with a return that day.
- **Liquidity**: last-20 PSA10 sold yen in the prior 30 days; if none, sum of chart-print yen in that window. **3% cap**, then renormalize.
- **Price**: lagged yen / sum of lagged yen (market weight). **Same 3% cap** so a Moonbreon-class slab cannot become the whole index. If the basket is so small that 1/n ≥ 3%, the cap is infeasible and raw weights stay.

Volume bars = count of basket cards with a PSA10 chart print that JST day (a lower-bound activity count, not lot volume).
