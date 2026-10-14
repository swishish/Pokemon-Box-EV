#!/usr/bin/env python3
"""Daily EN Base Set vintage indexes (1st Edition / Shadowless / Unlimited).

Three *fixed* chase baskets of English Base Set raw singles (TCGCSV category 3).
Pipeline = log one snapshot per day. Longer history is backfilled from the
TickerMint free API via tools/backfill_tickermint_vintage.py (printing-aware;
do **not** reuse tools/backfill_tickermint.py, which skips 1st Edition).

  python3 tools/refresh_market_vintage.py --self-test
  python3 tools/refresh_market_vintage.py -v --force
  python3 tools/backfill_tickermint_vintage.py --self-test
  python3 tools/backfill_tickermint_vintage.py --backfill --confirm BACKFILL

Writes (stdlib only):
  data/market-vintage/baskets.json       frozen ID lists + gaps (source of truth)
  data/market-vintage/latest.json        three indexes + constituents
  data/market-vintage/history.json       one point per index per day (append/upsert)
  data/market-vintage/price-history.json per-index basket prices by date
  data/market-vintage/latest.js          window.MARKET_VINTAGE fallback
  data/market-vintage/history.js         window.MARKET_VINTAGE_HISTORY fallback

Index math matches market-en: price-weighted with an S&P-style divisor so the
level stays continuous when a listed ID gains/loses a market print. First
retained daily snapshot of each index is rebased to 1,000.
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import os
import re
import sys
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import refresh_market_en as en  # noqa: E402

ROOT = os.path.dirname(HERE)
DATA_DIR = os.path.join(ROOT, "data", "market-vintage")
BASKETS_PATH = os.path.join(DATA_DIR, "baskets.json")
LATEST_PATH = os.path.join(DATA_DIR, "latest.json")
LATEST_JS_PATH = os.path.join(DATA_DIR, "latest.js")
HISTORY_PATH = os.path.join(DATA_DIR, "history.json")
HISTORY_JS_PATH = os.path.join(DATA_DIR, "history.js")
PRICE_HISTORY_PATH = os.path.join(DATA_DIR, "price-history.json")

USER_AGENT = "Pokemon-Box-EV/1.25 (+https://github.com/swishish/Pokemon-Box-EV)"
INDEX_ORDER = ("base-1st", "base-shadowless", "base-unlimited")
CHASE_RARITY = {"Holo Rare", "Rare Holo", "Rare"}
_BAD_NAME = (
    "error",
    "black dot",
    "red cheeks",
    "jumbo",
    "oversized",
    "miscut",
    "misprint",
    "box topper",
    "(staff",
    "[staff",
)
_NUM_PREFIX = re.compile(r"^(\d+)")


def _ua_get_bytes(url: str, timeout: int = 90) -> bytes:
    """Same retry as market-en, with this script's User-Agent."""
    saved = en.USER_AGENT
    en.USER_AGENT = USER_AGENT
    try:
        return en._get_bytes(url, timeout=timeout)
    finally:
        en.USER_AGENT = saved


def source_stamp() -> str | None:
    saved = en.USER_AGENT
    en.USER_AGENT = USER_AGENT
    try:
        return en.source_stamp()
    finally:
        en.USER_AGENT = saved


def pick_printing_price(rows: list[dict], needles: list[str], mode: str = "include"):
    """Pick a TCGPlayer market price for one printing family.

    mode=include: subTypeName must contain one of the needles (e.g. '1st edition').
    mode=exclude: skip rows whose subTypeName contains a needle (e.g. skip 1st).
    Highest positive market among matches wins. Empty / missing market is skipped
    (never treated as $0).
    """
    needles_l = [n.lower() for n in (needles or []) if n]
    mode = (mode or "include").lower()
    best = None
    for row in rows or []:
        val = en._parse_price(row.get("marketPrice"))
        if val is None:
            continue
        sub = row.get("subTypeName") or ""
        low = sub.lower()
        matched = any(n in low for n in needles_l) if needles_l else True
        if mode == "exclude":
            ok = not matched
        else:
            ok = matched
        if not ok:
            continue
        if best is None or val > best[0]:
            best = (val, sub)
    return best


def _excluded_name(name: str) -> bool:
    low = (name or "").lower()
    return any(k in low for k in _BAD_NAME)


def ingest_group_keep_rows(text: str, group: dict, products: dict) -> None:
    """Parse ProductsAndPrices.csv keeping every price row (all subtypes)."""
    set_name = group.get("name") or ""
    set_id = str(group.get("groupId") or "")
    grouped: dict[str, list[dict]] = {}
    for row in csv.DictReader(io.StringIO(text)):
        number = (row.get("extNumber") or "").strip()
        if not number:
            continue  # sealed / non-single
        name = row.get("name") or "Unknown"
        if _excluded_name(name) or en._excluded(name, set_name, number):
            continue
        pid = str(row.get("productId") or "").strip()
        if not pid:
            continue
        grouped.setdefault(pid, []).append(row)

    for pid, rows in grouped.items():
        first = rows[0]
        products[pid] = {
            "id": pid,
            "name": first.get("name") or "Unknown",
            "number": (first.get("extNumber") or "").strip(),
            "rarity": (first.get("extRarity") or "").strip(),
            "setName": set_name,
            "setId": set_id,
            "image": first.get("imageUrl") or "",
            "url": first.get("url") or "",
            "rows": rows,
        }


