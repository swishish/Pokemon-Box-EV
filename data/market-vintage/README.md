# Base Set vintage indexes (EN raw)

Three **fixed** chase baskets of English Base Set raw / ungraded singles:

| id | Printing | TCGCSV group | Subtype filter |
| --- | --- | --- | --- |
| `base-1st` | 1st Edition | 1663 Base Set (Shadowless) | `1st Edition` / `1st Edition Holofoil` |
| `base-shadowless` | Unlimited Shadowless (no stamp, no right-edge shadow) | 1663 | `Unlimited` / `Unlimited Holofoil` |
| `base-unlimited` | later Unlimited (with shadow) | 604 Base Set | Holofoil / Normal (not 1st Edition) |

Exact product IDs live in `baskets.json` (Holo Rare + Rare, cheatsheet #1–102 where IDs exist). Sealed, jumbo, JP, error variants (Black Dot Charizard, Red Cheeks Pikachu) are excluded. **Machamp 008/102** has no numbered TCGCSV product in either group — documented gap.

## Page: pulse + binder

`market-vintage.html` has two views on the same daily snapshot (no extra fetch):

| View | What |
| --- | --- |
| **指數 Pulse** (`#1st`) | Index level, 1D, sparkline, breadth, movers, top 10, full-basket table |
| **卡冊 Binder** (`#1st/binder`) | Binder-page grid of the same basket: thumb / placeholder, `#`, name, USD, 1D % when a previous snapshot exists |

The three index tabs (1st / Shadowless / Unlimited) filter both views. Binder also has name search and Holo Rare / Rare chips. Unpriced cards show **無市價**; Machamp 008 is a dashed gap pocket. Deep links: `#shadowless/binder`, `#unlimited/binder`.

## Daily log (v1)

Pipeline = **append one snapshot per index per day**. That is the whole history story for v1.

```bash
python3 tools/refresh_market_vintage.py --self-test
python3 tools/refresh_market_vintage.py -v --force
```

GitHub Action **Update EN market pulse** (`.github/workflows/update-market-en.yml`) runs this after the EN Top 500 job, same 21:23 UTC schedule / manual dispatch. Stamp check: if `https://tcgcsv.com/last-updated.txt` matches `latest.json` `sourceStamp`, the script exits without fetching.

Writes:

- `latest.json` / `latest.js` — three indexes, constituents, movers
- `history.json` / `history.js` — one point per index per `asOfDate` (upsert that day, never wipe)
- `price-history.json` — per-card prices for mover 1D/5D/30D/YTD once enough days exist

First retained daily snapshot of each index is rebased to **1,000**. When a listed ID later gets (or loses) a TCGPlayer Market Price, an S&P-style divisor keeps the level continuous — same as `market-en`.

## Backfill is deferred

**Do not** wire TickerMint or TCGCSV dated-archive replay for these vintage indexes yet. Period buttons (5D / 30D / YTD) stay N/A until enough **daily** points accumulate. Missing days are skipped; returns are never interpolated.

Rebuilding the frozen ID list (rare; does not append history):

```bash
python3 tools/refresh_market_vintage.py --rebuild-baskets -v
```
