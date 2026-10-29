# JP PSA10 broad benchmark

Static JSON for `market-jp-psa10-bench.html`. This is the public Japanese Pokémon TCG **PSA10** benchmark (復古／中世代／現行 + sibling baskets). EN Base daily indexes are a monitor on the same page, not a second product. `market-jp-psa10.html` only redirects here.

Pokeca intent (not a code clone): era sectors + √(sales×price) rank for display, equal-weight headline + liquidity/price with a 3% cap, quarterly membership. **Sector N is not a Pokeca floor of 80.** Membership is whatever clears the liquidity/price screens; composite is the union of the three era sectors.

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
| `composite` | 綜合 | Headline: union of screened VINTAGE + MODERN + CURRENT (N floats; no pad / no truncate) |
| `vintage` | 復古 | Pre-SM, ~1996–2016 (XY / BW / DP / … as Pokeca Vintage) |
| `modern` | 中世代 SM/S | Sun & Moon + Sword/Shield, including Tag Team in-sector |
| `current` | 現行 SV/M | Scarlet & Violet + MEGA — **not** limited to Box EV `data.json` chase IDs |
| `promo` | 特典 PROMO | Sibling: Grey Felt Hat class, Munch/Picasso, Mario/Luigi/poncho Pikachu, Extra Battle Day, other liquid JP promos |
| `midera` | Tag Team / MID | Sibling thermometer: Tag Team GX window (SM8b–SM12a / SMM). Overlaps MODERN on purpose so mid-era is visible |

The old ~60-card SV chase sleeve is no longer a public page. `market-jp-psa10.html` redirects here. `data/market-jp-psa10/` may still refresh on its own nightly job until that pipeline is removed.

## Cadence

- **Daily**: refresh SNK PSA10 prints for frozen members; **rebalance weights** from prior-day liquidity and lagged prices. Membership does not change.
- **Quarterly reconstitution**: 1 Jan / 1 Apr / 1 Jul / 1 Oct (JST). GitHub Action cron + `workflow_dispatch`. Manual override:

```bash
python3 tools/refresh_market_jp_psa10_bench.py --self-test
python3 tools/refresh_market_jp_psa10_bench.py --rebuild-baskets --confirm REBUILD -v
python3 tools/refresh_market_jp_psa10_bench.py --reselect -v
python3 tools/refresh_market_jp_psa10_bench.py --from-cache
python3 tools/refresh_market_jp_psa10_bench.py -v
```

`--rebuild-baskets` requires `--confirm REBUILD` (refuses to invent a basket). `--reselect` re-ranks from `scan-cache.json` + `series-cache.json` (fetches only missing PSA10 histories). Discovery is public `en/v1/search` plus apparel IDs already in this repo. Price history is JP `trading-history?condition_code=trading_card_single_psa10`. View-only; no login / buy / bid / list.

## Screens (N floats)

| Basket | Min last PSA10 print | Min 90-day print-days |
| --- | --- | --- |
| Vintage | ¥2,000 | 5 |
| Modern / Current | ¥2,000 | 6 |
| Promo | ¥2,500 | 5 |
| Tag Team / MID | ¥2,000 | 5 |

Print-days = distinct JST calendar days with a SNK PSA10 chart print (not Pokeca’s raw sale count). Rank inside a sector is still √(30d SNK yen × last print) for display; **every card that passes is kept**.

If a primary screen yields fewer than 20 names, that sector only loosens to ¥1,500 / 3 print-days and still keeps every passer. That is **not** a floor: N may remain below 20. We do not invent IDs and we do not pad/truncate to 80 or 500.

JP Pokémon singles only. EN / CN / KR / ID / TH print tags, sealed SKUs, and other TCGs are dropped (same as before).

## 規模依據 (peer sizes, not our N)

| Peer | Typical N | Role |
| --- | --- | --- |
| POKECA PSA INDEX | ~80+ per Vintage/Modern/Current historically; live TOTAL **~489** (target 500, Jevons) | Closest JP PSA10 sector model. They **floor 80** then fill leftover to 500. We keep the sector cut and screens, not the forced N. |
| Grade10 PTCG100 / PTCG500 | **100** / **500** | Broad PSA10 benchmarks, fixed top-N by 90d volume (EN+JP). Soft composite peer band. |
| Renaiss Pokémon | **~50** | Chase-scale Top 50, monthly. Not a broad-market size. |
| Former chase sleeve | **~60** | Retired public page. `market-jp-psa10.html` redirects to this benchmark. |

Soft composite band ~300–500 is a peer reference only. After these screens the live union is larger than 500 because Modern/Current SNK tape is deep; we refuse to cut liquid names to fake Pokeca’s 500.

Live reconstitution (`--reselect`, 2026-10-20) after this rule change:

| Series | Before (forced 80 / leftover-to-500) | After (screens only) |
| --- | --- | --- |
| Vintage | 80 | **77** (¥2,000 / 5 print-days; not loosened) |
| Modern | 80 | **358** (¥2,000 / 6) |
| Current | 80 | **446** (¥2,000 / 6) |
| Composite | 500 | **881** (union) |
| Promo | 80 | **96** (¥2,500 / 5) |
| Tag Team / MID | 80 | **134** (¥2,000 / 5) |

**JP Pokémon singles only.** EN / CN / KR / ID / TH print tags (`[SVP EN]`, `[CN]`, …) are dropped. Sealed SKUs (`Complete Set` / `全種セット` / booster boxes) are dropped. Other TCGs that leak through SNK search (e.g. Dragon Ball 孫悟空) are dropped. Grey Felt Hat’s liquid SNK listing is typically **SVP EN**, so it does not enter PROMO; JP-P equivalents would if they clear the tape floor. Van Gogh / Luigi poncho were not in the first SNK PSA10 scan (no inventing IDs). Mario / Extra Battle Day / Munch / Nagaba **singles** rank into PROMO by √(yen×price) — they are not force-inserted.

## Weights (Gary’s order)

1. **Equal** — 1/n of names with a return that day (arithmetic; not Pokeca Jevons).
2. **Liquidity** — prior-day 30d SNK sold yen (last-20 PSA10 trades, else chart-print yen). **3% cap**.
3. **Price** — lagged yen / sum. **Same 3% cap**.
4. **Price no-cap** — same lagged yen share, **overlay only** (does not change 1–3).

Volume bars = count of basket cards with a PSA10 chart print that JST day. Sparse prints: gap > 14d is a re-entry (0 return); remaining card-day returns winsorized at ±25%. Base 1,000 on the first Tokyo day with enough LOCF names.