def fetch_groups_needed(group_names: dict[str, str], verbose: bool = False) -> dict:
    """Fetch only the Base Set groups we need (not the whole Pokemon universe)."""
    products: dict = {}
    for gid in sorted(group_names, key=lambda x: int(x) if str(x).isdigit() else 0):
        url = f"{en.TCGCSV}/tcgplayer/{en.CATEGORY}/{gid}/ProductsAndPrices.csv"
        text = _ua_get_bytes(url).decode("utf-8-sig")
        name = group_names.get(gid) or f"group {gid}"
        ingest_group_keep_rows(text, {"groupId": str(gid), "name": name}, products)
        if verbose:
            n = sum(1 for p in products.values() if p["setId"] == str(gid))
            print(f"  group {gid} ({name}): {n} singles", flush=True)
    return products


def load_baskets(path: str = BASKETS_PATH) -> dict:
    raw = en.load_json(path, {})
    if not raw.get("indexes"):
        raise RuntimeError(f"baskets.json missing indexes: {path}")
    return raw


def index_ids(baskets: dict) -> list[str]:
    listed = [i for i in INDEX_ORDER if i in (baskets.get("indexes") or {})]
    extra = [k for k in (baskets.get("indexes") or {}) if k not in listed]
    return listed + extra


def prices_for_spec(products: dict, spec: dict) -> tuple[dict, dict, dict, list]:
    """Restrict catalog + prices to this index's frozen ID list + printing."""
    needles = spec.get("subtypeNeedles") or []
    mode = spec.get("subtypeMode") or "include"
    catalog: dict = {}
    prices: dict = {}
    subtypes: dict = {}
    listed_unpriced: list[dict] = []
    for row in spec.get("constituents") or []:
        pid = str(row.get("id") or "").strip()
        if not pid:
            continue
        live = products.get(pid)
        meta = {
            "id": pid,
            "name": (live or row).get("name") or row.get("name") or "Unknown",
            "number": (live or row).get("number") or row.get("number") or "",
            "rarity": (live or row).get("rarity") or row.get("rarity") or "",
            "setName": (live or {}).get("setName") or spec.get("groupName") or "",
            "setId": (live or {}).get("setId") or str(spec.get("groupId") or ""),
            "image": (live or row).get("image") or row.get("image") or "",
        }
        catalog[pid] = meta
        picked = pick_printing_price((live or {}).get("rows") or [], needles, mode)
        if picked:
            prices[pid] = round(picked[0], 2)
            subtypes[pid] = picked[1]
        else:
            listed_unpriced.append(
                {"id": pid, "name": meta["name"], "number": meta["number"], "rarity": meta["rarity"]}
            )
    return catalog, prices, subtypes, listed_unpriced


def _empty_index_block() -> dict:
    return {"points": []}


