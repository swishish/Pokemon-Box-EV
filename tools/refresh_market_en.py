#!/usr/bin/env python3
"""Build an S&P-style English raw-singles market pulse from TCGCSV dumps.

Phase 1 of Pokemon-Box-EV: English TCGPlayer market only (category 3).
No TCGPlayer API key. No SNKRDUNK / PriceCharting scrape.

Data source: TCGCSV daily Pokemon category dumps
  https://tcgcsv.com/tcgplayer/3/Groups.csv
  https://tcgcsv.com/tcgplayer/3/{groupId}/ProductsAndPrices.csv

Writes (stdlib only — GitHub Actions needs no pip install):
  data/market-en/latest.json         index + breadth + movers + basket (for next run)
  data/market-en/history.json        one point per snapshot date (sparkline)
  data/market-en/price-history.json  per-card basket prices by date (1D/5D/30D/YTD)
  data/market-en/latest.js           window.MARKET_EN fallback (file:// / cache)

Index math matches S&Poké-500 in spirit: price-weighted top 500 English raw
singles with an index divisor so the level stays continuous when the basket
turns over. First snapshot is rebased to 1,000; daily % / breadth appear once
a previous snapshot exists.
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
from datetime import date, datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(ROOT, "data", "market-en")
LATEST_PATH = os.path.join(DATA_DIR, "latest.json")
LATEST_JS_PATH = os.path.join(DATA_DIR, "latest.js")
HISTORY_PATH = os.path.join(DATA_DIR, "history.json")
PRICE_HISTORY_PATH = os.path.join(DATA_DIR, "price-history.json")

TCGCSV = "https://tcgcsv.com"
CATEGORY = 3  # English Pokemon
USER_AGENT = "Pokemon-Box-EV/1.19 (+https://github.com/swishish/Pokemon-Box-EV)"
BASE_INDEX_VALUE = 1000.0
TARGET_SIZE = 500
STALE_DAYS = 70
SLEEP_S = 0.1
TOP_MOVERS = 10
MOVER_PERIODS = ("1D", "5D", "30D", "YTD")
# Accept a baseline a few days older than the exact lookback if a dump was missed.
PERIOD_SLACK_DAYS = {"5D": 3, "30D": 7, "YTD": 21}
PRICE_HISTORY_KEEP_DAYS = 400
MOVER_FIELDS = ("id", "name", "number", "image", "setName", "price", "prevPrice", "changePct")

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


def write_latest_js(latest: dict) -> None:
    payload = json.dumps(latest, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    with open(LATEST_JS_PATH, "w", encoding="utf-8") as handle:
        handle.write("window.MARKET_EN = " + payload + ";\n")


def parse_iso_date(raw: str) -> date:
    return datetime.strptime(raw, "%Y-%m-%d").date()


def collect_basket_prices(constituents: list) -> dict[str, float]:
    prices: dict[str, float] = {}
    for row in constituents or []:
        pid = str(row.get("id") or "").strip()
        price = row.get("price")
        if not pid or price is None:
            continue
        try:
            val = float(price)
        except (TypeError, ValueError):
            continue
        if val > 0:
            prices[pid] = round(val, 2)
    return {pid: prices[pid] for pid in sorted(prices)}


def period_needed_by(as_of: str, period: str) -> str | None:
    """Earliest snapshot date required for a honest lookback (inclusive)."""
    as_of_d = parse_iso_date(as_of)
    if period == "1D":
        return None
    if period == "5D":
        return (as_of_d - timedelta(days=5)).isoformat()
    if period == "30D":
        return (as_of_d - timedelta(days=30)).isoformat()
    if period == "YTD":
        return date(as_of_d.year, 1, 1).isoformat()
    raise ValueError(f"unknown period {period}")


def pick_baseline_date(dates: list[str], as_of: str, period: str) -> str | None:
    """Latest stored snapshot that can serve as the period baseline.

    1D = previous snapshot (any gap). Other periods need a date on or before
    the lookback target, not older than PERIOD_SLACK_DAYS past that target.
    Dates after `as_of` are ignored. Missing history returns None — never
    substitute a nearer snapshot (that would invent a 5D/30D/YTD return).
    """
    known = sorted({d for d in dates if d and d < as_of})
    if period == "1D":
        return known[-1] if known else None
    target = period_needed_by(as_of, period)
    if not target:
        return None
    slack = PERIOD_SLACK_DAYS[period]
    earliest_ok = (parse_iso_date(target) - timedelta(days=slack)).isoformat()
    candidates = [d for d in known if earliest_ok <= d <= target]
    return candidates[-1] if candidates else None


def movers_from_constituents(
    constituents: list,
    baseline_prices: dict,
    top_n: int = TOP_MOVERS,
) -> tuple[list, list]:
    """Top gainers / losers vs a historical price map. Skips carried rows."""
    movable = []
    for row in constituents or []:
        if row.get("carried"):
            continue
        pid = str(row.get("id") or "")
        price = row.get("price")
        prior = baseline_prices.get(pid)
        if not pid or not price or not prior or prior <= 0:
            continue
        change_pct = round((float(price) / float(prior) - 1) * 100, 2)
        item = {k: row.get(k) for k in ("id", "name", "number", "image", "setName")}
        item["price"] = round(float(price), 2)
        item["prevPrice"] = round(float(prior), 2)
        item["changePct"] = change_pct
        movable.append(item)
    movable.sort(key=lambda c: c["changePct"], reverse=True)
    gainers = [{k: c[k] for k in MOVER_FIELDS} for c in movable[:top_n] if c["changePct"] > 0]
    losers = [
        {k: c[k] for k in MOVER_FIELDS}
        for c in reversed(movable[-top_n:])
        if c["changePct"] < 0
    ]
    return gainers, losers


def prune_price_points(points: list, as_of: str) -> list:
    """Keep ~400 recent days plus the last snapshot of each older year (YTD)."""
    as_of_d = parse_iso_date(as_of)
    cutoff = (as_of_d - timedelta(days=PRICE_HISTORY_KEEP_DAYS)).isoformat()
    by_date = {}
    for point in points or []:
        d = point.get("date")
        if d:
            by_date[d] = point
    year_end: dict[int, dict] = {}
    recent = []
    for d in sorted(by_date):
        point = by_date[d]
        if d >= cutoff:
            recent.append(point)
            continue
        year = int(d[:4])
        if year < as_of_d.year:
            year_end[year] = point
    recent_years = {int(p["date"][:4]) for p in recent}
    anchors = [year_end[y] for y in sorted(year_end) if y not in recent_years]
    return anchors + recent


def upsert_price_history(history: dict, latest: dict, now: datetime | None = None) -> dict:
    as_of = latest.get("asOfDate")
    if not as_of:
        return history if isinstance(history, dict) else {"points": []}
    if not isinstance(history, dict):
        history = {"points": []}
    prices = collect_basket_prices(latest.get("constituents") or [])
    points = [p for p in history.get("points", []) if p.get("date") != as_of]
    if prices:
        points.append({"date": as_of, "prices": prices})
    points = prune_price_points(points, as_of)
    stamp = (now or datetime.now(timezone.utc)).isoformat()
    return {"generated": stamp, "points": points}


def snapshot_range(price_history: dict) -> dict:
    dates = sorted(p["date"] for p in (price_history.get("points") or []) if p.get("date"))
    return {
        "earliest": dates[0] if dates else None,
        "latest": dates[-1] if dates else None,
        "count": len(dates),
    }


def build_period_movers(latest: dict, price_history: dict) -> dict:
    """1D copies the existing snapshot movers; 5D/30D/YTD use stored prices."""
    as_of = latest["asOfDate"]
    dates = [p["date"] for p in price_history.get("points") or [] if p.get("date")]
    prices_by_date = {
        p["date"]: p.get("prices") or {}
        for p in price_history.get("points") or []
        if p.get("date")
    }
    constituents = latest.get("constituents") or []
    out = {}
    for period in MOVER_PERIODS:
        needed = period_needed_by(as_of, period)
        baseline = pick_baseline_date(dates, as_of, period)
        if period == "1D":
            has_prev = latest.get("prevIndex") is not None or bool(latest.get("gainers") or latest.get("losers"))
            # Same lists as the legacy gainers/losers fields (vs previous snapshot).
            out[period] = {
                "available": bool(has_prev),
                "baselineDate": baseline,
                "neededBy": needed,
                "label": "vs previous snapshot" if has_prev else "need a previous snapshot",
                "gainers": latest.get("gainers") or [],
                "losers": latest.get("losers") or [],
            }
            continue
        if not baseline:
            out[period] = {
                "available": False,
                "baselineDate": None,
                "neededBy": needed,
                "label": (
                    f"need a snapshot on or before {needed}"
                    if needed
                    else "need a previous snapshot"
                ),
                "gainers": [],
                "losers": [],
            }
            continue
        gainers, losers = movers_from_constituents(constituents, prices_by_date.get(baseline) or {})
        out[period] = {
            "available": True,
            "baselineDate": baseline,
            "neededBy": needed,
            "label": f"vs {baseline}",
            "gainers": gainers,
            "losers": losers,
        }
    return out


def attach_period_movers(latest: dict, price_history: dict) -> dict:
    latest["snapshotRange"] = snapshot_range(price_history)
    latest["movers"] = build_period_movers(latest, price_history)
    return latest


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


def append_history(latest: dict, now: datetime) -> dict:
    history = load_json(HISTORY_PATH, {})
    if not isinstance(history, dict):
        history = {"points": []}
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
    return {"generated": now.isoformat(), "points": points}


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

    dates = [
        "2026-01-02",
        "2026-09-10",
        "2026-10-06",
        "2026-10-08",
        "2026-10-09",
        "2026-10-10",
        "2026-10-11",
    ]
    assert pick_baseline_date(dates, "2026-10-11", "1D") == "2026-10-10"
    assert pick_baseline_date(dates, "2026-10-11", "5D") == "2026-10-06"
    assert pick_baseline_date(dates, "2026-10-11", "30D") == "2026-09-10"
    # Jan 2 is after New Year's Day — must not stand in for YTD.
    assert pick_baseline_date(dates, "2026-10-11", "YTD") is None
    # Four-day repo history cannot fill 5D / 30D / YTD.
    short = ["2026-10-08", "2026-10-09", "2026-10-10", "2026-10-11"]
    assert pick_baseline_date(short, "2026-10-11", "1D") == "2026-10-10"
    assert pick_baseline_date(short, "2026-10-11", "5D") is None
    assert pick_baseline_date(short, "2026-10-11", "30D") is None
    assert pick_baseline_date(short, "2026-10-11", "YTD") is None
    # Do not use a nearer snapshot when the lookback date is missing.
    assert pick_baseline_date(["2026-10-08", "2026-10-11"], "2026-10-11", "5D") is None
    # Dec 31 previous year is a valid YTD baseline.
    assert pick_baseline_date(["2025-12-31", "2026-10-11"], "2026-10-11", "YTD") == "2025-12-31"
    assert period_needed_by("2026-10-11", "5D") == "2026-10-06"
    assert period_needed_by("2026-10-11", "30D") == "2026-09-11"
    assert period_needed_by("2026-10-11", "YTD") == "2026-01-01"

    constituents = [
        {"id": "a", "name": "A", "number": "1", "image": "", "setName": "S", "price": 110.0, "carried": False},
        {"id": "b", "name": "B", "number": "2", "image": "", "setName": "S", "price": 40.0, "carried": False},
        {"id": "c", "name": "C", "number": "3", "image": "", "setName": "S", "price": 80.0, "carried": True},
    ]
    gainers, losers = movers_from_constituents(constituents, {"a": 100.0, "b": 50.0, "c": 40.0})
    assert len(gainers) == 1 and gainers[0]["id"] == "a" and gainers[0]["changePct"] == 10.0
    assert len(losers) == 1 and losers[0]["id"] == "b" and losers[0]["changePct"] == -20.0

    fake_latest = {
        "asOfDate": "2026-10-11",
        "prevIndex": 993.39,
        "gainers": gainers,
        "losers": losers,
        "constituents": constituents,
    }
    hist = {
        "points": [
            {"date": "2026-10-06", "prices": {"a": 100.0, "b": 50.0}},
            {"date": "2026-10-08", "prices": {"a": 102.0, "b": 48.0}},
            {"date": "2026-10-11", "prices": {"a": 110.0, "b": 40.0}},
        ]
    }
    periods = build_period_movers(fake_latest, hist)
    assert periods["1D"]["available"] is True
    assert periods["5D"]["available"] is True
    assert periods["5D"]["baselineDate"] == "2026-10-06"
    assert periods["5D"]["gainers"][0]["id"] == "a"
    assert periods["5D"]["gainers"][0]["changePct"] == 10.0
    assert periods["30D"]["available"] is False
    assert periods["YTD"]["available"] is False

    pruned = prune_price_points(
        [
            {"date": "2024-12-30", "prices": {"a": 1}},
            {"date": "2025-06-01", "prices": {"a": 1.5}},
            {"date": "2025-12-31", "prices": {"a": 2}},
            {"date": "2026-10-11", "prices": {"a": 3}},
        ],
        "2026-10-11",
    )
    pruned_dates = [p["date"] for p in pruned]
    assert "2024-12-30" in pruned_dates  # last 2024 snapshot, kept for later YTD
    assert "2025-06-01" not in pruned_dates  # 2025-12-31 is inside the 400-day window
    assert "2025-12-31" in pruned_dates
    assert "2026-10-11" in pruned_dates

    print("self-test ok")


def rebuild_movers(now: datetime | None = None) -> None:
    """Recompute period movers from stored snapshots without fetching TCGCSV."""
    latest = load_json(LATEST_PATH, {})
    if not latest.get("asOfDate"):
        raise RuntimeError("latest.json is missing asOfDate")
    stamp = now or datetime.now(timezone.utc)
    price_history = upsert_price_history(load_json(PRICE_HISTORY_PATH, {}), latest, stamp)
    attach_period_movers(latest, price_history)
    write_json(LATEST_PATH, latest)
    write_json(PRICE_HISTORY_PATH, price_history)
    write_latest_js(latest)
    rng = latest.get("snapshotRange") or {}
    avail = [p for p, info in (latest.get("movers") or {}).items() if info.get("available")]
    print(
        f"movers rebuilt asOf {latest['asOfDate']} "
        f"| snapshots {rng.get('earliest')}→{rng.get('latest')} ({rng.get('count')}) "
        f"| available {', '.join(avail) or 'none'}",
        flush=True,
    )


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
    price_history = upsert_price_history(load_json(PRICE_HISTORY_PATH, {}), latest, now)
    attach_period_movers(latest, price_history)
    history = append_history(latest, now)
    write_json(LATEST_PATH, latest)
    write_json(HISTORY_PATH, history)
    write_json(PRICE_HISTORY_PATH, price_history)
    write_latest_js(latest)

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
    parser.add_argument(
        "--rebuild-movers",
        action="store_true",
        help="Recompute 1D/5D/30D/YTD movers from stored price history (no TCGCSV fetch)",
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
    try:
        build(verbose=args.verbose, force=args.force)
    except Exception as err:  # noqa: BLE001 — surface a clean CI failure
        print(f"ERROR: {err}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
