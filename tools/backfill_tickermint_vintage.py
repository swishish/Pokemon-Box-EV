#!/usr/bin/env python3
"""Backfill Base Set vintage indexes from the TickerMint free API.

Do **not** use tools/backfill_tickermint.py (market-en) for these indexes.
That script picks a “main” printing and skips 1st Edition, so 1st would
silently take Shadowless (TickerMint `Unlimited` / `Unlimited Holofoil`)
prices. This script reads printing filters from vintage baskets.json.

  python3 tools/backfill_tickermint_vintage.py --self-test
  python3 tools/backfill_tickermint_vintage.py --sample
  python3 tools/backfill_tickermint_vintage.py --backfill --confirm BACKFILL

TickerMint (non-commercial, 120 req/min, no key):
  GET https://api.tickermint.cards/products/{tcgplayerId}/prices
  Docs: https://tickermint.cards/developers/pokemon
  Linkback: https://tickermint.cards/card/{id}

Printing map (from data/market-vintage/baskets.json — verify there):
  base-1st         BSS group 1663  include “1st edition”
  base-shadowless  same BSS ids    include “unlimited”  (TickerMint label for Shadowless)
  base-unlimited   group 604 ids   exclude “1st edition”  (Holofoil / Normal)

1st and Shadowless share product IDs, so unique API calls are ~62 not 93.
Same cache dir as market-en (data/tickermint-cache/, gitignored).

Default start = 2024-02-08 (TickerMint free TCGplayer history begin).
Optional later cut: --start 2024-02-28.
Default sampling is weekly (--step-days 7, ~140 points/index).
Never fills missing days with $0. A card enters its index on the first
day that *this printing* has a positive market. S&P-style divisor.
Rebase each index to 1,000 on the first retained day (fixed historical
rebase, not per-period). --backfill --confirm BACKFILL is required to
rewrite live history.json + latest.json divisors so today's level moves
off the temporary Oct-2026 = 1,000 series.

--sample fetches a few BSS + Unlimited IDs (live) and writes nothing.
First full run is ~62 HTTP calls (~1 min at 120 req/min).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import backfill_tickermint as tm  # noqa: E402
import fetch_tcgcsv_archive as arch  # noqa: E402
import refresh_market_en as en  # noqa: E402
import refresh_market_vintage as vin  # noqa: E402

FIXTURE_DIR = os.path.join(HERE, "testdata", "tickermint-vintage")
SCRIPT_NAME = "tools/backfill_tickermint_vintage.py"


def unique_product_ids(
    baskets: dict,
    *,
    index_filter: list[str] | None = None,
) -> list[str]:
    """Deduped TCGPlayer IDs across vintage indexes (1st+Shadowless share BSS)."""
    seen: list[str] = []
    have: set[str] = set()
    for iid in vin.index_ids(baskets):
        if index_filter and iid not in index_filter:
            continue
        spec = (baskets.get("indexes") or {}).get(iid) or {}
        for row in spec.get("constituents") or []:
            pid = str(row.get("id") or "").strip()
            if pid and pid not in have:
                have.add(pid)
                seen.append(pid)
    return seen


def catalog_from_spec(spec: dict) -> dict:
    catalog: dict = {}
    for row in spec.get("constituents") or []:
        pid = str(row.get("id") or "").strip()
        if not pid:
            continue
        catalog[pid] = {
            "id": pid,
            "name": row.get("name") or "Unknown",
            "number": row.get("number") or "",
            "rarity": row.get("rarity") or "",
            "setName": spec.get("groupName") or "",
            "setId": str(spec.get("groupId") or ""),
            "image": row.get("image") or "",
        }
    return catalog


def printing_on_date(payload: dict, day: date, needles: list[str], mode: str):
    """Pick *this index's* printing for one calendar day.

    Uses vintage pick_printing_price (needles + include/exclude), never the
    EN representative that skips 1st Edition. Null/empty market is skipped
    (not zero-filled).
    """
    target = day.isoformat()
    rows: list[dict] = []
    if not isinstance(payload, dict):
        return None
    for printing, series in payload.items():
        if not isinstance(series, list):
            continue
        rec = None
        for item in series:
            if (item or {}).get("snapshot_date") == target:
                rec = item
                break
        if rec is None:
            continue
        rows.append(
            {
                "productId": "x",
                "marketPrice": rec.get("market"),
                "subTypeName": printing or "",
            }
        )
    return vin.pick_printing_price(rows, needles, mode)


def first_priced_date(payload: dict, needles: list[str], mode: str) -> str | None:
    best = None
    if not isinstance(payload, dict):
        return None
    for printing, series in payload.items():
        if not isinstance(series, list):
            continue
        sub = printing or ""
        for item in series:
            if en._parse_price((item or {}).get("market")) is None:
                continue
            dummy = [{"marketPrice": (item or {}).get("market"), "subTypeName": sub}]
            if vin.pick_printing_price(dummy, needles, mode) is None:
                continue
            ds = (item or {}).get("snapshot_date")
            if ds and (best is None or ds < best):
                best = ds
    return best


def make_prices_for_day(payloads: dict[str, dict], spec: dict):
    needles = spec.get("subtypeNeedles") or []
    mode = spec.get("subtypeMode") or "include"

    def prices_for_day(day: date) -> tuple[dict, dict]:
        prices: dict = {}
        subtypes: dict = {}
        for pid, payload in payloads.items():
            picked = printing_on_date(payload, day, needles, mode)
            if not picked:
                continue
            price, sub = picked
            prices[pid] = round(price, 2)
            subtypes[pid] = sub
        return prices, subtypes

    return prices_for_day


def load_fixture_payloads(fixture_dir: str) -> dict[str, dict]:
    price_dir = os.path.join(fixture_dir, "prices")
    out: dict[str, dict] = {}
    for name in os.listdir(price_dir):
        if not name.endswith(".json"):
            continue
        pid = name[:-5]
        with open(os.path.join(price_dir, name), "r", encoding="utf-8") as handle:
            out[pid] = json.load(handle)
    return out


def build_one_index(
    spec: dict,
    catalog: dict,
    payloads: dict[str, dict],
    *,
    start: date,
    end: date,
    step_days: int,
    verbose: bool = False,
    cfg: dict | None = None,
) -> dict:
    cfg = cfg or tm.load_config()
    dates = arch.daterange(start, end, step_days)
    # Fixed chase basket: every listed ID may enter once priced. min_priced=1
    # matches daily vintage (1st Edition is often thin on Charizard/Mewtwo/Blastoise).
    replayed = en.replay_price_series(
        dates,
        catalog,
        make_prices_for_day(payloads, spec),
        basket_size=max(len(catalog), 1),
        min_priced=1,
        verbose=verbose,
    )
    now = datetime.now(timezone.utc)
    points = replayed["points"]
    needles = spec.get("subtypeNeedles") or []
    mode = spec.get("subtypeMode") or "include"
    history = {
        "generated": now.isoformat(),
        "points": points,
        "baseValue": en.BASE_INDEX_VALUE,
        "baseDate": points[0]["date"],
        "archiveStart": cfg.get("archiveStart") or en.ARCHIVE_START,
        "optionalStart": cfg.get("optionalStart") or en.OPTIONAL_START,
        "source": "tickermint",
        "sourceDocs": cfg.get("docsUrl"),
        "sampling": {
            "stepDays": step_days,
            "start": dates[0].isoformat(),
            "end": dates[-1].isoformat(),
            "kept": len(points),
            "skipped": replayed["skipped"],
            "products": len(payloads),
        },
        "returns": en.period_returns(points, replayed["last_date"]),
    }
    return {
        "spec": spec,
        "history": history,
        "last": replayed["last"],
        "last_date": replayed["last_date"],
        "last_prices": replayed["last_prices"],
        "last_subtypes": replayed["last_subtypes"],
        "price_points": replayed.get("price_points") or [],
        "catalog": catalog,
        "firstPriced": {
            pid: first_priced_date(payloads[pid], needles, mode)
            for pid in catalog
            if pid in payloads
        },
    }


def _meta_from_prev(prev_index: dict, catalog: dict, pid: str) -> dict:
    by_id = {str(c.get("id")): c for c in (prev_index.get("constituents") or [])}
    by_id.update({str(c.get("id")): c for c in (prev_index.get("basket") or [])})
    return by_id.get(pid) or catalog.get(pid) or {
        "id": pid, "name": "Unknown", "number": "", "rarity": "",
        "setName": "", "setId": "", "image": "",
    }


def compose_index_latest(result: dict, prev_index: dict, now: datetime) -> dict:
    """Rebase one vintage index onto the TickerMint-chained divisor."""
    spec = result["spec"]
    history = result["history"]
    last = result["last"]
    last_date = result["last_date"]
    catalog = result["catalog"]
    last_prices = result["last_prices"]
    last_subtypes = result["last_subtypes"]
    points = history["points"]
    prev_snap = points[-2]["index"] if len(points) >= 2 else None
    constituents = []
    for rank, (pid, price) in enumerate(last["basket"], start=1):
        meta = _meta_from_prev(prev_index, catalog, pid)
        constituents.append(
            {
                "rank": rank,
                "id": pid,
                "name": meta.get("name") or "Unknown",
                "number": meta.get("number") or "",
                "rarity": meta.get("rarity") or "",
                "setName": meta.get("setName") or spec.get("groupName") or "",
                "setId": meta.get("setId") or str(spec.get("groupId") or ""),
                "image": meta.get("image") or "",
                "price": round(price, 2),
                "prevPrice": None,
                "changePct": None,
                "printing": last_subtypes.get(pid) or meta.get("printing"),
                "pricedAsOf": last_date if pid in last_prices else None,
                "carried": pid not in last_prices,
            }
        )
    priced_ids = {c["id"] for c in constituents}
    listed_unpriced = []
    basket_rows = [
        {k: c[k] for k in ("id", "name", "number", "rarity", "image", "price", "printing", "carried")}
        for c in constituents
    ]
    for pid, meta in catalog.items():
        if pid in priced_ids:
            continue
        live = _meta_from_prev(prev_index, catalog, pid)
        listed_unpriced.append(
            {
                "id": pid,
                "name": live.get("name") or meta.get("name") or "Unknown",
                "number": live.get("number") or meta.get("number") or "",
                "rarity": live.get("rarity") or meta.get("rarity") or "",
            }
        )
        basket_rows.append(
            {
                "id": pid,
                "name": live.get("name") or meta.get("name") or "Unknown",
                "number": live.get("number") or meta.get("number") or "",
                "rarity": live.get("rarity") or meta.get("rarity") or "",
                "image": live.get("image") or meta.get("image") or "",
                "price": None,
                "printing": None,
                "carried": False,
            }
        )
    basket_rows.sort(key=lambda c: (vin._num_key(c.get("number")), c.get("name") or ""))
    has_prev = prev_snap is not None
    index_value = last["index"]
    price_points = result.get("price_points") or []
    prior_prices = price_points[-2]["prices"] if len(price_points) >= 2 else {}
    latest_block = {
        "id": spec["id"],
        "name": spec.get("name"),
        "nameZh": spec.get("nameZh"),
        "printing": spec.get("printing"),
        "groupId": spec.get("groupId"),
        "groupName": spec.get("groupName"),
        "asOfDate": last_date,
        "universe": spec.get("universe"),
        "staleDays": en.STALE_DAYS,
        "index": round(index_value, 2),
        "prevIndex": round(prev_snap, 2) if has_prev else None,
        "change": round(index_value - prev_snap, 2) if has_prev else None,
        "changePct": round((index_value / prev_snap - 1) * 100, 2) if has_prev else None,
        "changeLabel": (
            "vs previous snapshot"
            if has_prev
            else "first snapshot — daily % after the next refresh"
        ),
        "divisor": last["divisor"],
        "baseValue": en.BASE_INDEX_VALUE,
        "baseDate": history["baseDate"],
        "archiveStart": history.get("archiveStart") or en.ARCHIVE_START,
        "listedCount": len(catalog),
        "constituentCount": len(constituents),
        "totalValue": round(last["sum_today"], 2),
        "pricedToday": sum(1 for c in constituents if not c["carried"]),
        "listedUnpriced": listed_unpriced,
        "breadth": {"available": False, "advancing": 0, "declining": 0, "unchanged": 0},
        "gainers": [],
        "losers": [],
        "top": [
            {k: c[k] for k in ("rank", "id", "name", "number", "image", "setName", "price", "rarity")}
            for c in constituents[:10]
        ],
        "basket": basket_rows,
        "constituents": constituents,
        "movers": {},
        "backfill": {
            "source": "tickermint",
            "start": history["baseDate"],
            "points": len(points),
            "docs": history.get("sourceDocs"),
        },
    }
    if prior_prices:
        en.apply_pairwise_movers(latest_block, prior_prices)
    return latest_block


def compose_payload(indexes: dict, baskets: dict, history: dict, now: datetime) -> dict:
    order = [i for i in vin.index_ids(baskets) if i in indexes]
    first = indexes[order[0]] if order else {}
    sampling = history.get("sampling") or {}
    payload = {
        "generated": now.isoformat(),
        "sourceStamp": None,
        "asOfDate": first.get("asOfDate"),
        "priceSource": "TCGplayer Market Price (TickerMint history + TCGCSV daily snap)",
        "universe": "English Base Set raw / ungraded singles — 1st Edition / Shadowless / Unlimited",
        "staleDays": en.STALE_DAYS,
        "baseValue": en.BASE_INDEX_VALUE,
        "backfill": history.get("backfill"),
        "basketRule": baskets.get("rule"),
        "gaps": baskets.get("gaps") or [],
        "indexOrder": order,
        "indexes": indexes,
    }
    if history.get("archiveStart"):
        payload["archiveStart"] = history["archiveStart"]
    if sampling:
        payload["sampling"] = sampling
    return payload


def compose_price_history(results: dict, now: datetime) -> dict:
    """Keep every retained weekly print — same dates as history.json points."""
    indexes = {}
    for iid, result in results.items():
        indexes[iid] = {
            "generated": now.isoformat(),
            "points": list(result.get("price_points") or []),
        }
    return {"generated": now.isoformat(), "source": "tickermint", "indexes": indexes}


def price_points_for_dates(spec: dict, payloads: dict[str, dict], days: list[date]) -> list[dict]:
    """Actual market prints on each history date (no carry, no $0 fill)."""
    prices_for_day = make_prices_for_day(payloads, spec)
    points = []
    for day in days:
        prices, _sub = prices_for_day(day)
        priced = {pid: round(pr, 2) for pid, pr in prices.items() if pr and pr > 0}
        if priced:
            points.append({"date": day.isoformat(), "prices": dict(sorted(priced.items()))})
    return points


def wrap_history(index_results: dict, baskets: dict, *, start: date, end: date, step_days: int, unique_n: int, cfg: dict) -> dict:
    now = datetime.now(timezone.utc)
    order = [i for i in vin.index_ids(baskets) if i in index_results]
    blocks = {iid: index_results[iid]["history"] for iid in order}
    base_dates = [blocks[iid]["baseDate"] for iid in order if blocks[iid].get("baseDate")]
    return {
        "generated": now.isoformat(),
        "baseValue": en.BASE_INDEX_VALUE,
        "backfill": {
            "source": "tickermint",
            "script": SCRIPT_NAME,
            "start": min(base_dates) if base_dates else start.isoformat(),
            "uniqueProducts": unique_n,
            "docs": cfg.get("docsUrl"),
        },
        "archiveStart": cfg.get("archiveStart") or en.ARCHIVE_START,
        "optionalStart": cfg.get("optionalStart") or en.OPTIONAL_START,
        "source": "tickermint",
        "sourceDocs": cfg.get("docsUrl"),
        "sampling": {
            "stepDays": step_days,
            "start": start.isoformat(),
            "end": end.isoformat(),
            "uniqueProducts": unique_n,
        },
        "indexOrder": order,
        "indexes": blocks,
    }


def _print_index_summary(label: str, hist: dict) -> None:
    pts = hist.get("points") or []
    print(
        f"  {label}: {hist.get('sampling', {}).get('kept', len(pts))} points "
        f"{pts[0]['date'] if pts else '?'} → {pts[-1]['date'] if pts else '?'} "
        f"index {pts[-1]['index']:.2f} (base {hist.get('baseDate')} = {hist.get('baseValue')})",
        flush=True,
    )
    rets = hist.get("returns") or {}
    for key in ("1D", "5D", "30D", "YTD"):
        r = rets.get(key) or {}
        flag = "yes" if r.get("available") else "no"
        extra = f"{r.get('from')} → {r.get('to')} ({r.get('pct')}%)" if r.get("available") else r.get("reason")
        print(f"    {key} available={flag}  {extra}", flush=True)


def _sample_pids(baskets: dict, n_bss: int, n_unl: int) -> list[str]:
    bss = unique_product_ids(baskets, index_filter=["base-1st", "base-shadowless"])
    unl = unique_product_ids(baskets, index_filter=["base-unlimited"])
    return bss[:n_bss] + unl[:n_unl]


def run_sample(verbose: bool = True, cfg: dict | None = None) -> dict:
    cfg = cfg or tm.load_config()
    baskets = vin.load_baskets()
    n = int(cfg.get("sampleIds") or 5)
    n_bss = max(1, min(3, n - 1))
    n_unl = max(1, n - n_bss)
    pids = _sample_pids(baskets, n_bss, n_unl)
    sleep_s = float(cfg.get("sleepSeconds") or 0.55)
    payloads = tm.fetch_payloads(pids, cfg, sleep_s=sleep_s, verbose=verbose, use_cache=True)
    latest = en.load_json(vin.LATEST_PATH, {})
    as_of = en._parse_day(latest.get("asOfDate")) or (datetime.now(timezone.utc).date() - timedelta(days=1))
    start = datetime.strptime(cfg["archiveStart"], "%Y-%m-%d").date()
    results = {}
    for iid in vin.index_ids(baskets):
        spec = baskets["indexes"][iid]
        catalog = {pid: meta for pid, meta in catalog_from_spec(spec).items() if pid in payloads}
        if not catalog:
            print(f"  skip {iid}: none of the sample IDs are in this basket", flush=True)
            continue
        sliced = {pid: payloads[pid] for pid in catalog if pid in payloads}
        results[iid] = build_one_index(
            spec, catalog, sliced,
            start=start, end=as_of,
            step_days=int(cfg.get("defaultStepDays") or 7),
            verbose=verbose, cfg=cfg,
        )
        _print_index_summary(f"SAMPLE {iid}", results[iid]["history"])
        needles = spec.get("subtypeNeedles") or []
        mode = spec.get("subtypeMode") or "include"
        for pid in list(catalog)[:3]:
            keys = list((payloads.get(pid) or {}).keys())
            first = results[iid]["firstPriced"].get(pid)
            print(
                f"    id={pid} printings={keys} filter={mode}:{needles} first={first}",
                flush=True,
            )
    print("SAMPLE (not writing history.json). Full rebase:", flush=True)
    print(f"  python3 {SCRIPT_NAME} --backfill --confirm BACKFILL", flush=True)
    return results


def run_backfill(
    *,
    start: date,
    end: date | None,
    step_days: int,
    max_ids: int | None,
    confirm: str,
    dry_run: bool,
    verbose: bool,
    cfg: dict | None = None,
) -> dict:
    if confirm != "BACKFILL" and not dry_run:
        raise RuntimeError(
            "Refusing to rebase vintage history.json. Re-run with --confirm BACKFILL "
            "(or --dry-run). This replaces the live Oct-2026 = 1,000 series. "
            "Do not mix the old chain with the TickerMint rebase."
        )
    cfg = cfg or tm.load_config()
    baskets = vin.load_baskets()
    pids = unique_product_ids(baskets)
    listed = sum(len((baskets["indexes"][i].get("constituents") or [])) for i in vin.index_ids(baskets))
    print(
        f"Vintage unique products={len(pids)} (listed slots={listed}; "
        f"1st+Shadowless share BSS ids). Do not use {tm.__file__}.",
        flush=True,
    )
    if max_ids:
        pids = pids[:max_ids]
    sleep_s = float(cfg.get("sleepSeconds") or 0.55)
    payloads = tm.fetch_payloads(pids, cfg, sleep_s=sleep_s, verbose=verbose, use_cache=True)
    if not payloads:
        raise RuntimeError("TickerMint returned no vintage product histories")
    latest = en.load_json(vin.LATEST_PATH, {})
    as_of = end or en._parse_day(latest.get("asOfDate")) or (datetime.now(timezone.utc).date() - timedelta(days=1))
    results = {}
    for iid in vin.index_ids(baskets):
        spec = baskets["indexes"][iid]
        catalog = catalog_from_spec(spec)
        if max_ids:
            catalog = {pid: meta for pid, meta in catalog.items() if pid in payloads}
        if not catalog:
            raise RuntimeError(f"{iid}: basket has no constituent IDs")
        sliced = {pid: payloads[pid] for pid in catalog if pid in payloads}
        if verbose:
            needles = spec.get("subtypeNeedles") or []
            mode = spec.get("subtypeMode") or "include"
            print(
                f"  {iid}: catalog {len(catalog)} fetched {len(sliced)} "
                f"filter {mode} {needles}",
                flush=True,
            )
        results[iid] = build_one_index(
            spec, catalog, sliced,
            start=start, end=as_of, step_days=step_days,
            verbose=verbose, cfg=cfg,
        )
        _print_index_summary(iid, results[iid]["history"])
    history = wrap_history(
        results, baskets,
        start=start, end=as_of, step_days=step_days,
        unique_n=len(payloads), cfg=cfg,
    )
    print(
        f"{'DRY RUN' if dry_run else 'Backfill'}: {len(results)} indexes, "
        f"unique HTTP/cache products={len(payloads)}",
        flush=True,
    )
    if dry_run:
        return {"history": history, "results": results}
    now = datetime.now(timezone.utc)
    prev_indexes = (latest or {}).get("indexes") or {}
    built = {
        iid: compose_index_latest(results[iid], prev_indexes.get(iid) or {}, now)
        for iid in history["indexOrder"]
    }
    price_history = compose_price_history(results, now)
    vin.attach_all_movers(built, price_history)
    latest_out = compose_payload(built, baskets, history, now)
    en.write_json(vin.HISTORY_PATH, history)
    vin.write_history_js(history)
    en.write_json(vin.LATEST_PATH, latest_out)
    vin.write_latest_js(latest_out)
    en.write_json(vin.PRICE_HISTORY_PATH, price_history)
    print(
        f"Wrote vintage history.json and latest.json asOf {latest_out['asOfDate']} "
        f"(rebased; first retained day of each index = 1,000).",
        flush=True,
    )
    print(
        "Daily TCGCSV job can now append on these divisors. "
        "Do not mix the old Oct-2026 = 1,000 series.",
        flush=True,
    )
    return {"history": history, "results": results, "latest": latest_out, "price_history": price_history}


def run_rebuild_movers(
    *,
    verbose: bool = False,
    dry_run: bool = False,
    cfg: dict | None = None,
) -> dict:
    """Fill price-history + movers from TickerMint without rebasing index levels."""
    cfg = cfg or tm.load_config()
    baskets = vin.load_baskets()
    history = en.load_json(vin.HISTORY_PATH, {}) or {}
    latest = en.load_json(vin.LATEST_PATH, {}) or {}
    hist_indexes = history.get("indexes") or {}
    if not hist_indexes:
        raise RuntimeError("history.json has no indexes — run --backfill --confirm BACKFILL first")
    pids = unique_product_ids(baskets)
    sleep_s = float(cfg.get("sleepSeconds") or 0.55)
    payloads = tm.fetch_payloads(pids, cfg, sleep_s=sleep_s, verbose=verbose, use_cache=True)
    if not payloads:
        raise RuntimeError("TickerMint returned no vintage product histories")
    now = datetime.now(timezone.utc)
    results: dict = {}
    built = dict(latest.get("indexes") or {})
    for iid in vin.index_ids(baskets):
        spec = baskets["indexes"][iid]
        block = hist_indexes.get(iid) or {}
        days = []
        for point in block.get("points") or []:
            day = en._parse_day(point.get("date"))
            if day:
                days.append(day)
        sliced = {pid: payloads[pid] for pid in catalog_from_spec(spec) if pid in payloads}
        points = price_points_for_dates(spec, sliced, days)
        results[iid] = {"price_points": points}
        row = built.get(iid)
        if row and len(points) >= 2:
            en.apply_pairwise_movers(row, points[-2].get("prices") or {})
        if verbose:
            print(f"  {iid}: {len(points)} priced snaps", flush=True)
    price_history = compose_price_history(results, now)
    vin.attach_all_movers(built, price_history)
    latest["generated"] = now.isoformat()
    latest["indexes"] = built
    if dry_run:
        return {"latest": latest, "price_history": price_history, "results": results}
    en.write_json(vin.LATEST_PATH, latest)
    vin.write_latest_js(latest)
    en.write_json(vin.PRICE_HISTORY_PATH, price_history)
    bits = []
    for iid in latest.get("indexOrder") or built:
        row = built[iid]
        movers = row.get("movers") or {}
        avail = [p for p, info in movers.items() if info.get("available")]
        n_g = len((movers.get("1D") or {}).get("gainers") or [])
        n_l = len((movers.get("1D") or {}).get("losers") or [])
        bits.append(f"{iid} 1D +{n_g}/-{n_l} periods={','.join(avail) or 'none'}")
    print(
        "Rebuilt vintage movers from TickerMint weekly prints (index levels unchanged) | "
        + " | ".join(bits),
        flush=True,
    )
    return {"latest": latest, "price_history": price_history, "results": results}


def self_test() -> None:
    cfg = tm.load_config()
    assert cfg["archiveStart"] == "2024-02-08"
    baskets = json.loads(open(os.path.join(FIXTURE_DIR, "baskets.json"), encoding="utf-8").read())
    payloads = load_fixture_payloads(FIXTURE_DIR)

    # Unique API calls: A+B shared by 1st/shadowless, U is Unlimited-only → 3 not 5.
    uids = unique_product_ids(baskets)
    assert uids == ["A", "B", "U"], uids
    assert set(unique_product_ids(baskets, index_filter=["base-1st"])) == {"A", "B"}
    assert set(unique_product_ids(baskets, index_filter=["base-shadowless"])) == {"A", "B"}
    assert unique_product_ids(baskets, index_filter=["base-unlimited"]) == ["U"]

    spec_1st = baskets["indexes"]["base-1st"]
    spec_sh = baskets["indexes"]["base-shadowless"]
    spec_un = baskets["indexes"]["base-unlimited"]

    # EN representative on the shared BSS id SKIPS 1st Edition (this is why
    # market-en backfill must not be reused). Vintage 1st must take 200.
    en_pick = tm.representative_on_date(payloads["A"], date(2024, 2, 8))
    assert en_pick and en_pick[0] == 100.0 and "unlimited" in en_pick[1].lower()
    first_pick = printing_on_date(payloads["A"], date(2024, 2, 8), ["1st edition"], "include")
    assert first_pick and first_pick[0] == 200.0 and "1st" in first_pick[1].lower()
    sh_pick = printing_on_date(payloads["A"], date(2024, 2, 8), ["unlimited"], "include")
    assert sh_pick and sh_pick[0] == 100.0 and "1st" not in sh_pick[1].lower()
    # Null market that day → no print (no zero-fill), for either printing.
    assert printing_on_date(payloads["A"], date(2024, 2, 9), ["1st edition"], "include") is None
    assert printing_on_date(payloads["A"], date(2024, 2, 9), ["unlimited"], "include") is None
    # Blastoise 1st has empty market — must not enter 1st; shadowless still prices it.
    assert printing_on_date(payloads["B"], date(2024, 2, 8), ["1st edition"], "include") is None
    b_sh = printing_on_date(payloads["B"], date(2024, 2, 8), ["unlimited"], "include")
    assert b_sh and b_sh[0] == 50.0
    # Unlimited exclude-1st: Holofoil 80 wins over Normal 10; 1st Edition 999 is ignored.
    un_pick = printing_on_date(payloads["U"], date(2024, 2, 8), ["1st edition"], "exclude")
    assert un_pick and un_pick[0] == 80.0 and "1st" not in un_pick[1].lower()

    # Build all three indexes. 1st must not contain Blastoise (B).
    start, end = date(2024, 2, 8), date(2026, 10, 11)
    results = {}
    for iid, spec in baskets["indexes"].items():
        catalog = catalog_from_spec(spec)
        sliced = {pid: payloads[pid] for pid in catalog}
        results[iid] = build_one_index(
            spec, catalog, sliced, start=start, end=end, step_days=7, verbose=False, cfg=cfg,
        )
    r1, rsh, run = results["base-1st"], results["base-shadowless"], results["base-unlimited"]
    assert r1["history"]["points"][0]["index"] == 1000.0
    assert r1["history"]["baseDate"] == "2024-02-08"
    assert "B" not in r1["last"]["ids"]
    assert "A" in r1["last"]["ids"]
    assert r1["last_subtypes"]["A"].lower().find("1st") >= 0
    # 2024-02-15: A 1st 220 on old basket 200 → +10%.
    assert abs(r1["history"]["points"][1]["index"] - 1100.0) < 1e-6
    # Shadowless: A 100 + B 50 on day 1; B is present (would leak into 1st if we
    # reused EN / Unlimited prices).
    assert set(rsh["last"]["ids"]) == {"A", "B"}
    assert rsh["last_subtypes"]["A"].lower().find("unlimited") >= 0
    assert "1st" not in rsh["last_subtypes"]["A"].lower()
    assert abs(rsh["history"]["points"][1]["index"] - 1100.0) < 1e-6
    assert run["last_subtypes"]["U"] == "Holofoil"
    assert abs(run["history"]["points"][1]["index"] - 1100.0) < 1e-6

    wre = r1["history"]["returns"]
    assert wre["1D"]["available"] and wre["5D"]["available"]
    assert wre["30D"]["available"] and wre["YTD"]["available"]

    wrapped = wrap_history(results, baskets, start=start, end=end, step_days=7, unique_n=3, cfg=cfg)
    assert wrapped["source"] == "tickermint"
    assert wrapped["backfill"]["script"] == SCRIPT_NAME
    assert wrapped["indexes"]["base-1st"]["points"][0]["index"] == 1000.0

    latest_1st = compose_index_latest(r1, {}, datetime(2026, 10, 12, tzinfo=timezone.utc))
    assert latest_1st["baseDate"] == "2024-02-08"
    assert latest_1st["divisor"] == r1["last"]["divisor"]
    assert any(row["id"] == "B" and row["price"] is None for row in latest_1st["basket"])
    assert {u["id"] for u in latest_1st["listedUnpriced"]} == {"B"}
    # Last two priced snaps (not a blank first-snapshot) populate 1D movers.
    assert r1["price_points"] and len(r1["price_points"]) >= 2
    assert latest_1st["breadth"]["available"] is True
    assert latest_1st["gainers"] or latest_1st["losers"]
    alakazam = next(c for c in latest_1st["constituents"] if c["id"] == "A")
    assert alakazam["prevPrice"] is not None and alakazam["changePct"] is not None

    latest_sh = compose_index_latest(rsh, {}, datetime(2026, 10, 12, tzinfo=timezone.utc))
    latest_un = compose_index_latest(run, {}, datetime(2026, 10, 12, tzinfo=timezone.utc))
    ph = compose_price_history(results, datetime(2026, 10, 12, tzinfo=timezone.utc))
    vin.attach_all_movers(
        {"base-1st": latest_1st, "base-shadowless": latest_sh, "base-unlimited": latest_un},
        ph,
    )
    for row in (latest_1st, latest_sh, latest_un):
        movers = row["movers"]
        assert movers["1D"]["available"]
        assert movers["1D"]["gainers"] or movers["1D"]["losers"] or row["breadth"]["available"]
        assert movers["5D"]["available"]
        assert movers["30D"]["available"]
        assert movers["YTD"]["available"]
        assert movers["5D"]["baselineDate"]  # on_or_before weekly cutoff, not slack-empty

    try:
        run_backfill(
            start=date(2024, 2, 8), end=None, step_days=7,
            max_ids=None, confirm="", dry_run=False, verbose=False, cfg=cfg,
        )
        raise AssertionError("expected --confirm BACKFILL guard")
    except RuntimeError as err:
        assert "Refusing to rebase" in str(err)

    # Live baskets: printing map + unique-ID count (~62, not 93).
    live = vin.load_baskets()
    live_ids = unique_product_ids(live)
    first_ids = unique_product_ids(live, index_filter=["base-1st"])
    sh_ids = unique_product_ids(live, index_filter=["base-shadowless"])
    un_ids = unique_product_ids(live, index_filter=["base-unlimited"])
    assert first_ids == sh_ids and first_ids
    assert not (set(first_ids) & set(un_ids))
    assert len(live_ids) == len(first_ids) + len(un_ids) == 62
    assert live["indexes"]["base-1st"]["subtypeMode"] == "include"
    assert "1st edition" in [n.lower() for n in live["indexes"]["base-1st"]["subtypeNeedles"]]
    assert live["indexes"]["base-shadowless"]["subtypeMode"] == "include"
    assert "unlimited" in [n.lower() for n in live["indexes"]["base-shadowless"]["subtypeNeedles"]]
    assert live["indexes"]["base-unlimited"]["subtypeMode"] == "exclude"
    assert "tickermint" in (live.get("backfill") or "").lower()

    print("self-test ok")


def _parse_day(raw: str | None, default: str | None = None) -> date:
    text = raw or default
    if not text:
        raise ValueError("date required")
    return datetime.strptime(text, "%Y-%m-%d").date()


def main(argv: list[str] | None = None) -> int:
    cfg = tm.load_config()
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--sample", action="store_true", help="Fetch a few BSS + Unlimited IDs; do not write live JSON")
    parser.add_argument("--backfill", action="store_true", help="Rebuild vintage history.json from TickerMint")
    parser.add_argument("--confirm", default="", help="Must be BACKFILL to write live history.json")
    parser.add_argument("--start", default=None, help=f"First date (default {cfg['archiveStart']})")
    parser.add_argument("--end", default=None, help="Last date (default: vintage latest.json asOfDate)")
    parser.add_argument(
        "--step-days",
        type=int,
        default=int(cfg.get("defaultStepDays") or 7),
        help="7 = weekly (default, ~140 points/index). 1 = daily ~980 points",
    )
    parser.add_argument("--max-ids", type=int, default=None, help="Cap unique product IDs (debug / smoke)")
    parser.add_argument(
        "--rebuild-movers",
        action="store_true",
        help="Fill price-history + Top 10/Bottom 10 from TickerMint weekly prints (does not rebase)",
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    if args.self_test:
        self_test()
        return 0
    try:
        if args.sample:
            run_sample(verbose=True, cfg=cfg)
            return 0
        if args.rebuild_movers:
            run_rebuild_movers(verbose=args.verbose or True, dry_run=args.dry_run, cfg=cfg)
            return 0
        if args.backfill:
            start = _parse_day(args.start, cfg["archiveStart"])
            end = _parse_day(args.end) if args.end else None
            run_backfill(
                start=start,
                end=end,
                step_days=args.step_days,
                max_ids=args.max_ids,
                confirm=args.confirm,
                dry_run=args.dry_run,
                verbose=args.verbose or True,
                cfg=cfg,
            )
            return 0
        parser.print_help()
        return 0
    except Exception as err:  # noqa: BLE001
        msg = str(err)
        print(f"ERROR: {msg}", file=sys.stderr)
        if "Refusing to rebase" in msg:
            return 2
        return 1


if __name__ == "__main__":
    sys.exit(main())
