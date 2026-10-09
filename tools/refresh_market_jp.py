#!/usr/bin/env python3
"""Build a Japanese market-heat snapshot from SNKRDUNK-primary signals.

Phase 2 of Pokemon-Box-EV. SNKRDUNK has no stable public API, so this script
does **not** scrape snkrdunk.com (or TCGCSV JP / TCGPlayer / PriceCharting).

Allowed inputs:
  1. Box-EV snapshot (`data.json`) — SNKRDUNK Grade A 掛價 already curated
     for chase cards this site tracks.
  2. Optional overlay / universe file `data/market-jp/inbox.json` — a manual
     or semi-structured export with 成交價 + 流動性 when Gary has one.
  3. `--empty` — write a valid empty snapshot so the page can show empty states.

Writes (stdlib only — GitHub Actions needs no pip install):
  data/market-jp/latest.json   heat + index + breadth + movers + basket
  data/market-jp/history.json  one point per snapshot date
  data/market-jp/latest.js     window.MARKET_JP fallback (file:// / cache)

Index math matches the EN pulse: price-weighted top N with an S&P-style
divisor. First snapshot is rebased to 1,000; daily % / breadth / composite
heat appear once a previous snapshot exists **or** the inbox carries
liquidity fields.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(ROOT, "data", "market-jp")
LATEST_PATH = os.path.join(DATA_DIR, "latest.json")
LATEST_JS_PATH = os.path.join(DATA_DIR, "latest.js")
HISTORY_PATH = os.path.join(DATA_DIR, "history.json")
INBOX_PATH = os.path.join(DATA_DIR, "inbox.json")
BOX_EV_PATH = os.path.join(ROOT, "data.json")
IMG_MAP_PATH = os.path.join(ROOT, "img_map.json")

BASE_INDEX_VALUE = 1000.0
TARGET_SIZE = 100
STALE_DAYS = 70
TOP_MOVERS = 10
SNKR_GARBAGE = 100000  # same typo floor as refresh_box_ev.py

SCHEMA_VERSION = 1

_YEN_CHARS = re.compile(r"[¥￥,\s]")


def _parse_yen(raw) -> float | None:
    if raw is None or raw == "":
        return None
    if isinstance(raw, (int, float)):
        val = float(raw)
        return val if val > 0 else None
    s = _YEN_CHARS.sub("", str(raw).strip())
    if not s:
        return None
    try:
        val = float(s)
    except ValueError:
        return None
    return val if val > 0 else None


def _parse_int(raw) -> int | None:
    if raw is None or raw == "":
        return None
    if isinstance(raw, bool):
        return None
    if isinstance(raw, (int, float)):
        if raw < 0:
            return None
        return int(raw)
    s = str(raw).strip().replace(",", "")
    if not s.isdigit():
        return None
    return int(s)


def _first(item: dict, *keys):
    for key in keys:
        if key in item and item[key] not in (None, ""):
            return item[key]
    return None


def liquidity_tier(item: dict) -> str:
    """high / medium / low / unknown from explicit tag or 成交+掛單."""
    explicit = _first(item, "liquidity", "liquidityTier", "liq")
    if explicit:
        tag = str(explicit).strip().lower()
        mapping = {
            "high": "high",
            "h": "high",
            "熱": "high",
            "高": "high",
            "medium": "medium",
            "mid": "medium",
            "m": "medium",
            "中": "medium",
            "low": "low",
            "l": "low",
            "薄": "low",
            "低": "low",
            "unknown": "unknown",
            "unk": "unknown",
            "na": "unknown",
            "n/a": "unknown",
        }
        if tag in mapping:
            return mapping[tag]
    listings = _parse_int(_first(item, "listings", "asks", "listingCount", "掛單"))
    sales7d = _parse_int(_first(item, "sales7d", "sales_7d", "sales7", "近週成交"))
    sales30d = _parse_int(_first(item, "sales30d", "sales_30d", "近月成交"))
    if sales7d is not None and sales7d >= 5:
        return "high"
    if listings is not None and listings >= 10:
        return "high"
    if sales7d is not None and sales7d >= 1:
        return "medium"
    if sales30d is not None and sales30d >= 3:
        return "medium"
    if listings is not None and listings >= 3:
        return "medium"
    if listings is not None or sales7d is not None or sales30d is not None:
        return "low"
    return "unknown"


def pick_price(item: dict) -> tuple[float | None, str | None, float | None, float | None]:
    """Return (effective, kind, sold, ask). Prefer 成交價 over Grade A 掛價."""
    sold = _parse_yen(
        _first(item, "soldPriceYen", "sold_price_yen", "lastSaleYen", "last_sale_yen", "成交", "成交價")
    )
    ask = _parse_yen(
        _first(item, "askYen", "ask_yen", "gradeA_snkr", "gradeA", "掛價", "priceYen", "price")
    )
    if sold and sold < SNKR_GARBAGE:
        return sold, "sold", sold, ask if ask and ask < SNKR_GARBAGE else None
    if ask and ask < SNKR_GARBAGE:
        return ask, "ask", sold, ask
    return None, None, sold, ask


def card_id(item: dict) -> str | None:
    explicit = _first(item, "id")
    if explicit:
        return str(explicit).strip()
    set_code = str(_first(item, "set", "setCode", "set_code") or "").strip()
    num = _first(item, "num", "number", "no")
    if set_code and num is not None and str(num).strip() != "":
        return f"{set_code}|{num}"
    return None


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
        handle.write("window.MARKET_JP = " + payload + ";\n")


def as_of_from_box_ev(raw: str | None, now: datetime) -> str:
    if raw:
        m = re.match(r"^(\d{4})-(\d{2})-(\d{2})", str(raw).strip())
        if m:
            return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    return now.date().isoformat()


def _age_days(iso: str | None, today_iso: str) -> int:
    try:
        a = datetime.strptime(iso or "", "%Y-%m-%d").date()
        b = datetime.strptime(today_iso, "%Y-%m-%d").date()
        return (b - a).days
    except ValueError:
        return 0


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
    """Price-weighted index step with an S&P-style divisor (same as EN pulse)."""
    effective = dict(carry or {})
    effective.update(prices)
    basket = rank_basket(effective, catalog, size)
    ids = [pid for pid, _ in basket]
    sum_today = sum(pr for _, pr in basket)
    if not ids:
        return {
            "index": None,
            "divisor": None,
            "ids": [],
            "basket": [],
            "sum_today": 0.0,
        }
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


def clamp(n: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, n))


def heat_label(score: float | None) -> str:
    if score is None:
        return "尚無綜合熱度"
    if score >= 70:
        return "熱"
    if score >= 55:
        return "偏熱"
    if score >= 45:
        return "平"
    if score >= 30:
        return "偏冷"
    return "冷"


def compute_heat(change_pct: float | None, breadth: dict, liq: dict) -> dict:
    """Composite 0–100. Needs momentum (history) and/or liquidity coverage."""
    components: dict = {"momentum": None, "breadth": None, "liquidity": None}
    liq_known = int(liq.get("known") or 0)
    n = int(liq.get("total") or 0) or 1
    if liq_known:
        high = int(liq.get("high") or 0)
        medium = int(liq.get("medium") or 0)
        low = int(liq.get("low") or 0)
        components["liquidity"] = round(
            100.0 * (high + 0.6 * medium + 0.25 * low) / n, 1
        )
    if change_pct is not None:
        components["momentum"] = round(50.0 + clamp(change_pct, -10.0, 10.0) * 5.0, 1)
    if breadth.get("available"):
        tot = (
            int(breadth.get("advancing") or 0)
            + int(breadth.get("declining") or 0)
            + int(breadth.get("unchanged") or 0)
        )
        if tot:
            components["breadth"] = round(
                50.0
                + 50.0
                * (int(breadth["advancing"]) - int(breadth["declining"]))
                / tot,
                1,
            )

    weights = []
    if components["momentum"] is not None:
        weights.append((0.45 if components["liquidity"] is not None else 0.65, components["momentum"]))
    if components["breadth"] is not None:
        weights.append((0.25 if components["liquidity"] is not None else 0.35, components["breadth"]))
    if components["liquidity"] is not None:
        weights.append((0.30 if components["momentum"] is not None else 1.0, components["liquidity"]))

    if not weights:
        return {
            "available": False,
            "score": None,
            "label": "首次快照 — 綜合熱度會在下一份快照或匯入流動性後出現",
            "components": components,
        }

    wsum = sum(w for w, _ in weights)
    score = round(sum(w * v for w, v in weights) / wsum, 1)
    return {
        "available": True,
        "score": score,
        "label": heat_label(score),
        "components": components,
    }


def load_img_map() -> dict:
    blob = load_json(IMG_MAP_PATH, {}) or {}
    return blob.get("cards") or {}


def box_ev_catalog(box: dict, img_map: dict) -> tuple[dict, dict, dict]:
    """Build catalog + ask prices + extra fields from data.json chase cards."""
    sets_meta = box.get("sets") or {}
    catalog: dict = {}
    prices: dict = {}
    extra: dict = {}
    for card in box.get("cards") or []:
        set_code = str(card.get("set") or "").strip()
        num = card.get("num")
        if not set_code or num is None:
            continue
        ask = _parse_yen(card.get("gradeA_snkr"))
        if not ask or ask >= SNKR_GARBAGE:
            continue
        pid = f"{set_code}|{num}"
        meta = sets_meta.get(set_code) or {}
        set_name = meta.get("zh") or meta.get("ja") or meta.get("en") or set_code
        image = img_map.get(pid) or img_map.get(f"{set_code}|{num}") or ""
        if image and not str(image).startswith(("http://", "https://", "./", "/")):
            image = "./" + image.lstrip("./")
        catalog[pid] = {
            "id": pid,
            "name": card.get("name") or "Unknown",
            "nameZh": card.get("name_zh") or "",
            "number": str(num),
            "rarity": card.get("pool") or "",
            "setName": set_name,
            "setCode": set_code,
            "image": image,
            "url": "",
        }
        prices[pid] = round(ask, 2)
        extra[pid] = {
            "askYen": round(ask, 2),
            "soldPriceYen": None,
            "priceKind": "ask",
            "listings": None,
            "sales7d": None,
            "sales30d": None,
            "liquidity": "unknown",
        }
    return catalog, prices, extra


def normalize_inbox_item(raw: dict) -> dict | None:
    if not isinstance(raw, dict):
        return None
    pid = card_id(raw)
    if not pid:
        return None
    price, kind, sold, ask = pick_price(raw)
    set_code = str(_first(raw, "set", "setCode", "set_code") or "").strip()
    num = _first(raw, "num", "number", "no")
    listings = _parse_int(_first(raw, "listings", "asks", "listingCount", "掛單"))
    sales7d = _parse_int(_first(raw, "sales7d", "sales_7d", "sales7", "近週成交"))
    sales30d = _parse_int(_first(raw, "sales30d", "sales_30d", "近月成交"))
    image = _first(raw, "image", "imageUrl") or ""
    url = _first(raw, "url", "snkrdunk_url", "snkrdunkUrl") or ""
    return {
        "id": pid,
        "name": str(_first(raw, "name", "nameJa", "name_ja") or pid),
        "nameZh": str(_first(raw, "nameZh", "name_zh", "nameTc") or ""),
        "number": str(num).strip() if num is not None else "",
        "rarity": str(_first(raw, "rarity", "pool") or ""),
        "setName": str(_first(raw, "setName", "set_name") or set_code),
        "setCode": set_code,
        "image": image,
        "url": url,
        "price": price,
        "priceKind": kind,
        "soldPriceYen": sold,
        "askYen": ask,
        "listings": listings,
        "sales7d": sales7d,
        "sales30d": sales30d,
        "liquidity": liquidity_tier(
            {
                "liquidity": _first(raw, "liquidity", "liquidityTier", "liq"),
                "listings": listings,
                "sales7d": sales7d,
                "sales30d": sales30d,
            }
        ),
    }


def load_inbox(path: str) -> tuple[list[dict], dict]:
    blob = load_json(path, None)
    if blob is None:
        return [], {}
    if isinstance(blob, list):
        items_raw, meta = blob, {}
    elif isinstance(blob, dict):
        items_raw = blob.get("items") or blob.get("cards") or blob.get("constituents") or []
        meta = {
            "asOfDate": blob.get("asOfDate") or blob.get("as_of") or blob.get("date"),
            "sourceNote": blob.get("sourceNote") or blob.get("note") or blob.get("notes"),
            "source": blob.get("source") or blob.get("priceSource"),
        }
    else:
        return [], {}
    items = []
    for raw in items_raw:
        norm = normalize_inbox_item(raw)
        if norm:
            items.append(norm)
    return items, meta


def overlay_inbox(catalog: dict, prices: dict, extra: dict, items: list[dict]) -> None:
    for item in items:
        pid = item["id"]
        if pid not in catalog:
            catalog[pid] = {
                "id": pid,
                "name": item["name"],
                "nameZh": item.get("nameZh") or "",
                "number": item.get("number") or "",
                "rarity": item.get("rarity") or "",
                "setName": item.get("setName") or item.get("setCode") or "",
                "setCode": item.get("setCode") or "",
                "image": item.get("image") or "",
                "url": item.get("url") or "",
            }
        else:
            for key in ("name", "nameZh", "number", "rarity", "setName", "setCode", "image", "url"):
                val = item.get(key)
                if val:
                    catalog[pid][key] = val
        slot = extra.setdefault(
            pid,
            {
                "askYen": None,
                "soldPriceYen": None,
                "priceKind": None,
                "listings": None,
                "sales7d": None,
                "sales30d": None,
                "liquidity": "unknown",
            },
        )
        if item.get("soldPriceYen"):
            slot["soldPriceYen"] = item["soldPriceYen"]
        if item.get("askYen"):
            slot["askYen"] = item["askYen"]
        if item.get("listings") is not None:
            slot["listings"] = item["listings"]
        if item.get("sales7d") is not None:
            slot["sales7d"] = item["sales7d"]
        if item.get("sales30d") is not None:
            slot["sales30d"] = item["sales30d"]
        if item.get("priceKind"):
            slot["priceKind"] = item["priceKind"]
        slot["liquidity"] = item.get("liquidity") or liquidity_tier(slot)
        if item.get("price"):
            prices[pid] = round(item["price"], 2)


def build_snapshot(
    catalog: dict,
    prices: dict,
    extra: dict,
    stamp: str | None,
    now: datetime,
    ingestion: str,
    source_note: str,
    price_source: str,
    universe: str,
    size: int = TARGET_SIZE,
    ignore_prev: bool = False,
) -> dict:
    prev = {} if ignore_prev else load_json(LATEST_PATH, {})
    history_points = (load_json(HISTORY_PATH, {}) or {}).get("points") or []
    if history_points and prev.get("constituents") is None and prev.get("index") is not None:
        raise RuntimeError(
            "latest.json is missing/corrupt but history.json has "
            f"{len(history_points)} points — restore data/market-jp/latest.json"
        )
    if prev.get("constituents") and not history_points:
        raise RuntimeError(
            "history.json is missing/corrupt but latest.json exists — "
            "restore data/market-jp/history.json"
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

    today_iso = as_of_from_box_ev(stamp, now)
    continuing = bool(prev_ids) and (
        prev.get("asOfDate") != today_iso or prev.get("prevIndex") is not None
    )
    if not continuing:
        prev_ids = None
        prev_divisor = None
        baseline_index = None
        baseline_prices = {}
    elif prev.get("asOfDate") == today_iso:
        baseline_index = prev.get("prevIndex") or prev_index
        baseline_prices = {
            c["id"]: c.get("prevPrice")
            for c in prev.get("constituents", [])
            if c.get("prevPrice")
        } or prev_prices
    else:
        baseline_index = prev_index
        baseline_prices = prev_prices

    carry = (
        {
            pid: pr
            for pid, pr in prev_prices.items()
            if _age_days(prev_priced_asof.get(pid), today_iso) <= STALE_DAYS
        }
        if continuing
        else {}
    )
    step = step_index(prices, catalog, prev_ids, prev_divisor, carry=carry, size=size)

    constituents = []
    advancing = declining = unchanged = 0
    kind_counts = {"sold": 0, "ask": 0, "unknown": 0}
    liq_counts = {"high": 0, "medium": 0, "low": 0, "unknown": 0}

    for rank, (pid, price) in enumerate(step["basket"], start=1):
        meta = catalog.get(pid) or {
            "id": pid,
            "name": "Unknown",
            "nameZh": "",
            "number": "",
            "rarity": "",
            "setName": "",
            "setCode": "",
            "image": "",
            "url": "",
        }
        ext = extra.get(pid) or {}
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
        kind = ext.get("priceKind") or ("ask" if live_today else None)
        if kind in kind_counts:
            kind_counts[kind] += 1
        else:
            kind_counts["unknown"] += 1
        liq = ext.get("liquidity") or "unknown"
        if liq not in liq_counts:
            liq = "unknown"
        liq_counts[liq] += 1
        constituents.append(
            {
                "rank": rank,
                "id": meta["id"],
                "name": meta.get("name") or "Unknown",
                "nameZh": meta.get("nameZh") or "",
                "number": str(meta.get("number") or ""),
                "rarity": meta.get("rarity") or "",
                "setName": meta.get("setName") or "",
                "setCode": meta.get("setCode") or "",
                "image": meta.get("image") or "",
                "url": meta.get("url") or "",
                "price": round(price, 2),
                "prevPrice": round(prior, 2) if prior else None,
                "changePct": change_pct,
                "priceKind": kind,
                "soldPriceYen": ext.get("soldPriceYen"),
                "askYen": ext.get("askYen"),
                "listings": ext.get("listings"),
                "sales7d": ext.get("sales7d"),
                "sales30d": ext.get("sales30d"),
                "liquidity": liq,
                "pricedAsOf": today_iso if live_today else prev_priced_asof.get(pid),
                "carried": not live_today,
            }
        )

    movable = [c for c in constituents if c["changePct"] is not None]
    movable.sort(key=lambda c: c["changePct"], reverse=True)
    fields = (
        "id",
        "name",
        "nameZh",
        "number",
        "image",
        "setName",
        "setCode",
        "rarity",
        "price",
        "prevPrice",
        "changePct",
        "priceKind",
        "liquidity",
    )
    gainers = [{k: c[k] for k in fields} for c in movable[:TOP_MOVERS] if c["changePct"] > 0]
    losers = [{k: c[k] for k in fields} for c in reversed(movable[-TOP_MOVERS:]) if c["changePct"] < 0]

    has_prev = baseline_index is not None and step["index"] is not None
    index_value = step["index"]
    change_abs = round(index_value - baseline_index, 2) if has_prev else None
    change_pct = round((index_value / baseline_index - 1) * 100, 2) if has_prev else None
    breadth_available = bool(movable)
    breadth = {
        "available": breadth_available,
        "advancing": advancing,
        "declining": declining,
        "unchanged": unchanged,
    }

    liq_known = liq_counts["high"] + liq_counts["medium"] + liq_counts["low"]
    liq = {
        "available": liq_known > 0,
        "known": liq_known,
        "total": len(constituents),
        "high": liq_counts["high"],
        "medium": liq_counts["medium"],
        "low": liq_counts["low"],
        "unknown": liq_counts["unknown"],
        "notes": (
            "籃子裡 "
            + str(liq_known)
            + " / "
            + str(len(constituents))
            + " 張有掛單或近週成交訊號。"
            if liq_known
            else "本快照沒有掛單數／近週成交筆數。把 SNKRDUNK 匯出放到 data/market-jp/inbox.json 再跑 refresh 即可補上流動性。"
        ),
    }
    heat = compute_heat(change_pct, breadth, liq)

    thin = []
    if liq_known:
        ranked_thin = [
            c
            for c in constituents
            if c.get("liquidity") in ("low", "medium")
        ]
        ranked_thin.sort(key=lambda c: ({"low": 0, "medium": 1}.get(c["liquidity"], 9), c["price"]))
        thin_fields = (
            "id",
            "name",
            "nameZh",
            "number",
            "setName",
            "price",
            "listings",
            "sales7d",
            "liquidity",
        )
        thin = [{k: c[k] for k in thin_fields} for c in ranked_thin[:10]]

    limitations = [
        "SNKRDUNK 沒有穩定公開 API；本頁不爬站、不打 TCGCSV／TCGPlayer JP、也不用 PriceCharting 付費 API。",
        "預設籃子來自 Box-EV 已整理的 SNKRDUNK Grade A 掛價（chase 卡），不是全站成交簿。",
        "成交價與流動性（掛單／近週成交）只有在 data/market-jp/inbox.json 匯入後才會進入綜合熱度。",
        "指數是本站追蹤的日版 chase Top 100（價格加權），不是官方指數，也不是投資建議。",
    ]

    latest = {
        "schemaVersion": SCHEMA_VERSION,
        "generated": now.isoformat(),
        "sourceStamp": stamp,
        "asOfDate": today_iso,
        "ingestion": ingestion,
        "priceSource": price_source,
        "universe": universe,
        "sourceNote": source_note,
        "staleDays": STALE_DAYS,
        "index": round(index_value, 2) if index_value is not None else None,
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
        "basketSize": size,
        "totalValue": round(step["sum_today"], 2),
        "pricedToday": sum(1 for c in constituents if not c["carried"]),
        "currency": "JPY",
        "priceKindCounts": kind_counts,
        "heat": heat,
        "liquidity": liq,
        "breadth": breadth,
        "gainers": gainers,
        "losers": losers,
        "top": [
            {
                k: c[k]
                for k in (
                    "rank",
                    "id",
                    "name",
                    "nameZh",
                    "number",
                    "image",
                    "setName",
                    "setCode",
                    "rarity",
                    "price",
                    "priceKind",
                    "liquidity",
                )
            }
            for c in constituents[:10]
        ],
        "thin": thin,
        "limitations": limitations,
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
            "heat": (latest.get("heat") or {}).get("score"),
            "totalValue": latest["totalValue"],
            "count": latest["constituentCount"],
        }
    )
    points.sort(key=lambda p: p["date"])
    return {"generated": now.isoformat(), "points": points}


def empty_snapshot(now: datetime) -> dict:
    return build_snapshot(
        catalog={},
        prices={},
        extra={},
        stamp=now.date().isoformat(),
        now=now,
        ingestion="empty",
        source_note="沒有 Box-EV 也沒有 inbox — 頁面會顯示空狀態。",
        price_source="none",
        universe="empty",
        size=TARGET_SIZE,
        ignore_prev=True,
    )


def assemble_universe(inbox_path: str | None, ingest_only: bool, empty: bool) -> tuple[dict, dict, dict, dict]:
    """Returns catalog, prices, extra, meta."""
    img_map = load_img_map()
    catalog: dict = {}
    prices: dict = {}
    extra: dict = {}
    box = load_json(BOX_EV_PATH, None) if not ingest_only and not empty else None
    box_as_of = None
    if box and not empty:
        catalog, prices, extra = box_ev_catalog(box, img_map)
        box_as_of = box.get("as_of")

    items, inbox_meta = ([], {})
    if inbox_path and os.path.exists(inbox_path) and not empty:
        items, inbox_meta = load_inbox(inbox_path)
        overlay_inbox(catalog, prices, extra, items)

    if empty:
        ingestion = "empty"
        price_source = "none"
        universe = "empty"
        source_note = "空快照（sample / empty state）。"
        stamp = None
    elif ingest_only:
        ingestion = "inbox"
        price_source = (
            inbox_meta.get("source")
            or "SNKRDUNK 成交價＋流動性（手動／半結構化 inbox.json）"
        )
        universe = "Inbox watchlist (SNKRDUNK-primary; not TCGCSV JP)"
        source_note = inbox_meta.get("sourceNote") or (
            f"inbox {len(items)} 筆"
        )
        stamp = str(inbox_meta.get("asOfDate") or "")
    elif items:
        ingestion = "box_ev+inbox"
        price_source = (
            "SNKRDUNK Grade A（Box-EV）＋ inbox 成交／流動性 overlay"
        )
        universe = "JP chase singles tracked by Box-EV (SNKRDUNK-primary Top 100)"
        source_note = inbox_meta.get("sourceNote") or (
            f"Box-EV Grade A overlay inbox {len(items)} 筆"
        )
        stamp = "|".join(
            x for x in (str(box_as_of or ""), str(inbox_meta.get("asOfDate") or f"n={len(items)}")) if x
        )
    else:
        ingestion = "box_ev"
        price_source = "SNKRDUNK Grade A 掛價（via Box-EV snapshot；非即時成交）"
        universe = "JP chase singles tracked by Box-EV (SNKRDUNK Grade A Top 100)"
        source_note = (
            "尚未匯入成交／流動性。這份快照用本站已有的 SNKRDUNK Grade A 掛價做價格籃子。"
        )
        stamp = str(box_as_of or "")

    return catalog, prices, extra, {
        "ingestion": ingestion,
        "price_source": price_source,
        "universe": universe,
        "source_note": source_note,
        "stamp": stamp or None,
        "inbox_count": len(items),
        "box_as_of": box_as_of,
    }


def self_test() -> None:
    catalog = {
        "a": {"id": "a", "name": "A", "nameZh": "", "number": "1", "rarity": "SAR", "setName": "S", "setCode": "S", "image": "", "url": ""},
        "b": {"id": "b", "name": "B", "nameZh": "", "number": "2", "rarity": "SAR", "setName": "S", "setCode": "S", "image": "", "url": ""},
        "c": {"id": "c", "name": "C", "nameZh": "", "number": "3", "rarity": "SAR", "setName": "S", "setCode": "S", "image": "", "url": ""},
    }
    day1 = {"a": 100.0, "b": 50.0, "c": 10.0}
    s1 = step_index(day1, catalog, None, None, size=2, base=1000.0)
    assert s1["ids"] == ["a", "b"]
    assert abs(s1["sum_today"] - 150.0) < 1e-9
    assert abs(s1["index"] - 1000.0) < 1e-9

    day2 = {"a": 110.0, "b": 55.0, "c": 10.0}
    s2 = step_index(day2, catalog, s1["ids"], s1["divisor"], size=2, base=1000.0)
    assert abs(s2["index"] - 1100.0) < 1e-6

    day3 = {"a": 110.0, "b": 1.0, "c": 80.0}
    s3 = step_index(day3, catalog, s2["ids"], s2["divisor"], size=2, base=1000.0)
    expected = 111.0 / s2["divisor"]
    assert abs(s3["index"] - expected) < 1e-6
    assert s3["ids"] == ["a", "c"]

    empty_step = step_index({}, {}, None, None, size=2, base=1000.0)
    assert empty_step["index"] is None and empty_step["ids"] == []

    sold_row = {"soldPriceYen": "¥46,000", "askYen": 50000, "listings": 12, "sales7d": 6}
    price, kind, sold, ask = pick_price(sold_row)
    assert kind == "sold" and price == 46000 and sold == 46000 and ask == 50000
    assert liquidity_tier(sold_row) == "high"
    assert liquidity_tier({"listings": 4}) == "medium"
    assert liquidity_tier({"listings": 1, "sales7d": 0}) == "low"
    assert liquidity_tier({}) == "unknown"
    assert liquidity_tier({"liquidity": "薄"}) == "low"

    garbage = {"gradeA_snkr": 111111, "set": "SV7a", "num": 83}
    assert pick_price(garbage)[0] is None

    inbox_item = normalize_inbox_item(
        {
            "set": "SV8a",
            "num": 217,
            "name": "ブラッキーex SAR",
            "name_zh": "月亮伊布ex SAR",
            "成交": 42000,
            "掛單": 8,
            "近週成交": 2,
        }
    )
    assert inbox_item and inbox_item["id"] == "SV8a|217"
    assert inbox_item["priceKind"] == "sold"
    assert inbox_item["liquidity"] == "medium"

    cat, pr, ext = {}, {}, {}
    overlay_inbox(cat, pr, ext, [inbox_item])
    assert "SV8a|217" in cat and pr["SV8a|217"] == 42000
    assert ext["SV8a|217"]["liquidity"] == "medium"

    h = compute_heat(None, {"available": False}, {"known": 0, "total": 0})
    assert h["available"] is False and h["score"] is None

    h2 = compute_heat(
        2.0,
        {"available": True, "advancing": 60, "declining": 30, "unchanged": 10},
        {"known": 40, "total": 100, "high": 10, "medium": 20, "low": 10, "unknown": 60},
    )
    assert h2["available"] is True and 0 <= h2["score"] <= 100
    assert h2["label"] in ("熱", "偏熱", "平", "偏冷", "冷")

    fake_now = datetime(2026, 10, 9, 7, 0, tzinfo=timezone.utc)
    assert as_of_from_box_ev("2026-10-07 18:54 Asia/Shanghai", fake_now) == "2026-10-07"
    assert as_of_from_box_ev(None, fake_now) == "2026-10-09"

    empty = empty_snapshot(fake_now)
    assert empty["ingestion"] == "empty"
    assert empty["constituentCount"] == 0
    assert empty["index"] is None
    assert empty["gainers"] == []
    assert empty["liquidity"]["available"] is False

    if os.path.exists(BOX_EV_PATH):
        box = load_json(BOX_EV_PATH, {})
        img_map = load_img_map()
        c2, p2, e2 = box_ev_catalog(box, img_map)
        assert len(p2) > 50
        sample_id = next(iter(p2))
        assert e2[sample_id]["priceKind"] == "ask"
        assert e2[sample_id]["liquidity"] == "unknown"
        # overlay sold price must beat Grade A ask for the index
        before = p2[sample_id]
        overlay_inbox(
            c2,
            p2,
            e2,
            [
                {
                    "id": sample_id,
                    "name": c2[sample_id]["name"],
                    "price": before + 1000,
                    "priceKind": "sold",
                    "soldPriceYen": before + 1000,
                    "askYen": before,
                    "listings": 11,
                    "sales7d": 5,
                    "sales30d": None,
                    "liquidity": "high",
                    "number": c2[sample_id]["number"],
                    "rarity": "",
                    "setName": "",
                    "setCode": "",
                    "image": "",
                    "url": "",
                }
            ],
        )
        assert p2[sample_id] == before + 1000
        assert e2[sample_id]["priceKind"] == "sold"
        assert e2[sample_id]["liquidity"] == "high"

    print("self-test ok")


def build(
    verbose: bool = False,
    force: bool = False,
    empty: bool = False,
    ingest_only: bool = False,
    inbox_path: str | None = None,
) -> None:
    inbox_path = inbox_path or INBOX_PATH
    catalog, prices, extra, meta = assemble_universe(inbox_path, ingest_only, empty)

    prev = load_json(LATEST_PATH, {})
    stamp = meta["stamp"]
    if stamp and prev.get("sourceStamp") == stamp and not force and not empty:
        print(f"JP heat source unchanged since {stamp}; nothing to do.", flush=True)
        return

    if verbose:
        print(
            f"  ingestion={meta['ingestion']} catalog={len(catalog)} "
            f"priced={len(prices)} inbox={meta['inbox_count']}",
            flush=True,
        )

    now = datetime.now(timezone.utc)
    latest = build_snapshot(
        catalog,
        prices,
        extra,
        stamp,
        now,
        ingestion=meta["ingestion"],
        source_note=meta["source_note"],
        price_source=meta["price_source"],
        universe=meta["universe"],
        ignore_prev=empty,
    )
    history = append_history(latest, now)
    write_json(LATEST_PATH, latest)
    write_json(HISTORY_PATH, history)
    write_latest_js(latest)

    ch = latest["changePct"]
    ch_s = "n/a (first snapshot)" if ch is None else f"{ch:+.2f}%"
    heat = latest["heat"]
    heat_s = "n/a" if not heat.get("available") else f"{heat['score']} {heat['label']}"
    idx = latest["index"]
    idx_s = "—" if idx is None else f"{idx:.2f}"
    print(
        f"JP heat index = {idx_s} ({ch_s}) · composite {heat_s} "
        f"| basket {latest['constituentCount']} "
        f"| total ¥{latest['totalValue']:,.0f} "
        f"| asOf {latest['asOfDate']} "
        f"| {latest['ingestion']}",
        flush=True,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-test", action="store_true", help="Run index / ingest checks and exit")
    parser.add_argument("--force", action="store_true", help="Rebuild even if source stamp is unchanged")
    parser.add_argument(
        "--empty",
        action="store_true",
        help="Write an empty snapshot (page empty states). Does not read data.json / inbox.",
    )
    parser.add_argument(
        "--ingest-only",
        action="store_true",
        help="Use inbox.json as the universe (no Box-EV Grade A seed)",
    )
    parser.add_argument(
        "--inbox",
        default=INBOX_PATH,
        help="Path to semi-structured SNKRDUNK snapshot (default data/market-jp/inbox.json)",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)
    if args.self_test:
        self_test()
        return 0
    try:
        build(
            verbose=args.verbose,
            force=args.force,
            empty=args.empty,
            ingest_only=args.ingest_only,
            inbox_path=args.inbox,
        )
    except Exception as err:  # noqa: BLE001 — surface a clean CI failure
        print(f"ERROR: {err}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
