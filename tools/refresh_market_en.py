#!/usr/bin/env python3
"""Build an S&P-style English raw-singles market pulse from TCGCSV dumps.

Phase 1 of Pokemon-Box-EV: English TCGPlayer market only (category 3).
No TCGPlayer API key. No SNKRDUNK / PriceCharting scrape.

Data source: TCGCSV daily Pokemon category dumps
  https://tcgcsv.com/tcgplayer/3/Groups.csv
  https://tcgcsv.com/tcgplayer/3/{groupId}/ProductsAndPrices.csv

Writes (stdlib only — GitHub Actions needs no pip install):
  data/market-en/latest.json   index + breadth + movers + basket (for next run)
  data/market-en/history.json  one point per snapshot date (append/upsert; never wipes)
  data/market-en/latest.js     window.MARKET_EN fallback (file:// / cache)
  data/market-en/history.js    window.MARKET_EN_HISTORY fallback

Index math matches S&Poké-500 in spirit: price-weighted top 500 English raw
singles with an index divisor so the level stays continuous when the basket
turns over. Rebased to 1,000 on the first snapshot — after
tools/backfill_market_en.py that first day is 2024-02-08 (TCGCSV archive
start). Daily Action continues from latest.json's divisor and appends one
history point; it must not rewrite the backfilled series.
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(ROOT, "data", "market-en")
LATEST_PATH = os.path.join(DATA_DIR, "latest.json")
LATEST_JS_PATH = os.path.join(DATA_DIR, "latest.js")
HISTORY_PATH = os.path.join(DATA_DIR, "history.json")
HISTORY_JS_PATH = os.path.join(DATA_DIR, "history.js")

TCGCSV = "https://tcgcsv.com"
CATEGORY = 3  # English Pokemon
USER_AGENT = "Pokemon-Box-EV/1.19 (+https://github.com/swishish/Pokemon-Box-EV)"
BASE_INDEX_VALUE = 1000.0
TARGET_SIZE = 500
STALE_DAYS = 70
SLEEP_S = 0.1
TOP_MOVERS = 10
ARCHIVE_START = "2024-02-08"  # earliest TCGCSV daily price archive (FAQ)

_SET_PREFIX = re.compile(r"^[A-Z0-9]{2,6}:\s+")
_JP_PROMO_NUMBER = re.compile(r"-P$", re.IGNORECASE)
_BAD_SET = ("jumbo", "miscellaneous", "oversized")
_BAD_NAME = (
    "box topper",
    "jumbo",
    "oversized",
    "(staff",
    "[staff",
    "miscut",
    "misprint",
    "error)",
    "error]",
)


def _get_bytes(url: str, timeout: int = 90) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    last: Exception | None = None
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except Exception as err:  # noqa: BLE001 — retry transients
            last = err
            time.sleep(2 ** attempt)
    raise RuntimeError(f"TCGCSV request failed: {url}\n{last}")


def source_stamp() -> str | None:
    """TCGCSV last-updated timestamp. Fail-open (None) so we still build."""
    try:
        text = _get_bytes(f"{TCGCSV}/last-updated.txt", timeout=30).decode("utf-8")
        return text.strip() or None
    except Exception:  # noqa: BLE001
        return None


def clean_set_name(name: str) -> str:
    return _SET_PREFIX.sub("", name or "").strip()


def _excluded(name: str, set_name: str, number: str | None) -> bool:
    low_set = (set_name or "").lower()
    low_name = (name or "").lower()
    if any(k in low_set for k in _BAD_SET) or any(k in low_name for k in _BAD_NAME):
        return True
    return bool(number and _JP_PROMO_NUMBER.search(number.strip()))


def _parse_price(raw: str | None) -> float | None:
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    try:
        val = float(s)
    except ValueError:
        return None
    return val if val > 0 else None


def prices_from_rows(rows: list[dict]) -> tuple[dict, dict]:
    """Group price rows by productId and pick a representative market price.

    Used by the live CSV ingest and by archive JSON (`results` arrays inside
    tcgcsv.com/archive/tcgplayer/prices-YYYY-MM-DD.ppmd.7z).
    """
    grouped: dict[str, list[dict]] = {}
    for row in rows:
        pid = str(row.get("productId") or "").strip()
        if not pid:
            continue
        grouped.setdefault(pid, []).append(row)
    prices: dict = {}
    subtypes: dict = {}
    for pid, grouped_rows in grouped.items():
        picked = pick_representative(grouped_rows)
        if picked:
            price, sub = picked
            prices[pid] = round(price, 2)
            subtypes[pid] = sub
    return prices, subtypes


def pick_representative(rows: list[dict]) -> tuple[float, str] | None:
    """Highest TCGplayer *market* price across regular printings.

    1st Edition rows are skipped unless they are the only priced printings
    (thin on TCGPlayer; same rule as S&Poké-500).
    """
    best = 0.0
    best_sub = ""
    fallback = 0.0
    fallback_sub = ""
    for row in rows:
        val = _parse_price(row.get("marketPrice"))
        if val is None:
            continue
        sub = row.get("subTypeName") or ""
        if "1st edition" in sub.lower():
            if val > fallback:
                fallback, fallback_sub = val, sub
        elif val > best:
            best, best_sub = val, sub
    if best:
        return best, best_sub
    if fallback:
        return fallback, fallback_sub
    return None


def fetch_groups() -> list[dict]:
    blob = _get_bytes(f"{TCGCSV}/tcgplayer/{CATEGORY}/Groups.csv")
    text = blob.decode("utf-8-sig")
    groups = []
    for row in csv.DictReader(io.StringIO(text)):
        gid = (row.get("groupId") or "").strip()
        if not gid:
            continue
        groups.append(
            {
                "groupId": gid,
                "name": clean_set_name(row.get("name") or ""),
                "abbreviation": (row.get("abbreviation") or "").strip(),
            }
        )
    if not groups:
        raise RuntimeError("TCGCSV Groups.csv returned no Pokemon sets")
    return groups


def ingest_group_csv(text: str, group: dict, catalog: dict, prices: dict, subtypes: dict) -> None:
    """Parse one ProductsAndPrices.csv into catalog + representative prices."""
    set_name = group["name"]
    set_id = str(group["groupId"])
    grouped: dict[str, list[dict]] = {}
    for row in csv.DictReader(io.StringIO(text)):
        number = (row.get("extNumber") or "").strip()
        if not number:
            continue  # sealed / non-single
        name = row.get("name") or "Unknown"
        if _excluded(name, set_name, number):
            continue
        pid = str(row.get("productId") or "").strip()
        if not pid:
            continue
        grouped.setdefault(pid, []).append(row)

    for pid, rows in grouped.items():
        first = rows[0]
        catalog[pid] = {
            "id": pid,
            "name": first.get("name") or "Unknown",
            "number": (first.get("extNumber") or "").strip(),
            "rarity": (first.get("extRarity") or "").strip(),
            "setName": set_name,
            "setId": set_id,
            "image": first.get("imageUrl") or "",
        }
        picked = pick_representative(rows)
        if picked:
            price, sub = picked
            prices[pid] = round(price, 2)
            subtypes[pid] = sub


def fetch_universe(verbose: bool = False, sleep_s: float = SLEEP_S) -> tuple[dict, dict, dict]:
    """Walk every Pokemon set dump. Returns (catalog, prices, subtypes)."""
    groups = fetch_groups()
    catalog: dict = {}
    prices: dict = {}
    subtypes: dict = {}
    failed = 0
    for i, group in enumerate(groups, 1):
        gid = group["groupId"]
        url = f"{TCGCSV}/tcgplayer/{CATEGORY}/{gid}/ProductsAndPrices.csv"
        try:
            text = _get_bytes(url).decode("utf-8-sig")
        except RuntimeError as err:
            failed += 1
            if verbose:
                print(f"  skip group {gid}: {err}", flush=True)
            continue
        ingest_group_csv(text, group, catalog, prices, subtypes)
        if verbose and i % 25 == 0:
            print(
                f"  dumps: {i}/{len(groups)} sets, "
                f"{len(catalog)} singles, {len(prices)} priced",
                flush=True,
            )
        if sleep_s:
            time.sleep(sleep_s)
    if not catalog:
        raise RuntimeError("TCGCSV returned an empty Pokemon singles catalog")
    if verbose and failed:
        print(f"  skipped {failed} set dumps", flush=True)
    return catalog, prices, subtypes


def rank_basket(prices: dict, catalog: dict, size: int = TARGET_SIZE) -> list[tuple[str, float]]:
    ranked = sorted(
        ((pid, pr) for pid, pr in prices.items() if pid in catalog and pr and pr > 0),
        key=lambda kv: kv[1],
        reverse=True,
    )
    return ranked[:size]


def step_index(
    prices: dict,
    catalog: dict,
    prev_ids: list[str] | None,
    prev_divisor: float | None,
    carry: dict | None = None,
    size: int = TARGET_SIZE,
    base: float = BASE_INDEX_VALUE,
) -> dict:
    """Price-weighted index step with an S&P-style divisor.

    Reprice yesterday's basket at today's prices for the day's return, then
    reset the divisor so today's (possibly rebalanced) basket stays continuous.
    """
    effective = dict(carry or {})
    effective.update(prices)
    basket = rank_basket(effective, catalog, size)
    ids = [pid for pid, _ in basket]
    sum_today = sum(pr for _, pr in basket)
    if not prev_divisor or not prev_ids:
        divisor = sum_today / base if sum_today else 1.0
        index = base
    else:
        sum_old_today = sum(effective.get(pid, 0.0) for pid in prev_ids) or sum_today
        index = sum_old_today / prev_divisor
        divisor = prev_divisor * (sum_today / sum_old_today) if sum_old_today else prev_divisor
    return {
        "index": index,
        "divisor": divisor,
        "ids": ids,
        "basket": basket,
        "sum_today": sum_today,
    }


def _age_days(iso: str | None, today_iso: str) -> int:
    try:
        a = datetime.strptime(iso or "", "%Y-%m-%d").date()
        b = datetime.strptime(today_iso, "%Y-%m-%d").date()
        return (b - a).days
    except ValueError:
        return 0


def as_of_date(stamp: str | None, now: datetime) -> str:
    """Prefer TCGCSV dump date so a morning build of last night's file
    does not collide with tonight's refresh on the same UTC calendar day."""
    if stamp:
        try:
            return datetime.strptime(stamp[:10], "%Y-%m-%d").date().isoformat()
        except ValueError:
            pass
    return now.date().isoformat()


