# JP sealed box shard

Live landed prices for the Box EV calculator. `calc.html` loads `data.json` (card EV) and overlays this shard. The sealed job does not rewrite the card bundle.

| File | Role |
| --- | --- |
| `latest.json` | `sealed_box_as_of`, per-set `landed` / `sealed_box` / shrink+no-shrink apparel ids / PriceCharting box avg |
| `latest.js` | `window.SEALED_BOXES` fallback when `fetch` is blocked (`file://`) |

## Who writes it

| | |
| --- | --- |
| Script | `tools/refresh_sealed_boxes.py` |
| Action | `.github/workflows/refresh-sealed-boxes.yml` (**Refresh sealed box prices**, Sundays 20:00 UTC) |

That job must not commit root `data.json`, `data.js`, `data/market-en/`, `data/market-vintage/`, `data/market-midera/`, `data/market-vintage-pc/`, or `data/market-jp-psa10-bench/`.

JP PSA10 benchmark and EN pulse jobs must not write this directory. They already own their own folders and only read `data.json` when they need known card apparel ids.

`data.json` still contains the last sealed snapshot copied before the split. It is a fallback if this shard fails to load. Scheduled refreshes do not update that copy.

PR #81 (open) still edits the card bundle. Leave it for price review. New sealed hygiene commits this shard only.