def build_one_snapshot(
    spec: dict,
    catalog: dict,
    prices: dict,
    subtypes: dict,
    listed_unpriced: list,
    prev_latest: dict | None,
    prev_history_block: dict | None,
    stamp: str | None,
    now: datetime,
) -> dict:
    """One vintage index, same divisor / carry rules as market-en."""
    prev = prev_latest or {}
    history_points = (prev_history_block or {}).get("points") or []
    if history_points and not prev.get("divisor"):
        raise RuntimeError(
            f"{spec['id']}: latest.json is missing/corrupt but history has "
            f"{len(history_points)} points — restore data/market-vintage/latest.json"
        )

    prev_divisor = prev.get("divisor")
    prev_index = prev.get("index")
    prev_prices = {c["id"]: c.get("price") for c in prev.get("constituents", []) if c.get("price")}
    prev_ids = [c["id"] for c in prev.get("constituents", [])] or None
    prev_priced_asof = {
        c["id"]: c.get("pricedAsOf") or prev.get("asOfDate")
        for c in prev.get("constituents", [])
    }
    prev_printing = {c["id"]: c.get("printing") for c in prev.get("constituents", [])}

    today_iso = en.as_of_date(stamp, now)
    if prev.get("asOfDate") == today_iso:
        # Same-day upsert: keep yesterday's baseline. A true first snapshot
        # has prevIndex=None — do not compare the index to itself as a fake 1D.
        # After a TickerMint rebase prevPrice is often null; do not fall back
        # to today's prices (that would invent a 0% component 1D).
        baseline_index = prev.get("prevIndex")
        baseline_prices = {
            c["id"]: c.get("prevPrice")
            for c in prev.get("constituents", [])
            if c.get("prevPrice")
        }
    else:
        baseline_index = prev_index
        baseline_prices = prev_prices

    carry = {
        pid: pr
        for pid, pr in prev_prices.items()
        if en._age_days(prev_priced_asof.get(pid), today_iso) <= en.STALE_DAYS
    }
    step = en.step_index(
        prices,
        catalog,
        prev_ids,
        prev_divisor,
        carry=carry,
        size=max(len(catalog), 1),
        base=en.BASE_INDEX_VALUE,
    )

    constituents = []
    advancing = declining = unchanged = 0
    for rank, (pid, price) in enumerate(step["basket"], start=1):
        meta = catalog.get(pid) or {
            "id": pid,
            "name": "Unknown",
            "number": "",
            "rarity": "",
            "setName": "",
            "setId": "",
            "image": "",
        }
        prior = baseline_prices.get(pid)
        live_today = pid in prices
        change_pct = None
        if prior and prior > 0 and live_today:
            change_pct = round((price / prior - 1) * 100, 2)
            if change_pct > 0:
                advancing += 1
            elif change_pct < 0:
                declining += 1
            else:
                unchanged += 1
        constituents.append(
            {
                "rank": rank,
                "id": meta["id"],
                "name": meta["name"],
                "number": meta.get("number") or "",
                "rarity": meta.get("rarity") or "",
                "setName": meta.get("setName") or "",
                "setId": meta.get("setId") or "",
                "image": meta.get("image") or "",
                "price": round(price, 2),
                "prevPrice": round(prior, 2) if prior else None,
                "changePct": change_pct,
                "printing": (subtypes.get(pid) if live_today else prev_printing.get(pid)) or None,
                "pricedAsOf": today_iso if live_today else prev_priced_asof.get(pid),
                "carried": not live_today,
            }
        )

    movable = [c for c in constituents if c["changePct"] is not None]
    movable.sort(key=lambda c: c["changePct"], reverse=True)
    fields = ("id", "name", "number", "image", "setName", "price", "prevPrice", "changePct")
    gainers = [{k: c[k] for k in fields} for c in movable[: en.TOP_MOVERS] if c["changePct"] > 0]
    losers = [{k: c[k] for k in fields} for c in reversed(movable[-en.TOP_MOVERS :]) if c["changePct"] < 0]

    has_prev = baseline_index is not None
    index_value = step["index"]
    change_abs = round(index_value - baseline_index, 2) if has_prev else None
    change_pct = round((index_value / baseline_index - 1) * 100, 2) if has_prev else None
    base_date = (
        prev.get("baseDate")
        or (history_points[0].get("date") if history_points else None)
        or today_iso
    )
    by_number = sorted(constituents, key=lambda c: (_num_key(c.get("number")), c.get("name") or ""))
    priced_ids = {c["id"] for c in by_number}
    basket_rows = [
        {k: c[k] for k in ("id", "name", "number", "rarity", "image", "price", "printing", "carried")}
        for c in by_number
    ]
    for row in listed_unpriced:
        pid = str(row.get("id") or "")
        if not pid or pid in priced_ids:
            continue
        meta = catalog.get(pid) or row
        basket_rows.append(
            {
                "id": pid,
                "name": meta.get("name") or row.get("name") or "Unknown",
                "number": meta.get("number") or row.get("number") or "",
                "rarity": meta.get("rarity") or row.get("rarity") or "",
                "image": meta.get("image") or "",
                "price": None,
                "printing": None,
                "carried": False,
            }
        )
    basket_rows.sort(key=lambda c: (_num_key(c.get("number")), c.get("name") or ""))

    latest = {
        "id": spec["id"],
        "name": spec.get("name"),
        "nameZh": spec.get("nameZh"),
        "printing": spec.get("printing"),
        "groupId": spec.get("groupId"),
        "groupName": spec.get("groupName"),
        "asOfDate": today_iso,
        "universe": spec.get("universe"),
        "staleDays": en.STALE_DAYS,
        "index": round(index_value, 2),
        "prevIndex": round(baseline_index, 2) if has_prev else None,
        "change": change_abs,
        "changePct": change_pct,
        "changeLabel": (
            "vs previous snapshot"
            if has_prev
            else "first snapshot — daily % after the next refresh"
        ),
        "divisor": step["divisor"],
        "baseValue": en.BASE_INDEX_VALUE,
        "baseDate": base_date,
        "listedCount": len(catalog),
        "constituentCount": len(constituents),
        "totalValue": round(step["sum_today"], 2),
        "pricedToday": sum(1 for c in constituents if not c["carried"]),
        "listedUnpriced": listed_unpriced,
        "breadth": {
            "available": bool(movable),
            "advancing": advancing,
            "declining": declining,
            "unchanged": unchanged,
        },
        "gainers": gainers,
        "losers": losers,
        "top": [
            {k: c[k] for k in ("rank", "id", "name", "number", "image", "setName", "price", "rarity")}
            for c in constituents[:10]
        ],
        "basket": basket_rows,
        "constituents": constituents,
    }
    return latest


def _num_key(number: str | None) -> int:
    match = _NUM_PREFIX.match(number or "")
    return int(match.group(1)) if match else 9999


