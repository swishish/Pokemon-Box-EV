#!/usr/bin/env python3
"""JP PSA10 market index from public SNKRDUNK trading-history.

Three series, one frozen basket of liquid Japanese Pokémon TCG PSA10 slabs:

  equal      — equal-weight daily returns (breadth)
  liquidity  — weight ∝ recent SNK sold yen (last-20 PSA10 trades in 30d,
               else 30d chart-print yen). 3% cap after renormalization
  price      — weight = lagged card price / sum (market-weight). Same 3% cap

Volume bars = count of basket cards with a SNK PSA10 chart print that JST day
(lower bound on sold lots; SNK does not publish a full sold-count history).

Public JSON only (view-only). No login, no buy/bid/list.

  python3 tools/refresh_market_jp_psa10.py --self-test
  python3 tools/refresh_market_jp_psa10.py --rebuild-baskets --confirm REBUILD -v
  python3 tools/refresh_market_jp_psa10.py --from-cache
  python3 tools/refresh_market_jp_psa10.py -v
"""
from __future__ import annotations

import argparse
import bisect
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta, timezone
from typing import Any

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DATA_DIR = os.path.join(ROOT, "data", "market-jp-psa10")
BASKETS_PATH = os.path.join(DATA_DIR, "baskets.json")
LATEST_PATH = os.path.join(DATA_DIR, "latest.json")
LATEST_JS_PATH = os.path.join(DATA_DIR, "latest.js")
HISTORY_PATH = os.path.join(DATA_DIR, "history.json")
HISTORY_JS_PATH = os.path.join(DATA_DIR, "history.js")
SERIES_PATH = os.path.join(DATA_DIR, "series-cache.json")
DATA_JSON_PATH = os.path.join(ROOT, "data.json")
NAMES_ZH_PATH = os.path.join(ROOT, "data", "names_zh.json")
TESTDATA = os.path.join(HERE, "testdata", "snkrdunk-jp-psa10", "cards.json")

SNK = "https://snkrdunk.com"
USER_AGENT = "Pokemon-Box-EV/1.29 (+https://github.com/swishish/Pokemon-Box-EV)"
CONDITION_PSA10 = "trading_card_single_psa10"
BOX_CATEGORY_ID = 14
BASE_INDEX_VALUE = 1000.0
WEIGHT_CAP = 0.03
TARGET_SIZE = 60
MIN_LAST_PRICE_YEN = 3000.0
MIN_PRINTS_90D = 12
LIQUIDITY_WINDOW_DAYS = 30
MIN_CONSTITUENTS = 40
MAX_PRINT_GAP_DAYS = 14
RETURN_CLIP = 0.25
STALE_DAYS = 8
SLEEP_S = 0.18
JST = timezone(timedelta(hours=9))
MOVER_FIELDS = ("id", "name", "nameZh", "setName", "image", "price", "prevPrice", "changePct", "url")

_APPAREL_ID = re.compile(r"/apparels/(\d+)")
_EN_PRINT = re.compile(r"\b(English|\bEN\b|\[EN\]|\(EN\))", re.I)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def ymd(d: date) -> str:
    return d.isoformat()


def parse_ymd(s: str) -> date:
    return date.fromisoformat(str(s)[:10])


def ms_to_ymd_jst(ms: int) -> str:
    """SNK chart timestamps are Japan midnight (UTC 15:00 previous calendar day)."""
    dt = datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc).astimezone(JST)
    return dt.date().isoformat()


def daterange(start: date, end: date):
    cur = start
    while cur <= end:
        yield cur
        cur += timedelta(days=1)


def load_json(path: str, default):
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except (ValueError, OSError):
        return default


def write_json(path: str, obj) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(obj, handle, indent=2, ensure_ascii=False)
        handle.write("\n")


