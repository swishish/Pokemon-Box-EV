#!/usr/bin/env python3
"""Broad JP PSA10 market benchmark (Pokeca-style sectors) from public SNKRDUNK.

Public JP PSA10 benchmark. `market-jp-psa10.html` redirects here.
This pipeline does **not** replace that page.

Headline COMPOSITE = union of VINTAGE + MODERN + CURRENT after liquidity /
price screens. N floats — no per-sector floor, no pad-to-N, no truncate-to-N.
Parallel sibling series: PROMO and MID/Tag Team (same screen rule).

Four daily-rebalanced weight lines (site order):
  1. equal
  2. liquidity-weighted, 3% per-card cap
  3. price-weighted, 3% per-card cap
  4. price-weighted no-cap overlay (does not change 1–3)

Membership reconstitution is **quarterly** (Jan/Apr/Jul/Oct 1 JST), plus
manual `--rebuild-baskets --confirm REBUILD`. Daily job only refreshes
prints and rebalances weights.

Public JSON only (view-only). No login, no buy/bid/list.

  python3 tools/refresh_market_jp_psa10_bench.py --self-test
  python3 tools/refresh_market_jp_psa10_bench.py --rebuild-baskets --confirm REBUILD -v
  python3 tools/refresh_market_jp_psa10_bench.py --reselect -v
  python3 tools/refresh_market_jp_psa10_bench.py --from-cache
  python3 tools/refresh_market_jp_psa10_bench.py -v
"""
from __future__ import annotations

import argparse
import os
import re
import sys
import time
from datetime import datetime, timedelta
from typing import Any

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from jp_psa10_lib import (  # noqa: E402
    BASE_INDEX_VALUE,
    CONDITION_PSA10,
    JST,
    LIQUIDITY_WINDOW_DAYS,
    MAX_PRINT_GAP_DAYS,
    RETURN_CLIP,
    SNK,
    WEIGHT_CAP,
    _now,
    build_index,
    cap_weights,
    classify_card,
    constituents_payload,
    dump_series_cache,
    extract_set_code,
    fetch_apparel,
    fetch_psa10_history,
    fetch_search,
    is_english_printing,
    is_trading_card_single,
    liquidity_score,
    load_json,
    load_series_cache,
    locf_prices,
    looks_like_pokemon,
    looks_like_sealed,
    match_name_zh,
    movers_from_prices,
    ms_to_ymd_jst,
    next_reconstitution,
    parse_prints,
    parse_trades,
    parse_ymd,
    period_returns,
    pokeca_score,
    print_count_between,
    quarter_start,
    reconstitution_due,
    series_from_fixture_cards,
    write_js,
    write_json,
    ymd,
)
DATA_DIR = os.path.join(ROOT, "data", "market-jp-psa10-bench")
BASKETS_PATH = os.path.join(DATA_DIR, "baskets.json")
UNIVERSE_PATH = os.path.join(DATA_DIR, "universe.json")
SCAN_PATH = os.path.join(DATA_DIR, "scan-cache.json")
LATEST_PATH = os.path.join(DATA_DIR, "latest.json")
LATEST_JS_PATH = os.path.join(DATA_DIR, "latest.js")
HISTORY_PATH = os.path.join(DATA_DIR, "history.json")
HISTORY_JS_PATH = os.path.join(DATA_DIR, "history.js")
SERIES_PATH = os.path.join(DATA_DIR, "series-cache.json")
DATA_JSON_PATH = os.path.join(ROOT, "data.json")
NAMES_ZH_PATH = os.path.join(ROOT, "data", "names_zh.json")
CHASE_BASKETS = os.path.join(ROOT, "data", "market-jp-psa10", "baskets.json")
TESTDATA = os.path.join(HERE, "testdata", "snkrdunk-jp-psa10-bench", "cards.json")

# Quality screens — sector N is whatever passes. Not a floor, not a cap.
MIN_LAST_PRICE_YEN = 2000.0
MIN_PRINTS_90D = 6
MIN_PRINTS_90D_VINTAGE = 5
MIN_LAST_PRICE_PROMO = 2500.0
MIN_PRINTS_90D_PROMO = 5
MIN_PRINTS_90D_MIDERA = 5
# If a primary screen yields fewer than this, loosen that sector only and
# keep every card that then passes. This is not a membership floor: N may
# still be below 20 after the relaxed screen.
THIN_SCREEN_RELAX_BELOW = 20
MIN_PRINTS_90D_RELAXED = 3
MIN_LAST_PRICE_RELAXED = 1500.0
MIN_CONSTITUENTS = 40  # index base-date threshold, not a basket-size target
STALE_DAYS = 8
SLEEP_S = 0.18
SEARCH_PER_PAGE = 20
SEARCH_PAGES_DEFAULT = 2
SEARCH_PAGES_DEEP = 4

# Peer sizes (documentation only — never enforced as N).
PEER_SIZE_NOTES = {
    "pokeca": (
        "POKECA PSA INDEX (psa-index.com): Vintage/Modern/Current each floor 80 "
        "historically; leftover to a 500 target by sales volume; live TOTAL ~489 "
        "Jevons constituents (2026-10-20). Screens: Vintage ≥1 sale / ¥3,000; "
        "Modern+Current ≥3 sales / ¥5,000."
    ),
    "grade10": (
        "Grade10 PTCG100 = top 100 by trailing 90d pooled sales (min 5 sales); "
        "PTCG500 = top 500 (same rule). EN+JP PSA10, quarterly. Fixed N."
    ),
    "renaiss": (
        "Renaiss Pokémon index = monthly Top 50 most-traded PSA10 — chase-scale, "
        "not a broad-market peer."
    ),
    "chaseSleeve": (
        "market-jp-psa10.html redirects to this benchmark. The old ~60 SV chase "
        "sleeve is not a public page and is out of scope for this basket."
    ),
    "compositeRule": (
        "Composite is the union of screened VINTAGE+MODERN+CURRENT. Soft peer "
        "band ~300–500 (Pokeca ~489 / Grade10 500) is a reference only — we do "
        "not pad or truncate to hit it."
    ),
}

SERIES_META = {
    "composite": {"labelZh": "綜合", "labelEn": "Composite", "kind": "headline"},
    "vintage": {"labelZh": "復古", "labelEn": "Vintage", "kind": "sector"},
    "modern": {"labelZh": "中世代 SM/S", "labelEn": "Modern", "kind": "sector"},
    "current": {"labelZh": "現行 SV/M", "labelEn": "Current", "kind": "sector"},
    "promo": {"labelZh": "特典 PROMO", "labelEn": "Promo", "kind": "sibling"},
    "midera": {"labelZh": "Tag Team / MID", "labelEn": "Mid-era", "kind": "sibling"},
}

_APPAREL_ID = re.compile(r"/apparels/(\d+)|/trading-cards/(\d+)")


