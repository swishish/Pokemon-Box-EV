# Base Set vintage indexes (EN raw)

Three **fixed** chase baskets of English Base Set raw / ungraded singles:

| id | Printing | TCGCSV group | Subtype filter |
| --- | --- | --- | --- |
| `base-1st` | 1st Edition | 1663 Base Set (Shadowless) | `1st Edition` / `1st Edition Holofoil` |
| `base-shadowless` | Unlimited Shadowless (no stamp, no right-edge shadow) | 1663 | `Unlimited` / `Unlimited Holofoil` |
| `base-unlimited` | later Unlimited (with shadow) | 604 Base Set | Holofoil / Normal (not 1st Edition) |

Exact product IDs live in `baskets.json` (Holo Rare + Rare, cheatsheet #1–102 where IDs exist). Sealed, jumbo, JP, error variants (Black Dot Charizard, Red Cheeks Pikachu) are excluded. **Machamp 008/102** has no numbered TCGCSV product in either group — documented gap.

1st and Shadowless **share the same 31 BSS product IDs** (TickerMint labels Shadowless as `Unlimited` / `Unlimited Holofoil` under BSS). Unlimited has its own 31 IDs. Unique TickerMint calls ≈ **62**, not 93.

## Page: pulse + binder

`market-vintage.html` has two views on the same snapshot (no extra fetch):

| View | What |
| --- | --- |
| **指數 Pulse** (`#unlimited` default) | Index level, 1D / 5D / 30D / 1Y / YTD / All / custom sparkline (All = full available history, no rebase), plus `#overlay` three-set compare (old `#all` still opens overlay), breadth for the same movers period, movers, **lag vs index** (30D / YTD, most-lagging first), top 10, full-basket table |
| **卡冊 Binder** (`#1st/binder`) | Binder-page grid of the same basket: thumb / placeholder, `#`, name, USD, 1D % when a previous snapshot exists |

The three index tabs (1st / Shadowless / Unlimited) plus **三條 Overlay** filter both views. Binder also has name search and Holo Rare / Rare chips. Unpriced cards show **無市價**; Machamp 008 is a dashed gap pocket. Deep links: `#shadowless/binder`, `#unlimited/binder`, `#overlay`.

## Daily log

Pipeline = **append one snapshot per index per day** on whatever divisor is in `latest.json`. After a TickerMint rebase, that divisor is the 2024-02-08 = 1,000 chain.

```bash
python3 tools/refresh_market_vintage.py --self-test
python3 tools/refresh_market_vintage.py -v --force
```

GitHub Action **Update EN market pulse** (`.github/workflows/update-market-en.yml`) runs this after the EN Top 500 job, same 21:23 UTC schedule / manual dispatch. Stamp check: if `https://tcgcsv.com/last-updated.txt` matches `latest.json` `sourceStamp`, the script exits without fetching.

Writes:

- `latest.json` / `latest.js` — three indexes, constituents, movers
- `history.json` / `history.js` — one point per index per `asOfDate` (upsert that day, never wipe; keeps TickerMint backfill points)
- `price-history.json` — per-card prices for mover 1D/5D/30D/YTD. TickerMint `--backfill` / `--rebuild-movers` writes the weekly prints so Top 10 / Bottom 10 work immediately; daily TCGCSV then appends
- `cheatsheet.json` — slim 1st / Shadowless / Unlimited prices (Holo Rare + Rare only) for the sister [Base Unlimited cheatsheet](https://swishish.github.io/Base-Set-Unlimited-Cheatsheet/). Missing `usd` means no market print — do not invent. Machamp 008 is in `gaps`.

First retained snapshot of each index is rebased to **1,000**. When a listed ID later gets (or loses) a TCGPlayer Market Price, an S&P-style divisor keeps the level continuous — same as `market-en`.

## TickerMint history backfill (Gary self-run)

Free, non-commercial, 120 req/min, no key. ToS requires [linkback](https://tickermint.cards/developers/pokemon). **Do not** reuse `tools/backfill_tickermint.py` — that script picks a “main” printing and skips 1st Edition.

```bash
python3 tools/backfill_tickermint_vintage.py --self-test
python3 tools/backfill_tickermint_vintage.py --sample
python3 tools/backfill_tickermint_vintage.py --backfill --confirm BACKFILL
```

1. `--self-test`: no network. Checks printing filters, unique-ID count (62), `$0` skip, rebase confirm guard.
2. `--sample`: a few BSS + Unlimited IDs, **does not** rewrite live JSON. Seconds.
3. `--backfill --confirm BACKFILL`: ~62 unique product calls (~1 min), weekly points (`--step-days 7`, ~140/index), rebases each index to **1,000** on the first retained day, rewrites `history.json` + `latest.json` divisors **and** `price-history.json` so Top gainers / losers / Breadth use the last two priced snaps (no empty “first snapshot” UI). Today's index **level will move** off the temporary Oct-2026 = 1,000 series. Type `BACKFILL` on purpose — do not mix the old chain.
4. `--rebuild-movers`: refill `price-history.json` + mover lists from TickerMint **without** changing index levels. Use this after an older backfill that cleared `prevPrice`.

Optional later start: `--start 2024-02-28`. Daily series: `--step-days 1`.

Cache: `data/tickermint-cache/` (gitignored; shared with market-en). History JSON is committed after Gary (or the manual workflow) runs the rebase — this PR ships the script, not a regenerated 140-point file.

Rebuilding the frozen ID list (rare; does not append history):

```bash
python3 tools/refresh_market_vintage.py --rebuild-baskets -v
```