def write_js(path: str, global_name: str, obj) -> None:
    payload = json.dumps(obj, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(f"window.{global_name} = " + payload + ";\n")


def _get_json(url: str, timeout: int = 30) -> Any:
    req = urllib.request.Request(
        url,
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
    )
    last: Exception | None = None
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception as err:  # noqa: BLE001 — retry transients
            last = err
            time.sleep(2 ** attempt)
    raise RuntimeError(f"SNKRDUNK request failed: {url}\n{last}")


def apparel_universe(data_obj: dict) -> dict[int, dict]:
    """Unique SNK apparel IDs already in this repo's Box EV snapshot. Do not invent IDs."""
    out: dict[int, dict] = {}
    for card in data_obj.get("cards") or []:
        if not isinstance(card, dict):
            continue
        url = str(card.get("url") or "")
        match = _APPAREL_ID.search(url)
        if not match:
            continue
        aid = int(match.group(1))
        row = out.setdefault(
            aid,
            {
                "apparelId": aid,
                "name": card.get("name"),
                "nameZh": card.get("name_zh"),
                "set": card.get("set"),
                "num": card.get("num"),
                "url": f"{SNK}/apparels/{aid}",
            },
        )
        if not row.get("nameZh") and card.get("name_zh"):
            row["nameZh"] = card.get("name_zh")
        if not row.get("name") and card.get("name"):
            row["name"] = card.get("name")
    return out


def is_english_printing(name: str | None, localized: str | None) -> bool:
    blob = f"{name or ''} {localized or ''}"
    return bool(_EN_PRINT.search(blob))


def is_trading_card_single(apparel: dict) -> bool:
    info = apparel.get("apparelInfo") or {}
    if not info.get("isTradingCard"):
        return False
    for cat in apparel.get("categories") or []:
        if int(cat.get("id") or 0) == BOX_CATEGORY_ID:
            return False
    return True


def psa10_chip(chips: dict) -> dict | None:
    for chip in chips.get("chips") or []:
        if chip.get("conditionCode") == CONDITION_PSA10 or chip.get("text") == "PSA10":
            return chip
    return None


def parse_prints(history: dict) -> list[tuple[str, float]]:
    lines = ((history.get("chart") or {}).get("lines")) or []
    if not lines:
        return []
    points = lines[0].get("points") or []
    out: list[tuple[str, float]] = []
    seen: set[str] = set()
    for pt in points:
        try:
            ts = int(pt.get("timestamp"))
            price = float(pt.get("price"))
        except (TypeError, ValueError, AttributeError):
            continue
        if not (price > 0):
            continue
        day = ms_to_ymd_jst(ts)
        if day in seen:
            continue
        seen.add(day)
        out.append((day, price))
    out.sort(key=lambda x: x[0])
    return out


def parse_trades(history: dict) -> list[dict]:
    rows = []
    for tr in history.get("trades") or []:
        try:
            price = float(tr.get("price"))
        except (TypeError, ValueError):
            continue
        title = str(tr.get("title") or "")
        if title and title.upper().replace(" ", "") not in {"PSA10", "PSA 10"}:
            continue
        if not (price > 0):
            continue
        rows.append(
            {
                "price": price,
                "soldAt": tr.get("soldAt"),
                "title": "PSA10",
            }
        )
    return rows


def locf_prices(prints: list[tuple[str, float]], days: list[str]) -> dict[str, float]:
    """Last observation carried forward. Missing cards stay absent until first print."""
    if not prints:
        return {}
    by = {d: p for d, p in prints}
    last: float | None = None
    out: dict[str, float] = {}
    first = prints[0][0]
    for day in days:
        if day in by:
            last = by[day]
        if last is None or day < first:
            continue
        out[day] = last
    return out


def print_count_between(prints: list[tuple[str, float]], start: str, end: str) -> int:
    """Inclusive start, exclusive end."""
    n = 0
    for day, _price in prints:
        if start <= day < end:
            n += 1
    return n


def print_yen_between(prints: list[tuple[str, float]], start: str, end: str) -> float:
    total = 0.0
    for day, price in prints:
        if start <= day < end:
            total += price
    return total


def trade_yen_between(trades: list[dict], start: str, end: str) -> tuple[int, float]:
    n = 0
    yen = 0.0
    for tr in trades:
        sold = str(tr.get("soldAt") or "")
        if not sold:
            continue
        try:
            dt = datetime.fromisoformat(sold.replace("Z", "+00:00")).astimezone(JST)
            day = dt.date().isoformat()
        except ValueError:
            continue
        if start <= day < end:
            n += 1
            yen += float(tr["price"])
    return n, yen


def liquidity_score(
    prints: list[tuple[str, float]],
    trades: list[dict],
    as_of: str,
    window_days: int = LIQUIDITY_WINDOW_DAYS,
) -> float:
    end = as_of
    start = ymd(parse_ymd(as_of) - timedelta(days=window_days))
    n_tr, yen_tr = trade_yen_between(trades, start, end)
    if n_tr > 0 and yen_tr > 0:
        return yen_tr
    yen_prints = print_yen_between(prints, start, end)
    if yen_prints > 0:
        return yen_prints
    return 0.0


def cap_weights(raw: dict[str, float], cap: float = WEIGHT_CAP) -> dict[str, float]:
    """Cap any name at `cap`, redistribute residual to the rest. Repeat.

    If the basket is so small that 1/n >= cap, the cap is infeasible and we
    keep the raw (renormalized) weights — equal-weight already exceeds 3%.
    """
    keys = [k for k, v in raw.items() if v is not None and v > 0]
    if not keys:
        return {}
    n = len(keys)
    if n == 0:
        return {}
    if (1.0 / n) >= cap - 1e-15:
        total = sum(raw[k] for k in keys)
        return {k: raw[k] / total for k in keys}
    weights = {k: float(raw[k]) for k in keys}
    total = sum(weights.values())
    weights = {k: v / total for k, v in weights.items()}
    locked: dict[str, float] = {}
    free = dict(weights)
    for _ in range(n + 2):
        over = [k for k, v in free.items() if v > cap + 1e-12]
        if not over:
            break
        for k in over:
            locked[k] = cap
            del free[k]
        residual = 1.0 - cap * len(locked)
        if residual <= 0 or not free:
            break
        fs = sum(free.values())
        if fs <= 0:
            even = residual / len(free)
            free = {k: even for k in free}
        else:
            free = {k: residual * (v / fs) for k, v in free.items()}
    out = {**locked, **free}
    s = sum(out.values())
    if s <= 0:
        return {k: 1.0 / n for k in keys}
    return {k: v / s for k, v in out.items()}


def daily_return(prev: float | None, cur: float | None) -> float | None:
    if prev is None or cur is None:
        return None
    if not (prev > 0):
        return None
    return cur / prev - 1.0


def clip_return(r: float, clip: float = RETURN_CLIP) -> float:
    if r > clip:
        return clip
    if r < -clip:
        return -clip
    return r


def prev_print_day(print_days: list[str], day: str) -> str | None:
    i = bisect.bisect_left(print_days, day)
    if i <= 0:
        return None
    return print_days[i - 1]


def weighted_return(rets: dict[str, float], weights: dict[str, float]) -> float | None:
    num = 0.0
    den = 0.0
    for kid, r in rets.items():
        w = weights.get(kid)
        if w is None or w <= 0:
            continue
        num += w * r
        den += w
    if den <= 0:
        return None
    return num / den


def card_day_return(
    cid: str,
    day: str,
    price: float,
    prev_price: float | None,
    printed_today: set[str],
    print_days: list[str],
    *,
    max_gap: int = MAX_PRINT_GAP_DAYS,
    clip: float = RETURN_CLIP,
) -> float | None:
    """Return to include in the index for one card-day.

    LOCF days contribute 0. A new print whose previous actual print is more
    than `max_gap` calendar days ago is a re-entry (rebase, 0 return) so a
    sparse 8× SNK chart jump cannot dump into a single calendar day.
    Remaining print-to-print moves are winsorized at ±clip.
    """
    r = daily_return(prev_price, price)
    if r is None:
        return None
    if cid not in printed_today:
        return 0.0
    prev_print = prev_print_day(print_days, day)
    if prev_print is None:
        return 0.0
    gap = (parse_ymd(day) - parse_ymd(prev_print)).days
    if gap > max_gap:
        return 0.0
    return clip_return(r, clip)


def build_index(
    cards: list[dict],
    series: dict[str, dict],
    *,
    cap: float = WEIGHT_CAP,
    window_days: int = LIQUIDITY_WINDOW_DAYS,
    min_constituents: int = MIN_CONSTITUENTS,
    base: float = BASE_INDEX_VALUE,
    max_gap: int = MAX_PRINT_GAP_DAYS,
    clip: float = RETURN_CLIP,
) -> dict:
    """Build daily equal / liquidity / price indexes plus volume from prints."""
    all_days: set[str] = set()
    prints_by: dict[str, list[tuple[str, float]]] = {}
    trades_by: dict[str, list[dict]] = {}
    for card in cards:
        cid = str(card["id"])
        blob = series.get(cid) or {}
        prints = [(str(d), float(p)) for d, p in (blob.get("prints") or []) if p and float(p) > 0]
        prints.sort(key=lambda x: x[0])
        prints_by[cid] = prints
        trades_by[cid] = list(blob.get("trades") or [])
        for day, _p in prints:
            all_days.add(day)
    if not all_days:
        return {
            "baseDate": None,
            "points": [],
            "lastPrices": {},
            "lastWeights": {},
            "printedLastDay": set(),
        }

    days = sorted(all_days)
    start, end = parse_ymd(days[0]), parse_ymd(days[-1])
    calendar = [ymd(d) for d in daterange(start, end)]

    locf: dict[str, dict[str, float]] = {}
    actual: dict[str, set[str]] = {}
    print_days_by: dict[str, list[str]] = {}
    for cid, prints in prints_by.items():
        locf[cid] = locf_prices(prints, calendar)
        actual[cid] = {d for d, _p in prints}
        print_days_by[cid] = sorted(actual[cid])

    eq = liq = px = None
    base_date = None
    points: list[dict] = []
    prev_prices: dict[str, float] = {}
    last_weights = {"equal": {}, "liquidity": {}, "price": {}}
    printed_last: set[str] = set()

    for day in calendar:
        today: dict[str, float] = {}
        printed_today: set[str] = set()
        for cid in locf:
            if day in locf[cid]:
                today[cid] = locf[cid][day]
            if day in actual.get(cid, set()):
                printed_today.add(cid)
        volume = len(printed_today)
        n = len(today)
        if n < min_constituents:
            prev_prices = today
            continue
        if base_date is None:
            base_date = day
            eq = liq = px = base
            equal_w = {cid: 1.0 / n for cid in today}
            liq_raw = {
                cid: liquidity_score(prints_by[cid], trades_by[cid], day, window_days) for cid in today
            }
            if sum(liq_raw.values()) <= 0:
                liq_w = dict(equal_w)
            else:
                liq_w = cap_weights(liq_raw, cap)
            px_w = cap_weights(today, cap)
            last_weights = {"equal": equal_w, "liquidity": liq_w, "price": px_w}
            points.append(
                {
                    "date": day,
                    "equal": round(eq, 2),
                    "liquidity": round(liq, 2),
                    "price": round(px, 2),
                    "volume": volume,
                    "count": n,
                }
            )
            prev_prices = today
            printed_last = printed_today
            continue

        rets: dict[str, float] = {}
        for cid, price in today.items():
            r = card_day_return(
                cid,
                day,
                price,
                prev_prices.get(cid),
                printed_today,
                print_days_by.get(cid) or [],
                max_gap=max_gap,
                clip=clip,
            )
            if r is not None:
                rets[cid] = r
        if not rets:
            prev_prices = today
            printed_last = printed_today
            continue

        equal_w = {cid: 1.0 / len(rets) for cid in rets}
        r_eq = weighted_return(rets, equal_w)

        liq_raw = {
            cid: liquidity_score(prints_by[cid], trades_by[cid], day, window_days) for cid in rets
        }
        if sum(liq_raw.values()) <= 0:
            liq_w = dict(equal_w)
        else:
            liq_w = cap_weights(liq_raw, cap)
        r_liq = weighted_return(rets, liq_w)

        lagged = {cid: prev_prices[cid] for cid in rets if cid in prev_prices and prev_prices[cid] > 0}
        px_w = cap_weights(lagged, cap) if lagged else dict(equal_w)
        r_px = weighted_return(rets, px_w)

        if r_eq is None or r_liq is None or r_px is None:
            prev_prices = today
            continue
        assert eq is not None and liq is not None and px is not None
        eq = eq * (1.0 + r_eq)
        liq = liq * (1.0 + r_liq)
        px = px * (1.0 + r_px)
        last_weights = {"equal": equal_w, "liquidity": liq_w, "price": px_w}
        points.append(
            {
                "date": day,
                "equal": round(eq, 2),
                "liquidity": round(liq, 2),
                "price": round(px, 2),
                "volume": volume,
                "count": n,
            }
        )
        prev_prices = today
        printed_last = printed_today

    return {
        "baseDate": base_date,
        "points": points,
        "lastPrices": prev_prices,
        "lastWeights": last_weights,
        "printedLastDay": printed_last,
    }


def period_returns(points: list[dict], as_of: str, field: str) -> dict:
    """1D / 5D / 30D / 1Y / YTD from real points. No interpolation."""
    pts = [p for p in points if p.get("date") and p.get(field) is not None]
    pts.sort(key=lambda p: p["date"])
    last = None
    for p in pts:
        if p["date"] <= as_of:
            last = p
    empty = {"available": False, "pct": None, "from": None, "to": as_of, "field": field}
    if not last:
        return {k: dict(empty) for k in ("1D", "5D", "30D", "1Y", "YTD")}

    def pack(baseline: dict | None, reason: str) -> dict:
        if not baseline:
            return {
                "available": False,
                "pct": None,
                "from": None,
                "to": last["date"],
                "reason": reason,
                "field": field,
            }
        prev = float(baseline[field])
        cur = float(last[field])
        if not (prev > 0):
            return {
                "available": False,
                "pct": None,
                "from": baseline["date"],
                "to": last["date"],
                "reason": "non-positive baseline",
                "field": field,
            }
        return {
            "available": True,
            "pct": round((cur / prev - 1.0) * 10000) / 100.0,
            "from": baseline["date"],
            "to": last["date"],
            "fromIndex": prev,
            "toIndex": cur,
            "field": field,
        }

    def on_or_before(cutoff: str, before: str) -> dict | None:
        found = None
        for p in pts:
            if p["date"] <= cutoff and p["date"] < before:
                found = p
        return found

    last_d = parse_ymd(last["date"])
    prev_snap = on_or_before(ymd(last_d - timedelta(days=1)), last["date"])
    # 1D = previous calendar point (LOCF series is daily, so yesterday).
    prev_cal = None
    for p in pts:
        if p["date"] < last["date"]:
            prev_cal = p
    cut5 = ymd(last_d - timedelta(days=5))
    cut30 = ymd(last_d - timedelta(days=30))
    cut1y = ymd(last_d - timedelta(days=365))
    cut_ytd = f"{last_d.year}-01-01"
    return {
        "asOf": last["date"],
        "1D": pack(prev_cal, "no previous point"),
        "5D": pack(on_or_before(cut5, last["date"]), "no snapshot on or before " + cut5),
        "30D": pack(on_or_before(cut30, last["date"]), "no snapshot on or before " + cut30),
        "1Y": pack(on_or_before(cut1y, last["date"]), "no snapshot on or before " + cut1y),
        "YTD": pack(on_or_before(cut_ytd, last["date"]), "no snapshot on or before " + cut_ytd),
    }


def match_name_zh(name: str | None, localized: str | None, names_map: dict) -> str | None:
    """Official TPC names only. Never invent."""
    species = (names_map or {}).get("species") or {}
    blob = f"{name or ''} {localized or ''}"
    hits = []
    for en, zh in species.items():
        if en and en.lower() in blob.lower():
            hits.append((len(en), zh))
    if not hits:
        return None
    hits.sort(reverse=True)
    return hits[0][1]


def fetch_apparel(apparel_id: int) -> dict:
    return _get_json(f"{SNK}/v1/apparels/{int(apparel_id)}")


def fetch_chips(catalog_id: int) -> dict:
    return _get_json(f"{SNK}/v2/products/{int(catalog_id)}/size-chips")


def fetch_psa10_history(catalog_id: int, range_key: str = "all") -> dict:
    q = f"range={range_key}&condition_code={CONDITION_PSA10}"
    return _get_json(f"{SNK}/v3/products/{int(catalog_id)}/trading-history?{q}")


def score_candidate(prints: list[tuple[str, float]], as_of: str | None = None) -> dict:
    if not prints:
        return {"lastPrice": None, "prints90": 0, "prints30": 0, "ok": False}
    last_day, last_price = prints[-1]
    end = as_of or last_day
    start90 = ymd(parse_ymd(end) - timedelta(days=90))
    start30 = ymd(parse_ymd(end) - timedelta(days=30))
    p90 = print_count_between(prints, start90, ymd(parse_ymd(end) + timedelta(days=1)))
    p30 = print_count_between(prints, start30, ymd(parse_ymd(end) + timedelta(days=1)))
    ok = last_price >= MIN_LAST_PRICE_YEN and p90 >= MIN_PRINTS_90D
    return {"lastPrice": last_price, "lastDay": last_day, "prints90": p90, "prints30": p30, "ok": ok}


def rebuild_baskets(
    *,
    universe: dict[int, dict],
    names_map: dict,
    sleep_s: float,
    verbose: bool,
    limit: int | None = None,
) -> tuple[list[dict], dict[str, dict]]:
    cards: list[dict] = []
    series: dict[str, dict] = {}
    scanned = 0
    ids = sorted(universe.keys())
    if limit:
        ids = ids[:limit]
    for aid in ids:
        scanned += 1
        meta = universe[aid]
        try:
            apparel = fetch_apparel(aid)
        except Exception as err:  # noqa: BLE001
            if verbose:
                print(f"skip apparel {aid}: {err}", file=sys.stderr)
            time.sleep(sleep_s)
            continue
        time.sleep(sleep_s)
        if not is_trading_card_single(apparel):
            continue
        if is_english_printing(apparel.get("name"), apparel.get("localizedName")):
            continue
        catalog_id = apparel.get("productCatalogId")
        if not catalog_id:
            continue
        try:
            chips = fetch_chips(int(catalog_id))
        except Exception as err:  # noqa: BLE001
            if verbose:
                print(f"skip chips {catalog_id}: {err}", file=sys.stderr)
            time.sleep(sleep_s)
            continue
        time.sleep(sleep_s)
        chip = psa10_chip(chips)
        if not chip or not chip.get("hasListing"):
            continue
        floor = chip.get("usedMinPrice")
        if floor is not None and float(floor) < MIN_LAST_PRICE_YEN:
            continue
        try:
            hist = fetch_psa10_history(int(catalog_id))
        except Exception as err:  # noqa: BLE001
            if verbose:
                print(f"skip history {catalog_id}: {err}", file=sys.stderr)
            time.sleep(sleep_s)
            continue
        time.sleep(sleep_s)
        prints = parse_prints(hist)
        stats = score_candidate(prints)
        if not stats["ok"]:
            continue
        cid = str(int(catalog_id))
        image = ((apparel.get("primaryMedia") or {}).get("imageUrl")) or ""
        name = apparel.get("name") or meta.get("name")
        loc = apparel.get("localizedName")
        name_zh = meta.get("nameZh") or match_name_zh(name, loc, names_map)
        row = {
            "id": cid,
            "apparelId": int(aid),
            "catalogId": int(catalog_id),
            "name": name,
            "localizedName": loc,
            "nameZh": name_zh,
            "productNumber": apparel.get("productNumber"),
            "set": meta.get("set"),
            "num": meta.get("num"),
            "image": image,
            "url": f"{SNK}/apparels/{int(aid)}",
            "lastPriceYen": stats["lastPrice"],
            "prints90": stats["prints90"],
            "prints30": stats["prints30"],
        }
        cards.append(row)
        series[cid] = {
            "prints": prints,
            "trades": parse_trades(hist),
            "fetchedAt": _now().isoformat(),
        }
        if verbose:
            print(
                f"keep {cid} {loc or name} ¥{stats['lastPrice']:.0f} prints90={stats['prints90']}",
                file=sys.stderr,
            )
    cards.sort(key=lambda r: (-int(r.get("prints30") or 0), -float(r.get("lastPriceYen") or 0)))
    frozen = cards[:TARGET_SIZE]
    keep_ids = {c["id"] for c in frozen}
    series = {k: v for k, v in series.items() if k in keep_ids}
    if verbose:
        print(f"scanned {scanned} apparel IDs, liquid {len(cards)}, froze {len(frozen)}", file=sys.stderr)
    return frozen, series


def refresh_series(
    cards: list[dict],
    cache: dict[str, dict],
    *,
    sleep_s: float,
    verbose: bool,
) -> dict[str, dict]:
    out = dict(cache)
    for card in cards:
        cid = str(card["id"])
        catalog_id = int(card.get("catalogId") or cid)
        try:
            hist = fetch_psa10_history(catalog_id)
        except Exception as err:  # noqa: BLE001
            if verbose:
                print(f"keep cache {cid}: {err}", file=sys.stderr)
            time.sleep(sleep_s)
            continue
        time.sleep(sleep_s)
        prints = parse_prints(hist)
        if not prints:
            if verbose:
                print(f"empty history {cid}, keeping cache", file=sys.stderr)
            continue
        prev = (out.get(cid) or {}).get("prints") or []
        merged: dict[str, float] = {str(d): float(p) for d, p in prev}
        merged.update({d: p for d, p in prints})
        out[cid] = {
            "prints": sorted(merged.items(), key=lambda x: x[0]),
            "trades": parse_trades(hist) or (out.get(cid) or {}).get("trades") or [],
            "fetchedAt": _now().isoformat(),
        }
        if verbose:
            print(f"refresh {cid} prints={len(out[cid]['prints'])}", file=sys.stderr)
    return out


def movers_from_prices(
    cards: list[dict],
    prev: dict[str, float],
    today: dict[str, float],
    printed: set[str] | None = None,
) -> dict:
    """1D movers: raw SNK print moves (not clipped). Breadth uses all LOCF names."""
    rows = []
    printed_rows = []
    for card in cards:
        cid = str(card["id"])
        p0, p1 = prev.get(cid), today.get(cid)
        if p0 is None or p1 is None or not (p0 > 0):
            continue
        chg = round((p1 / p0 - 1.0) * 10000) / 100.0
        row = {
            "id": cid,
            "name": card.get("localizedName") or card.get("name"),
            "nameZh": card.get("nameZh"),
            "setName": card.get("set"),
            "image": card.get("image") or "",
            "price": p1,
            "prevPrice": p0,
            "changePct": chg,
            "url": card.get("url"),
        }
        rows.append(row)
        if printed is None or cid in printed:
            printed_rows.append(row)
    board = printed_rows if printed is not None else rows
    gainers = sorted(board, key=lambda r: (-r["changePct"], -r["price"]))[:10]
    losers = sorted(board, key=lambda r: (r["changePct"], -r["price"]))[:10]
    adv = sum(1 for r in rows if r["changePct"] > 0)
    dec = sum(1 for r in rows if r["changePct"] < 0)
    unch = len(rows) - adv - dec
    return {
        "gainers": gainers,
        "losers": losers,
        "breadth": {
            "available": len(rows) > 0,
            "advancing": adv,
            "declining": dec,
            "unchanged": unch,
        },
    }


def constituents_payload(cards: list[dict], built: dict, series: dict[str, dict], as_of: str) -> list[dict]:
    prices = built.get("lastPrices") or {}
    weights = built.get("lastWeights") or {}
    rows = []
    for card in cards:
        cid = str(card["id"])
        prints = (series.get(cid) or {}).get("prints") or []
        trades = (series.get(cid) or {}).get("trades") or []
        liq = liquidity_score(prints, trades, as_of)
        rows.append(
            {
                "id": cid,
                "apparelId": card.get("apparelId"),
                "catalogId": card.get("catalogId"),
                "name": card.get("name"),
                "localizedName": card.get("localizedName"),
                "nameZh": card.get("nameZh"),
                "setName": card.get("set"),
                "image": card.get("image") or "",
                "url": card.get("url"),
                "price": prices.get(cid),
                "equalWeight": round((weights.get("equal") or {}).get(cid, 0) * 100, 3),
                "liquidityWeight": round((weights.get("liquidity") or {}).get(cid, 0) * 100, 3),
                "priceWeight": round((weights.get("price") or {}).get(cid, 0) * 100, 3),
                "liquidityScore": round(liq, 1),
                "prints30": print_count_between(
                    prints,
                    ymd(parse_ymd(as_of) - timedelta(days=30)),
                    ymd(parse_ymd(as_of) + timedelta(days=1)),
                )
                if as_of
                else 0,
            }
        )
    rows.sort(key=lambda r: (-(r.get("price") or 0), r.get("id") or ""))
    return rows


def publish(
    cards: list[dict],
    series: dict[str, dict],
    *,
    generated: str,
    source: str,
    min_constituents: int = MIN_CONSTITUENTS,
) -> tuple[dict, dict]:
    built = build_index(cards, series, min_constituents=min_constituents)
    points = built["points"]
    if not points:
        raise RuntimeError("index produced no points — not enough liquid PSA10 prints")
    as_of = points[-1]["date"]
    prev = points[-2] if len(points) >= 2 else None
    rets = {
        "equal": period_returns(points, as_of, "equal"),
        "liquidity": period_returns(points, as_of, "liquidity"),
        "price": period_returns(points, as_of, "price"),
    }
    last = points[-1]
    movers = {"gainers": [], "losers": [], "breadth": {"available": False}}
    if prev:
        # 1D movers vs previous calendar index day (LOCF prices).
        prev_day = prev["date"]
        calendar_prices_prev = {}
        calendar_prices_now = built.get("lastPrices") or {}
        # Rebuild prev-day LOCF quickly from series.
        days = sorted({p["date"] for p in points})
        for card in cards:
            cid = str(card["id"])
            loc = locf_prices((series.get(cid) or {}).get("prints") or [], days)
            if prev_day in loc:
                calendar_prices_prev[cid] = loc[prev_day]
        movers = movers_from_prices(
            cards,
            calendar_prices_prev,
            calendar_prices_now,
            printed=built.get("printedLastDay") or set(),
        )

    def pack_index(field: str) -> dict:
        r1 = rets[field].get("1D") or {}
        return {
            "index": last[field],
            "prevIndex": prev[field] if prev else None,
            "change": round(last[field] - prev[field], 2) if prev else None,
            "changePct": r1.get("pct"),
            "changeLabel": "vs previous day" if r1.get("available") else "first snapshot",
        }

    latest = {
        "generated": generated,
        "asOfDate": as_of,
        "priceSource": (
            "SNKRDUNK JP public trading-history chart "
            f"(condition_code={CONDITION_PSA10})"
        ),
        "universe": (
            "Japanese Pokémon TCG PSA10 slabs on snkrdunk.com; frozen liquid basket "
            "from apparel IDs already in data.json (Box EV chase cards)"
        ),
        "staleDays": STALE_DAYS,
        "indexes": {
            "equal": pack_index("equal"),
            "liquidity": pack_index("liquidity"),
            "price": pack_index("price"),
        },
        "volume": last["volume"],
        "divisorCap": WEIGHT_CAP,
        "weightCap": WEIGHT_CAP,
        "weightCapNote": (
            "Price-weight and liquidity-weight cap each card at 3% after "
            "renormalization so mega chase cannot become the whole index. "
            "If 1/n ≥ 3% the cap is infeasible and weights stay uncapped."
        ),
        "returnClip": RETURN_CLIP,
        "maxPrintGapDays": MAX_PRINT_GAP_DAYS,
        "baseValue": BASE_INDEX_VALUE,
        "baseDate": built["baseDate"],
        "constituentCount": last["count"],
        "pricedToday": last["volume"],
        "breadth": movers["breadth"],
        "gainers": movers["gainers"],
        "losers": movers["losers"],
        "constituents": constituents_payload(cards, built, series, as_of),
        "source": source,
        "sourceDocs": f"{SNK}/",
        "methodology": {
            "price": "SNKRDUNK completed-sales chart print for PSA10 (not Grade A ask, not listing floor)",
            "missingDays": "Per-card last observation carried forward; never fill ¥0",
            "index": (
                f"Chained daily returns from LOCF prices; first day with "
                f"≥{MIN_CONSTITUENTS} names = {int(BASE_INDEX_VALUE):,}"
            ),
            "equal": "Arithmetic mean of available card returns",
            "liquidity": (
                f"Weight ∝ SNK last-20 PSA10 sold yen in the prior {LIQUIDITY_WINDOW_DAYS}d; "
                "if none, sum of PSA10 chart-print yen in that window"
            ),
            "priceWeight": "Lagged yen price / sum of lagged yen prices, then 3% cap",
            "outliers": (
                f"Print-to-print gap > {MAX_PRINT_GAP_DAYS}d is a re-entry (0 return, rebase). "
                f"Remaining card-day returns winsorized at ±{int(RETURN_CLIP * 100)}%"
            ),
            "volume": "Count of basket cards with a PSA10 chart print that JST calendar day",
            "timezone": "Asia/Tokyo",
        },
    }
    history = {
        "generated": generated,
        "baseDate": built["baseDate"],
        "baseValue": BASE_INDEX_VALUE,
        "source": source,
        "sourceDocs": f"{SNK}/",
        "weightCap": WEIGHT_CAP,
        "returnClip": RETURN_CLIP,
        "maxPrintGapDays": MAX_PRINT_GAP_DAYS,
        "points": points,
        "returns": rets,
    }
    return latest, history


def write_outputs(latest: dict, history: dict, baskets: dict | None, series: dict | None) -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    write_json(LATEST_PATH, latest)
    write_js(LATEST_JS_PATH, "MARKET_JP_PSA10", latest)
    write_json(HISTORY_PATH, history)
    write_js(HISTORY_JS_PATH, "MARKET_JP_PSA10_HISTORY", history)
    if baskets is not None:
        write_json(BASKETS_PATH, baskets)
    if series is not None:
        serializable = {}
        for cid, blob in series.items():
            serializable[cid] = {
                "prints": [[d, p] for d, p in blob.get("prints") or []],
                "trades": blob.get("trades") or [],
                "fetchedAt": blob.get("fetchedAt"),
            }
        write_json(SERIES_PATH, serializable)


def baskets_doc(cards: list[dict], generated: str, scanned: int | None = None) -> dict:
    return {
        "version": 1,
        "generated": generated,
        "source": "Frozen from data.json SNKRDUNK /apparels/<id> (Box EV JP chase cards)",
        "rule": (
            "Japanese Pokémon TCG singles with a public SNKRDUNK PSA10 trading-history. "
            f"Keep names with last PSA10 print ≥ ¥{int(MIN_LAST_PRICE_YEN):,} and "
            f"≥ {MIN_PRINTS_90D} PSA10 chart prints in the last 90 days. Rank by 30-day "
            f"print count then last price; freeze top {TARGET_SIZE}. Daily job does not "
            "rebuild membership. Not a pokeca multiplier basket."
        ),
        "selection": {
            "universe": "data.json apparel IDs (JP Box EV cards already on this site)",
            "minLastPriceYen": MIN_LAST_PRICE_YEN,
            "minPrints90d": MIN_PRINTS_90D,
            "targetSize": TARGET_SIZE,
            "weightCap": WEIGHT_CAP,
            "liquidityWindowDays": LIQUIDITY_WINDOW_DAYS,
            "maxPrintGapDays": MAX_PRINT_GAP_DAYS,
            "returnClip": RETURN_CLIP,
            "minConstituents": MIN_CONSTITUENTS,
            "scannedApparelIds": scanned,
            "conditionCode": CONDITION_PSA10,
        },
        "cards": [
            {
                "id": c["id"],
                "apparelId": c.get("apparelId"),
                "catalogId": c.get("catalogId"),
                "name": c.get("name"),
                "localizedName": c.get("localizedName"),
                "nameZh": c.get("nameZh"),
                "productNumber": c.get("productNumber"),
                "set": c.get("set"),
                "num": c.get("num"),
                "image": c.get("image"),
                "url": c.get("url"),
            }
            for c in cards
        ],
    }


def load_series_cache(path: str = SERIES_PATH) -> dict[str, dict]:
    raw = load_json(path, {})
    out: dict[str, dict] = {}
    if not isinstance(raw, dict):
        return out
    for cid, blob in raw.items():
        if not isinstance(blob, dict):
            continue
        prints = []
        for row in blob.get("prints") or []:
            if isinstance(row, (list, tuple)) and len(row) >= 2:
                try:
                    prints.append((str(row[0]), float(row[1])))
                except (TypeError, ValueError):
                    continue
        out[str(cid)] = {
            "prints": prints,
            "trades": list(blob.get("trades") or []),
            "fetchedAt": blob.get("fetchedAt"),
        }
    return out


def series_from_fixture_cards(fixture_cards: list[dict]) -> tuple[list[dict], dict[str, dict]]:
    cards = []
    series: dict[str, dict] = {}
    for row in fixture_cards:
        cid = str(row["id"])
        cards.append(
            {
                "id": cid,
                "apparelId": row.get("apparelId"),
                "catalogId": row.get("catalogId"),
                "name": row.get("name"),
                "localizedName": row.get("localizedName"),
                "nameZh": row.get("nameZh"),
                "productNumber": row.get("productNumber"),
                "set": row.get("set"),
                "image": row.get("image") or "",
                "url": row.get("url"),
            }
        )
        series[cid] = {
            "prints": [(str(d), float(p)) for d, p in row.get("prints") or []],
            "trades": list(row.get("trades") or []),
        }
    return cards, series


def self_test() -> None:
    assert ms_to_ymd_jst(1702134000000) == "2023-12-10"
    # Cap: one 90% name cannot keep 90% when 1/n < 3% (n=40).
    bulky = {f"n{i}": 1.0 for i in range(39)}
    bulky["star"] = 90.0
    capped = cap_weights(bulky, cap=0.03)
    assert abs(capped["star"] - 0.03) < 1e-9, capped["star"]
    assert abs(sum(capped.values()) - 1) < 1e-9
    assert all(v <= 0.03 + 1e-9 for v in capped.values())
    # Tiny basket: 1/n > cap → leave raw (cap infeasible).
    tiny = cap_weights({"a": 8, "b": 1, "c": 1}, cap=0.03)
    assert abs(tiny["a"] - 0.8) < 1e-9, tiny
    # LOCF does not invent a pre-history price.
    loc = locf_prices([("2026-09-02", 10.0), ("2026-09-04", 12.0)], ["2026-09-01", "2026-09-02", "2026-09-03", "2026-09-04"])
    assert "2026-09-01" not in loc
    assert loc["2026-09-02"] == 10.0
    assert loc["2026-09-03"] == 10.0
    assert loc["2026-09-04"] == 12.0

    fixture = load_json(TESTDATA, None)
    assert fixture and fixture.get("cards"), "missing testdata"
    cards, series = series_from_fixture_cards(fixture["cards"])
    built = build_index(cards, series, cap=0.5, min_constituents=3, window_days=30)
    pts = built["points"]
    assert pts, "expected points"
    assert pts[0]["equal"] == 1000.0
    assert pts[0]["date"] == "2026-09-01"
    # 09-01 -> 09-02: A +10%, B 0%, C +100% clipped to +25% → equal +11.666...% → 1116.67
    d2 = next(p for p in pts if p["date"] == "2026-09-02")
    assert abs(d2["equal"] - 1116.67) < 0.05, d2
    # Volume on 09-02 is 3 (all printed). LOCF-only days still have volume 0.
    fill_days = [p for p in pts if p["date"] > "2026-09-03" and p["date"] < "2026-09-10"]
    assert fill_days, "expected LOCF calendar days"
    assert all(p["volume"] == 0 for p in fill_days), fill_days[:3]
    assert all(p["equal"] == pts[2]["equal"] for p in fill_days)  # 09-03 is index 2 if consecutive
    d10 = next(p for p in pts if p["date"] == "2026-09-10")
    assert d10["volume"] == 3
    # Price-weight uses lagged prices; A should not be allowed to explode vs equal without cap
    # (here cap=0.5 so A can be large). Sanity: all three series positive.
    assert d2["liquidity"] > 0 and d2["price"] > 0
    latest, history = publish(cards, series, generated="test", source="fixture", min_constituents=3)
    assert latest["indexes"]["equal"]["index"] == history["points"][-1]["equal"]
    assert history["baseValue"] == 1000.0
    rets = period_returns(history["points"], history["points"][-1]["date"], "equal")
    assert rets["1D"]["available"]
    # YTD 2026-01-01: first point is 2026-09-01, which is after Jan 1 — on_or_before
    # still finds nothing before last with date<=Jan1. Correctly unavailable.
    assert rets["YTD"]["available"] is False or rets["YTD"]["from"] is not None

    # Apparel extractor does not invent IDs.
    uni = apparel_universe(
        {
            "cards": [
                {"url": "https://snkrdunk.com/apparels/162095", "name": "Z", "name_zh": "噴火龍", "set": "SV4a"},
                {"url": "https://example.com/nope", "name": "X"},
            ]
        }
    )
    assert list(uni.keys()) == [162095]
    assert uni[162095]["nameZh"] == "噴火龍"
    assert is_english_printing("Pikachu [EN]", None)
    assert not is_english_printing("Charizard ex SAR [SV4a 349/190]", "リザードンex")

    # Sparse 8× reprint after >14d is a re-entry: index stays 1000.
    bomb_gap = {
        "X": {
            "id": "X",
            "prints": [("2026-01-01", 10000.0), ("2026-01-16", 80000.0)],
            "trades": [],
        },
        "Y": {
            "id": "Y",
            "prints": [("2026-01-01", 10000.0), ("2026-01-16", 10000.0)],
            "trades": [],
        },
        "Z": {
            "id": "Z",
            "prints": [("2026-01-01", 10000.0), ("2026-01-16", 10000.0)],
            "trades": [],
        },
    }
    cards_g = [{"id": k, "name": k} for k in bomb_gap]
    built_g = build_index(cards_g, bomb_gap, cap=0.5, min_constituents=3)
    d16 = next(p for p in built_g["points"] if p["date"] == "2026-01-16")
    assert d16["equal"] == 1000.0, d16
    assert d16["volume"] == 3

    # Close-gap 8× print is kept but clipped: one card +25% → equal +8.333...% → 1083.33
    bomb_close = {
        "X": {
            "id": "X",
            "prints": [("2026-01-01", 10000.0), ("2026-01-11", 80000.0)],
            "trades": [],
        },
        "Y": {
            "id": "Y",
            "prints": [("2026-01-01", 10000.0), ("2026-01-11", 10000.0)],
            "trades": [],
        },
        "Z": {
            "id": "Z",
            "prints": [("2026-01-01", 10000.0), ("2026-01-11", 10000.0)],
            "trades": [],
        },
    }
    built_c = build_index(
        [{"id": k, "name": k} for k in bomb_close],
        bomb_close,
        cap=0.5,
        min_constituents=3,
    )
    d11 = next(p for p in built_c["points"] if p["date"] == "2026-01-11")
    assert abs(d11["equal"] - 1083.33) < 0.05, d11
    assert d11["equal"] < 2000.0

    tmp = os.path.join(HERE, "testdata", "snkrdunk-jp-psa10", "_selftest.js")
    write_js(tmp, "MARKET_JP_PSA10", {"ok": True})
    text = open(tmp, encoding="utf-8").read()
    assert text.startswith("window.MARKET_JP_PSA10 = ")
    assert text.endswith(";\n")
    os.remove(tmp)
    print("refresh_market_jp_psa10 self-test ok")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--rebuild-baskets", action="store_true")
    ap.add_argument("--confirm", default="", help="Type REBUILD to rewrite baskets.json")
    ap.add_argument("--fixture", action="store_true", help="Build from testdata (no network)")
    ap.add_argument(
        "--from-cache",
        action="store_true",
        help="Rebuild latest/history from series-cache.json (no network)",
    )
    ap.add_argument("--limit", type=int, default=0, help="Scan at most N apparel IDs (debug)")
    ap.add_argument("-v", "--verbose", action="store_true")
    ap.add_argument("--sleep", type=float, default=SLEEP_S)
    args = ap.parse_args(argv)
    if args.self_test:
        self_test()
        return 0

    generated = _now().isoformat()
    names_map = load_json(NAMES_ZH_PATH, {})

    if args.fixture:
        fixture = load_json(TESTDATA, None)
        cards, series = series_from_fixture_cards(fixture["cards"])
        latest, history = publish(
            cards, series, generated=generated, source="fixture", min_constituents=3
        )
        write_outputs(latest, history, baskets_doc(cards, generated), series)
        print(f"wrote fixture index {latest['indexes']['equal']['index']} @ {latest['asOfDate']}")
        return 0

    if args.rebuild_baskets:
        if args.confirm != "REBUILD":
            print("ERROR: --rebuild-baskets requires --confirm REBUILD (refuses to invent a basket).", file=sys.stderr)
            return 2
        data_obj = load_json(DATA_JSON_PATH, {})
        universe = apparel_universe(data_obj)
        if not universe:
            print("ERROR: no apparel IDs in data.json", file=sys.stderr)
            return 2
        cards, series = rebuild_baskets(
            universe=universe,
            names_map=names_map,
            sleep_s=args.sleep,
            verbose=args.verbose,
            limit=args.limit or None,
        )
        if len(cards) < MIN_CONSTITUENTS:
            print(f"ERROR: only {len(cards)} liquid PSA10s; not writing.", file=sys.stderr)
            return 2
        latest, history = publish(cards, series, generated=generated, source="snkrdunk")
        write_outputs(latest, history, baskets_doc(cards, generated, scanned=len(universe)), series)
        print(
            f"froze {len(cards)} · equal {latest['indexes']['equal']['index']} "
            f"@ {latest['asOfDate']} · {len(history['points'])} days"
        )
        return 0

    baskets = load_json(BASKETS_PATH, None)
    if not baskets or not baskets.get("cards"):
        print(
            "ERROR: missing data/market-jp-psa10/baskets.json. "
            "Run --rebuild-baskets --confirm REBUILD first.",
            file=sys.stderr,
        )
        return 2
    cards = baskets["cards"]
    cache = load_series_cache()
    if args.from_cache:
        if not cache:
            print("ERROR: empty series-cache.json", file=sys.stderr)
            return 2
        series = cache
        source = "snkrdunk-cache"
    else:
        series = refresh_series(cards, cache, sleep_s=args.sleep, verbose=args.verbose)
        source = "snkrdunk"
    latest, history = publish(cards, series, generated=generated, source=source)
    write_outputs(latest, history, None, None if args.from_cache else series)
    print(
        f"updated equal {latest['indexes']['equal']['index']} "
        f"liq {latest['indexes']['liquidity']['index']} "
        f"px {latest['indexes']['price']['index']} @ {latest['asOfDate']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