def apparel_universe_from_data(data_obj: dict) -> dict[int, dict]:
    """Unique SNK apparel IDs already in Box EV data.json. Do not invent IDs."""
    out: dict[int, dict] = {}
    for card in data_obj.get("cards") or []:
        if not isinstance(card, dict):
            continue
        url = str(card.get("url") or "")
        match = _APPAREL_ID.search(url)
        if not match:
            continue
        aid = int(match.group(1) or match.group(2))
        row = out.setdefault(
            aid,
            {
                "apparelId": aid,
                "name": card.get("name"),
                "nameZh": card.get("name_zh"),
                "set": card.get("set"),
                "num": card.get("num"),
                "url": f"{SNK}/apparels/{aid}",
                "source": "data.json",
            },
        )
        if not row.get("nameZh") and card.get("name_zh"):
            row["nameZh"] = card.get("name_zh")
    return out


def discovery_queries() -> list[tuple[str, int]]:
    """(keyword, pages). Public search only — IDs come from SNK, never invented."""
    deep = SEARCH_PAGES_DEEP
    shallow = SEARCH_PAGES_DEFAULT
    queries: list[tuple[str, int]] = []

    current_sets = [
        "SV1", "SV1a", "SV1S", "SV2", "SV2a", "SV3", "SV3a", "SV4", "SV4a",
        "SV5", "SV5a", "SV5K", "SV5M", "SV6", "SV6a", "SV7", "SV7a",
        "SV8", "SV8a", "SV9", "SV9a", "SV10", "SV11", "SV11B", "SV11W",
        "M1L", "M1S", "M2", "M2a", "M3", "M4", "M5", "M6", "M6a",
    ]
    modern_sets = [
        "SM1", "SM2", "SM3", "SM4", "SM5", "SM6", "SM7", "SM8", "SM8b",
        "SM9", "SM9a", "SM9b", "SM10", "SM10a", "SM10b",
        "SM11", "SM11a", "SM11b", "SM12", "SM12a", "SMM",
        "S1H", "S1W", "S2", "S3", "S4", "S4a", "S5", "S5a", "S5R",
        "S6", "S6a", "S7", "S7D", "S7R", "S8", "S8b", "S8a",
        "S9", "S9a", "S10", "S10a", "S10b", "S11", "S11a", "S12", "S12a",
    ]
    vintage_sets = [
        "XY1", "XY2", "XY3", "XY4", "XY5", "XY6", "XY7", "XY8", "XY9", "XY10", "XY11",
        "CP1", "CP2", "CP3", "CP4", "CP5", "CP6",
        "BW1", "BW2", "BW3", "BW4", "BW5", "BW6", "BW7", "BW8", "BW9",
        "L1", "L2", "L3", "DP1", "PT1", "ADV",
    ]
    for code in current_sets:
        queries.append((f"{code} SAR", shallow))
        queries.append((f"{code} SR", shallow))
    for code in modern_sets:
        queries.append((f"{code} SR", shallow))
        queries.append((f"{code} UR", 1))
    for code in vintage_sets:
        queries.append((f"{code} SR", shallow))
        queries.append((f"{code} UR", 1))

    extras = [
        ("TAG TEAM", deep),
        ("SM12a", deep),
        ("s8b CHR", deep),
        ("s12a SAR", deep),
        ("リザードン SAR", deep),
        ("リザードン SR", deep),
        ("ピカチュウ SAR", deep),
        ("ブラッキー SAR", deep),
        ("ニンフィア SAR", shallow),
        ("ミュウ SAR", shallow),
        ("ミュウツー SAR", shallow),
        ("SR XY", deep),
        ("UR XY", shallow),
        ("SR BW", shallow),
        ("ポンチョ ピカチュウ", deep),
        ("マリオピカチュウ", deep),
        ("ルイージピカチュウ", shallow),
        ("長場 ピカチュウ", deep),
        ("Yu Nagaba", shallow),
        ("Grey Felt Hat", deep),
        ("ゴッホ ピカチュウ", shallow),
        ("ムンク モクロー", shallow),
        ("エクストラバトル", deep),
        (" Extra Battle Day", shallow),
        ("ピカチュウ プロモ", deep),
        ("S-P ピカチュウ", shallow),
        ("SM-P", shallow),
        ("SV-P SAR", shallow),
        ("XY-P SR", shallow),
        ("メガリザードン SR", shallow),
        ("レックウザ SR", shallow),
        ("ギラティナ SR", shallow),
        ("ルギア SR", shallow),
        ("ポケキュン SR", 1),
        ("青い衝撃 SR", 1),
        ("赤い閃光 SR", 1),
        ("ライジングフィスト SR", 1),
        ("VSTARユニバース SAR", shallow),
        ("シャイニートレジャー SAR", shallow),
        ("テラスタルフェス SAR", shallow),
        ("151 SAR", shallow),
        ("マスターボール SAR", 1),
    ]
    queries.extend(extras)
    # Deduplicate keeping first (deeper pages win if listed first).
    seen: set[str] = set()
    out: list[tuple[str, int]] = []
    for kw, pages in queries:
        key = kw.strip()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append((key, pages))
    return out


def harvest_search_ids(
    *,
    sleep_s: float,
    verbose: bool,
    limit_queries: int | None = None,
) -> dict[int, dict]:
    found: dict[int, dict] = {}
    queries = discovery_queries()
    if limit_queries:
        queries = queries[:limit_queries]
    for qi, (kw, pages) in enumerate(queries, 1):
        for page in range(1, pages + 1):
            try:
                payload = fetch_search(kw, page=page, per_page=SEARCH_PER_PAGE)
            except Exception as err:  # noqa: BLE001
                if verbose:
                    print(f"search skip {kw!r} p{page}: {err}", file=sys.stderr)
                time.sleep(sleep_s)
                continue
            time.sleep(sleep_s)
            for row in payload.get("streetwears") or []:
                if not isinstance(row, dict):
                    continue
                if row.get("isTradingCard") is False:
                    continue
                try:
                    aid = int(row.get("id"))
                except (TypeError, ValueError):
                    continue
                name = row.get("name") or ""
                if looks_like_sealed(name):
                    continue
                if is_english_printing(name, None):
                    continue
                found.setdefault(
                    aid,
                    {
                        "apparelId": aid,
                        "name": name,
                        "url": f"{SNK}/apparels/{aid}",
                        "source": "snk-search",
                        "query": kw,
                    },
                )
        if verbose and qi % 20 == 0:
            print(f"search {qi}/{len(queries)} · unique IDs {len(found)}", file=sys.stderr)
    if verbose:
        print(f"search done · {len(queries)} queries · {len(found)} apparel IDs", file=sys.stderr)
    return found