def merge_all_history(history: dict, indexes: dict, now: datetime) -> dict:
    if not isinstance(history, dict):
        history = {}
    out_indexes = dict(history.get("indexes") or {})
    for iid, latest in indexes.items():
        block = out_indexes.get(iid) or {}
        merged = en.merge_history_point(block, latest, now)
        # Keep TickerMint rebase metadata (archiveStart / source / sampling).
        # period_returns already computed from kept points — do not rewrite
        # reasons or drop the historical chain.
        out_indexes[iid] = merged
    backfill = history.get("backfill")
    if not backfill:
        backfill = "deferred"
    tickermint = (
        history.get("source") == "tickermint"
        or (isinstance(backfill, dict) and backfill.get("source") == "tickermint")
    )
    if not tickermint:
        for block in out_indexes.values():
            if isinstance(block, dict) and block.get("source") != "tickermint":
                block.pop("archiveStart", None)
                block.pop("optionalStart", None)
    out = {
        "generated": now.isoformat(),
        "baseValue": en.BASE_INDEX_VALUE,
        "backfill": backfill,
        "indexOrder": list(indexes.keys()),
        "indexes": out_indexes,
    }
    if tickermint:
        for key in ("archiveStart", "optionalStart", "source", "sourceDocs", "sampling"):
            if history.get(key) is not None:
                out[key] = history[key]
    return out


def merge_all_price_history(price_history: dict, indexes: dict, now: datetime) -> dict:
    if not isinstance(price_history, dict):
        price_history = {}
    out = dict(price_history.get("indexes") or {})
    for iid, latest in indexes.items():
        prev = out.get(iid) or {"points": []}
        out[iid] = en.upsert_price_history(prev, latest, now)
    return {"generated": now.isoformat(), "indexes": out}


def attach_all_movers(
    indexes: dict,
    price_history: dict,
    baseline_strategy: str = "on_or_before",
) -> None:
    """Weekly TickerMint series uses on_or_before (same cutoff as index returns)."""
    ph_indexes = (price_history or {}).get("indexes") or {}
    for iid, latest in indexes.items():
        en.attach_period_movers(
            latest,
            ph_indexes.get(iid) or {"points": []},
            baseline_strategy=baseline_strategy,
        )


def write_latest_js(latest: dict) -> None:
    en._write_js(LATEST_JS_PATH, "MARKET_VINTAGE", latest)


def write_history_js(history: dict) -> None:
    en._write_js(HISTORY_JS_PATH, "MARKET_VINTAGE_HISTORY", history)


def build_payload(indexes: dict, baskets: dict, stamp: str | None, now: datetime, history: dict | None = None) -> dict:
    order = [i for i in index_ids(baskets) if i in indexes]
    first = indexes[order[0]] if order else {}
    history = history or {}
    backfill = history.get("backfill") or "deferred"
    tickermint = isinstance(backfill, dict) and backfill.get("source") == "tickermint"
    price_source = (
        "TCGplayer Market Price (TickerMint history + TCGCSV daily snap)"
        if tickermint
        else "TCGplayer Market Price (via tcgcsv.com daily snapshot)"
    )
    payload = {
        "generated": now.isoformat(),
        "sourceStamp": stamp,
        "asOfDate": first.get("asOfDate"),
        "priceSource": price_source,
        "universe": "English Base Set raw / ungraded singles — 1st Edition / Shadowless / Unlimited",
        "staleDays": en.STALE_DAYS,
        "baseValue": en.BASE_INDEX_VALUE,
        "backfill": backfill,
        "basketRule": baskets.get("rule"),
        "gaps": baskets.get("gaps") or [],
        "indexOrder": order,
        "indexes": indexes,
    }
    if history.get("archiveStart"):
        payload["archiveStart"] = history["archiveStart"]
    return payload


def build(verbose: bool = False, force: bool = False) -> None:
    baskets = load_baskets()
    prev_latest = en.load_json(LATEST_PATH, {})
    stamp = source_stamp()
    if stamp and prev_latest.get("sourceStamp") == stamp and not force:
        print(f"TCGCSV unchanged since {stamp}; vintage nothing to do.", flush=True)
        return

    group_names = {
        str(spec.get("groupId")): spec.get("groupName") or f"group {spec.get('groupId')}"
        for spec in baskets["indexes"].values()
        if spec.get("groupId")
    }
    print("Fetching Base Set groups from TCGCSV ...", flush=True)
    products = fetch_groups_needed(group_names, verbose=verbose)
    if not products:
        raise RuntimeError("TCGCSV returned no Base Set singles")

    now = datetime.now(timezone.utc)
    prev_indexes = prev_latest.get("indexes") or {}
    prev_history = en.load_json(HISTORY_PATH, {}) or {}
    hist_blocks = prev_history.get("indexes") or {}
    built: dict = {}
    for iid in index_ids(baskets):
        spec = baskets["indexes"][iid]
        catalog, prices, subtypes, unpriced = prices_for_spec(products, spec)
        if not catalog:
            raise RuntimeError(f"{iid}: basket has no constituent IDs")
        if verbose:
            print(
                f"  {iid}: listed {len(catalog)} / priced {len(prices)} / unpriced {len(unpriced)}",
                flush=True,
            )
        built[iid] = build_one_snapshot(
            spec,
            catalog,
            prices,
            subtypes,
            unpriced,
            prev_indexes.get(iid) or {},
            hist_blocks.get(iid) or {},
            stamp,
            now,
        )

    price_history = merge_all_price_history(en.load_json(PRICE_HISTORY_PATH, {}), built, now)
    attach_all_movers(built, price_history)
    history = merge_all_history(prev_history, built, now)
    latest = build_payload(built, baskets, stamp, now, history)

    en.write_json(LATEST_PATH, latest)
    en.write_json(HISTORY_PATH, history)
    en.write_json(PRICE_HISTORY_PATH, price_history)
    write_latest_js(latest)
    write_history_js(history)

    bits = []
    for iid in latest["indexOrder"]:
        row = built[iid]
        ch = row["changePct"]
        ch_s = "n/a (first snapshot)" if ch is None else f"{ch:+.2f}%"
        bits.append(f"{row['name']}={row['index']:.2f} ({ch_s}, n={row['constituentCount']})")
    print(
        f"vintage asOf {latest.get('asOfDate')} | " + " | ".join(bits),
        flush=True,
    )