def load_json(path: str, default):
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except (ValueError, OSError):
        return default


def write_json(path: str, obj) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(obj, handle, indent=2, ensure_ascii=False)
        handle.write("\n")


def _write_js(path: str, global_name: str, obj) -> None:
    payload = json.dumps(obj, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(f"window.{global_name} = " + payload + ";\n")


def write_latest_js(latest: dict) -> None:
    _write_js(LATEST_JS_PATH, "MARKET_EN", latest)


def write_history_js(history: dict) -> None:
    _write_js(HISTORY_JS_PATH, "MARKET_EN_HISTORY", history)


def build_snapshot(catalog: dict, prices: dict, subtypes: dict, stamp: str | None, now: datetime) -> dict:
    prev = load_json(LATEST_PATH, {})
    history_points = (load_json(HISTORY_PATH, {}) or {}).get("points") or []
    if history_points and not prev.get("divisor"):
        raise RuntimeError(
            "latest.json is missing/corrupt but history.json has "
            f"{len(history_points)} points — restore data/market-en/latest.json"
        )
    if prev.get("constituents") and not history_points:
        raise RuntimeError(
            "history.json is missing/corrupt but latest.json exists — "
            "restore data/market-en/history.json"
        )

    prev_divisor = prev.get("divisor")
    prev_index = prev.get("index")
    prev_prices = {
        c["id"]: c.get("price") for c in prev.get("constituents", []) if c.get("price")
    }
    prev_ids = [c["id"] for c in prev.get("constituents", [])] or None
    prev_priced_asof = {
        c["id"]: c.get("pricedAsOf") or prev.get("asOfDate")
        for c in prev.get("constituents", [])
    }
    prev_printing = {c["id"]: c.get("printing") for c in prev.get("constituents", [])}

    today_iso = as_of_date(stamp, now)
    if prev.get("asOfDate") == today_iso:
        baseline_index = prev.get("prevIndex") or prev_index
        baseline_prices = {
            c["id"]: c.get("prevPrice")
            for c in prev.get("constituents", [])
            if c.get("prevPrice")
        } or prev_prices
    else:
        baseline_index = prev_index
        baseline_prices = prev_prices

    carry = {
        pid: pr
        for pid, pr in prev_prices.items()
        if _age_days(prev_priced_asof.get(pid), today_iso) <= STALE_DAYS
    }
    step = step_index(prices, catalog, prev_ids, prev_divisor, carry=carry)

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
    gainers = [{k: c[k] for k in fields} for c in movable[:TOP_MOVERS] if c["changePct"] > 0]
    losers = [{k: c[k] for k in fields} for c in reversed(movable[-TOP_MOVERS:]) if c["changePct"] < 0]

    has_prev = baseline_index is not None
    index_value = step["index"]
    change_abs = round(index_value - baseline_index, 2) if has_prev else None
    change_pct = round((index_value / baseline_index - 1) * 100, 2) if has_prev else None
    breadth_available = bool(movable)
    base_date = (
        prev.get("baseDate")
        or (history_points[0].get("date") if history_points else None)
        or today_iso
    )

    latest = {
        "generated": now.isoformat(),
        "sourceStamp": stamp,
        "asOfDate": today_iso,
        "priceSource": "TCGplayer Market Price (via tcgcsv.com daily snapshot)",
        "universe": "English raw / ungraded singles (TCGPlayer category 3)",
        "staleDays": STALE_DAYS,
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
        "baseValue": BASE_INDEX_VALUE,
        "baseDate": base_date,
        "constituentCount": len(constituents),
        "totalValue": round(step["sum_today"], 2),
        "pricedToday": sum(1 for c in constituents if not c["carried"]),
        "breadth": {
            "available": breadth_available,
            "advancing": advancing,
            "declining": declining,
            "unchanged": unchanged,
        },
        "gainers": gainers,
        "losers": losers,
        "top": [
            {k: c[k] for k in ("rank", "id", "name", "number", "image", "setName", "price")}
            for c in constituents[:10]
        ],
        "constituents": constituents,
    }
    return latest


def merge_history_point(history, latest: dict, now: datetime) -> dict:
    """Upsert today's point. Keeps backfill metadata and older dates intact."""
    if not isinstance(history, dict):
        history = {}
    as_of = latest["asOfDate"]
    points = [p for p in history.get("points", []) if p.get("date") != as_of]
    points.append(
        {
            "date": as_of,
            "index": latest["index"],
            "totalValue": latest["totalValue"],
            "count": latest["constituentCount"],
        }
    )
    points.sort(key=lambda p: p["date"])
    out = dict(history)
    out["generated"] = now.isoformat()
    out["points"] = points
    out.setdefault("baseValue", latest.get("baseValue", BASE_INDEX_VALUE))
    out.setdefault(
        "baseDate",
        latest.get("baseDate") or (points[0]["date"] if points else ARCHIVE_START),
    )
    return out


def append_history(latest: dict, now: datetime) -> dict:
    return merge_history_point(load_json(HISTORY_PATH, {}), latest, now)


def self_test() -> None:
    catalog = {
        "a": {"id": "a", "name": "A", "number": "1", "rarity": "", "setName": "S", "setId": "1", "image": ""},
        "b": {"id": "b", "name": "B", "number": "2", "rarity": "", "setName": "S", "setId": "1", "image": ""},
        "c": {"id": "c", "name": "C", "number": "3", "rarity": "", "setName": "S", "setId": "1", "image": ""},
    }
    day1 = {"a": 100.0, "b": 50.0, "c": 10.0}
    s1 = step_index(day1, catalog, None, None, size=2, base=1000.0)
    assert s1["ids"] == ["a", "b"]
    assert abs(s1["sum_today"] - 150.0) < 1e-9
    assert abs(s1["index"] - 1000.0) < 1e-9
    assert abs(s1["divisor"] - 0.15) < 1e-9

    # Prices up 10% on the same basket — index should rise ~10%.
    day2 = {"a": 110.0, "b": 55.0, "c": 10.0}
    s2 = step_index(day2, catalog, s1["ids"], s1["divisor"], size=2, base=1000.0)
    assert abs(s2["index"] - 1100.0) < 1e-6

    # Rebalance: c enters, b leaves. Repriced old basket is a+b = 110+1 = 111
    # if b crashes; index should follow old-basket return, not jump with membership.
    day3 = {"a": 110.0, "b": 1.0, "c": 80.0}
    s3 = step_index(day3, catalog, s2["ids"], s2["divisor"], size=2, base=1000.0)
    # old basket a,b at today: 110+1 = 111; prev divisor from s2 = 165/1100 = 0.15
    expected = 111.0 / s2["divisor"]
    assert abs(s3["index"] - expected) < 1e-6
    assert s3["ids"] == ["a", "c"]

    rows = [
        {"marketPrice": "10", "subTypeName": "Holofoil"},
        {"marketPrice": "12", "subTypeName": "1st Edition Holofoil"},
        {"marketPrice": "8", "subTypeName": "Reverse Holofoil"},
    ]
    picked = pick_representative(rows)
    assert picked and picked[0] == 10.0 and picked[1] == "Holofoil"

    only_1st = [{"marketPrice": "20", "subTypeName": "1st Edition"}]
    picked2 = pick_representative(only_1st)
    assert picked2 and picked2[0] == 20.0

    cat: dict = {}
    pr: dict = {}
    st: dict = {}
    csv_text = (
        "productId,name,imageUrl,extNumber,extRarity,marketPrice,subTypeName\n"
        "1,Pikachu,http://img,025/102,Rare,5.00,Holofoil\n"
        "1,Pikachu,http://img,025/102,Rare,4.00,Reverse Holofoil\n"
        "2,Booster Box,http://img,,,199.00,Normal\n"
        "3,Pikachu 227/S-P,http://img,227/S-P,Promo,999.00,Holofoil\n"
    )
    ingest_group_csv(csv_text, {"groupId": "9", "name": "Base Set"}, cat, pr, st)
    assert "1" in cat and cat["1"]["name"] == "Pikachu"
    assert pr["1"] == 5.0
    assert "2" not in cat  # sealed (no number)
    assert "3" not in cat  # JP promo -P

    fake_now = datetime(2026, 10, 9, 7, 0, tzinfo=timezone.utc)
    assert as_of_date("2026-10-08T20:05:18+0000", fake_now) == "2026-10-08"
    assert as_of_date(None, fake_now) == "2026-10-09"

    archive_rows = [
        {"productId": 1, "marketPrice": 5.0, "subTypeName": "Holofoil"},
        {"productId": 1, "marketPrice": 12.0, "subTypeName": "1st Edition Holofoil"},
        {"productId": 1, "marketPrice": 4.0, "subTypeName": "Reverse Holofoil"},
        {"productId": 9, "marketPrice": 0, "subTypeName": "Normal"},
    ]
    pr3, st3 = prices_from_rows(archive_rows)
    assert pr3 == {"1": 5.0} and st3["1"] == "Holofoil"

    preserved = merge_history_point(
        {
            "baseDate": ARCHIVE_START,
            "baseValue": 1000,
            "sampling": {"stepDays": 7, "denseDays": 183},
            "points": [
                {"date": "2024-02-08", "index": 1000.0, "totalValue": 1, "count": 500},
                {"date": "2026-10-08", "index": 1200.0, "totalValue": 2, "count": 500},
            ],
        },
        {
            "asOfDate": "2026-10-08",
            "index": 1201.5,
            "totalValue": 3,
            "constituentCount": 500,
            "baseValue": 1000,
            "baseDate": ARCHIVE_START,
        },
        fake_now,
    )
    assert preserved["baseDate"] == ARCHIVE_START
    assert preserved["sampling"]["denseDays"] == 183
    assert [p["date"] for p in preserved["points"]] == ["2024-02-08", "2026-10-08"]
    assert preserved["points"][-1]["index"] == 1201.5
    print("self-test ok")


def build(verbose: bool = False, force: bool = False) -> None:
    prev = load_json(LATEST_PATH, {})
    stamp = source_stamp()
    if stamp and prev.get("sourceStamp") == stamp and not force:
        print(f"TCGCSV unchanged since {stamp}; nothing to do.", flush=True)
        return

    print("Fetching Pokemon category dumps from TCGCSV ...", flush=True)
    catalog, prices, subtypes = fetch_universe(verbose=verbose)
    if len(prices) < TARGET_SIZE:
        raise RuntimeError(f"Only {len(prices)} priced English singles from TCGCSV")
    print(f"  catalog {len(catalog)} singles / {len(prices)} with market price", flush=True)

    now = datetime.now(timezone.utc)
    latest = build_snapshot(catalog, prices, subtypes, stamp, now)
    history = append_history(latest, now)
    write_json(LATEST_PATH, latest)
    write_json(HISTORY_PATH, history)
    write_latest_js(latest)
    write_history_js(history)

    ch = latest["changePct"]
    ch_s = "n/a (first snapshot)" if ch is None else f"{ch:+.2f}%"
    print(
        f"EN pulse = {latest['index']:.2f} ({ch_s}) "
        f"| basket {latest['constituentCount']} "
        f"| total ${latest['totalValue']:,.0f} "
        f"| asOf {latest['asOfDate']}",
        flush=True,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-test", action="store_true", help="Run index-math checks and exit")
    parser.add_argument("--force", action="store_true", help="Rebuild even if TCGCSV stamp is unchanged")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)
    if args.self_test:
        self_test()
        return 0
    try:
        build(verbose=args.verbose, force=args.force)
    except Exception as err:  # noqa: BLE001 — surface a clean CI failure
        print(f"ERROR: {err}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
