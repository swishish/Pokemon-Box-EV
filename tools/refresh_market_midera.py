#!/usr/bin/env python3
"""Daily EN mid-era chase index (Diamond & Pearl → Sun & Moon Top ~50).

One *frozen* price-weighted basket of English raw singles. Not a weekly
rebalance. Index math matches market-en / vintage: no $0 fill, a listed ID
enters on its first priced day, S&P-style divisor, first retained snap = 1,000
(until TickerMint rebases to 2024-02-08).

  python3 tools/refresh_market_midera.py --self-test
  python3 tools/refresh_market_midera.py -v --force
  python3 tools/backfill_tickermint_midera.py --self-test
  python3 tools/backfill_tickermint_midera.py --backfill --confirm BACKFILL

Writes (stdlib only):
  data/market-midera/baskets.json       frozen ID list (source of truth)
  data/market-midera/latest.json        index + constituents + movers
  data/market-midera/history.json       one point per day (append/upsert)
  data/market-midera/price-history.json basket prices by date
  data/market-midera/latest.js          window.MARKET_MIDERA fallback
  data/market-midera/history.js         window.MARKET_MIDERA_HISTORY fallback

Daily job fetches only the TCGCSV groups that hold a frozen ID (~28 sets),
not the whole Pokemon universe. Base 1st / Shadowless / Unlimited stay in
tools/refresh_market_vintage.py — this script does not touch them.
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import refresh_market_en as en  # noqa: E402
import refresh_market_vintage as vin  # noqa: E402

ROOT = os.path.dirname(HERE)
DATA_DIR = os.path.join(ROOT, "data", "market-midera")
BASKETS_PATH = os.path.join(DATA_DIR, "baskets.json")
LATEST_PATH = os.path.join(DATA_DIR, "latest.json")
LATEST_JS_PATH = os.path.join(DATA_DIR, "latest.js")
HISTORY_PATH = os.path.join(DATA_DIR, "history.json")
HISTORY_JS_PATH = os.path.join(DATA_DIR, "history.js")
PRICE_HISTORY_PATH = os.path.join(DATA_DIR, "price-history.json")
EN_LATEST_PATH = os.path.join(ROOT, "data", "market-en", "latest.json")

USER_AGENT = "Pokemon-Box-EV/1.26 (+https://github.com/swishish/Pokemon-Box-EV)"
INDEX_ID = "mid-era-50"
TARGET_SIZE = 50

# Wizards / EX / SWSH / SV / mixed-era junk — never mid-era.
_EXCLUDE_NEEDLES = (
    "sword",
    "shield",
    "scarlet",
    "violet",
    "paldea",
    "obsidian",
    "crown zenith",
    "brilliant stars",
    "astral",
    "lost origin",
    "silver tempest",
    "fusion strike",
    "evolving skies",
    "chilling reign",
    "battle styles",
    "vivid voltage",
    "darkness ablaze",
    "rebel clash",
    "champion's path",
    "shining fates",
    "celebrations",
    "pokemon go",
    "surging sparks",
    "twilight masquerade",
    "destined rivals",
    "prismatic",
    "mega evolution",
    "phantasmal",
    "white flare",
    "black bolt",
    "base set",
    "jungle",
    "fossil",
    "team rocket",
    "gym ",
    "neo ",
    "legendary collection",
    "wotc",
    "expedition",
    "aquapolis",
    "skyridge",
    "southern islands",
    "ex ",
    "nintendo promo",
    "30th",
    "prize pack",
    "world championship",
    "league & championship",
    "alternate art promo",
    "countdown",
    "ascended heroes",
    "jumbo",
    "miscellaneous",
    "pop series 1",
    "pop series 2",
    "pop series 3",
    "pop series 4",
    "pop series 5",
)

# Exact TCGCSV cleaned names (DP → SM).
_MID_ERA_NAMES = frozenset(
    {
        "diamond and pearl",
        "diamond & pearl",
        "diamond and pearl promos",
        "mysterious treasures",
        "secret wonders",
        "great encounters",
        "majestic dawn",
        "legends awakened",
        "stormfront",
        "platinum",
        "rising rivals",
        "supreme victors",
        "arceus",
        "rumble",
        "pop series 6",
        "pop series 7",
        "pop series 8",
        "pop series 9",
        "heartgold soulsilver",
        "heartgold & soulsilver",
        "hgss promos",
        "unleashed",
        "undaunted",
        "triumphant",
        "call of legends",
        "black and white",
        "black & white",
        "black and white promos",
        "emerging powers",
        "noble victories",
        "next destinies",
        "dark explorers",
        "dragons exalted",
        "dragon vault",
        "boundaries crossed",
        "plasma storm",
        "plasma freeze",
        "plasma blast",
        "legendary treasures",
        "legendary treasures: radiant collection",
        "kalos starter set",
        "xy",
        "xy base set",
        "xy promos",
        "double crisis",
        "generations",
        "generations: radiant collection",
        "sun & moon",
        "sun and moon",
        "sm base set",
        "sm promos",
        "shining legends",
        "dragon majesty",
        "detective pikachu",
        "hidden fates",
        "hidden fates: shiny vault",
    }
)

_MID_ERA_PREFIXES = (
    "sm - ",
    "sm base",
    "sm promo",
    "xy - ",
    "xy base",
    "xy promo",
    "hidden fates",
    "shining legends",
    "dragon majesty",
    "detective pikachu",
    "diamond and pearl",
    "heartgold",
    "black and white",
    "legendary treasures",
    "mcdonald's promos 2011",
    "mcdonald's promos 2012",
    "mcdonald's promos 2014",
    "mcdonald's promos 2015",
    "mcdonald's promos 2016",
    "mcdonald's promos 2017",
    "mcdonald's promos 2018",
    "mcdonald's promos 2019",
    "pop series 6",
    "pop series 7",
    "pop series 8",
    "pop series 9",
)


def _ua_get_bytes(url: str, timeout: int = 90) -> bytes:
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


def is_mid_era_set(name: str) -> bool:
    """True for English DP → SM sets (incl. BW / XY / SM)."""
    low = en.clean_set_name(name or "").lower()
    if not low:
        return False
    if any(n in low for n in _EXCLUDE_NEEDLES):
        return False
    if low in _MID_ERA_NAMES:
        return True
    return any(low.startswith(p) or p in low for p in _MID_ERA_PREFIXES)


def load_baskets(path: str = BASKETS_PATH) -> dict:
    raw = en.load_json(path, {})
    if not (raw.get("index") or {}).get("constituents"):
        raise RuntimeError(f"baskets.json missing mid-era constituents: {path}")
    return raw


def basket_rows(baskets: dict) -> list[dict]:
    return list((baskets.get("index") or {}).get("constituents") or [])


def group_names_from_baskets(baskets: dict) -> dict[str, str]:
    spec = baskets.get("index") or {}
    out: dict[str, str] = {}
    for row in spec.get("groups") or []:
        gid = str(row.get("groupId") or "").strip()
        if gid:
            out[gid] = row.get("groupName") or f"group {gid}"
    for row in spec.get("constituents") or []:
        gid = str(row.get("setId") or "").strip()
        if gid and gid not in out:
            out[gid] = row.get("setName") or f"group {gid}"
    return out


def fetch_groups_needed(group_names: dict[str, str], verbose: bool = False) -> tuple[dict, dict, dict]:
    """Fetch only the frozen-ID groups (not the whole EN universe)."""
    catalog: dict = {}
    prices: dict = {}
    subtypes: dict = {}
    for gid in sorted(group_names, key=lambda x: int(x) if str(x).isdigit() else 0):
        url = f"{en.TCGCSV}/tcgplayer/{en.CATEGORY}/{gid}/ProductsAndPrices.csv"
        text = _ua_get_bytes(url).decode("utf-8-sig")
        name = group_names.get(gid) or f"group {gid}"
        en.ingest_group_csv(text, {"groupId": str(gid), "name": name}, catalog, prices, subtypes)
        if verbose:
            n = sum(1 for p in catalog.values() if p.get("setId") == str(gid))
            print(f"  group {gid} ({name}): {n} singles", flush=True)
    return catalog, prices, subtypes


def prices_for_basket(
    catalog_all: dict,
    prices_all: dict,
    subtypes_all: dict,
    rows: list[dict],
) -> tuple[dict, dict, dict, list]:
    """Restrict the live dump to the frozen ID list. Empty market ≠ $0."""
    catalog: dict = {}
    prices: dict = {}
    subtypes: dict = {}
    listed_unpriced: list[dict] = []
    for row in rows:
        pid = str(row.get("id") or "").strip()
        if not pid:
            continue
        live = catalog_all.get(pid)
        meta = {
            "id": pid,
            "name": (live or row).get("name") or row.get("name") or "Unknown",
            "number": (live or row).get("number") or row.get("number") or "",
            "rarity": (live or row).get("rarity") or row.get("rarity") or "",
            "setName": (live or row).get("setName") or row.get("setName") or "",
            "setId": str((live or row).get("setId") or row.get("setId") or ""),
            "image": (live or row).get("image") or row.get("image") or "",
        }
        catalog[pid] = meta
        price = prices_all.get(pid)
        if price and float(price) > 0:
            prices[pid] = round(float(price), 2)
            subtypes[pid] = subtypes_all.get(pid) or ""
        else:
            listed_unpriced.append(
                {
                    "id": pid,
                    "name": meta["name"],
                    "number": meta["number"],
                    "rarity": meta["rarity"],
                    "setName": meta["setName"],
                }
            )
    return catalog, prices, subtypes, listed_unpriced


def _index_spec(baskets: dict) -> dict:
    spec = dict(baskets.get("index") or {})
    spec.setdefault("id", INDEX_ID)
    spec.setdefault("name", "Mid-era Top 50")
    spec.setdefault("nameZh", "中世代 Top 50")
    spec.setdefault(
        "universe",
        "English DP→SM raw / ungraded chase singles (TCGPlayer Market, NM)",
    )
    return spec


def flatten_latest(block: dict, baskets: dict, stamp: str | None, now: datetime, history: dict) -> dict:
    """Public latest.json is EN-flat so market-midera.html can clone market-en."""
    backfill = history.get("backfill") or baskets.get("backfill") or "deferred"
    tickermint = (
        history.get("source") == "tickermint"
        or (isinstance(backfill, dict) and backfill.get("source") == "tickermint")
    )
    price_source = (
        "TCGplayer Market Price (TickerMint history + TCGCSV daily snap)"
        if tickermint
        else "TCGplayer Market Price (via tcgcsv.com daily snapshot)"
    )
    payload = {
        "generated": now.isoformat(),
        "sourceStamp": stamp,
        "asOfDate": block.get("asOfDate"),
        "priceSource": price_source,
        "universe": block.get("universe") or (baskets.get("index") or {}).get("universe"),
        "staleDays": en.STALE_DAYS,
        "id": INDEX_ID,
        "name": block.get("name") or "Mid-era Top 50",
        "nameZh": block.get("nameZh") or "中世代 Top 50",
        "era": "DP-SM",
        "index": block.get("index"),
        "prevIndex": block.get("prevIndex"),
        "change": block.get("change"),
        "changePct": block.get("changePct"),
        "changeLabel": block.get("changeLabel"),
        "divisor": block.get("divisor"),
        "baseValue": en.BASE_INDEX_VALUE,
        "baseDate": block.get("baseDate"),
        "listedCount": block.get("listedCount"),
        "constituentCount": block.get("constituentCount"),
        "totalValue": block.get("totalValue"),
        "pricedToday": block.get("pricedToday"),
        "listedUnpriced": block.get("listedUnpriced") or [],
        "breadth": block.get("breadth"),
        "gainers": block.get("gainers") or [],
        "losers": block.get("losers") or [],
        "top": block.get("top") or [],
        "basket": block.get("basket") or [],
        "constituents": block.get("constituents") or [],
        "movers": block.get("movers") or {},
        "snapshotRange": block.get("snapshotRange"),
        "backfill": backfill,
        "basketRule": baskets.get("rule"),
        "selection": baskets.get("selection"),
    }
    if history.get("archiveStart"):
        payload["archiveStart"] = history["archiveStart"]
    elif tickermint:
        payload["archiveStart"] = en.ARCHIVE_START
    return payload


def write_latest_js(latest: dict) -> None:
    en._write_js(LATEST_JS_PATH, "MARKET_MIDERA", latest)


def write_history_js(history: dict) -> None:
    en._write_js(HISTORY_JS_PATH, "MARKET_MIDERA_HISTORY", history)


def merge_history(prev: dict, latest: dict, now: datetime) -> dict:
    """Append/upsert today. Keep a TickerMint rebase if one is already in place."""
    if not isinstance(prev, dict):
        prev = {}
    merged = en.merge_history_point(prev, latest, now)
    if prev.get("source") == "tickermint" or (
        isinstance(prev.get("backfill"), dict) and prev["backfill"].get("source") == "tickermint"
    ):
        merged["source"] = "tickermint"
        merged["backfill"] = prev.get("backfill")
        for key in ("archiveStart", "optionalStart", "sourceDocs", "sampling"):
            if prev.get(key) is not None:
                merged[key] = prev[key]
        if prev.get("baseDate"):
            merged["baseDate"] = prev["baseDate"]
    else:
        merged.setdefault("backfill", "deferred")
    return merged


def build(verbose: bool = False, force: bool = False) -> None:
    baskets = load_baskets()
    rows = basket_rows(baskets)
    if not rows:
        raise RuntimeError("mid-era basket is empty")
    prev_latest = en.load_json(LATEST_PATH, {})
    stamp = source_stamp()
    if stamp and prev_latest.get("sourceStamp") == stamp and not force:
        print(f"TCGCSV unchanged since {stamp}; mid-era nothing to do.", flush=True)
        return

    group_names = group_names_from_baskets(baskets)
    print(f"Fetching {len(group_names)} mid-era groups from TCGCSV ...", flush=True)
    catalog_all, prices_all, subtypes_all = fetch_groups_needed(group_names, verbose=verbose)
    catalog, prices, subtypes, unpriced = prices_for_basket(
        catalog_all, prices_all, subtypes_all, rows
    )
    if not catalog:
        raise RuntimeError("mid-era basket has no constituent IDs")
    if verbose:
        print(
            f"  listed {len(catalog)} / priced {len(prices)} / unpriced {len(unpriced)}",
            flush=True,
        )

    now = datetime.now(timezone.utc)
    prev_history = en.load_json(HISTORY_PATH, {}) or {}
    spec = _index_spec(baskets)
    block = vin.build_one_snapshot(
        spec,
        catalog,
        prices,
        subtypes,
        unpriced,
        prev_latest,
        prev_history,
        stamp,
        now,
    )
    price_history = en.upsert_price_history(en.load_json(PRICE_HISTORY_PATH, {}), block, now)
    en.attach_period_movers(block, price_history)
    history = merge_history(prev_history, block, now)
    latest = flatten_latest(block, baskets, stamp, now, history)
    latest["movers"] = block.get("movers") or {}
    latest["snapshotRange"] = block.get("snapshotRange")
    latest["gainers"] = block.get("gainers") or []
    latest["losers"] = block.get("losers") or []
    latest["breadth"] = block.get("breadth")

    en.write_json(LATEST_PATH, latest)
    en.write_json(HISTORY_PATH, history)
    en.write_json(PRICE_HISTORY_PATH, price_history)
    write_latest_js(latest)
    write_history_js(history)

    ch = latest.get("changePct")
    ch_s = "n/a (first snapshot)" if ch is None else f"{ch:+.2f}%"
    print(
        f"mid-era asOf {latest.get('asOfDate')} | {latest.get('index'):.2f} ({ch_s}, "
        f"n={latest.get('constituentCount')}/{latest.get('listedCount')})",
        flush=True,
    )


def rebuild_baskets(verbose: bool = False) -> dict:
    """Regenerate baskets.json from the committed EN Top 500 (does not append history).

    Daily refresh never calls this. The frozen list is the product.
    """
    en_latest = en.load_json(EN_LATEST_PATH, {})
    rows = []
    for row in en_latest.get("constituents") or []:
        if not is_mid_era_set(row.get("setName") or ""):
            continue
        price = row.get("price")
        if not price or float(price) <= 0:
            continue
        rows.append(row)
    rows.sort(key=lambda r: float(r.get("price") or 0), reverse=True)
    top = rows[:TARGET_SIZE]
    if len(top) < 40:
        raise RuntimeError(f"mid-era rebuild found only {len(top)} priced names in EN Top 500")
    groups: dict[str, str] = {}
    constituents = []
    for rank, row in enumerate(top, start=1):
        gid = str(row.get("setId") or "")
        if gid:
            groups[gid] = row.get("setName") or f"group {gid}"
        constituents.append(
            {
                "id": str(row.get("id") or ""),
                "number": row.get("number") or "",
                "name": row.get("name") or "Unknown",
                "rarity": row.get("rarity") or "",
                "setName": row.get("setName") or "",
                "setId": gid,
                "image": row.get("image") or "",
                "asOfRank": rank,
                "asOfPrice": round(float(row["price"]), 2),
            }
        )
    now = datetime.now(timezone.utc)
    baskets = {
        "version": 1,
        "generated": now.isoformat(),
        "source": (
            f"Frozen from EN Top 500 TCGCSV snapshot asOf {en_latest.get('asOfDate')} "
            f"({os.path.relpath(EN_LATEST_PATH, ROOT)})"
        ),
        "rule": (
            "Fixed chase basket: English raw singles, Diamond & Pearl through Sun & Moon "
            "(DP / Platinum / HGSS / BW / XY / SM). Exclude Wizards vintage, EX-era, "
            "Sword & Shield, and Scarlet & Violet. Price-weighted S&P-style. Not a weekly "
            "rebalance (unlike a Double Holo Mid-Era 100)."
        ),
        "selection": {
            "method": (
                "Take the committed EN Top 500 (liquid TCGPlayer Market Price names), "
                "keep only mid-era sets (DP→SM inclusive), rank by that snapshot's market "
                "price, freeze the top 50 product IDs."
            ),
            "asOfDate": en_latest.get("asOfDate"),
            "universeRankSource": "data/market-en/latest.json",
            "targetSize": TARGET_SIZE,
            "minAsOfPrice": constituents[-1]["asOfPrice"],
            "maxAsOfPrice": constituents[0]["asOfPrice"],
            "setCount": len(groups),
        },
        "sizeNote": (
            "50 frozen TCGPlayer product IDs. Daily job does not rebuild this list."
        ),
        "backfill": (
            "TickerMint free API via tools/backfill_tickermint_midera.py. Daily job "
            "appends on the rebased divisor after --backfill --confirm BACKFILL."
        ),
        "index": {
            "id": INDEX_ID,
            "name": "Mid-era Top 50",
            "nameZh": "中世代 Top 50",
            "era": "DP-SM",
            "universe": "English DP→SM raw / ungraded chase singles (TCGPlayer Market, NM)",
            "constituentCount": len(constituents),
            "groups": [
                {"groupId": gid, "groupName": groups[gid]}
                for gid in sorted(groups, key=lambda x: int(x) if x.isdigit() else 0)
            ],
            "constituents": constituents,
        },
    }
    en.write_json(BASKETS_PATH, baskets)
    if verbose:
        print(f"rewrote {BASKETS_PATH} n={len(constituents)} sets={len(groups)}", flush=True)
    return baskets


def rebuild_movers(now: datetime | None = None) -> None:
    """Fill Top 10 / Bottom 10 from the last two *priced* weekly snaps.

    Same pairwise path as EN Pulse PR #31: never leave prevPrice null when a
    prior print exists. Does not rebase the index.
    """
    latest = en.load_json(LATEST_PATH, {})
    if not latest.get("constituents"):
        raise RuntimeError("latest.json is missing constituents")
    stamp = now or datetime.now(timezone.utc)
    price_history = en.load_json(PRICE_HISTORY_PATH, {}) or {}
    priced = [p for p in (price_history.get("points") or []) if p.get("prices")]
    if len(priced) >= 2:
        en.apply_pairwise_movers(latest, priced[-2].get("prices") or {})
    en.attach_period_movers(latest, price_history, baseline_strategy="on_or_before")
    latest["generated"] = stamp.isoformat()
    en.write_json(LATEST_PATH, latest)
    write_latest_js(latest)
    movers = latest.get("movers") or {}
    avail = [p for p, info in movers.items() if info.get("available")]
    n_g = len((movers.get("1D") or {}).get("gainers") or latest.get("gainers") or [])
    n_l = len((movers.get("1D") or {}).get("losers") or latest.get("losers") or [])
    print(
        f"mid-era movers rebuilt from last two priced snaps | "
        f"1D +{n_g}/-{n_l} periods={','.join(avail) or 'none'}",
        flush=True,
    )


def self_test() -> None:
    assert is_mid_era_set("SM - Team Up")
    assert is_mid_era_set("Diamond and Pearl")
    assert is_mid_era_set("Black and White Promos")
    assert is_mid_era_set("XY - Flashfire")
    assert is_mid_era_set("Hidden Fates: Shiny Vault")
    assert is_mid_era_set("Call of Legends")
    assert is_mid_era_set("POP Series 9")
    assert not is_mid_era_set("Base Set")
    assert not is_mid_era_set("Base Set (Shadowless)")
    assert not is_mid_era_set("EX Ruby and Sapphire")
    assert not is_mid_era_set("EX Team Rocket Returns")
    assert not is_mid_era_set("Sword & Shield Base Set")
    assert not is_mid_era_set("SWSH01: Sword & Shield Base Set")
    assert not is_mid_era_set("Scarlet & Violet 151")
    assert not is_mid_era_set("POP Series 1")
    assert not is_mid_era_set("Jungle")
    assert not is_mid_era_set("Legendary Collection")

    csv_text = (
        "productId,name,imageUrl,extNumber,extRarity,marketPrice,subTypeName,url\n"
        "A,Latias & Latios GX,http://img,170/181,Ultra Rare,100.00,Holofoil,http://t\n"
        "A,Latias & Latios GX,http://img,170/181,Ultra Rare,999.00,1st Edition Holofoil,http://t\n"
        "B,Gengar & Mimikyu GX,http://img,165/181,Ultra Rare,50.00,Holofoil,http://t\n"
        "C,Charizard GX,http://img,150/147,Rainbow Rare,,Holofoil,http://t\n"
        "S,Booster Box,http://img,,,199.00,Normal,http://t\n"
        "J,Pikachu Jumbo,http://img,058/102,Common,5.00,Normal,http://t\n"
    )
    catalog: dict = {}
    prices: dict = {}
    subtypes: dict = {}
    en.ingest_group_csv(
        csv_text, {"groupId": "2377", "name": "SM - Team Up"}, catalog, prices, subtypes
    )
    assert "A" in catalog and "B" in catalog
    assert "S" not in catalog
    assert "J" not in catalog
    assert prices["A"] == 100.0  # 1st Edition 999 is not the representative
    assert prices["B"] == 50.0
    assert "C" not in prices  # empty market is not $0

    rows = [
        {"id": "A", "name": "Latias & Latios GX", "number": "170/181", "rarity": "Ultra Rare", "setName": "SM - Team Up", "setId": "2377"},
        {"id": "B", "name": "Gengar & Mimikyu GX", "number": "165/181", "rarity": "Ultra Rare", "setName": "SM - Team Up", "setId": "2377"},
        {"id": "C", "name": "Charizard GX", "number": "150/147", "rarity": "Rainbow Rare", "setName": "SM - Burning Shadows", "setId": "1957"},
    ]
    cat, pr, sub, unpriced = prices_for_basket(catalog, prices, subtypes, rows)
    assert pr == {"A": 100.0, "B": 50.0}
    assert [u["id"] for u in unpriced] == ["C"]
    assert cat["C"]["name"] == "Charizard GX"

    spec = {
        "id": INDEX_ID,
        "name": "Mid-era Top 50",
        "nameZh": "中世代 Top 50",
        "universe": "test",
        "constituents": rows,
    }
    fake_now = datetime(2026, 10, 12, 12, 0, tzinfo=timezone.utc)
    snap1 = vin.build_one_snapshot(spec, cat, pr, sub, unpriced, {}, {}, "2026-10-12T20:05:00+0000", fake_now)
    assert snap1["index"] == 1000.0
    assert snap1["constituentCount"] == 2
    assert any(r["id"] == "C" and r["price"] is None for r in snap1["basket"])
    assert snap1["changePct"] is None

    snap2 = vin.build_one_snapshot(
        spec,
        cat,
        {"A": 110.0, "B": 55.0},
        {"A": "Holofoil", "B": "Holofoil"},
        unpriced,
        snap1,
        {"points": [{"date": "2026-10-12", "index": 1000.0}]},
        "2026-10-13T20:05:00+0000",
        datetime(2026, 10, 13, 12, 0, tzinfo=timezone.utc),
    )
    assert abs(snap2["index"] - 1100.0) < 1e-6
    assert snap2["changePct"] == 10.0

    # Previously unpriced C joins at 200. Old basket return must not jump.
    cat3 = {
        **cat,
        "C": {**cat["C"], "setName": "SM - Burning Shadows"},
    }
    snap3 = vin.build_one_snapshot(
        spec,
        cat3,
        {"A": 110.0, "B": 55.0, "C": 200.0},
        {"A": "Holofoil", "B": "Holofoil", "C": "Holofoil"},
        [],
        snap2,
        {"points": [{"date": "2026-10-12", "index": 1000.0}, {"date": "2026-10-13", "index": 1100.0}]},
        "2026-10-14T20:05:00+0000",
        datetime(2026, 10, 14, 12, 0, tzinfo=timezone.utc),
    )
    assert abs(snap3["index"] - 1100.0) < 1e-6
    assert snap3["constituentCount"] == 3

    hist = merge_history({}, snap1, fake_now)
    assert hist["backfill"] == "deferred"
    assert hist["points"][0]["index"] == 1000.0
    assert not hist["returns"]["5D"]["available"]

    seeded = {
        "backfill": {"source": "tickermint", "script": "tools/backfill_tickermint_midera.py"},
        "archiveStart": "2024-02-08",
        "source": "tickermint",
        "baseDate": "2024-02-08",
        "points": [{"date": "2024-02-08", "index": 1000.0, "totalValue": 150.0, "count": 2}],
    }
    hist_tm = merge_history(seeded, snap1, fake_now)
    assert hist_tm["backfill"]["source"] == "tickermint"
    assert hist_tm["archiveStart"] == "2024-02-08"
    assert hist_tm["baseDate"] == "2024-02-08"
    assert [p["date"] for p in hist_tm["points"]] == ["2024-02-08", "2026-10-12"]

    live = load_baskets()
    spec_live = live["index"]
    ids = [c["id"] for c in spec_live["constituents"]]
    assert spec_live["constituentCount"] == len(ids) == len(set(ids))
    assert 45 <= len(ids) <= 55
    for row in spec_live["constituents"]:
        assert is_mid_era_set(row["setName"]), row["setName"]
        assert row["id"]
        assert row["setId"]
    assert "tickermint" in (live.get("backfill") or "").lower()
    assert "2024-02-08" in (live.get("backfill") or "")
    assert not any(is_mid_era_set(n) is False for n in ("SM - Team Up",))

    baskets_env = {"index": spec, "rule": "test", "selection": {"targetSize": 3}}
    flat = flatten_latest(snap1, baskets_env, "2026-10-12T20:05:00+0000", fake_now, hist)
    assert flat["index"] == 1000.0
    assert flat["id"] == INDEX_ID
    assert len(flat["constituents"]) == 2

    print("self-test ok")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-test", action="store_true", help="Run index-math checks and exit")
    parser.add_argument("--force", action="store_true", help="Rebuild even if TCGCSV stamp is unchanged")
    parser.add_argument(
        "--rebuild-baskets",
        action="store_true",
        help="Regenerate baskets.json from committed EN Top 500 (does not append history)",
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