def _num_sort_key(number: str) -> int:
    return _num_key(number)


def rebuild_baskets(verbose: bool = False) -> dict:
    """Regenerate baskets.json from live TCGCSV (does not append history)."""
    groups = {
        "604": "Base Set",
        "1663": "Base Set (Shadowless)",
    }
    products_by_group: dict[str, dict] = {}
    for gid, name in groups.items():
        url = f"{en.TCGCSV}/tcgplayer/{en.CATEGORY}/{gid}/ProductsAndPrices.csv"
        text = _ua_get_bytes(url).decode("utf-8-sig")
        bucket: dict = {}
        ingest_group_keep_rows(text, {"groupId": gid, "name": name}, bucket)
        products_by_group[gid] = bucket
        if verbose:
            print(f"  rebuild group {gid}: {len(bucket)} numbered singles", flush=True)

    def chase_list(bucket: dict) -> list[dict]:
        rows = []
        for pid, meta in bucket.items():
            if (meta.get("rarity") or "") not in CHASE_RARITY:
                continue
            rows.append(
                {
                    "id": pid,
                    "number": meta.get("number") or "",
                    "name": meta.get("name") or "Unknown",
                    "rarity": meta.get("rarity") or "",
                    "image": meta.get("image") or "",
                }
            )
        rows.sort(key=lambda c: (_num_sort_key(c["number"]), c["name"]))
        return rows

    def unpriced(bucket: dict, ids: list[str], needles, mode) -> list[dict]:
        out = []
        for pid in ids:
            meta = bucket.get(pid) or {}
            picked = pick_printing_price(meta.get("rows") or [], needles, mode)
            if picked is None:
                out.append(
                    {
                        "id": pid,
                        "number": meta.get("number") or "",
                        "name": meta.get("name") or "",
                        "rarity": meta.get("rarity") or "",
                    }
                )
        return out

    sh_chase = chase_list(products_by_group["1663"])
    un_chase = chase_list(products_by_group["604"])
    sh_ids = [c["id"] for c in sh_chase]
    un_ids = [c["id"] for c in un_chase]

    sh_numbers = {_num_key(c["number"]) for c in sh_chase}
    un_numbers = {_num_key(c["number"]) for c in un_chase}
    gaps = []
    if 8 not in sh_numbers and 8 not in un_numbers:
        gaps.append(
            {
                "number": "008/102",
                "name": "Machamp",
                "reason": (
                    "No numbered TCGCSV product in Base Set (group 604) or "
                    "Base Set (Shadowless) (group 1663). Classic 1st Edition-only "
                    "holo; omitted until a product ID exists."
                ),
            }
        )

    now = datetime.now(timezone.utc)
    baskets = {
        "version": 1,
        "generated": now.isoformat(),
        "source": "TCGCSV category 3 ProductsAndPrices (groups 604 + 1663)",
        "rule": (
            "Fixed chase baskets: Base Set EN raw singles, Holo Rare + Rare only "
            "(cheatsheet #1–102 where IDs exist). Split by printing. Sealed / jumbo "
            "/ JP / error variants excluded. Not mid-era (Jungle/Fossil/Rocket)."
        ),
        "sizeNote": (
            "Practical holo+rare-heavy start (~31 IDs). Commons / uncommons / Energy "
            "omitted so breadth is not dominated by $1 cards. Full 102 can be added "
            "later without changing index math (divisor)."
        ),
        "backfill": (
            "TickerMint free API via tools/backfill_tickermint_vintage.py "
            "(printing-aware; 1st vs Shadowless vs Unlimited). Do not reuse "
            "tools/backfill_tickermint.py — that skips 1st Edition. Daily job "
            "appends on the rebased divisor after --backfill --confirm BACKFILL."
        ),
        "gaps": gaps,
        "indexes": {
            "base-1st": {
                "id": "base-1st",
                "name": "Base 1st Edition",
                "nameZh": "Base 第一版",
                "printing": "1st Edition",
                "groupId": "1663",
                "groupName": "Base Set (Shadowless)",
                "abbreviation": "BSS",
                "subtypeNeedles": ["1st edition"],
                "subtypeMode": "include",
                "subtypeNote": (
                    "TCGPlayer stores 1st Edition as subtypes on Base Set (Shadowless) "
                    "products (1st Edition / 1st Edition Holofoil). There is no separate "
                    "1st Edition group in TCGCSV."
                ),
                "universe": "English Base Set 1st Edition raw / ungraded (TCGPlayer Market, NM)",
                "constituentCount": len(sh_chase),
                "unpricedAtBasketBuild": unpriced(
                    products_by_group["1663"], sh_ids, ["1st edition"], "include"
                ),
                "constituents": sh_chase,
            },
            "base-shadowless": {
                "id": "base-shadowless",
                "name": "Base Shadowless",
                "nameZh": "Base 無陰影",
                "printing": "Unlimited Shadowless",
                "groupId": "1663",
                "groupName": "Base Set (Shadowless)",
                "abbreviation": "BSS",
                "subtypeNeedles": ["unlimited"],
                "subtypeMode": "include",
                "subtypeNote": (
                    "Unlimited / Unlimited Holofoil subtypes on Base Set (Shadowless) "
                    "products = no 1st stamp, no right-edge shadow."
                ),
                "universe": "English Base Set Shadowless (Unlimited) raw / ungraded (TCGPlayer Market, NM)",
                "constituentCount": len(sh_chase),
                "unpricedAtBasketBuild": unpriced(
                    products_by_group["1663"], sh_ids, ["unlimited"], "include"
                ),
                "constituents": sh_chase,
            },
            "base-unlimited": {
                "id": "base-unlimited",
                "name": "Base Unlimited",
                "nameZh": "Base 無限版",
                "printing": "Unlimited (with shadow)",
                "groupId": "604",
                "groupName": "Base Set",
                "abbreviation": "BS",
                "subtypeNeedles": ["1st edition"],
                "subtypeMode": "exclude",
                "subtypeNote": (
                    "Later Unlimited with right-edge shadow. TCGCSV group 604 uses "
                    "Holofoil / Normal (not 1st Edition)."
                ),
                "universe": "English Base Set Unlimited (shadowed) raw / ungraded (TCGPlayer Market, NM)",
                "constituentCount": len(un_chase),
                "unpricedAtBasketBuild": unpriced(
                    products_by_group["604"], un_ids, ["1st edition"], "exclude"
                ),
                "constituents": un_chase,
            },
        },
    }
    en.write_json(BASKETS_PATH, baskets)
    print(
        f"wrote {BASKETS_PATH} | "
        + " | ".join(
            f"{iid} n={spec['constituentCount']} unpriced={len(spec['unpricedAtBasketBuild'])}"
            for iid, spec in baskets["indexes"].items()
        ),
        flush=True,
    )
    return baskets


