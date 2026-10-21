# Base Set PSA / CGC population

Static JSON for `market-base-psa-pop.html`. Compares English **1st Edition**, **Unlimited**, and **Base Set 2** populations for Base Set **#1–102**. The page displays **PSA＋CGC combined** per grade. Beckett/BGS is not collected.

| File | Role |
| --- | --- |
| `catalog.json` | Card identity + PriceCharting `pop/item` slugs (source of truth; do not invent slugs) |
| `latest.json` / `latest.js` | Weekly snapshot the page reads (`window.BASE_PSA_POP` fallback) |

## Refresh

Public PriceCharting POP HTML only (view-only, no login, no API key):

```
GET https://www.pricecharting.com/pop/item/{slug}
```

```bash
python3 tools/refresh_base_psa_pop.py --self-test
python3 tools/refresh_base_psa_pop.py --root .
```

Parse errors **exit non-zero and do not write**. Totals must equal the sum of grade rows. Only PSA and CGC columns are stored; Beckett columns are ignored. The page adds PSA+CGC per grade (missing CGC is omitted, not invented as zero).

PriceCharting currently publishes **PSA + CGC**. Half grades are already bucketed into the lower whole grade on those pages (8.5→8). The page can still show separate half-grade rows when a future snapshot lists them.

Rebuild the slug catalog from PriceCharting console listings (does not invent URIs):

```bash
python3 tools/build_base_psa_pop_catalog.py
```

GitHub Action **Update Base PSA pop** (`.github/workflows/update-base-psa-pop.yml`): weekly + manual. PR CI only runs `--self-test`.