def merge_universe(*parts: dict[int, dict]) -> dict[int, dict]:
    out: dict[int, dict] = {}
    for part in parts:
        for aid, row in part.items():
            prev = out.get(int(aid))
            if prev is None:
                out[int(aid)] = dict(row)
                out[int(aid)]["apparelId"] = int(aid)
            else:
                for k, v in row.items():
                    if v and not prev.get(k):
                        prev[k] = v
    return out


def score_prints(prints: list[tuple[str, float]], as_of: str | None = None) -> dict:
    if not prints:
        return {"lastPrice": None, "prints90": 0, "prints30": 0, "ok": False}
    last_day, last_price = prints[-1]
    end = as_of or last_day
    start90 = ymd(parse_ymd(end) - timedelta(days=90))
    start30 = ymd(parse_ymd(end) - timedelta(days=30))
    p90 = print_count_between(prints, start90, ymd(parse_ymd(end) + timedelta(days=1)))
    p30 = print_count_between(prints, start30, ymd(parse_ymd(end) + timedelta(days=1)))
    return {"lastPrice": last_price, "lastDay": last_day, "prints90": p90, "prints30": p30}


def passes_threshold(stats: dict, *, min_price: float, min_prints: int) -> bool:
    price = stats.get("lastPrice")
    return bool(price and price >= min_price and int(stats.get("prints90") or 0) >= min_prints)


_CHASE_HINT = re.compile(
    r"\b(SAR|SSR|MUR|BWR|\bMA\b|SR|UR|HR|CHR|CSR|\bAR\b|\bFA\b|GX|VMAX|VSTAR|"
    r"PROMO|TAG TEAM|ex SAR|ex SR|ACE SPEC)\b",
    re.I,
)
_VINTAGE_KEEP = re.compile(r"\b(R|RR|EX|Holo|SR|UR|PROMO)\b", re.I)


def _worth_history(name: str | None, loc: str | None, cls: dict) -> bool:
    blob = f"{name or ''} {loc or ''}"
    if cls.get("promo") or cls.get("midera"):
        return True
    if _CHASE_HINT.search(blob):
        return True
    if cls.get("era") == "vintage" and _VINTAGE_KEEP.search(blob):
        return True
    return False


def scan_universe(
    universe: dict[int, dict],
    *,
    names_map: dict,
    sleep_s: float,
    verbose: bool,
    limit: int | None = None,
    existing_series: dict[str, dict] | None = None,
    existing_scan: list[dict] | None = None,
) -> tuple[list[dict], dict[str, dict]]:
    """Fetch apparel + PSA10 history. Skip sealed / EN / non-singles. Do not invent IDs."""
    series: dict[str, dict] = dict(existing_series or {})
    cards = [c for c in (existing_scan or []) if str(c.get("id") or "") in series]
    seen_aids = {int(c["apparelId"]) for c in cards if c.get("apparelId") is not None}
    ids = [aid for aid in sorted(universe.keys()) if aid not in seen_aids]
    if limit:
        ids = ids[:limit]
    scanned = len(seen_aids)
    kept = len(cards)
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
        name = apparel.get("name") or meta.get("name")
        loc = apparel.get("localizedName")
        if is_english_printing(name, loc) or looks_like_sealed(name, loc):
            continue
        cls = classify_card(
            name=name,
            localized=loc,
            product_number=apparel.get("productNumber"),
        )
        if not looks_like_pokemon(name, loc, apparel.get("productNumber"), cls):
            continue
        catalog_id = apparel.get("productCatalogId")
        if not catalog_id:
            continue
        if not cls.get("era") and not cls.get("promo") and not cls.get("midera"):
            continue
        if not _worth_history(name, loc, cls):
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
        if not prints:
            continue
        stats = score_prints(prints)
        cid = str(int(catalog_id))
        image = ((apparel.get("primaryMedia") or {}).get("imageUrl")) or ""
        name_zh = meta.get("nameZh") or match_name_zh(name, loc, names_map)
        liq = liquidity_score(prints, parse_trades(hist), stats.get("lastDay") or ymd(datetime.now(JST).date()))
        row = {
            "id": cid,
            "apparelId": int(aid),
            "catalogId": int(catalog_id),
            "name": name,
            "localizedName": loc,
            "nameZh": name_zh,
            "productNumber": apparel.get("productNumber"),
            "set": meta.get("set") or cls.get("setCode"),
            "setCode": cls.get("setCode"),
            "era": cls.get("era"),
            "promo": cls.get("promo"),
            "midera": cls.get("midera"),
            "image": image,
            "url": f"{SNK}/apparels/{int(aid)}",
            "lastPriceYen": stats["lastPrice"],
            "prints90": stats["prints90"],
            "prints30": stats["prints30"],
            "liquidityYen": round(liq, 1),
            "score": round(pokeca_score(liq, float(stats["lastPrice"] or 0)), 2),
        }
        cards.append(row)
        prev = (series.get(cid) or {}).get("prints") or []
        merged: dict[str, float] = {str(d): float(p) for d, p in prev}
        merged.update({d: p for d, p in prints})
        series[cid] = {
            "prints": sorted(merged.items(), key=lambda x: x[0]),
            "trades": parse_trades(hist) or (series.get(cid) or {}).get("trades") or [],
            "fetchedAt": _now().isoformat(),
        }
        kept += 1
        if verbose:
            print(
                f"cand {cid} {cls.get('era') or '-'}{'P' if cls.get('promo') else ''}"
                f"{'M' if cls.get('midera') else ''} {loc or name} "
                f"¥{stats['lastPrice']:.0f} p90={stats['prints90']}",
                file=sys.stderr,
            )
        if scanned % 40 == 0:
            write_json(SCAN_PATH, {"generated": _now().isoformat(), "candidates": cards})
            write_json(SERIES_PATH, dump_series_cache(series))
            if verbose:
                print(f"scanned {scanned} (+{len(ids)} todo) kept {kept}", file=sys.stderr)
    if verbose:
        print(f"scan finished {scanned} apparel · liquid-enough prints {kept}", file=sys.stderr)
    return cards, series


def _threshold_for(key: str, *, relaxed: bool) -> tuple[float, int]:
    if relaxed:
        return MIN_LAST_PRICE_RELAXED, MIN_PRINTS_90D_RELAXED
    if key == "vintage":
        return MIN_LAST_PRICE_YEN, MIN_PRINTS_90D_VINTAGE
    if key == "promo":
        return MIN_LAST_PRICE_PROMO, MIN_PRINTS_90D_PROMO
    if key == "midera":
        return MIN_LAST_PRICE_YEN, MIN_PRINTS_90D_MIDERA
    return MIN_LAST_PRICE_YEN, MIN_PRINTS_90D


def _passes_card(c: dict, min_price: float, min_prints: int) -> bool:
    return passes_threshold(
        {"lastPrice": c.get("lastPriceYen"), "prints90": c.get("prints90")},
        min_price=min_price,
        min_prints=min_prints,
    )


