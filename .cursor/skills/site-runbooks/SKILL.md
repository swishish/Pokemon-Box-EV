---
name: site-runbooks
description: >-
  Procedures for Pokemon Box EV market indexes, Chase_Fit export, and Box EV
  snapshots. Use when refreshing sealed boxes or card EV, exporting Chase_Fit,
  editing market or chase pages, or checking anchors, the retired JP PSA10
  sleeve, and what not to resurrect.
---

# Site runbooks

Read and follow `docs/runbooks/market-and-snapshots.md` before changing market data, Chase_Fit, or Box EV.

UI Ready checks, the product boundaries, and the fail-closed secret-env write hook stay in `AGENTS.md`. Do not resurrect the retired ~60-card SV chase sleeve, do not commit new files under `data/market-jp-psa10/`, and do not mix the EN Base daily anchor (2024-02-08 = 1000) with the PriceCharting monthly anchor (2023-01-01 = 1000). Base Set PSA population is a monthly POP census plus a weekly `--prices-only` price pass (`update-base-psa-pop.yml`); do not put that census back on a weekly scrape. Commands and the rest of the how-to are in the runbook.
