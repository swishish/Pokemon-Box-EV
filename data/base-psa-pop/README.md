# Base Set PSA / CGC population

Static JSON for `market-base-psa-pop.html`. Compares English **1st Edition**, **Shadowless**, **Unlimited**, and **Base Set 2** populations for Base Set **#1–102**. The page defaults to **PSA＋CGC combined** per grade and can split the two companies. Beckett/BGS is not collected. Half grades are always bucketed into the lower whole grade (no toggle).

| File | Role |
| --- | --- |
| `catalog.json` | Card identity + PriceCharting `pop/item` slugs (source of truth; do not invent slugs) |
| `latest.json` / `latest.js` | Weekly snapshot the page reads (`window.BASE_PSA_POP` fallback) |
| `thumbs.json` | URL pointers only (pokemontcg.io `base1/{n}.png`, HTTP 200). The page fetches this file and one image after the user picks a card. |

## Refresh

Public PriceCharting POP HTML only (view-only, no login, no API key):

```
GET https://www.pricecharting.com/pop/item/{slug}
```

```bash
python3 tools/build_base_psa_pop_catalog.py --self-test
python3 tools/refresh_base_psa_pop.py --self-test
python3 tools/refresh_base_psa_pop.py --root .
python3 tools/refresh_base_psa_pop.py --root . --prints shadowless   # merge one printing into latest.json
python3 tools/refresh_base_psa_pop.py --root . --prices-only   # game-page indicative prices only
```

Parse errors **exit non-zero and do not write**. Totals must equal the sum of grade rows. Only PSA and CGC columns are stored; Beckett columns are ignored. The page adds PSA+CGC per grade (missing CGC is omitted, not invented as zero).

Indicative prices come from the matching PriceCharting **game** page (`/game/{slug}`), not the POP table. Column IDs: `used`＝raw, `complete`＝Grade 7, `new`＝Grade 8, `graded`＝Grade 9, `manual_only`＝PSA 10. Missing cells are omitted (never invented). A failed price fetch keeps the previous `pc_prices` or leaves the print without prices. The page shows HKD at USD × 7.80.

PriceCharting currently publishes **PSA + CGC**. Half grades are already bucketed into the lower whole grade on those pages (8.5→8). The page always displays whole grades (8.5→8).

Rebuild the slug catalog from PriceCharting console listings (does not invent URIs):

```bash
python3 tools/build_base_psa_pop_catalog.py
```

GitHub Action **Update Base PSA pop** (`.github/workflows/update-base-psa-pop.yml`): weekly + manual. PR CI only runs `--self-test`.