def _rank_cards(rows: list[dict]) -> list[dict]:
    return sorted(
        rows,
        key=lambda r: (-float(r.get("score") or 0), -float(r.get("lastPriceYen") or 0)),
    )


def _screen_pool(candidates: list[dict], pred, min_price: float, min_prints: int) -> list[dict]:
    return _rank_cards(
        [c for c in candidates if pred(c) and _passes_card(c, min_price, min_prints)]
    )


def _maybe_loosen(
    rows: list[dict],
    candidates: list[dict],
    pred,
    key: str,
    notes: dict[str, Any],
    min_price: float,
    min_prints: int,
) -> list[dict]:
    notes["screens"][key] = {
        "minLastPriceYen": min_price,
        "minPrints90d": min_prints,
        "n": len(rows),
    }
    if len(rows) >= THIN_SCREEN_RELAX_BELOW:
        return rows
    relaxed = _screen_pool(candidates, pred, MIN_LAST_PRICE_RELAXED, MIN_PRINTS_90D_RELAXED)
    notes["loosened"][key] = {
        "reason": (
            f"primary screen yielded {len(rows)} < {THIN_SCREEN_RELAX_BELOW}; "
            "loosened yen/print-day threshold and kept every card that passed "
            "(not a floor — N may still sit below 20)"
        ),
        "minLastPriceYen": MIN_LAST_PRICE_RELAXED,
        "minPrints90d": MIN_PRINTS_90D_RELAXED,
        "primaryN": len(rows),
        "n": len(relaxed),
    }
    notes["screens"][key] = {
        "minLastPriceYen": MIN_LAST_PRICE_RELAXED,
        "minPrints90d": MIN_PRINTS_90D_RELAXED,
        "n": len(relaxed),
        "loosened": True,
        "primaryN": len(rows),
        "primaryMinLastPriceYen": min_price,
        "primaryMinPrints90d": min_prints,
    }
    return relaxed


def select_baskets(candidates: list[dict]) -> dict:
    """Liquidity/price screens only. N floats. No floor, no pad, no truncate.

    Composite is the union of screened VINTAGE + MODERN + CURRENT.
    PROMO / MIDERA are sibling series with the same no-size-target rule.
    """
    candidates = [
        c
        for c in candidates
        if not is_english_printing(c.get("name"), c.get("localizedName"))
        and not looks_like_sealed(c.get("name"), c.get("localizedName"))
        and looks_like_pokemon(c.get("name"), c.get("localizedName"), c.get("productNumber"))
    ]
    notes: dict[str, Any] = {
        "screens": {},
        "loosened": {},
        "compositeRule": "union of screened vintage + modern + current",
        "peers": PEER_SIZE_NOTES,
    }

    sectors: dict[str, list[dict]] = {}
    for era in ("vintage", "modern", "current"):
        price, prints = _threshold_for(era, relaxed=False)

        def pred(c: dict, era=era) -> bool:
            return c.get("era") == era and not c.get("promo")

        rows = _screen_pool(candidates, pred, price, prints)
        sectors[era] = _maybe_loosen(rows, candidates, pred, era, notes, price, prints)

    composite = sectors["vintage"] + sectors["modern"] + sectors["current"]
    notes["screens"]["composite"] = {
        "rule": "union",
        "n": len(composite),
        "softPeerBand": "300-500 (Pokeca ~489 / Grade10 PTCG500); not enforced",
    }

    def sibling(flag: str, key: str) -> list[dict]:
        price, prints = _threshold_for(key, relaxed=False)

        def pred(c: dict, flag=flag) -> bool:
            return bool(c.get(flag))

        rows = _screen_pool(candidates, pred, price, prints)
        return _maybe_loosen(rows, candidates, pred, key, notes, price, prints)

    promo = sibling("promo", "promo")
    midera = sibling("midera", "midera")

    return {
        "vintage": sectors["vintage"],
        "modern": sectors["modern"],
        "current": sectors["current"],
        "composite": composite,
        "promo": promo,
        "midera": midera,
        "notes": notes,
    }


def slim_card(c: dict) -> dict:
    return {
        "id": c["id"],
        "apparelId": c.get("apparelId"),
        "catalogId": c.get("catalogId"),
        "name": c.get("name"),
        "localizedName": c.get("localizedName"),
        "nameZh": c.get("nameZh"),
        "productNumber": c.get("productNumber"),
        "set": c.get("set"),
        "setCode": c.get("setCode"),
        "era": c.get("era"),
        "promo": c.get("promo"),
        "midera": c.get("midera"),
        "image": c.get("image"),
        "url": c.get("url"),
    }


def baskets_doc(picks: dict, generated: str, scanned: int, reconstituted_on: str) -> dict:
    counts = {k: len(picks.get(k) or []) for k in SERIES_META}
    return {
        "version": 1,
        "generated": generated,
        "reconstitutedOn": reconstituted_on,
        "cadence": {
            "weights": "daily (prior-day liquidity / lagged price)",
            "membership": "quarterly on Jan/Apr/Jul/Oct 1 JST, plus --rebuild-baskets",
            "nextReconstitution": next_reconstitution(parse_ymd(reconstituted_on)),
        },
        "source": (
            "SNKRDUNK public search + apparel IDs already in data.json / chase baskets. "
            "IDs are discovered, never invented."
        ),
        "rule": (
            "Japanese Pokémon TCG singles with public SNKRDUNK PSA10 trading-history. "
            "Era sectors VINTAGE (pre-SM, ~1996–2016) / MODERN (SM + S, incl. Tag Team in-sector) / "
            "CURRENT (SV + MEGA) keep every card that clears the liquidity/price screen. "
            "No per-sector floor, no pad-to-N, no truncate-to-N. "
            "Composite is the union of the three screened sectors. "
            "PROMO and MID/Tag Team are parallel sibling series, same screen rule and weight math. "
            "market-jp-psa10.html redirects to this benchmark; it is not a live sleeve."
        ),
        "selection": {
            "sectorFloor": None,
            "sectorCap": None,
            "compositeRule": "union of screened vintage + modern + current",
            "softCompositePeerBand": "300-500 (not enforced)",
            "thinScreenRelaxBelow": THIN_SCREEN_RELAX_BELOW,
            "minLastPriceYen": MIN_LAST_PRICE_YEN,
            "minPrints90d": MIN_PRINTS_90D,
            "minPrints90dVintage": MIN_PRINTS_90D_VINTAGE,
            "minLastPricePromoYen": MIN_LAST_PRICE_PROMO,
            "minPrints90dPromo": MIN_PRINTS_90D_PROMO,
            "minPrints90dMidera": MIN_PRINTS_90D_MIDERA,
            "minLastPriceRelaxedYen": MIN_LAST_PRICE_RELAXED,
            "minPrints90dRelaxed": MIN_PRINTS_90D_RELAXED,
            "weightCap": WEIGHT_CAP,
            "liquidityWindowDays": LIQUIDITY_WINDOW_DAYS,
            "maxPrintGapDays": MAX_PRINT_GAP_DAYS,
            "returnClip": RETURN_CLIP,
            "minConstituents": MIN_CONSTITUENTS,
            "scannedApparelIds": scanned,
            "conditionCode": CONDITION_PSA10,
            "peers": PEER_SIZE_NOTES,
            "notes": picks.get("notes") or {},
        },
        "counts": counts,
        "cards": {k: [slim_card(c) for c in (picks.get(k) or [])] for k in SERIES_META},
    }


