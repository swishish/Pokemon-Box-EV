# TickerMint cache (English Pokémon)

Local cache of `GET https://api.tickermint.cards/products/{id}/prices`.

TickerMint product ids are TCGplayer `productId`s — the same ids TCGCSV uses.

Shared by:

- EN Top 500 — `tools/backfill_tickermint.py` (representative printing; **skips 1st Edition**)
- Base Set vintage — `tools/backfill_tickermint_vintage.py` (exact printing per index; 1st + Shadowless share BSS ids)
- Mid-era Top 50 — `tools/backfill_tickermint_midera.py` (EN representative printing; DP→SM frozen IDs)

This folder is **gitignored except this README**. Do not commit the JSON dumps.

## EN Top 500 (copy-paste)

```bash
python3 tools/backfill_tickermint.py --self-test
python3 tools/backfill_tickermint.py --sample
python3 tools/backfill_tickermint.py --backfill --confirm BACKFILL
python3 tools/backfill_tickermint.py --rebuild-movers
```

## Base Set vintage (copy-paste)

**Do not** reuse the EN script here — it would price 1st Edition as Shadowless.

```bash
python3 tools/backfill_tickermint_vintage.py --self-test
python3 tools/backfill_tickermint_vintage.py --sample
python3 tools/backfill_tickermint_vintage.py --backfill --confirm BACKFILL
```

- `--sample` = a few cards, no live JSON rewrite (safe).
- `--backfill --confirm BACKFILL` = rewrite `history.json` + `price-history.json` (rebases each index to 1,000 on the first kept day; Top 10 / Bottom 10 / Breadth use the last two priced snaps). Default is **weekly** points (`--step-days 7`). Daily: add `--step-days 1`.
- `--rebuild-movers` = refill EN or vintage movers from TickerMint **without** changing index levels.
- Start date default **2024-02-08**. Optional later cut: `--start 2024-02-28`.
- Vintage unique calls ≈ 62 (31 BSS shared by 1st+Shadowless + 31 Unlimited). First run ~1 min at 120 req/min.

## Mid-era Top 50 (copy-paste)

```bash
python3 tools/backfill_tickermint_midera.py --self-test
python3 tools/backfill_tickermint_midera.py --sample
python3 tools/backfill_tickermint_midera.py --backfill --confirm BACKFILL
```

~50 unique IDs. Same confirm guard as EN / vintage. Does not touch Base 1st / Shadowless / Unlimited.

Free API: 120 requests/minute, no key, non-commercial + [link back to TickerMint](https://tickermint.cards/developers/pokemon). The $99 bulk CSV dump is **not** used here.
