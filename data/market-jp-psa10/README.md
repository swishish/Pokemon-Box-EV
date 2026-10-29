# JP PSA10 index (retired public page)

`market-jp-psa10.html` is a bookmark redirect to `market-jp-psa10-bench.html`. This folder is **not** a second thermometer. The nightly job may still refresh the frozen ~60-card SV chase basket; a later cleanup can remove the workflow. Do not point nav or the homepage at this sleeve.

Static JSON below: three chained-return series from **one frozen basket** of liquid Japanese Pokémon TCG **PSA10** slabs on SNKRDUNK.

| File | Role |
| --- | --- |
| `baskets.json` | Frozen apparel / catalog IDs (source of truth for membership) |
| `latest.json` / `latest.js` | Today: three index levels, breadth, movers, constituent weights |
| `history.json` / `history.js` | Daily points: `equal`, `liquidity`, `price`, `volume` |
| `series-cache.json` | Per-card SNK PSA10 chart prints + last-20 trades (merge on refresh) |

## Refresh

Public SNKRDUNK JSON only (view-only, no login):

```
GET https://snkrdunk.com/v3/products/{catalogId}/trading-history?range=all&condition_code=trading_card_single_psa10
```

Chart timestamps are **Asia/Tokyo** midnight. Missing days: last observation carried forward; never ¥0.

Sparse SNK chart prints are noisy (multi-day gaps, occasional 8× jumps). A print whose previous actual print is **> 14 calendar days** ago is a re-entry (0 return, rebase). Remaining card-day returns are winsorized at **±25%**. Index base is 1,000 on the first Tokyo calendar day with **≥ 40** names that have a LOCF price.

```bash
python3 tools/refresh_market_jp_psa10.py --self-test
python3 tools/refresh_market_jp_psa10.py --rebuild-baskets --confirm REBUILD -v
python3 tools/refresh_market_jp_psa10.py --from-cache
python3 tools/refresh_market_jp_psa10.py -v
```

`--rebuild-baskets` scans apparel IDs already in `data.json` (this site’s JP Box EV chase cards). It does **not** invent SNK IDs. Filters: last PSA10 print ≥ ¥3,000 and ≥ 12 chart-print days in 90 days; freeze top 60 by 30-day activity. Daily job **does not** re-rank.

GitHub Action **Update JP PSA10 index** (`.github/workflows/update-market-jp-psa10.yml`): nightly + manual. PR CI only runs `--self-test`. If SNK rate-limits Actions, run the script locally and commit — the job will not wipe a good basket on a failed fetch (failed card keeps `series-cache.json`).

## Weights

- **Equal**: 1/n of names with a return that day (breadth).
- **Liquidity**: last-20 PSA10 sold yen in the prior 30 days; if none, sum of chart-print yen in that window. **3% cap**, then renormalize.
- **Price**: lagged yen / sum of lagged yen (market weight). **Same 3% cap** so a Moonbreon-class slab cannot become the whole index. If the basket is so small that 1/n ≥ 3%, the cap is infeasible and raw weights stay.

Volume bars = count of basket cards with a PSA10 **chart print that JST day**. SNK does not publish a full sold-count history (only ~20 recent trades), so bars are a lower-bound activity count, not pokeca lot volume.

## Gaps

- History starts when **40** of the frozen 60 names have a PSA10 print (not a 2024-02-08 TickerMint rebase). Earlier days exist in `series-cache.json` but do not enter the index.
- SNK `trading-history?range=all` is still a chart of sampled / recent prints, not a full sold-lot tape. Volume bars count print-days, not lots.
- Universe is Box EV chase cards with known apparel IDs, not every JP PSA10 on SNK.
- Grade A / EN printings / sealed boxes are excluded.
- Genuine multi-week rallies that print less often than every 14 days are treated as re-entries and **do not** enter the chained return (by design, to stop sparse bombs).