def publish_one(cards: list[dict], series: dict[str, dict], *, min_constituents: int) -> dict:
    built = build_index(cards, series, min_constituents=min_constituents)
    points = built["points"]
    if not points:
        raise RuntimeError("index produced no points — not enough liquid PSA10 prints")
    as_of = points[-1]["date"]
    prev = points[-2] if len(points) >= 2 else None
    fields = ("equal", "liquidity", "price", "priceNoCap")
    rets = {f: period_returns(points, as_of, f) for f in fields}
    last = points[-1]
    movers = {"gainers": [], "losers": [], "breadth": {"available": False}}
    if prev:
        prev_day = prev["date"]
        days = sorted({p["date"] for p in points})
        calendar_prices_prev = {}
        calendar_prices_now = built.get("lastPrices") or {}
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

    return {
        "built": built,
        "points": points,
        "returns": rets,
        "asOfDate": as_of,
        "indexes": {f: pack_index(f) for f in fields},
        "volume": last["volume"],
        "constituentCount": last["count"],
        "pricedToday": last["volume"],
        "breadth": movers["breadth"],
        "gainers": movers["gainers"],
        "losers": movers["losers"],
        "constituents": constituents_payload(cards, built, series, as_of),
        "baseDate": built["baseDate"],
        "baseValue": BASE_INDEX_VALUE,
    }


