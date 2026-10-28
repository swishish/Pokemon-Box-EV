# English Base indexes (PriceCharting)

Four **fixed** chase baskets of English raw / ungraded singles. Price source is **PriceCharting only**. This is not the JP PSA10 SNKRDUNK benchmark and not the TCGPlayer daily tape in `data/market-vintage/`.

| id | Printing | Cards | History start | Anchor |
| --- | --- | --- | --- | --- |
| `base-1st` | Base Set 1st Edition | 32 | 2021-01-01 | 2023-01-01 = 1000 |
| `base-shadowless` | Base Set Shadowless | 32 | 2021-01-01 | 2023-01-01 = 1000 |
| `base-unlimited` | Base Set Unlimited | 31 | 2020-12-01 | 2023-01-01 = 1000 |
| `base-set-2` | Base Set 2 | 29 | 2020-12-01 | 2023-01-01 = 1000 |

1st Edition’s December 2020 print was too thin (fewer than 15 positive `used` prices), so that index’s first **kept** month is 2021-01-01. The other three are thick from 2020-12-01. All four include 2023-01-01.

## Basket

Same chase cut as the TCGPlayer vintage page: Base Set numbers **1–22 and 70–79** (Holo Rare + Rare). Commons, uncommons, and energy (including Double Colorless #96) are out.

Slugs are copied from `data/base-psa-pop/catalog.json` (PriceCharting console URIs, not invented). Numeric product ids and images are copied from the public console JSON (`id`, `imageUri`).

| Gap | Why |
| --- | --- |
| Machamp Unlimited | No Unlimited slug in the catalog |
| Machamp, Clefairy Doll, Devolution Spray on Base Set 2 | Not in the PriceCharting `pokemon-base-set-2` console listing |

## Price and rebase

- Column: game-page `VGPC.chart_data.used` (ungraded / loose). Cents → USD. **Zero is “no print” and is dropped.**
- Cadence: **one point per month** (about the 1st, UTC). PriceCharting does not publish a weekly series on that chart, so none is interpolated.
- Weighting: price-weighted sum of that month’s positive `used` prices. A month needs **≥ 15** priced cards. The month-to-month return uses the **overlap** with the previous kept month (≥ 12 names) so a missing card is not treated as $0. The divisor resets so the level does not jump when the priced set changes.
- Rebase: scale the chain so **2023-01-01 = 1,000**. Earlier months stay on that same chain (they are not rebased to 1,000 themselves).

## Where it shows

- `market-vintage.html#pc/1st` (also `#pc/shadowless`, `#pc/unlimited`, `#pc/base2`, `#pc/overlay`) — view **PC 月線**
- `market-jp-psa10-bench.html#en-1st` (and `#en-shadowless`, `#en-unlimited`, `#en-base2`) — the EN row’s **primary** line is the daily TCG/TickerMint tape in `data/market-vintage/` (2024-02-08 = 1000). This monthly series is the optional **PC 月線** overlay on that chart, and the long history on `market-vintage.html#pc/…`. The six JP SNK series are unchanged. `#current` still opens the JP current sector.

## Refresh

```bash
python3 tools/refresh_market_vintage_pc.py --self-test
python3 tools/refresh_market_vintage_pc.py --sample   # four Charizard pages, writes nothing
python3 tools/refresh_market_vintage_pc.py --root . -v
python3 tools/refresh_market_vintage_pc.py --from-cache   # rebuild JSON from data/market-vintage-pc/cache/
```

GitHub Action **Update EN Base PriceCharting indexes** (`.github/workflows/update-market-vintage-pc.yml`) rebuilds the full monthly chain and commits `data/market-vintage-pc/`. It does not run inside the TCGCSV daily job. A failed fetch does not invent prices or product ids.

`cache/` holds parsed `used` series so a rerun can skip pages. It is gitignored. The committed files are `baskets.json`, `latest.json`, `history.json`, `price-history.json`, and the `.js` fallbacks.
