# AGENTS.md — Pokemon-Box-EV

## Project
Static GitHub Pages EV calculator for JP Pokemon boxes (friends-facing).
No login, no live scrape in the browser. Data = snapshot (`ev_live`).

## Key files
- `index.html` — UI + calc logic (keep as single-page; do not split unless asked)
- `data.json` / `data.js` — snapshot; page `fetch`es JSON, falls back to `data.js`
- `img/`, `img_map.json` — box/chase images
- `tools/refresh_box_ev.py` — offline data refresh only

## Preview
- Prefer: `python3 -m http.server 8765` then open `http://127.0.0.1:8765/`
- Or open `index.html` locally; never embed full `EV_DATA` inside `index.html` scripts

## Do
- Surgical edits; match existing style and Traditional Chinese UI copy
- Keep four EV % metrics on the same denominator semantics
- If refreshing data, run/update via `tools/` and keep `data.json` + `data.js` in sync
- Bump version note in README changelog when shipping user-visible changes

## Don’t
- Don’t add backend, auth, or live price APIs to the Pages site
- Don’t invent market prices or POP; use provided snapshot fields
- Don’t drive-by refactor unrelated CSS/JS
- Don’t commit secrets, API keys, or large unrelated binaries

## PRs
- Small, focused diffs; title like `[Box-EV] <change>`
- Note if data snapshot changed and approximate source/date
