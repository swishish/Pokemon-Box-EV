#!/usr/bin/env python3
"""English Base-era indexes from PriceCharting only.

Four fixed baskets, ungraded (`used`) guide prices:

  base-1st         Base Set 1st Edition
  base-shadowless  Base Set Shadowless
  base-unlimited   Base Set Unlimited
  base-set-2       Base Set 2

Slugs come from data/base-psa-pop/catalog.json (already HTTP-listed on
PriceCharting). Numeric product ids and images come from the public console
JSON (`id`, `imageUri`). History comes from `VGPC.chart_data` on each game
page. The `used` series is the ungraded column. Values are cents. Zero means
"no print" and is dropped — never stored as a price.

PriceCharting's public chart is one point per month (about the 1st UTC).
There is no weekly series on that blob, so this script does not invent one.

  python3 tools/refresh_market_vintage_pc.py --self-test
  python3 tools/refresh_market_vintage_pc.py --sample
  python3 tools/refresh_market_vintage_pc.py --root .

Does not read SNKRDUNK. Does not touch data/market-vintage (TCGCSV / TickerMint)
or data/market-jp-psa10-bench.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))

import build_base_psa_pop_catalog as pc_catalog  # noqa: E402
import refresh_market_en as en  # noqa: E402

DATA_DIR = ROOT / "data" / "market-vintage-pc"
CATALOG_PATH = ROOT / "data" / "base-psa-pop" / "catalog.json"
CACHE_DIR = DATA_DIR / "cache"
GAME_PREFIX = "https://www.pricecharting.com/game/"
UA = pc_catalog.UA
ANCHOR = "2023-01-01"
BASE = 1000.0
# Half of the ~32-card chase list. A thinner month is skipped, not zero-filled.
MIN_PRICED = 15
MIN_OVERLAP = 12
# Charizard 1st ungraded is thousands of dollars. Below this, cents were misread.
CHARIZARD_MIN_CENTS = 100_000

# Same chase cut as data/market-vintage baskets (holo + rare, no energy).
# #8 Machamp is included only when that printing has a catalog slug.
CHASE_NUMBERS = set(list(range(1, 17)) + list(range(17, 23)) + list(range(70, 80)))

INDEXES = (
    {
        "id": "base-1st",
        "print": "1st",
        "name": "Base 1st Edition",
        "nameZh": "Base 第一版",
        "printing": "1st Edition",
        "setName": "Base Set",
        "denom": 102,
        "hash": "1st",
    },
    {
        "id": "base-shadowless",
        "print": "shadowless",
        "name": "Base Shadowless",
        "nameZh": "Base 無陰影",
        "printing": "Shadowless",
        "setName": "Base Set",
        "denom": 102,
        "hash": "shadowless",
    },
    {
        "id": "base-unlimited",
        "print": "unlimited",
        "name": "Base Unlimited",
        "nameZh": "Base 無限版",
        "printing": "Unlimited",
        "setName": "Base Set",
        "denom": 102,
        "hash": "unlimited",
    },
    {
        "id": "base-set-2",
        "print": "base2",
        "name": "Base Set 2",
        "nameZh": "Base Set 2",
        "printing": "Base Set 2",
        "setName": "Base Set 2",
        "denom": 130,
        "hash": "base2",
    },
)

CHART_RE = re.compile(r"VGPC\.chart_data\s*=\s*(\{.*?\})\s*;", re.S)
PRODUCT_RE = re.compile(r'data-product-id=["\'](\d+)["\']')
RARITY_LABEL = {"holo": "Holo Rare", "rare": "Rare"}


class PcError(RuntimeError):
    pass


def game_url(slug: str) -> str:
    parts = str(slug).lstrip("/").split("/")
    return GAME_PREFIX + "/".join(urllib.request.quote(part, safe="-._~'") for part in parts)


def number_label(n: int, denom: int) -> str:
    return f"{int(n):03d}/{int(denom)}"


def chase_cards(catalog: dict) -> list[dict]:
    rows = []
    for card in catalog.get("cards") or []:
        try:
            number = int(card.get("number_base"))
        except (TypeError, ValueError):
            continue
        if number not in CHASE_NUMBERS:
            continue
        if card.get("rarity") not in {"holo", "rare"}:
            continue
        rows.append(card)
    rows.sort(key=lambda c: (int(c["number_base"]), c.get("id") or ""))
    return rows


def build_baskets(catalog: dict, products_by_slug: dict[str, dict] | None = None) -> dict:
    """Pair chase cards with catalog slugs. Product ids only when console JSON has them."""
    products_by_slug = products_by_slug or {}
    gaps = []
    indexes: dict[str, dict] = {}
    for spec in INDEXES:
        constituents = []
        for card in chase_cards(catalog):
            slug = (card.get("prints") or {}).get(spec["print"])
            number = card.get("number_base2") if spec["print"] == "base2" else card.get("number_base")
            label_number = number_label(number, spec["denom"]) if number else ""
            if not slug:
                gaps.append(
                    {
                        "index": spec["id"],
                        "name": card.get("name"),
                        "number": label_number or number_label(card["number_base"], 102),
                        "reason": f"No PriceCharting slug for {spec['printing']} in base-psa-pop catalog",
                    }
                )
                continue
            product = products_by_slug.get(slug) or {}
            pid = str(product.get("id") or "").strip()
            image = product.get("imageUri") or ""
            if image and not str(image).startswith("http"):
                image = ""
            row = {
                "cardId": card.get("id"),
                "name": card.get("name"),
                "number": label_number,
                "rarity": RARITY_LABEL.get(card.get("rarity"), card.get("rarity") or ""),
                "slug": slug,
                "url": game_url(slug),
                "image": image or None,
            }
            if pid:
                row["id"] = pid
            constituents.append(row)
        if len(constituents) < MIN_PRICED:
            raise PcError(
                f"{spec['id']} matched {len(constituents)} slugs, need at least {MIN_PRICED}"
            )
        indexes[spec["id"]] = {
            "id": spec["id"],
            "name": spec["name"],
            "nameZh": spec["nameZh"],
            "printing": spec["printing"],
            "setName": spec["setName"],
            "hash": spec["hash"],
            "print": spec["print"],
            "universe": (
                f"English {spec['setName']} {spec['printing']} raw / ungraded "
                "(PriceCharting used column)"
            ),
            "pcColumn": "used",
            "constituentCount": len(constituents),
            "constituents": constituents,
        }
    return {
        "version": 1,
        "source": "PriceCharting public console JSON + game-page VGPC.chart_data",
        "priceColumn": "used",
        "priceColumnMeaning": "Ungraded / loose raw guide price. Not PSA 10, not SNKRDUNK.",
        "rule": (
            "Fixed chase baskets: English Base Set and Base Set 2, Holo Rare + Rare "
            "(catalog numbers 1–22 and 70–79, the same cut as market-vintage). "
            "Machamp #8 is included only when that printing has a catalog slug. "
            "Energy (including Double Colorless #96), commons, uncommons, sealed, "
            "and error variants are omitted. Slugs are copied from "
            "data/base-psa-pop/catalog.json. Product ids are copied from console JSON."
        ),
        "history": (
            "Monthly points from PriceCharting VGPC.chart_data.used (cents → USD). "
            f"A month needs at least {MIN_PRICED} positive prints. "
            f"Rebase: {ANCHOR} (or the first later thick month) = {int(BASE)}. "
            "No weekly interpolation. No SNKRDUNK."
        ),
        "gaps": gaps,
        "indexOrder": [spec["id"] for spec in INDEXES],
        "indexes": indexes,
    }


def parse_chart_html(html: str) -> tuple[str | None, dict[str, list[tuple[str, int]]]]:
    """Return (product id, {column: [(YYYY-MM-DD, cents), ...]}). Zeros dropped."""
    if not html:
        raise PcError("empty PriceCharting HTML")
    pid_m = PRODUCT_RE.search(html)
    chart_m = CHART_RE.search(html)
    if not chart_m:
        raise PcError("VGPC.chart_data missing")
    try:
        blob = json.loads(chart_m.group(1))
    except json.JSONDecodeError as err:
        raise PcError(f"chart_data is not JSON: {err}") from err
    if not isinstance(blob, dict):
        raise PcError("chart_data is not an object")
    series: dict[str, list[tuple[str, int]]] = {}
    for column, points in blob.items():
        if not isinstance(points, list):
            continue
        by_date: dict[str, int] = {}
        for item in points:
            if not isinstance(item, (list, tuple)) or len(item) < 2:
                continue
            try:
                ms = int(item[0])
                cents = int(item[1])
            except (TypeError, ValueError):
                continue
            if cents <= 0 or ms <= 0:
                continue
            day = datetime.fromtimestamp(ms / 1000, tz=timezone.utc).date().isoformat()
            by_date[day] = cents
        series[str(column)] = sorted(by_date.items())
    return (pid_m.group(1) if pid_m else None), series


def usd_from_cents(cents: int) -> float:
    return round(int(cents) / 100.0, 2)


def step_month(
    prices: dict[str, float],
    prev_prices: dict[str, float] | None,
    prev_index: float | None,
) -> dict | None:
    """Price-weighted step. Overlap only — a missing card is not priced at zero."""
    today = {pid: pr for pid, pr in prices.items() if pr and pr > 0}
    if len(today) < MIN_PRICED:
        return None
    sum_today = sum(today.values())
    if not prev_prices or not prev_index or prev_index <= 0:
        return {
            "index": BASE,
            "divisor": sum_today / BASE,
            "ids": sorted(today),
            "sum": sum_today,
            "count": len(today),
            "overlap": len(today),
        }
    shared = [pid for pid in prev_prices if pid in today and prev_prices[pid] > 0]
    if len(shared) < MIN_OVERLAP:
        return None
    sum_prev = sum(prev_prices[pid] for pid in shared)
    sum_now = sum(today[pid] for pid in shared)
    if sum_prev <= 0 or sum_now <= 0:
        return None
    index = prev_index * (sum_now / sum_prev)
    return {
        "index": index,
        "divisor": sum_today / index,
        "ids": sorted(today),
        "sum": sum_today,
        "count": len(today),
        "overlap": len(shared),
    }


def chain_index(month_prices: dict[str, dict[str, float]]) -> dict:
    """month_prices: date → {productId: usd}. Dates before and after ANCHOR are kept."""
    dates = sorted(month_prices)
    raw_points = []
    prev_prices = None
    prev_index = None
    skipped = []
    for day in dates:
        step = step_month(month_prices[day], prev_prices, prev_index)
        if step is None:
            skipped.append(day)
            continue
        raw_points.append(
            {
                "date": day,
                "index": step["index"],
                "totalValue": round(step["sum"], 2),
                "count": step["count"],
                "overlap": step["overlap"],
                "divisor": step["divisor"],
                "prices": {pid: round(pr, 2) for pid, pr in sorted(month_prices[day].items()) if pr and pr > 0},
            }
        )
        prev_prices = month_prices[day]
        prev_index = step["index"]
    anchor = next((p for p in raw_points if p["date"] >= ANCHOR), None)
    if anchor is None:
        raise PcError(f"no thick month on or after {ANCHOR}")
    if anchor["index"] <= 0:
        raise PcError("anchor index is not positive")
    scale = BASE / anchor["index"]
    points = []
    for p in raw_points:
        points.append(
            {
                "date": p["date"],
                "index": round(p["index"] * scale, 2),
                "totalValue": p["totalValue"],
                "count": p["count"],
                "divisor": round(p["divisor"] / scale, 8),
            }
        )
    anchor_out = next(p for p in points if p["date"] == anchor["date"])
    if abs(anchor_out["index"] - BASE) > 0.02:
        raise PcError(f"rebase failed: {anchor_out}")
    return {
        "points": points,
        "pricePoints": [
            {"date": p["date"], "prices": raw["prices"]}
            for p, raw in zip(points, raw_points)
        ],
        "anchorDate": anchor["date"],
        "skipped": skipped,
        "firstDate": points[0]["date"],
        "lastDate": points[-1]["date"],
    }


def _parse_day(iso: str) -> date | None:
    try:
        return datetime.strptime(iso, "%Y-%m-%d").date()
    except ValueError:
        return None


def period_returns(points: list[dict]) -> dict:
    """1D/5D stay unavailable unless a real point sits inside that window.

    The chart is monthly, so a naive 'last point on or before T-5 days' would
    label a one-month move as 5D. 30D accepts the previous month (within 45 days).
    """
    empty = {"available": False, "pct": None, "from": None, "to": None, "reason": "no snapshots"}
    if not points:
        return {"1D": empty, "5D": empty, "30D": empty, "1Y": empty, "YTD": empty, "ALL": empty}
    last = points[-1]
    last_d = _parse_day(last["date"])

    def pack(baseline: dict | None, reason: str) -> dict:
        if baseline is None or not last_d:
            return {
                "available": False,
                "pct": None,
                "from": None,
                "to": last["date"],
                "reason": reason,
            }
        prev = float(baseline["index"])
        cur = float(last["index"])
        if prev <= 0:
            return {
                "available": False,
                "pct": None,
                "from": baseline["date"],
                "to": last["date"],
                "reason": "non-positive baseline",
            }
        return {
            "available": True,
            "pct": round((cur / prev - 1) * 100, 2),
            "from": baseline["date"],
            "to": last["date"],
            "fromIndex": round(prev, 2),
            "toIndex": round(cur, 2),
            "reason": None,
        }

    def on_or_before(cutoff: date, earliest: date | None) -> dict | None:
        found = None
        for p in points:
            pd = _parse_day(p["date"])
            if pd is None or pd >= last_d:
                continue
            if pd <= cutoff and (earliest is None or pd >= earliest):
                found = p
        return found

    prev = points[-2] if len(points) >= 2 else None
    out = {
        "asOf": last["date"],
        "1D": pack(
            on_or_before(last_d - timedelta(days=1), last_d - timedelta(days=3)) if last_d else None,
            "monthly series has no 1-day point",
        ),
        "5D": pack(
            on_or_before(last_d - timedelta(days=5), last_d - timedelta(days=8)) if last_d else None,
            "monthly series has no 5-day point",
        ),
        "30D": pack(
            on_or_before(last_d - timedelta(days=28), last_d - timedelta(days=45)) if last_d else None,
            "no prior month inside 45 days",
        ),
        "1Y": pack(
            on_or_before(last_d - timedelta(days=365), last_d - timedelta(days=400)) if last_d else None,
            "no point near one year earlier",
        ),
        "YTD": pack(
            on_or_before(date(last_d.year, 1, 1), date(last_d.year - 1, 12, 1)) if last_d else None,
            "no December/January point for YTD",
        ),
        "ALL": pack(points[0] if len(points) >= 2 else None, "need two points"),
    }
    if prev and out["30D"]["available"] is False:
        # Keep the key even when the previous print is slightly outside 45 days.
        pass
    return out


def movers_from_prices(constituents: list[dict], prev: dict[str, float], cur: dict[str, float]) -> dict:
    by_id = {str(c.get("id")): c for c in constituents if c.get("id")}
    rows = []
    adv = dec = unch = 0
    for pid, price in cur.items():
        old = prev.get(pid)
        if not old or old <= 0 or not price or price <= 0:
            continue
        pct = round((price / old - 1) * 100, 2)
        meta = by_id.get(pid) or {}
        if pct > 0:
            adv += 1
        elif pct < 0:
            dec += 1
        else:
            unch += 1
        rows.append(
            {
                "id": pid,
                "name": meta.get("name") or pid,
                "number": meta.get("number") or "",
                "image": meta.get("image"),
                "url": meta.get("url"),
                "price": round(price, 2),
                "prevPrice": round(old, 2),
                "changePct": pct,
            }
        )
    rows.sort(key=lambda r: r["changePct"], reverse=True)
    return {
        "breadth": {
            "available": bool(rows),
            "advancing": adv,
            "declining": dec,
            "unchanged": unch,
            "label": "vs previous PriceCharting month",
        },
        "gainers": rows[:10],
        "losers": list(reversed(rows[-10:])) if rows else [],
    }


def compose_latest_index(spec: dict, chain: dict, constituents: list[dict]) -> dict:
    points = chain["points"]
    last = points[-1]
    prev = points[-2] if len(points) >= 2 else None
    price_points = chain["pricePoints"]
    cur_prices = price_points[-1]["prices"] if price_points else {}
    prev_prices = price_points[-2]["prices"] if len(price_points) >= 2 else {}
    enriched = []
    for row in constituents:
        pid = str(row.get("id") or "")
        price = cur_prices.get(pid)
        old = prev_prices.get(pid)
        item = dict(row)
        item["price"] = round(price, 2) if price else None
        item["prevPrice"] = round(old, 2) if old else None
        if price and old and old > 0:
            item["changePct"] = round((price / old - 1) * 100, 2)
        else:
            item["changePct"] = None
        item["pcColumn"] = "used"
        enriched.append(item)
    enriched.sort(key=lambda r: (-(r["price"] or 0), r.get("number") or ""))
    top = []
    for rank, row in enumerate(enriched, start=1):
        if row.get("price") is None:
            continue
        top.append(
            {
                "rank": rank,
                "id": row.get("id"),
                "name": row.get("name"),
                "number": row.get("number"),
                "image": row.get("image"),
                "url": row.get("url"),
                "price": row.get("price"),
                "rarity": row.get("rarity"),
            }
        )
        if len(top) >= 10:
            break
    moved = movers_from_prices(constituents, prev_prices, cur_prices)
    has_prev = prev is not None
    index_value = last["index"]
    prev_index = prev["index"] if prev else None
    return {
        "id": spec["id"],
        "name": spec["name"],
        "nameZh": spec["nameZh"],
        "printing": spec["printing"],
        "setName": spec["setName"],
        "hash": spec["hash"],
        "asOfDate": last["date"],
        "universe": spec["universe"],
        "priceSource": "PriceCharting",
        "pcColumn": "used",
        "index": index_value,
        "prevIndex": prev_index,
        "change": round(index_value - prev_index, 2) if has_prev else None,
        "changePct": round((index_value / prev_index - 1) * 100, 2) if has_prev and prev_index else None,
        "changeLabel": "vs previous PriceCharting month" if has_prev else "first month",
        "divisor": last["divisor"],
        "baseValue": BASE,
        "baseDate": chain["anchorDate"],
        "firstDate": chain["firstDate"],
        "listedCount": len(constituents),
        "constituentCount": sum(1 for r in enriched if r.get("price")),
        "listedUnpriced": [
            {"id": r.get("id"), "name": r.get("name"), "number": r.get("number"), "slug": r.get("slug")}
            for r in enriched
            if not r.get("price")
        ],
        "totalValue": last["totalValue"],
        "pricedToday": last["count"],
        "breadth": moved["breadth"],
        "gainers": moved["gainers"],
        "losers": moved["losers"],
        "top": top,
        "basket": enriched,
        "constituents": [r for r in enriched if r.get("price")],
    }


def load_catalog(path: Path | None = None) -> dict:
    src = path or CATALOG_PATH
    return json.loads(src.read_text(encoding="utf-8"))


def index_products(products: list[dict]) -> dict[str, dict]:
    out = {}
    for product in products:
        console = product.get("consoleUri") or ""
        uri = product.get("productUri") or ""
        if not console or not uri:
            continue
        out[f"{console}/{uri}"] = product
    return out


def fetch_bytes(url: str, *, timeout: int = 45) -> str:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": UA,
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "en-US,en;q=0.8",
        },
    )
    last: Exception | None = None
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                if resp.status != 200:
                    raise PcError(f"HTTP {resp.status} for {url}")
                return resp.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as err:
            # 400 is a bad request (for example Accept: application/json on a game page).
            # Retrying it will not succeed.
            if err.code and 400 <= err.code < 500 and err.code != 429:
                raise PcError(f"HTTP {err.code} for {url}") from err
            last = err
            if attempt < 3:
                time.sleep(1.5 * (attempt + 1))
        except (urllib.error.URLError, TimeoutError, PcError) as err:
            last = err
            if attempt < 3:
                time.sleep(1.5 * (attempt + 1))
    raise PcError(f"fetch failed for {url}: {last}")


def cache_path(pid: str) -> Path:
    return CACHE_DIR / f"{pid}.json"


def load_cached_series(pid: str) -> dict | None:
    path = cache_path(pid)
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def save_cached_series(pid: str, payload: dict) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_path(pid).write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


def series_from_html(slug: str, html: str, *, expect_id: str | None) -> dict:
    pid, series = parse_chart_html(html)
    if expect_id and pid and pid != expect_id:
        raise PcError(f"{slug}: page product {pid} != console id {expect_id}")
    used = series.get("used") or []
    if not used:
        raise PcError(f"{slug}: no positive used points")
    resolved = pid or expect_id
    if not resolved:
        raise PcError(f"{slug}: no product id on page or console")
    return {
        "id": resolved,
        "slug": slug,
        "column": "used",
        "points": [{"date": day, "cents": cents} for day, cents in used],
        "first": used[0][0],
        "last": used[-1][0],
        "fetched": datetime.now(timezone.utc).isoformat(),
    }


def gather_series(baskets: dict, *, sleep_s: float, use_cache: bool, verbose: bool) -> dict[str, dict]:
    """Fetch each unique product once. Keyed by product id."""
    wanted: list[tuple[str, str]] = []
    seen = set()
    for spec in baskets["indexes"].values():
        for row in spec["constituents"]:
            pid = str(row.get("id") or "")
            slug = row.get("slug")
            if not pid or not slug or pid in seen:
                continue
            seen.add(pid)
            wanted.append((pid, slug))
    out: dict[str, dict] = {}
    failures: list[str] = []
    for i, (pid, slug) in enumerate(wanted, start=1):
        if use_cache:
            cached = load_cached_series(pid)
            if cached and cached.get("points") and cached.get("slug") == slug:
                out[pid] = cached
                if verbose:
                    print(f"  cache {i}/{len(wanted)} {slug}", flush=True)
                continue
        if verbose:
            print(f"  fetch {i}/{len(wanted)} {slug}", flush=True)
        try:
            html = fetch_bytes(game_url(slug))
            parsed = series_from_html(slug, html, expect_id=pid)
        except PcError as err:
            failures.append(f"{slug}: {err}")
            print(f"  skip {slug}: {err}", file=sys.stderr)
            continue
        save_cached_series(pid, parsed)
        out[pid] = parsed
        if sleep_s:
            time.sleep(sleep_s)
    if failures:
        print(f"chart fetch gaps: {len(failures)}", file=sys.stderr)
        for line in failures:
            print(f"  {line}", file=sys.stderr)
    if len(out) < len(wanted) * 0.8:
        raise PcError(f"only {len(out)}/{len(wanted)} charts parsed — refusing to write a thin index")
    return out


def month_maps_for_index(spec: dict, series_by_id: dict[str, dict]) -> tuple[dict[str, dict[str, float]], list[dict]]:
    missing = []
    per_card: dict[str, dict[str, float]] = {}
    for row in spec["constituents"]:
        pid = str(row.get("id") or "")
        blob = series_by_id.get(pid)
        if not pid or not blob:
            missing.append({"name": row.get("name"), "number": row.get("number"), "slug": row.get("slug"), "reason": "no chart"})
            continue
        prices = {}
        for point in blob.get("points") or []:
            cents = int(point["cents"])
            if cents <= 0:
                continue
            prices[point["date"]] = usd_from_cents(cents)
        if not prices:
            missing.append({"name": row.get("name"), "number": row.get("number"), "slug": row.get("slug"), "reason": "no positive used price"})
            continue
        per_card[pid] = prices
    months: dict[str, dict[str, float]] = {}
    for pid, prices in per_card.items():
        for day, usd in prices.items():
            months.setdefault(day, {})[pid] = usd
    return months, missing


def assert_cents_scale(series_by_id: dict[str, dict], baskets: dict) -> None:
    """Charizard 1st must look like cents. Refuse the whole write if not."""
    spec = baskets["indexes"]["base-1st"]
    zard = next((r for r in spec["constituents"] if r.get("cardId") == "charizard"), None)
    if not zard or not zard.get("id"):
        raise PcError("Charizard 1st is missing a PriceCharting product id")
    blob = series_by_id.get(str(zard["id"]))
    if not blob or not blob.get("points"):
        raise PcError("Charizard 1st has no used series — refusing to invent a scale")
    peak = max(int(p["cents"]) for p in blob["points"])
    if peak < CHARIZARD_MIN_CENTS:
        raise PcError(
            f"Charizard 1st peak used price is {peak} cents (${peak/100:.2f}). "
            "Expected thousands of dollars. Refusing to write in case the unit changed."
        )


def build_payloads(baskets: dict, series_by_id: dict[str, dict]) -> tuple[dict, dict, dict]:
    assert_cents_scale(series_by_id, baskets)
    now = datetime.now(timezone.utc).isoformat()
    history_indexes = {}
    latest_indexes = {}
    price_indexes = {}
    coverage = {}
    for iid in baskets["indexOrder"]:
        spec = baskets["indexes"][iid]
        months, missing = month_maps_for_index(spec, series_by_id)
        chain = chain_index(months)
        history_indexes[iid] = {
            "generated": now,
            "baseValue": BASE,
            "baseDate": chain["anchorDate"],
            "firstDate": chain["firstDate"],
            "lastDate": chain["lastDate"],
            "source": "pricecharting",
            "pcColumn": "used",
            "points": chain["points"],
            "skippedMonths": chain["skipped"],
            "returns": period_returns(chain["points"]),
        }
        latest_indexes[iid] = compose_latest_index(spec, chain, spec["constituents"])
        price_indexes[iid] = {"generated": now, "points": chain["pricePoints"]}
        priced_from = min((p["date"] for p in chain["points"]), default=None)
        coverage[iid] = {
            "cards": spec["constituentCount"],
            "withSeries": spec["constituentCount"] - len(missing),
            "missing": missing,
            "firstDate": chain["firstDate"],
            "anchorDate": chain["anchorDate"],
            "lastDate": chain["lastDate"],
            "points": len(chain["points"]),
            "reaches2023": chain["firstDate"] <= ANCHOR,
        }
        if priced_from and priced_from > ANCHOR:
            coverage[iid]["reaches2023"] = False
    history = {
        "generated": now,
        "baseValue": BASE,
        "anchorRule": f"{ANCHOR} or the first later month with >= {MIN_PRICED} priced cards = {int(BASE)}",
        "cadence": "monthly",
        "cadenceNote": (
            "PriceCharting public VGPC.chart_data is one ungraded (used) point per month. "
            "Weekly points are not published on that chart and are not interpolated."
        ),
        "priceSource": "pricecharting",
        "pcColumn": "used",
        "backfill": {
            "source": "pricecharting",
            "script": "tools/refresh_market_vintage_pc.py",
            "column": "used",
            "anchor": ANCHOR,
            "minPriced": MIN_PRICED,
            "minOverlap": MIN_OVERLAP,
        },
        "indexOrder": baskets["indexOrder"],
        "indexes": history_indexes,
        "coverage": coverage,
    }
    latest = {
        "generated": now,
        "asOfDate": max(block["asOfDate"] for block in latest_indexes.values()),
        "priceSource": "PriceCharting ungraded (used) — not SNKRDUNK, not TCGPlayer",
        "universe": "English Base Set 1st Edition, Shadowless, Unlimited, and Base Set 2 raw singles",
        "baseValue": BASE,
        "anchorRule": history["anchorRule"],
        "cadence": "monthly",
        "cadenceNote": history["cadenceNote"],
        "basketRule": baskets.get("rule"),
        "gaps": baskets.get("gaps") or [],
        "indexOrder": baskets["indexOrder"],
        "indexes": latest_indexes,
        "coverage": coverage,
    }
    price_history = {
        "generated": now,
        "source": "pricecharting",
        "pcColumn": "used",
        "note": "Positive ungraded USD only. Missing months are omitted.",
        "indexOrder": baskets["indexOrder"],
        "indexes": price_indexes,
    }
    return latest, history, price_history


def write_outputs(root: Path, baskets: dict, latest: dict, history: dict, price_history: dict) -> None:
    data = root / "data" / "market-vintage-pc"
    data.mkdir(parents=True, exist_ok=True)
    blobs = {
        "baskets.json": baskets,
        "latest.json": latest,
        "history.json": history,
        "price-history.json": price_history,
    }
    for name, obj in blobs.items():
        (data / name).write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    en._write_js(str(data / "latest.js"), "MARKET_VINTAGE_PC", latest)
    en._write_js(str(data / "history.js"), "MARKET_VINTAGE_PC_HISTORY", history)


def attach_product_ids(baskets: dict, verbose: bool) -> None:
    if verbose:
        print("fetch PriceCharting consoles", flush=True)
    products = index_products(pc_catalog.fetch_console("pokemon-base-set"))
    products.update(index_products(pc_catalog.fetch_console("pokemon-base-set-2")))
    missing_ids = []
    for spec in baskets["indexes"].values():
        for row in spec["constituents"]:
            product = products.get(row["slug"])
            if not product or not product.get("id"):
                missing_ids.append(row["slug"])
                continue
            row["id"] = str(product["id"])
            image = product.get("imageUri") or ""
            row["image"] = image if str(image).startswith("http") else None
    if missing_ids:
        raise PcError("console JSON has no product id for: " + ", ".join(missing_ids[:12]))


def run_refresh(root: Path, *, sleep_s: float, use_cache: bool, verbose: bool) -> dict:
    catalog = load_catalog(root / "data" / "base-psa-pop" / "catalog.json")
    baskets = build_baskets(catalog)
    baskets["generated"] = datetime.now(timezone.utc).isoformat()
    need_ids = any(not row.get("id") for spec in baskets["indexes"].values() for row in spec["constituents"])
    if need_ids or not use_cache:
        attach_product_ids(baskets, verbose)
    series = gather_series(baskets, sleep_s=sleep_s, use_cache=use_cache, verbose=verbose)
    latest, history, price_history = build_payloads(baskets, series)
    for iid, cov in history["coverage"].items():
        if cov["firstDate"] > ANCHOR:
            print(
                f"NOTE {iid} starts {cov['firstDate']} (after {ANCHOR}); "
                f"points={cov['points']} missing={len(cov['missing'])}",
                file=sys.stderr,
            )
        elif verbose:
            print(
                f"{iid}: {cov['firstDate']} → {cov['lastDate']} "
                f"anchor {cov['anchorDate']} points {cov['points']}",
                flush=True,
            )
    write_outputs(root, baskets, latest, history, price_history)
    return history["coverage"]


def run_sample(verbose: bool) -> None:
    """Fetch one liquid card per printing and print the used-series span. Writes nothing."""
    catalog = load_catalog()
    baskets = build_baskets(catalog)
    probes = []
    for spec in INDEXES:
        rows = baskets["indexes"][spec["id"]]["constituents"]
        row = next((r for r in rows if r.get("cardId") == "charizard"), None) or rows[0]
        probes.append((spec["id"], row))
    print("sample only — not writing JSON", flush=True)
    for iid, row in probes:
        html = fetch_bytes(game_url(row["slug"]))
        pid, series = parse_chart_html(html)
        used = series.get("used") or []
        first = used[0][0] if used else None
        last = used[-1][0] if used else None
        on_anchor = any(day == ANCHOR for day, _ in used)
        peak = max((cents for _, cents in used), default=0)
        print(
            f"{iid} {row['slug']} id={pid} points={len(used)} "
            f"{first} → {last} has_{ANCHOR}={on_anchor} peak_cents={peak}",
            flush=True,
        )
        if verbose and used:
            print(f"  first usd={usd_from_cents(used[0][1])} last usd={usd_from_cents(used[-1][1])}", flush=True)


def self_test() -> None:
    html = """
    <div data-product-id="111"></div>
    <script>
    VGPC.chart_data = {"used":[[1672556400000,10000],[1675234800000,0],[1677654000000,11000]],
      "manualonly":[[1672556400000,999]]};
    </script>
    """
    pid, series = parse_chart_html(html)
    assert pid == "111"
    assert series["used"] == [("2023-01-01", 10000), ("2023-03-01", 11000)], series["used"]
    assert usd_from_cents(320380) == 3203.80

    # Two cards, Jan anchor = 1000, Feb overlap return, March card B missing.
    months = {
        "2022-12-01": {"a": 100.0, "b": 50.0},
        "2023-01-01": {"a": 80.0, "b": 40.0},
        "2023-02-01": {"a": 88.0, "b": 40.0},
        "2023-03-01": {"a": 90.0},  # too thin if MIN were 2 — override by calling step directly
    }
    # Direct step math (MIN_PRICED is 15, so use step_month only when we patch counts).
    saved = globals()["MIN_PRICED"], globals()["MIN_OVERLAP"]
    globals()["MIN_PRICED"] = 1
    globals()["MIN_OVERLAP"] = 1
    try:
        chain = chain_index(
            {
                "2022-12-01": {"a": 100.0, "b": 50.0},
                "2023-01-01": {"a": 120.0, "b": 60.0},
                "2023-02-01": {"a": 132.0, "b": 60.0},
                "2023-04-01": {"a": 50.0},  # overlap 1, allowed in this test
            }
        )
    finally:
        globals()["MIN_PRICED"], globals()["MIN_OVERLAP"] = saved
    assert chain["anchorDate"] == "2023-01-01", chain["anchorDate"]
    jan = next(p for p in chain["points"] if p["date"] == "2023-01-01")
    assert jan["index"] == 1000.0, jan
    dec = chain["points"][0]
    # Dec sum 150, Jan sum 180 → Dec index = 1000 * 150/180
    assert abs(dec["index"] - round(1000.0 * 150 / 180, 2)) < 0.02, dec
    feb = next(p for p in chain["points"] if p["date"] == "2023-02-01")
    # overlap a+b: 192/180
    assert abs(feb["index"] - round(1000.0 * 192 / 180, 2)) < 0.02, feb
    # April only has card a. Overlap with Feb is {a}: 50/132. Not a zero-fill of b.
    april = chain["points"][-1]
    assert april["date"] == "2023-04-01"
    assert april["count"] == 1
    assert abs(april["index"] - round(feb["index"] * (50 / 132), 2)) < 0.05, april

    globals()["MIN_PRICED"] = 2
    globals()["MIN_OVERLAP"] = 2
    try:
        thin = chain_index(
            {
                "2023-01-01": {"a": 10.0, "b": 10.0},
                "2023-02-01": {"a": 12.0},
                "2023-03-01": {"a": 14.0, "b": 10.0},
            }
        )
    finally:
        globals()["MIN_PRICED"], globals()["MIN_OVERLAP"] = saved
    assert [p["date"] for p in thin["points"]] == ["2023-01-01", "2023-03-01"], thin
    assert "2023-02-01" in thin["skipped"]

    catalog = {
        "cards": [
            {
                "id": "charizard",
                "name": "Charizard",
                "number_base": 4,
                "number_base2": 4,
                "rarity": "holo",
                "prints": {
                    "1st": "pokemon-base-set/charizard-1st-edition-4",
                    "shadowless": "pokemon-base-set/charizard-shadowless-4",
                    "unlimited": "pokemon-base-set/charizard-4",
                    "base2": "pokemon-base-set-2/charizard-4",
                },
            },
            {
                "id": "machamp",
                "name": "Machamp",
                "number_base": 8,
                "number_base2": None,
                "rarity": "holo",
                "prints": {
                    "1st": "pokemon-base-set/machamp-1st-edition-8",
                    "shadowless": "pokemon-base-set/machamp-shadowless-8",
                },
            },
            {
                "id": "pidgey",
                "name": "Pidgey",
                "number_base": 57,
                "number_base2": 73,
                "rarity": "common",
                "prints": {"1st": "pokemon-base-set/pidgey-1st-edition-57"},
            },
        ]
    }
    # build_baskets requires MIN_PRICED slugs. Pad with synthetic chase holos.
    for n in list(range(1, 17)) + list(range(17, 23)) + list(range(70, 80)):
        if n in {4, 8}:
            continue
        catalog["cards"].append(
            {
                "id": f"c{n}",
                "name": f"Card {n}",
                "number_base": n,
                "number_base2": n,
                "rarity": "holo" if n <= 16 else "rare",
                "prints": {
                    "1st": f"pokemon-base-set/c{n}-1st",
                    "shadowless": f"pokemon-base-set/c{n}-sh",
                    "unlimited": f"pokemon-base-set/c{n}",
                    "base2": f"pokemon-base-set-2/c{n}",
                },
            }
        )
    baskets = build_baskets(
        catalog,
        {
            "pokemon-base-set/charizard-1st-edition-4": {
                "id": "715593",
                "imageUri": "https://storage.googleapis.com/images.pricecharting.com/example/60.jpg",
            }
        },
    )
    zard = next(r for r in baskets["indexes"]["base-1st"]["constituents"] if r["cardId"] == "charizard")
    assert zard["id"] == "715593"
    assert zard["url"].endswith("/game/pokemon-base-set/charizard-1st-edition-4")
    assert "snkrdunk.com" not in json.dumps(baskets).lower()
    assert all(r["cardId"] != "pidgey" for r in baskets["indexes"]["base-1st"]["constituents"])
    unl_ids = {r["cardId"] for r in baskets["indexes"]["base-unlimited"]["constituents"]}
    assert "machamp" not in unl_ids
    assert any(g["name"] == "Machamp" and g["index"] == "base-unlimited" for g in baskets["gaps"])
    base2_ids = {r["cardId"] for r in baskets["indexes"]["base-set-2"]["constituents"]}
    assert "machamp" not in base2_ids
    assert "charizard" in base2_ids

    # Full tiny chain through build_payloads with MIN patched and a cents guard bypass
    # by planting a huge charizard series.
    series = {}
    for spec in baskets["indexes"].values():
        for i, row in enumerate(spec["constituents"]):
            row["id"] = row.get("id") or str(1000 + i + (spec["id"].__hash__() % 50))
            # unique ids
        for row in spec["constituents"]:
            pid = str(row["id"])
            cents = 500_000 if row["cardId"] == "charizard" else 2000 + (int(pid) % 50)
            series[pid] = {
                "id": pid,
                "slug": row["slug"],
                "points": [
                    {"date": "2022-12-01", "cents": cents},
                    {"date": "2023-01-01", "cents": cents + 100},
                    {"date": "2023-02-01", "cents": cents + 200},
                ],
            }
    # ids may collide across indexes because hash trick; series_from one id is shared.
    # Rebuild unique ids per row.
    series = {}
    n = 1
    for spec in baskets["indexes"].values():
        for row in spec["constituents"]:
            n += 1
            row["id"] = str(n)
            cents = 800_000 if row["cardId"] == "charizard" and spec["id"] == "base-1st" else 2500
            series[row["id"]] = {
                "id": row["id"],
                "slug": row["slug"],
                "points": [
                    {"date": "2022-12-01", "cents": cents},
                    {"date": "2023-01-01", "cents": int(cents * 1.1)},
                    {"date": "2026-10-01", "cents": int(cents * 1.2)},
                ],
            }
    latest, history, prices = build_payloads(baskets, series)
    assert history["indexes"]["base-set-2"]["baseDate"] == "2023-01-01"
    assert history["indexes"]["base-1st"]["firstDate"] == "2022-12-01"
    assert history["indexes"]["base-1st"]["points"][1]["index"] == 1000.0
    blob = json.dumps({"latest": latest, "history": history})
    assert "snkrdunk.com" not in blob.lower()
    assert latest["priceSource"].startswith("PriceCharting")
    rets = history["indexes"]["base-1st"]["returns"]
    assert rets["1D"]["available"] is False
    assert rets["5D"]["available"] is False
    assert rets["ALL"]["available"] is True
    assert prices["indexes"]["base-set-2"]["points"]

    # Live catalog, no network: four baskets, machamp gap, chase size.
    live = build_baskets(load_catalog())
    assert live["indexOrder"] == ["base-1st", "base-shadowless", "base-unlimited", "base-set-2"]
    assert live["indexes"]["base-1st"]["constituentCount"] >= 31
    assert live["indexes"]["base-set-2"]["constituentCount"] == 29
    assert any(g["index"] == "base-set-2" and g["name"] == "Machamp" for g in live["gaps"])
    print("refresh_market_vintage_pc self-test OK")
    print(
        "catalog baskets",
        {iid: spec["constituentCount"] for iid, spec in live["indexes"].items()},
        "gaps",
        len(live["gaps"]),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Refresh EN Base PriceCharting indexes")
    parser.add_argument("--root", default=str(ROOT))
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--sample", action="store_true", help="Fetch four Charizard pages, write nothing")
    parser.add_argument("--from-cache", action="store_true", help="Rebuild JSON from cache/; still needs console ids if baskets have none")
    parser.add_argument("--sleep", type=float, default=0.35, help="Seconds between game-page fetches")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return 0
    if args.sample:
        run_sample(args.verbose)
        return 0
    coverage = run_refresh(
        Path(args.root),
        sleep_s=0 if args.from_cache else args.sleep,
        use_cache=args.from_cache,
        verbose=args.verbose or True,
    )
    print(json.dumps({k: {kk: vv for kk, vv in cov.items() if kk != "missing"} for k, cov in coverage.items()}, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except PcError as err:
        print(f"ERROR: {err}", file=sys.stderr)
        raise SystemExit(1)
