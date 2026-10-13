# Mid-era Top 50 (EN raw, DP → SM)

Price-weighted chase index for English singles from **Diamond & Pearl through Sun & Moon** (includes BW / XY / SM). Excludes Wizards vintage and Sword & Shield / Scarlet & Violet modern.

This is a **frozen** list of ~50 liquid chase IDs — not a weekly rebalance like a Double Holo Mid-Era 100.

## How the ~50 were chosen

1. Start from the committed EN Top 500 (`data/market-en/latest.json`, asOf **2026-10-12**). Those names already have a TCGPlayer Market Price (liquidity screen).
2. Keep only mid-era sets: DP / Platinum / HGSS / BW / XY / SM (and era promos such as DP / BW / XY / SM Black Star, Call of Legends shinies, Hidden Fates Shiny Vault, Pokémon Rumble).
3. Drop Wizards, EX-era, SWSH, SV, mixed junk (Prize Pack, League & Championship, World Championship decks).
4. Rank remaining names by that snapshot’s market price. Freeze the **top 50** product IDs in `baskets.json`.
5. Daily refresh **does not** rebuild the list. Maintenance only: `python3 tools/refresh_market_midera.py --rebuild-baskets`.

Chase style in the freeze: holo / EX / GX / FA / SA / Prime / LV.X / Legend / Secret / Rainbow / Shiny Vault (plus a few liquid era promos that cleared the Top 500 rank).

## Index rules

Same philosophy as `market-en` / Base vintage:

- TCGPlayer **Market Price** (NM raw). EN representative printing (1st Edition skipped unless it is the only priced print).
- Missing / empty market is **skipped**, never treated as $0.
- A listed ID enters the index on its first priced day. S&P-style divisor keeps the level continuous.
- Rebase: **1,000 at 2024-02-08** after TickerMint `--backfill --confirm BACKFILL`. Period views only zoom the chart; they do not re-rebase to 1,000. Top 10 / Bottom 10 use the last two priced weekly snaps (`--rebuild-movers`).

`market-midera.html` has Pulse (1D / 5D / 30D / YTD + movers / breadth) and a Binder grid on the same snapshot.

## Daily log

GitHub Action **Update EN market pulse** runs `python3 tools/refresh_market_midera.py` after EN Top 500 and vintage. Stamp match → exit. New dump → fetch only the ~28 groups that hold a frozen ID, append/upsert that day.

```bash
python3 tools/refresh_market_midera.py --self-test
python3 tools/refresh_market_midera.py -v --force
```

## TickerMint history backfill

~50 unique product IDs (one call each). Weekly history from **2024-02-08** is committed after `--backfill --confirm BACKFILL`.

```bash
python3 tools/backfill_tickermint_midera.py --self-test
python3 tools/backfill_tickermint_midera.py --sample
python3 tools/backfill_tickermint_midera.py --backfill --confirm BACKFILL
python3 tools/backfill_tickermint_midera.py --rebuild-movers
```

Default `--step-days 7` (~140 points). Start **2024-02-08**. Do **not** use `backfill_tickermint_vintage.py` here (printing filters are Base Set only). Cache: `data/tickermint-cache/` (gitignored, shared with EN / vintage). `--rebuild-movers` refills Top 10 / Bottom 10 from the last two priced snaps without changing the index level.
