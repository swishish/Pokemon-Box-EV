# AGENTS.md

Portable index for this site. Other tools can `@` this file. Durable rules live here; the how-to is not loaded every turn.

Read `docs/runbooks/market-and-snapshots.md` before refreshing market data, Chase_Fit, or Box EV, or before changing those products. Cursor also loads `.cursor/skills/site-runbooks/SKILL.md`, which points at that runbook.

## UI Ready (required)

Before marking **UI Ready** or merging any UI change:

- Verify **both mobile and desktop viewports**.
- Confirm there is **no overflow, misalignment, hidden sections, or layout bugs**.
- Prefer checking the **live/preview UI**, not only code review.

Preview: `python3 -m http.server 8765` then open `http://127.0.0.1:8765/` (or the GitHub Pages URL).

## Product boundaries

- Market pages auto-update via `.github/workflows/update-market-en.yml` (daily). **Chase 公允值 and Box EV do not.**
- The JP PSA10 product is the broad benchmark `market-jp-psa10-bench.html` (`update-market-jp-psa10-bench.yml`: daily weights, quarterly membership): 復古／中世代／現行 plus sibling baskets. EN Base daily indexes stay on that page as 監察／旁路 (`#en-1st` and siblings), not a second product page.
- `market-jp-psa10.html` is a bookmark redirect to the benchmark. The ~60-card SV chase sleeve nightly is gone (`update-market-jp-psa10.yml` removed). `data/market-jp-psa10/` is frozen history, not a public page, and nothing should commit new files there. Do not bring that sleeve back as a parallel thermometer, and do not fold its 60-card membership into JP 基準.
- English Base **daily** indexes (1st Edition, Shadowless, Unlimited, Base Set 2) live in `data/market-vintage/` (TCGPlayer market via TickerMint + TCGCSV). Anchor: first retained day **2024-02-08 = 1000**. The bench page draws this daily tape as the primary series and may overlay PriceCharting. Do not source them from SNKRDUNK, and do not replace the JP PSA10 series with them.
- English Base **PriceCharting** indexes stay in `data/market-vintage-pc/` (`update-market-vintage-pc.yml`). Ungraded `used` prices, monthly, **2023-01-01 = 1000**. Open `market-vintage.html#pc/1st` (also shadowless / unlimited / base2). Do not mix the two anchors.
- Base Set PSA POP (`market-base-psa-pop.html`, `update-base-psa-pop.yml`) refetches the PriceCharting POP census **monthly** (`17 11 1 * *`, 19:17 HKT on the 1st) and game-page prices **weekly** (`17 11 * * 1`, Monday 19:17 HKT, `--prices-only`). PR CI is self-test only. Do not invent pops or prices, and do not put the census back on a weekly cron.
- Chase_Fit publishes sheet **Chase_Fit** only (not `Chase_Fit_Large`). Do not invent Chinese names, SNKRDUNK apparel IDs, or SNKR/PC image hashes. Vintage_Fit Strong Buys (e.g. 臭泥 Grimer #88) stay on the Shortlist **vintage** rows, not on `chase-fit.html`, unless Vintage_Fit is added later.
- Sealed box denom refresh does not refresh card Grade A / pity rates and must not invent apparel IDs or prices. `tools/refresh_box_ev.py` exits without writing when PriceCharting console HTML is missing (it will not invent prices or bump `as_of`). Grade A singles in `data.json` are not auto-refreshed. `calc.html` turns the freshness banner orange after ≥8 days (card snapshot `as_of`).

## Secret env files

Do not create or edit `.env`, `.env.*` (including `.env.local` and `.env.example`), `*.env`, names like `prod.env.local`, or `.envrc`. Ordinary non-secret edits stay allowed.

`.cursor/hooks.json` runs `.cursor/hooks/block-secret-env-writes.py` for Agent write/edit tools (`preToolUse`) and shell commands (`beforeShellExecution`) with `failClosed: true`. If the script crashes, times out, or cannot tell whether a write hits one of those paths, the write is denied.

```bash
python3 .cursor/hooks/block-secret-env-writes.py --self-test
printf '%s' '{"tool_name":"Write","tool_input":{"path":".env","contents":"X=1"}}' | python3 .cursor/hooks/block-secret-env-writes.py
printf '%s' '{"tool_name":"Write","tool_input":{"path":"README.md","contents":"mentions .env"}}' | python3 .cursor/hooks/block-secret-env-writes.py
```

Self-test exits 0. The first pipe prints `"permission": "deny"`; the second prints `"permission": "allow"`. Desktop hook logs: Output panel → Hooks. Tab completions have no pre-edit permission hook; this covers Agent writes and shell.

## Index

| When | Read |
| --- | --- |
| UI Ready, boundaries, this hook | This file |
| Commands, archives, thumbs, column checks | `docs/runbooks/market-and-snapshots.md` |
| Hook script | `.cursor/hooks/block-secret-env-writes.py` |