def publish_all(
    picks: dict,
    series: dict[str, dict],
    *,
    generated: str,
    source: str,
    min_constituents: int = MIN_CONSTITUENTS,
    baskets_meta: dict | None = None,
) -> tuple[dict, dict]:
    series_out = {}
    hist_series = {}
    as_of = None
    for key in SERIES_META:
        cards = picks.get(key) or []
        if len(cards) < 3:
            continue
        mc = min(min_constituents, max(3, len(cards) // 2))
        packed = publish_one(cards, series, min_constituents=mc)
        meta = SERIES_META[key]
        series_out[key] = {
            "key": key,
            "labelZh": meta["labelZh"],
            "labelEn": meta["labelEn"],
            "kind": meta["kind"],
            "asOfDate": packed["asOfDate"],
            "indexes": packed["indexes"],
            "volume": packed["volume"],
            "constituentCount": packed["constituentCount"],
            "pricedToday": packed["pricedToday"],
            "breadth": packed["breadth"],
            "gainers": packed["gainers"],
            "losers": packed["losers"],
            "constituents": packed["constituents"],
            "baseDate": packed["baseDate"],
            "baseValue": packed["baseValue"],
        }
        hist_series[key] = {
            "baseDate": packed["baseDate"],
            "baseValue": BASE_INDEX_VALUE,
            "points": packed["points"],
            "returns": packed["returns"],
        }
        as_of = packed["asOfDate"]
    if not series_out:
        raise RuntimeError("no series produced points")
    headline = series_out.get("composite") or next(iter(series_out.values()))
    latest = {
        "generated": generated,
        "asOfDate": as_of,
        "headline": "composite" if "composite" in series_out else headline["key"],
        "priceSource": (
            "SNKRDUNK JP public trading-history chart "
            f"(condition_code={CONDITION_PSA10})"
        ),
        "universe": (
            "Japanese Pokémon TCG PSA10 slabs on snkrdunk.com; broad discovered "
            "universe (search + data.json), not the Box EV chase-only 60"
        ),
        "vsChase": (
            "market-jp-psa10.html redirects here. This page is the representable "
            "JP PSA10 benchmark."
        ),
        "staleDays": STALE_DAYS,
        "divisorCap": WEIGHT_CAP,
        "weightCap": WEIGHT_CAP,
        "weightCapNote": (
            "Liquidity-weight and price-weight cap each card at 3% after "
            "renormalization. priceNoCap is the same lagged-yen share with no cap "
            "(overlay only). Equal-weight is 1/n. If 1/n ≥ 3% the cap is infeasible."
        ),
        "returnClip": RETURN_CLIP,
        "maxPrintGapDays": MAX_PRINT_GAP_DAYS,
        "baseValue": BASE_INDEX_VALUE,
        "cadence": (baskets_meta or {}).get("cadence"),
        "counts": {k: (series_out.get(k) or {}).get("constituentCount") for k in SERIES_META},
        "indexes": headline["indexes"],
        "volume": headline["volume"],
        "constituentCount": headline["constituentCount"],
        "pricedToday": headline["pricedToday"],
        "breadth": headline["breadth"],
        "series": series_out,
        "source": source,
        "sourceDocs": f"{SNK}/",
        "methodology": {
            "price": "SNKRDUNK completed-sales chart print for PSA10 (not Grade A ask)",
            "missingDays": "Per-card last observation carried forward; never fill ¥0",
            "index": (
                f"Chained daily returns from LOCF prices; first day with "
                f"≥{MIN_CONSTITUENTS} names = {int(BASE_INDEX_VALUE):,}"
            ),
            "equal": "Arithmetic mean of available card returns (site headline; not Pokeca Jevons)",
            "liquidity": (
                f"Weight ∝ SNK last-20 PSA10 sold yen in the prior {LIQUIDITY_WINDOW_DAYS}d "
                "as of the previous JST day; if none, sum of PSA10 chart-print yen. 3% cap."
            ),
            "priceWeight": "Lagged yen price / sum of lagged yen prices, then 3% cap",
            "priceNoCap": "Same lagged yen share, no 3% cap — overlay only",
            "outliers": (
                f"Print-to-print gap > {MAX_PRINT_GAP_DAYS}d is a re-entry (0 return, rebase). "
                f"Remaining card-day returns winsorized at ±{int(RETURN_CLIP * 100)}%"
            ),
            "volume": "Count of basket cards with a PSA10 chart print that JST calendar day",
            "timezone": "Asia/Tokyo",
            "reconstitution": "Quarterly membership (Jan/Apr/Jul/Oct 1 JST); daily weight rebalance",
            "sectors": (
                "VINTAGE pre-SM (~1996–2016: XY/BW/DP/…); MODERN SM+S (Tag Team lives here too); "
                "CURRENT SV+MEGA. N floats from yen/print-day screens (no floor, no cap). "
                "Composite = union of the three sectors. PROMO and MID/Tag Team are sibling series."
            ),
        },
    }
    history = {
        "generated": generated,
        "source": source,
        "sourceDocs": f"{SNK}/",
        "weightCap": WEIGHT_CAP,
        "returnClip": RETURN_CLIP,
        "maxPrintGapDays": MAX_PRINT_GAP_DAYS,
        "series": hist_series,
    }
    return latest, history


def write_outputs(latest: dict, history: dict, baskets: dict | None, series: dict | None, universe: dict | None = None, scan: list | None = None) -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    write_json(LATEST_PATH, latest)
    write_js(LATEST_JS_PATH, "MARKET_JP_PSA10_BENCH", latest)
    write_json(HISTORY_PATH, history)
    write_js(HISTORY_JS_PATH, "MARKET_JP_PSA10_BENCH_HISTORY", history)
    if baskets is not None:
        write_json(BASKETS_PATH, baskets)
    if series is not None:
        write_json(SERIES_PATH, dump_series_cache(series))
    if universe is not None:
        write_json(
            UNIVERSE_PATH,
            {
                "generated": latest.get("generated"),
                "count": len(universe),
                "ids": sorted(int(k) for k in universe),
            },
        )
    if scan is not None:
        write_json(SCAN_PATH, {"generated": latest.get("generated"), "candidates": scan})


def refresh_series(cards: list[dict], cache: dict[str, dict], *, sleep_s: float, verbose: bool) -> dict[str, dict]:
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


def unique_basket_cards(baskets: dict) -> list[dict]:
    cards_by: dict[str, dict] = {}
    blob = baskets.get("cards") or baskets
    for key in SERIES_META:
        for c in blob.get(key) or []:
            cards_by[str(c["id"])] = c
    return list(cards_by.values())


def picks_from_baskets(baskets: dict) -> dict:
    blob = baskets.get("cards") or {}
    return {k: list(blob.get(k) or []) for k in SERIES_META}


def self_test() -> None:
    assert ms_to_ymd_jst(1702134000000) == "2023-12-10"
    bulky = {f"n{i}": 1.0 for i in range(39)}
    bulky["star"] = 90.0
    capped = cap_weights(bulky, cap=0.03)
    assert abs(capped["star"] - 0.03) < 1e-9, capped["star"]
    nocap = cap_weights(bulky, cap=1.0)
    assert nocap["star"] > 0.5, nocap["star"]
    assert quarter_start(parse_ymd("2026-10-20")) == parse_ymd("2026-10-01")
    assert reconstitution_due("2026-07-01", parse_ymd("2026-10-20")) is True
    assert reconstitution_due("2026-10-01", parse_ymd("2026-10-20")) is False
    assert reconstitution_due(None, parse_ymd("2026-10-20")) is True

    cls_v = classify_card(name='Giratina EX SR :1ED [XY7 091/081](Expansion Pack "Bandit Ring")')
    assert cls_v["era"] == "vintage" and cls_v["setCode"] == "XY7", cls_v
    cls_tt = classify_card(name='Pikachu & Zekrom GX RR [SM12a 041/173](High Class Pack "TAG TEAM GX Tag All Stars")')
    assert cls_tt["era"] == "modern" and cls_tt["midera"] is True, cls_tt
    cls_cur = classify_card(name='Charizard ex SAR [SV2a 201/165](Enhanced Expansion Pack "Pokemon Card 151")')
    assert cls_cur["era"] == "current", cls_cur
    cls_m = classify_card(name='MEGA Charizard X ex MA [M2a 223/193](High Class Pack "MEGA Dream ex")')
    assert cls_m["era"] == "current", cls_m
    cls_s = classify_card(name='Vaporeon CHR[s8b 189/184](High Class Pack"VMAX Climax")')
    assert cls_s["era"] == "modern" and cls_s["setCode"] == "S8B", cls_s
    cls_p = classify_card(name="Mario Pikachu: PROMO[XY-P 293/XY-P](XY-P Promotional cards)")
    assert cls_p["promo"] is True and cls_p["era"] == "vintage", cls_p
    cls_hat = classify_card(name="Pikachu with Grey Felt Hat: PROMO [SV-P 085]")
    assert cls_hat["promo"] is True
    assert is_english_printing("Pikachu with Grey Felt Hat: PROMO [SVP EN 085]", None)
    assert is_english_printing("Pikachu P [SV-P 153] [CN] (Promotional Cards)", None)
    assert not is_english_printing("リザードンex SAR [SV2a 201/165]", "リザードンex")
    assert looks_like_sealed('Pokemon Card Game Scarlet & Violet High Class Pack "Terastal Festival ex" Box')
    assert looks_like_sealed(
        "Pokemon Card Game YU NAGABA x Pokemon Card Game Eevees Promotional Pack Complete Set: PROMO [SV-P ]",
        "ポケモンカードゲーム YU NAGABA × ポケモンカードゲーム イーブイズ 全種セット",
    )
    assert looks_like_pokemon("Mario Pikachu: PROMO[XY-P 293/XY-P](XY-P Promotional cards)")
    assert not looks_like_pokemon(
        "Son Goku SR :Special Memorial [FS11-07]",
        "孫悟空 SR★ :特別記念品 [FS11-07](プロモーションカード「アルティメットバトル 2026 vol.2」)",
    )
    assert extract_set_code("foo [SV8a 217/187] bar") == "SV8A"

    fixture = load_json(TESTDATA, None)
    assert fixture and fixture.get("cards"), "missing testdata"
    cards, series = series_from_fixture_cards(fixture["cards"])
    scored = [
        {
            **c,
            "lastPriceYen": (series[c["id"]]["prints"] or [[None, 0]])[-1][1],
            "prints90": len(series[c["id"]]["prints"]),
            "prints30": len(series[c["id"]]["prints"]),
            "score": pokeca_score(10000, (series[c["id"]]["prints"] or [[None, 1]])[-1][1]),
        }
        for c in cards
    ]
    picks = select_baskets(scored)
    assert len(picks["vintage"]) >= 3, {k: len(picks[k]) for k in SERIES_META}
    assert len(picks["modern"]) >= 3
    assert len(picks["current"]) >= 3
    assert picks["promo"], "expected promo sibling"
    assert picks["midera"], "expected midera sibling"
    assert len(picks["composite"]) == (
        len(picks["vintage"]) + len(picks["modern"]) + len(picks["current"])
    )
    assert "SECTOR_FLOOR" not in globals()
    assert "PROMO_TARGET" not in globals()
    assert "MIDERA_TARGET" not in globals()
    assert "TARGET_COMPOSITE" not in globals()

    def _jp(i: int, era: str, price: float, prints: int, **extra: Any) -> dict:
        name = {
            "vintage": 'Giratina EX SR :1ED [XY7 091/081](Expansion Pack "Bandit Ring")',
            "modern": 'Vaporeon CHR[s8b 189/184](High Class Pack "VMAX Climax")',
            "current": 'Charizard ex SAR [SV2a 201/165](Enhanced Expansion Pack "Pokemon Card 151")',
        }[era]
        return {
            "id": f"{era}-{i}",
            "name": name,
            "localizedName": name.split()[0],
            "era": era,
            "promo": False,
            "midera": False,
            "lastPriceYen": price,
            "prints90": prints,
            "score": pokeca_score(10000, price),
            **extra,
        }

    fat_modern = [_jp(i, "modern", 8000 + i, 8) for i in range(90)]
    fat_picks = select_baskets(scored + fat_modern)
    assert len(fat_picks["modern"]) == 90 + len(picks["modern"]), len(fat_picks["modern"])
    assert len(fat_picks["modern"]) != 80

    thin_v = [_jp(1, "vintage", 9000, 6), _jp(2, "vintage", 9000, 6)]
    thin_picks = select_baskets(thin_v + scored)
    # Fixture already has 3 vintage that pass; adding 2 more must keep all 5 — never pad to 80.
    assert len(thin_picks["vintage"]) == len(picks["vintage"]) + 2
    assert len(thin_picks["vintage"]) < 80

    only_thin = [
        _jp(1, "vintage", 5000, 6),
        _jp(2, "vintage", 4000, 3),
        _jp(3, "vintage", 4000, 3),
        _jp(4, "vintage", 4000, 3),
        _jp(5, "vintage", 4000, 3),
        _jp(1, "modern", 8000, 8),
        _jp(1, "current", 8000, 8),
    ]
    loosened = select_baskets(only_thin)
    assert loosened["notes"]["loosened"].get("vintage"), loosened["notes"]
    assert loosened["notes"]["screens"]["vintage"]["n"] == 5
    assert len(loosened["vintage"]) == 5
    assert len(loosened["composite"]) == 7

    sealed_promo = {
        "id": "202503",
        "name": "Pokemon Card Game YU NAGABA x Pokemon Card Game Eevees Promotional Pack Complete Set: PROMO [SV-P ]",
        "localizedName": "ポケモンカードゲーム YU NAGABA × ポケモンカードゲーム イーブイズ 全種セット",
        "promo": True,
        "era": "current",
        "lastPriceYen": 80000,
        "prints90": 40,
        "score": 9e9,
    }
    picks_sealed = select_baskets(scored + [sealed_promo])
    assert all(c["id"] != "202503" for c in picks_sealed["promo"]), "Complete Set must not enter PROMO"
    goku_promo = {
        "id": "892234",
        "name": "Son Goku SR :Special Memorial [FS11-07]",
        "localizedName": "孫悟空 SR★ :特別記念品 [FS11-07](プロモーションカード「アルティメットバトル 2026 vol.2」)",
        "promo": True,
        "era": None,
        "lastPriceYen": 90000,
        "prints90": 40,
        "score": 9e9,
    }
    picks_goku = select_baskets(scored + [goku_promo])
    assert all(c["id"] != "892234" for c in picks_goku["promo"]), "non-Pokémon TCG must not enter PROMO"
    built = build_index(picks["composite"], series, cap=0.5, min_constituents=3, window_days=30)
    pts = built["points"]
    assert pts, "expected points"
    assert pts[0]["equal"] == 1000.0
    assert "priceNoCap" in pts[0]
    latest, history = publish_all(
        picks, series, generated="test", source="fixture", min_constituents=3
    )
    assert "composite" in latest["series"]
    assert latest["series"]["composite"]["indexes"]["equal"]["index"] == history["series"]["composite"]["points"][-1]["equal"]
    assert "priceNoCap" in latest["series"]["composite"]["indexes"]
    # Overlay must be allowed to diverge from capped price (fixture has a fat name).
    assert latest["series"]["composite"]["indexes"]["priceNoCap"]["index"] > 0
    print("refresh_market_jp_psa10_bench self-test ok")


def seed_universe() -> dict[int, dict]:
    parts = []
    data_obj = load_json(DATA_JSON_PATH, {})
    parts.append(apparel_universe_from_data(data_obj))
    chase = load_json(CHASE_BASKETS, {})
    chase_ids: dict[int, dict] = {}
    for c in chase.get("cards") or []:
        try:
            aid = int(c.get("apparelId"))
        except (TypeError, ValueError):
            continue
        chase_ids[aid] = {
            "apparelId": aid,
            "name": c.get("name"),
            "nameZh": c.get("nameZh"),
            "set": c.get("set"),
            "url": c.get("url") or f"{SNK}/apparels/{aid}",
            "source": "chase-60",
        }
    parts.append(chase_ids)
    prev = load_json(UNIVERSE_PATH, {})
    prev_ids: dict[int, dict] = {}
    for aid in prev.get("ids") or []:
        try:
            prev_ids[int(aid)] = {"apparelId": int(aid), "source": "universe.json", "url": f"{SNK}/apparels/{int(aid)}"}
        except (TypeError, ValueError):
            continue
    parts.append(prev_ids)
    return merge_universe(*parts)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument(
        "--reconstitution-due",
        action="store_true",
        help="Print yes/no whether quarterly membership rebuild is due (always exit 0)",
    )
    ap.add_argument("--rebuild-baskets", action="store_true")
    ap.add_argument("--confirm", default="", help="Type REBUILD to rewrite baskets.json")
    ap.add_argument("--fixture", action="store_true", help="Build from testdata (no network)")
    ap.add_argument("--from-cache", action="store_true", help="Rebuild latest/history from series-cache (no network)")
    ap.add_argument(
        "--reselect",
        action="store_true",
        help="Re-rank baskets from scan-cache + series-cache (no network)",
    )
    ap.add_argument("--discover-only", action="store_true", help="Run SNK search, write universe.json, exit")
    ap.add_argument("--limit", type=int, default=0, help="Scan at most N apparel IDs (debug)")
    ap.add_argument("--limit-queries", type=int, default=0, help="Run at most N search queries (debug)")
    ap.add_argument("--skip-search", action="store_true", help="Rebuild from existing universe + data.json only")
    ap.add_argument("-v", "--verbose", action="store_true")
    ap.add_argument("--sleep", type=float, default=SLEEP_S)
    args = ap.parse_args(argv)
    if args.self_test:
        self_test()
        return 0
    if args.reconstitution_due:
        baskets = load_json(BASKETS_PATH, {}) or {}
        due = reconstitution_due(baskets.get("reconstitutedOn"))
        print("yes" if due else "no")
        return 0

    generated = _now().isoformat()
    names_map = load_json(NAMES_ZH_PATH, {})

    if args.reselect:
        scan = load_json(SCAN_PATH, {})
        candidates = list(scan.get("candidates") or [])
        series = load_series_cache(SERIES_PATH)
        if not candidates or not series:
            print("ERROR: --reselect needs scan-cache.json and series-cache.json", file=sys.stderr)
            return 2
        picks = select_baskets(candidates)
        missing = [c for k in SERIES_META for c in picks[k] if str(c["id"]) not in series]
        if missing:
            uniq = {str(c["id"]): c for c in missing}
            print(f"reselect fetching {len(uniq)} missing series", file=sys.stderr)
            series = refresh_series(list(uniq.values()), series, sleep_s=args.sleep, verbose=args.verbose)
        still_missing = {c["id"] for k in SERIES_META for c in picks[k] if str(c["id"]) not in series}
        if still_missing:
            print(
                f"WARN: dropping {len(still_missing)} screened cards with no PSA10 series cache",
                file=sys.stderr,
            )
            picks["notes"]["droppedMissingSeries"] = sorted(still_missing)
            for key in SERIES_META:
                picks[key] = [c for c in picks[key] if c["id"] not in still_missing]
        reconstituted = ymd(datetime.now(JST).date())
        uni = load_json(UNIVERSE_PATH, {})
        scanned = int(uni.get("count") or len(uni.get("ids") or []))
        baskets = baskets_doc(picks, generated, scanned=scanned, reconstituted_on=reconstituted)
        latest, history = publish_all(
            picks, series, generated=generated, source="snkrdunk-reselect", baskets_meta=baskets
        )
        write_outputs(latest, history, baskets, series, scan=candidates)
        print(f"reselected { {k: len(picks[k]) for k in SERIES_META} } · composite equal {latest['series']['composite']['indexes']['equal']['index']} @ {latest['asOfDate']}")
        return 0

    if args.fixture:
        fixture = load_json(TESTDATA, None)
        cards, series = series_from_fixture_cards(fixture["cards"])
        for c in cards:
            prints = series[c["id"]]["prints"]
            c["lastPriceYen"] = prints[-1][1] if prints else 0
            c["prints90"] = len(prints)
            c["prints30"] = len(prints)
            c["score"] = pokeca_score(8000, float(c["lastPriceYen"] or 0))
        picks = select_baskets(cards)
        baskets = baskets_doc(picks, generated, scanned=len(cards), reconstituted_on=ymd(datetime.now(JST).date()))
        latest, history = publish_all(picks, series, generated=generated, source="fixture", min_constituents=3, baskets_meta=baskets)
        write_outputs(latest, history, baskets, series)
        print(f"wrote fixture composite {latest['series']['composite']['indexes']['equal']['index']} @ {latest['asOfDate']}")
        return 0

    if args.discover_only:
        uni = harvest_search_ids(
            sleep_s=args.sleep,
            verbose=args.verbose,
            limit_queries=args.limit_queries or None,
        )
        uni = merge_universe(seed_universe(), uni)
        write_json(
            UNIVERSE_PATH,
            {"generated": generated, "count": len(uni), "ids": sorted(uni)},
        )
        print(f"wrote {len(uni)} apparel IDs to {UNIVERSE_PATH}")
        return 0

    if args.rebuild_baskets:
        if args.confirm != "REBUILD":
            print("ERROR: --rebuild-baskets requires --confirm REBUILD (refuses to invent a basket).", file=sys.stderr)
            return 2
        universe = seed_universe()
        if not args.skip_search:
            discovered = harvest_search_ids(
                sleep_s=args.sleep,
                verbose=args.verbose,
                limit_queries=args.limit_queries or None,
            )
            universe = merge_universe(universe, discovered)
        if not universe:
            print("ERROR: empty universe after discovery", file=sys.stderr)
            return 2
        cache = load_series_cache(SERIES_PATH)
        prev_scan = load_json(SCAN_PATH, {})
        existing_scan = list(prev_scan.get("candidates") or []) if isinstance(prev_scan, dict) else []
        candidates, series = scan_universe(
            universe,
            names_map=names_map,
            sleep_s=args.sleep,
            verbose=args.verbose,
            limit=args.limit or None,
            existing_series=cache,
            existing_scan=existing_scan,
        )
        if not candidates:
            print("ERROR: no PSA10 candidates", file=sys.stderr)
            return 2
        picks = select_baskets(candidates)
        for era in ("vintage", "modern", "current", "promo", "midera"):
            n = len(picks[era])
            if n < THIN_SCREEN_RELAX_BELOW:
                print(
                    f"WARN: {era} has {n} names after screens "
                    f"(thin-relax threshold is {THIN_SCREEN_RELAX_BELOW}, not a floor)",
                    file=sys.stderr,
                )
        keep_ids = {c["id"] for k in SERIES_META for c in picks[k]}
        series = {k: v for k, v in series.items() if k in keep_ids}
        reconstituted = ymd(datetime.now(JST).date())
        baskets = baskets_doc(picks, generated, scanned=len(universe), reconstituted_on=reconstituted)
        latest, history = publish_all(
            picks, series, generated=generated, source="snkrdunk", baskets_meta=baskets
        )
        write_outputs(
            latest,
            history,
            baskets,
            series,
            universe=universe,
            scan=candidates,
        )
        counts = {k: len(picks[k]) for k in SERIES_META}
        print(f"froze {counts} · composite equal {latest['series']['composite']['indexes']['equal']['index']} @ {latest['asOfDate']}")
        return 0

    baskets = load_json(BASKETS_PATH, None)
    if not baskets or not (baskets.get("cards") or {}).get("composite"):
        print(
            "ERROR: missing data/market-jp-psa10-bench/baskets.json. "
            "Run --rebuild-baskets --confirm REBUILD first.",
            file=sys.stderr,
        )
        return 2
    picks = picks_from_baskets(baskets)
    cards = unique_basket_cards(baskets)
    cache = load_series_cache(SERIES_PATH)
    if args.from_cache:
        if not cache:
            print("ERROR: empty series-cache.json", file=sys.stderr)
            return 2
        series = cache
        source = "snkrdunk-cache"
    else:
        series = refresh_series(cards, cache, sleep_s=args.sleep, verbose=args.verbose)
        source = "snkrdunk"
    latest, history = publish_all(picks, series, generated=generated, source=source, baskets_meta=baskets)
    write_outputs(latest, history, None, None if args.from_cache else series)
    eq = latest["series"]["composite"]["indexes"]["equal"]["index"]
    print(
        f"updated composite equal {eq} "
        f"liq {latest['series']['composite']['indexes']['liquidity']['index']} "
        f"px {latest['series']['composite']['indexes']['price']['index']} "
        f"nocap {latest['series']['composite']['indexes']['priceNoCap']['index']} "
        f"@ {latest['asOfDate']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