def self_test() -> None:
    rows = [
        {"marketPrice": "10", "subTypeName": "Unlimited Holofoil"},
        {"marketPrice": "50", "subTypeName": "1st Edition Holofoil"},
        {"marketPrice": "8", "subTypeName": "Unlimited"},
        {"marketPrice": "", "subTypeName": "1st Edition"},
    ]
    first = pick_printing_price(rows, ["1st edition"], "include")
    assert first and first[0] == 50.0 and "1st" in first[1].lower()
    shadow = pick_printing_price(rows, ["unlimited"], "include")
    assert shadow and shadow[0] == 10.0
    unl = pick_printing_price(rows, ["1st edition"], "exclude")
    assert unl and unl[0] == 10.0
    empty = pick_printing_price(
        [{"marketPrice": "", "subTypeName": "1st Edition Holofoil"}],
        ["1st edition"],
        "include",
    )
    assert empty is None  # missing market is not $0

    csv_text = (
        "productId,name,imageUrl,extNumber,extRarity,marketPrice,subTypeName,url\n"
        "A,Alakazam,http://img,001/102,Holo Rare,12.00,Unlimited Holofoil,http://t\n"
        "A,Alakazam,http://img,001/102,Holo Rare,40.00,1st Edition Holofoil,http://t\n"
        "B,Blastoise,http://img,002/102,Holo Rare,20.00,Unlimited Holofoil,http://t\n"
        "B,Blastoise,http://img,002/102,Holo Rare,,1st Edition Holofoil,http://t\n"
        "S,Booster Box,http://img,,,199.00,Normal,http://t\n"
        "J,Pikachu Jumbo,http://img,058/102,Common,5.00,Normal,http://t\n"
        "E,Charizard (Black Dot Error),http://img,004/102,Holo Rare,999.00,Holofoil,http://t\n"
        "P,Pikachu 227/S-P,http://img,227/S-P,Promo,9.00,Holofoil,http://t\n"
        "U,Venusaur,http://img,015/102,Holo Rare,15.00,Holofoil,http://t\n"
    )
    products: dict = {}
    ingest_group_keep_rows(csv_text, {"groupId": "1663", "name": "Base Set (Shadowless)"}, products)
    assert "A" in products and "B" in products and "U" in products
    assert "S" not in products  # sealed
    assert "J" not in products  # jumbo
    assert "E" not in products  # error variant
    assert "P" not in products  # JP promo -P

    spec_1st = {
        "id": "base-1st",
        "name": "Base 1st Edition",
        "nameZh": "Base 第一版",
        "printing": "1st Edition",
        "groupId": "1663",
        "groupName": "Base Set (Shadowless)",
        "subtypeNeedles": ["1st edition"],
        "subtypeMode": "include",
        "universe": "test",
        "constituents": [
            {"id": "A", "name": "Alakazam", "number": "001/102", "rarity": "Holo Rare"},
            {"id": "B", "name": "Blastoise", "number": "002/102", "rarity": "Holo Rare"},
        ],
    }
    cat, prices, subs, unpriced = prices_for_spec(products, spec_1st)
    assert prices == {"A": 40.0}  # Blastoise 1st has empty market
    assert [u["id"] for u in unpriced] == ["B"]
    assert subs["A"] == "1st Edition Holofoil"

    spec_sh = dict(spec_1st, id="base-shadowless", subtypeNeedles=["unlimited"], subtypeMode="include")
    cat_s, prices_s, subs_s, unpriced_s = prices_for_spec(products, spec_sh)
    assert prices_s == {"A": 12.0, "B": 20.0}
    assert not unpriced_s
    assert "1st" not in (subs_s["A"] or "").lower()

    fake_now = datetime(2026, 10, 12, 12, 0, tzinfo=timezone.utc)
    snap1 = build_one_snapshot(spec_1st, cat, prices, subs, unpriced, {}, {}, "2026-10-12T20:05:00+0000", fake_now)
    assert snap1["index"] == 1000.0
    assert snap1["constituentCount"] == 1  # only Alakazam priced
    assert any(r["id"] == "B" and r["price"] is None for r in snap1["basket"])
    assert snap1["changePct"] is None
    assert snap1["asOfDate"] == "2026-10-12"
    assert snap1["baseDate"] == "2026-10-12"

    # Same basket +10% — index should rise ~10%.
    prices2 = {"A": 44.0}
    snap2 = build_one_snapshot(
        spec_1st, cat, prices2, {"A": "1st Edition Holofoil"}, unpriced, snap1, {"points": [{"date": "2026-10-12", "index": 1000.0}]},
        "2026-10-13T20:05:00+0000",
        datetime(2026, 10, 13, 12, 0, tzinfo=timezone.utc),
    )
    assert abs(snap2["index"] - 1100.0) < 1e-6
    assert snap2["changePct"] == 10.0
    assert snap2["baseDate"] == "2026-10-12"

    # Previously unpriced Blastoise joins at 200. Old basket return must not jump.
    prices3 = {"A": 44.0, "B": 200.0}
    cat3, _, subs3, unpriced3 = prices_for_spec(
        {
            "A": products["A"],
            "B": {
                **products["B"],
                "rows": [{"marketPrice": "200", "subTypeName": "1st Edition Holofoil"}],
            },
        },
        spec_1st,
    )
    snap3 = build_one_snapshot(
        spec_1st,
        cat3,
        prices3,
        {"A": "1st Edition Holofoil", "B": "1st Edition Holofoil"},
        [],
        snap2,
        {"points": [{"date": "2026-10-12", "index": 1000.0}, {"date": "2026-10-13", "index": 1100.0}]},
        "2026-10-14T20:05:00+0000",
        datetime(2026, 10, 14, 12, 0, tzinfo=timezone.utc),
    )
    assert abs(snap3["index"] - 1100.0) < 1e-6  # old basket a=44, same as yesterday
    assert snap3["constituentCount"] == 2
    # Divisor reset so 44+200 maps to 1100.
    assert abs(snap3["divisor"] - (244.0 / 1100.0)) < 1e-9

    hist = merge_all_history({}, {"base-1st": snap1}, fake_now)
    assert hist["backfill"] == "deferred"
    assert hist["indexes"]["base-1st"]["points"][0]["index"] == 1000.0
    rets = hist["indexes"]["base-1st"]["returns"]
    assert not rets["1D"]["available"]
    assert not rets["5D"]["available"]
    assert not rets["30D"]["available"]
    assert not rets["YTD"]["available"]

    # Daily append must keep a TickerMint rebase (baseDate / archiveStart / source).
    seeded = {
        "backfill": {"source": "tickermint", "script": "tools/backfill_tickermint_vintage.py"},
        "archiveStart": "2024-02-08",
        "source": "tickermint",
        "indexes": {
            "base-1st": {
                "points": [{"date": "2024-02-08", "index": 1000.0, "totalValue": 40.0, "count": 1}],
                "baseDate": "2024-02-08",
                "archiveStart": "2024-02-08",
                "source": "tickermint",
            }
        },
    }
    hist_tm = merge_all_history(seeded, {"base-1st": snap1}, fake_now)
    assert hist_tm["backfill"]["source"] == "tickermint"
    assert hist_tm["archiveStart"] == "2024-02-08"
    assert hist_tm["indexes"]["base-1st"]["baseDate"] == "2024-02-08"
    assert [p["date"] for p in hist_tm["indexes"]["base-1st"]["points"]] == ["2024-02-08", "2026-10-12"]

    hist2 = merge_all_history(hist, {"base-1st": snap2}, datetime(2026, 10, 13, tzinfo=timezone.utc))
    rets2 = hist2["indexes"]["base-1st"]["returns"]
    assert rets2["1D"]["available"] and rets2["1D"]["from"] == "2026-10-12"
    assert not rets2["5D"]["available"]  # do not invent a 5D return

    # Same-day upsert replaces the point, does not duplicate.
    snap2b = dict(snap2, index=1101.0, totalValue=44.0, constituentCount=1)
    hist2b = merge_all_history(hist2, {"base-1st": snap2b}, datetime(2026, 10, 13, tzinfo=timezone.utc))
    pts = hist2b["indexes"]["base-1st"]["points"]
    assert [p["date"] for p in pts] == ["2026-10-12", "2026-10-13"]
    assert pts[-1]["index"] == 1101.0

    # Rebuilding the first snapshot the same day must not invent a 0% 1D.
    snap1b = build_one_snapshot(
        spec_1st, cat, prices, subs, unpriced, snap1, {"points": [{"date": "2026-10-12", "index": 1000.0}]},
        "2026-10-12T20:05:00+0000",
        fake_now,
    )
    assert snap1b["changePct"] is None and snap1b["prevIndex"] is None

    # Frozen baskets on disk list three indexes and document Machamp.
    live_baskets = load_baskets()
    for iid in INDEX_ORDER:
        assert iid in live_baskets["indexes"]
        spec = live_baskets["indexes"][iid]
        assert spec["constituentCount"] == len(spec["constituents"]) >= 15
        ids = [c["id"] for c in spec["constituents"]]
        assert len(ids) == len(set(ids))
    gap_names = {g.get("name") for g in live_baskets.get("gaps") or []}
    assert "Machamp" in gap_names
    assert "tickermint" in (live_baskets.get("backfill") or "").lower()
    assert "backfill_tickermint.py" in (live_baskets.get("backfill") or "")
    assert live_baskets["indexes"]["base-1st"]["subtypeMode"] == "include"
    assert live_baskets["indexes"]["base-shadowless"]["subtypeNeedles"] == ["unlimited"]
    assert live_baskets["indexes"]["base-unlimited"]["subtypeMode"] == "exclude"

    # After rebase, pairwise % can be recovered from the last two priced snaps.
    ph = {
        "points": [
            {"date": "2026-10-08", "prices": {"A": 40.0}},
            {"date": "2026-10-12", "prices": {"A": 44.0}},
        ]
    }
    snap_empty = {
        "asOfDate": "2026-10-12",
        "prevIndex": 1000.0,
        "gainers": [],
        "losers": [],
        "breadth": {"available": False, "advancing": 0, "declining": 0, "unchanged": 0},
        "constituents": [
            {
                "id": "A",
                "name": "Alakazam",
                "number": "001/102",
                "image": "",
                "setName": "S",
                "price": 44.0,
                "prevPrice": None,
                "changePct": None,
                "carried": False,
            }
        ],
    }
    attach_all_movers({"base-1st": snap_empty}, {"indexes": {"base-1st": ph}})
    assert snap_empty["gainers"] and snap_empty["gainers"][0]["changePct"] == 10.0
    assert snap_empty["breadth"]["available"] is True
    assert snap_empty["movers"]["1D"]["available"] is True
    assert snap_empty["movers"]["1D"]["baselineDate"] == "2026-10-08"
    assert snap_empty["movers"]["1D"]["breadth"]["available"] is True
    assert snap_empty["movers"]["1D"]["breadth"]["advancing"] == 1

    print("self-test ok")


