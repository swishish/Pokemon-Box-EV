# TCGCSV archive cache (English Pokémon)

Local cache for daily TCGPlayer price archives from [TCGCSV](https://tcgcsv.com/faq).

```
https://tcgcsv.com/archive/tcgplayer/prices-YYYY-MM-DD.ppmd.7z
```

After extract, Pokémon is **categoryId 3**: `{date}/3/{groupId}/prices`.

This folder is **gitignored except this README**. Do not commit 7z files or extracted JSON — a full daily history is multi-GB.

## Layout

| Path | What |
| --- | --- |
| `pokemon-en/YYYY-MM-DD.csv` | Compact EN Pokémon representative market prices (`productId,marketPrice,subTypeName`) — the actual price-history table |
| `{date}/3/{groupId}/prices` | Raw extracted JSON (only if you pass `--keep-raw`) |
| `7z/prices-YYYY-MM-DD.ppmd.7z` | Original archive (only if you pass `--keep-raw`) |

## Refresh

```bash
# Probe (exits 2 if TCGCSV is still serving the takedown page)
python3 tools/fetch_tcgcsv_archive.py --probe

# Sample: last 5 UTC days, skip missing, polite 0.5s pause, 7z extract
python3 tools/fetch_tcgcsv_archive.py --sample

# Chosen window (default start = 2024-02-08, TCGCSV archive begin)
python3 tools/fetch_tcgcsv_archive.py --download --start 2024-02-08 --end 2024-03-01 --step-days 7

# Gary's optional later cut (not the archive default)
python3 tools/fetch_tcgcsv_archive.py --download --start 2024-02-28 --end 2024-03-15
```

Replay compact CSVs through the same index math as `tools/refresh_market_en.py`. **Do not** write `history.json` until you mean to rebase the live series:

```bash
python3 tools/fetch_tcgcsv_archive.py --replay --from-dir data/tcgcsv-archive \
  --out data/market-en/history-sample.json --dry-run
```

Full rebuild of the public pulse (rebases 1,000 onto the first kept archive day):

```bash
python3 tools/fetch_tcgcsv_archive.py --backfill --confirm BACKFILL
```

## Size (when archives are public again)

| | Ballpark |
| --- | --- |
| One `prices-YYYY-MM-DD.ppmd.7z` | ~10–40 MB (all TCGplayer categories) |
| Pokémon category 3 extract | ~1–8 MB uncompressed / day |
| Compact `pokemon-en/*.csv` | ~0.2–1 MB / day |
| Sample (`--sample`, 5 days) | ~50–200 MB download, then compact CSVs only |
| Weekly 2024-02-08 → now | ~140 archives |
| Daily 2024-02-08 → now (~980 days) | **Do not git this.** ~10–40 GB of 7z, or ~0.2–1 GB of compact CSV |
| `data/market-en/history.json` | ~0.1 KB / point — this is what GitHub Pages serves |

`--step-days 7` is the cheap backfill path. `--start 2024-02-28` only skips 20 days (~3%).

As of 2026-10-12 TCGCSV still returns **HTTP 403** on `/archive/` (“temporarily removed due to rising server costs”). The script probes first and does not invent prices.
