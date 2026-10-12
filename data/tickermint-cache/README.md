# TickerMint cache (English Pokémon)

Local cache of `GET https://api.tickermint.cards/products/{id}/prices` for the
current EN Top 500 (`data/market-en/latest.json` constituents).

TickerMint product ids are TCGplayer `productId`s — the same ids TCGCSV uses.

This folder is **gitignored except this README**. Do not commit the JSON dumps.

## Refresh (copy-paste)

```bash
python3 tools/backfill_tickermint.py --self-test
python3 tools/backfill_tickermint.py --sample
python3 tools/backfill_tickermint.py --backfill --confirm BACKFILL
```

- `--sample` = a few cards, no live JSON rewrite (safe).
- `--backfill --confirm BACKFILL` = rewrite `history.json` (rebases the index to 1,000 on the first kept day). Default is **weekly** points (`--step-days 7`). Daily: add `--step-days 1`.
- Start date default **2024-02-08**. Optional later cut: `--start 2024-02-28`.

Free API: 120 requests/minute, no key, non-commercial + [link back to TickerMint](https://tickermint.cards/developers/pokemon). The $99 bulk CSV dump is **not** used here.