def rebuild_movers(now: datetime | None = None) -> None:
    """Recompute period movers from stored price history (no TCGCSV fetch)."""
    latest = en.load_json(LATEST_PATH, {})
    indexes = latest.get("indexes") or {}
    if not indexes:
        raise RuntimeError("latest.json is missing indexes")
    stamp = now or datetime.now(timezone.utc)
    price_history = en.load_json(PRICE_HISTORY_PATH, {}) or {}
    attach_all_movers(indexes, price_history)
    latest["generated"] = stamp.isoformat()
    latest["indexes"] = indexes
    en.write_json(LATEST_PATH, latest)
    write_latest_js(latest)
    bits = []
    for iid in latest.get("indexOrder") or indexes:
        row = indexes[iid]
        movers = row.get("movers") or {}
        avail = [p for p, info in movers.items() if info.get("available")]
        n_g = len((movers.get("1D") or {}).get("gainers") or row.get("gainers") or [])
        n_l = len((movers.get("1D") or {}).get("losers") or row.get("losers") or [])
        bits.append(f"{iid} 1D +{n_g}/-{n_l} periods={','.join(avail) or 'none'}")
    print("vintage movers rebuilt | " + " | ".join(bits), flush=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-test", action="store_true", help="Run index-math checks and exit")
    parser.add_argument("--force", action="store_true", help="Rebuild even if TCGCSV stamp is unchanged")
    parser.add_argument(
        "--rebuild-baskets",
        action="store_true",
        help="Regenerate data/market-vintage/baskets.json from live TCGCSV (does not append history)",
    )
    parser.add_argument(
        "--rebuild-movers",
        action="store_true",
        help="Recompute 1D/5D/30D/1Y/YTD movers from stored price history (no TCGCSV fetch)",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)
    if args.self_test:
        self_test()
        return 0
    if args.rebuild_movers:
        try:
            rebuild_movers()
        except Exception as err:  # noqa: BLE001
            print(f"ERROR: {err}", file=sys.stderr)
            return 1
        return 0
    if args.rebuild_baskets:
        try:
            rebuild_baskets(verbose=args.verbose)
        except Exception as err:  # noqa: BLE001
            print(f"ERROR: {err}", file=sys.stderr)
            return 1
        return 0
    try:
        build(verbose=args.verbose, force=args.force)
    except Exception as err:  # noqa: BLE001
        print(f"ERROR: {err}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
