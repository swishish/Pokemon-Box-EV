# JP PSA10 broad benchmark

Static JSON for `market-jp-psa10-bench.html`. A **representable** Japanese Pokémon TCG **PSA10** market, alongside (not instead of) the frozen ~60-card SV chase sleeve on `market-jp-psa10.html`.

Pokeca intent (not a code clone): sector floors of 80, leftover slots by sales volume, rank √(sales×price), equal-weight headline + liquidity/price with a 3% cap, quarterly membership.

| File | Role |
| --- | --- |
| `universe.json` | Discovered SNK apparel IDs (search + `data.json` / chase 60). Never invented. |
| `baskets.json` | Frozen membership per series (source of truth between reconstitutions) |
| `latest.json` / `latest.js` | Today: six series, four weight lines, breadth, movers, constituents |
| `history.json` / `history.js` | Daily points: `equal`, `liquidity`, `price`, `priceNoCap`, `volume` |
| `series-cache.json` | Per-card SNK PSA10 chart prints + last-20 trades |
| `scan-cache.json` | Last reconstitution candidate stats (resume + shortfall notes) |

## Series

| Key | Chinese | What |
| --- | --- | --- |
| `composite` | 綜合 | Headline: VINTAGE + MODERN + CURRENT (floor 80 each, leftover by √(yen×price) toward 500) |
| `vintage` | 復古 | Pre-SM, ~1996–2016 (XY / BW / DP / … as Pokeca Vintage) |
| `modern` | 中世代 SM/S | Sun & Moon + Sword/Shield, including Tag Team in-sector |
| `current` | 現行 SV/M | Scarlet & Violet + MEGA — **not** limited to Box EV `data.json` chase IDs |
| `promo` | 特典 PROMO | Sibling: Grey Felt Hat class, Munch/Picasso, Mario/Luigi/poncho Pikachu, Extra Battle Day, other liquid JP promos |
| `midera` | Tag Team / MID | Sibling thermometer: Tag Team GX window (SM8b–SM12a / SMM). Overlaps MODERN on purpose so mid-era is visible |

The old 60 on `market-jp-psa10.html` is the **SV chase sleeve**. It still builds on the nightly chase job.

## Cadence

- **Daily**: refresh SNK PSA10 prints for frozen members; **rebalance weights** from prior-day liquidity and lagged prices. Membership does not change.
- **Quarterly reconstitution**: 1 Jan / 1 Apr / 1 Jul / 1 Oct (JST). GitHub Action cron + `workflow_dispatch`. Manual override:

```bash
python3 tools/refresh_market_jp_psa10_bench.py --self-test
python3 tools/refresh_market_jp_psa10_bench.py --rebuild-baskets --confirm REBUILD -v
python3 tools/refresh_market_jp_psa10_bench.py --from-cache
python3 tools/refresh_market_jp_psa10_bench.py -v
```

`--rebuild-baskets` requires `--confirm REBUILD` (refuses to invent a basket). Discovery is public `en/v1/search` plus apparel IDs already in this repo. Price history is JP `trading-history?condition_code=trading_card_single_psa10`. View-only; no login / buy / bid / list.

If a sector cannot reach 80 after expanded search + a relaxed floor (¥1,500 / 3 print-days), `baskets.json` records the shortfall instead of inventing cards.

**JP Pokémon singles only.** EN / CN / KR / ID / TH print tags (`[SVP EN]`, `[CN]`, …) are dropped. Sealed SKUs (`Complete Set` / `全種セット` / booster boxes) are dropped. Other TCGs that leak through SNK search (e.g. Dragon Ball 孫悟空) are dropped. Grey Felt Hat’s liquid SNK listing is typically **SVP EN**, so it does not enter PROMO; JP-P equivalents would if they clear the tape floor. Van Gogh / Luigi poncho were not in the first SNK PSA10 scan (no inventing IDs). Mario / Extra Battle Day / Munch / Nagaba **singles** rank into PROMO by √(yen×price) — they are not force-inserted.

## Weights (Gary’s order)

1. **Equal** — 1/n of names with a return that day (arithmetic; not Pokeca Jevons).
2. **Liquidity** — prior-day 30d SNK sold yen (last-20 PSA10 trades, else chart-print yen). **3% cap**.
3. **Price** — lagged yen / sum. **Same 3% cap**.
4. **Price no-cap** — same lagged yen share, **overlay only** (does not change 1–3).

Volume bars = count of basket cards with a PSA10 chart print that JST day. Sparse prints: gap > 14d is a re-entry (0 return); remaining card-day returns winsorized at ±25%. Base 1,000 on the first Tokyo day with enough LOCF names.
