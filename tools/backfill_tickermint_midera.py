#!/usr/bin/env python3
"""Backfill the mid-era Top 50 from the TickerMint free API.

Uses the EN representative printing (same as tools/backfill_tickermint.py):
highest regular market that day; 1st Edition skipped unless it is the only
priced print. Do **not** reuse this for Base Set vintage (1st / Shadowless
need tools/backfill_tickermint_vintage.py).

  python3 tools/backfill_tickermint_midera.py --self-test
  python3 tools/backfill_tickermint_midera.py --sample
  python3 tools/backfill_tickermint_midera.py --backfill --confirm BACKFILL
  python3 tools/backfill_tickermint_midera.py --rebuild-movers

Default start = 2024-02-08. Weekly points (--step-days 7). Never fills
missing days with $0. A card enters on its first positive market date.
--backfill --confirm BACKFILL rebases live history.json + latest.json so
today's level leaves the temporary first-daily-snap = 1,000 series.

~50 unique product IDs (~25–40s at 120 req/min). Shared cache:
data/tickermint-cache/ (gitignored).
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import date, datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import backfill_tickermint as tm  # noqa: E402
import fetch_tcgcsv_archive as arch  # noqa: E402
import refresh_market_en as en  # noqa: E402
import refresh_market_midera as mid  # noqa: E402

SCRIPT_NAME = "tools/backfill_tickermint_midera.py"
FIXTURE_DIR = os.path.join(HERE, "testdata", "tickermint")


def build_history(
    catalog: dict,
    payloads: dict[str, dict],
    *,
    start: date,
    end: date,
    step_days: int,
    verbose: bool = False,
    cfg: dict | None = None,
) -> dict:
    """Same as EN TickerMint replay, but keep weekly price_points for movers."""
    cfg = cfg or tm.load_config()
    dates = arch.daterange(start, end, step_days)
    replayed = en.replay_price_series(
        dates,
        catalog,
        tm.make_prices_for_day(payloads),
        basket_size=max(len(catalog), 1),
        min_priced=1,
        verbose=verbose,
    )
    now = datetime.now(timezone.utc)
    points = replayed["points"]
    history = {
        "generated": now.isoformat(),
        "baseDate": points[0]["date"],
        "baseValue": en.BASE_INDEX_VALUE,
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
        "points": points,
        "returns": en.period_returns(points, replayed["last_date"]),
    }
    return {
        "history": history,
        "last": replayed["last"],
        "last_date": replayed["last_date"],
        "last_prices": replayed["last_prices"],
        "last_subtypes": replayed["last_subtypes"],
        "price_points": replayed.get("price_points") or [],
        "catalog": catalog,
        "firstPriced": {pid: tm.first_priced_date(p) for pid, p in payloads.items()},
    }


def catalog_from_baskets(path: str | None = None) -> tuple[dict, dict]:
    baskets = mid.load_baskets(path or mid.BASKETS_PATH)
    latest = en.load_json(mid.LATEST_PATH, {})
    catalog: dict = {}
    for row in mid.basket_rows(baskets):
        pid = str(row.get("id") or "").strip()
        if not pid:
            continue
        catalog[pid] = {
            "id": pid,
            "name": row.get("name") or "Unknown",
            "number": row.get("number") or "",
            "rarity": row.get("rarity") or "",
            "setName": row.get("setName") or "",
            "setId": str(row.get("setId") or ""),
            "image": row.get("image") or "",
        }
    if not catalog:
        raise RuntimeError("baskets.json has no mid-era IDs")
    return catalog, latest


def compose_latest(result: dict, prev_latest: dict, now: datetime) -> dict:
    history = result["history"]
    last = result["last"]
    last_date = result["last_date"]
    catalog = result["catalog"]
    last_prices = result["last_prices"]
    last_subtypes = result["last_subtypes"]
    points = history["points"]
    prev_index = points[-2]["index"] if len(points) >= 2 else None
    by_id = {str(c.get("id")): c for c in (prev_latest.get("constituents") or [])}
    by_id.update({str(c.get("id")): c for c in (prev_latest.get("basket") or [])})
    constituents = []
    for rank, (pid, price) in enumerate(last["basket"], start=1):
        meta = by_id.get(pid) or catalog.get(pid) or {
            "id": pid, "name": "Unknown", "number": "", "rarity": "",
            "setName": "", "setId": "", "image": "",
        }
        constituents.append(
            {
                "rank": rank,
                "id": pid,
                "name": meta.get("name") or "Unknown",
                "number": meta.get("number") or "",
                "rarity": meta.get("rarity") or "",
                "setName": meta.get("setName") or "",
                "setId": meta.get("setId") or "",
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
        live = by_id.get(pid) or meta
        listed_unpriced.append(
            {
                "id": pid,
                "name": live.get("name") or meta.get("name") or "Unknown",
                "number": live.get("number") or meta.get("number") or "",
                "rarity": live.get("rarity") or meta.get("rarity") or "",
                "setName": live.get("setName") or meta.get("setName") or "",
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
    has_prev = prev_index is not None
    index_value = last["index"]
    price_points = result.get("price_points") or []
    prior_prices = price_points[-2]["prices"] if len(price_points) >= 2 else {}
    latest = {
        "generated": now.isoformat(),
        "sourceStamp": None,
        "asOfDate": last_date,
        "priceSource": "TCGplayer Market Price (TickerMint history + TCGCSV daily snap)",
        "universe": "English DP→SM raw / ungraded chase singles (TCGPlayer Market, NM)",
        "staleDays": en.STALE_DAYS,
        "id": mid.INDEX_ID,
        "name": "Mid-era Top 50",
        "nameZh": "中世代 Top 50",
        "era": "DP-SM",
        "index": round(index_value, 2),
        "prevIndex": round(prev_index, 2) if has_prev else None,
        "change": round(index_value - prev_index, 2) if has_prev else None,
        "changePct": round((index_value / prev_index - 1) * 100, 2) if has_prev else None,
        "changeLabel": "vs previous snapshot" if has_prev else "first snapshot — daily % after the next refresh",
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
            {k: c[k] for k in ("rank", "id", "name", "number", "image", "setName", "price")}
            for c in constituents[:10]
        ],
        "basket": basket_rows,
        "constituents": constituents,
        "movers": {},
        "backfill": {
            "source": "tickermint",
            "script": SCRIPT_NAME,
            "start": history["baseDate"],
            "points": len(points),
            "uniqueProducts": len(catalog),
            "docs": history.get("sourceDocs"),
        },
    }
    if prior_prices:
        en.apply_pairwise_movers(latest, prior_prices)
    return latest


def compose_price_history(result: dict, now: datetime) -> dict:
    return {
        "generated": now.isoformat(),
        "points": list(result.get("price_points") or []),
    }


def run_sample(verbose: bool = True, cfg: dict | None = None) -> dict:
    cfg = cfg or tm.load_config()
    catalog, latest = catalog_from_baskets()
    n = int(cfg.get("sampleIds") or 5)
    pids = list(catalog.keys())[:n]
    sleep_s = float(cfg.get("sleepSeconds") or 0.55)
    payloads = tm.fetch_payloads(pids, cfg, sleep_s=sleep_s, verbose=verbose, use_cache=True)
    as_of = en._parse_day(latest.get("asOfDate")) or (datetime.now(timezone.utc).date() - timedelta(days=1))
    start = datetime.strptime(cfg["archiveStart"], "%Y-%m-%d").date()
    result = build_history(
        {pid: catalog[pid] for pid in payloads},
        payloads,
        start=start,
        end=as_of,
        step_days=int(cfg.get("defaultStepDays") or 7),
        verbose=verbose,
        cfg=cfg,
    )
    hist = result["history"]
    rets = hist["returns"]
    print(
        f"SAMPLE (not writing history.json): {hist['sampling']['kept']} points "
        f"{hist['points'][0]['date']} → {hist['points'][-1]['date']} "
        f"index {hist['points'][-1]['index']:.2f}",
        flush=True,
    )
    for key in ("1D", "5D", "30D", "YTD"):
        r = rets[key]
        flag = "yes" if r["available"] else "no"
        extra = f"{r['from']} → {r['to']} ({r['pct']}%)" if r["available"] else r.get("reason")
        print(f"  {key} available={flag}  {extra}", flush=True)
    print(
        "Full rebase (rewrites live JSON):\n"
        "  python3 tools/backfill_tickermint_midera.py --backfill --confirm BACKFILL",
        flush=True,
    )
    return result


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
            "Refusing to rebase history.json. Re-run with --confirm BACKFILL "
            "(or --dry-run). This replaces the live index level."
        )
    cfg = cfg or tm.load_config()
    catalog, latest = catalog_from_baskets()
    pids = list(catalog.keys())
    if max_ids:
        pids = pids[:max_ids]
        catalog = {pid: catalog[pid] for pid in pids}
    sleep_s = float(cfg.get("sleepSeconds") or 0.55)
    payloads = tm.fetch_payloads(pids, cfg, sleep_s=sleep_s, verbose=verbose, use_cache=True)
    if not payloads:
        raise RuntimeError("TickerMint returned no product histories")
    as_of = end or en._parse_day(latest.get("asOfDate")) or (datetime.now(timezone.utc).date() - timedelta(days=1))
    result = build_history(
        catalog,
        payloads,
        start=start,
        end=as_of,
        step_days=step_days,
        verbose=verbose,
        cfg=cfg,
    )
    hist = result["history"]
    hist["backfill"] = {
        "source": "tickermint",
        "script": SCRIPT_NAME,
        "start": hist["baseDate"],
        "uniqueProducts": len(payloads),
        "docs": hist.get("sourceDocs"),
    }
    print(
        f"{'DRY RUN' if dry_run else 'Backfill'}: {hist['sampling']['kept']} points, "
        f"{hist['points'][0]['date']} → {hist['points'][-1]['date']}, "
        f"index {hist['points'][-1]['index']:.2f}",
        flush=True,
    )
    for key in ("1D", "5D", "30D", "YTD"):
        r = hist["returns"][key]
        print(
            f"  {key}: {'available ' + str(r['pct']) + '%' if r['available'] else 'disabled — ' + (r.get('reason') or '')}",
            flush=True,
        )
    if dry_run:
        return result
    now = datetime.now(timezone.utc)
    latest_out = compose_latest(result, latest, now)
    price_history = compose_price_history(result, now)
    en.attach_period_movers(latest_out, price_history, baseline_strategy="on_or_before")
    baskets = mid.load_baskets()
    latest_out["basketRule"] = baskets.get("rule")
    latest_out["selection"] = baskets.get("selection")
    en.write_json(mid.HISTORY_PATH, hist)
    mid.write_history_js(hist)
    en.write_json(mid.LATEST_PATH, latest_out)
    mid.write_latest_js(latest_out)
    en.write_json(mid.PRICE_HISTORY_PATH, price_history)
    print(
        f"Wrote mid-era history.json ({len(hist['points'])} points) and latest.json "
        f"asOf {latest_out['asOfDate']} (rebased from {hist['baseDate']}).",
        flush=True,
    )
    print("Daily TCGCSV job can now append on this divisor. Do not mix the old 1,000 series.", flush=True)
    return result


def self_test() -> None:
    cfg = tm.load_config()
    assert cfg["archiveStart"] == "2024-02-08"
    # Reuse EN fixtures: 1st Edition must not win; null market is skipped.
    payloads = tm.load_fixture_payloads(FIXTURE_DIR)
    catalog = en.load_json(os.path.join(FIXTURE_DIR, "catalog.json"), {})
    picked = tm.representative_on_date(payloads["a"], date(2024, 2, 8))
    assert picked and picked[0] == 100.0 and picked[1] == "Holofoil"
    assert tm.representative_on_date(payloads["a"], date(2024, 2, 9)) is None
    assert tm.first_priced_date(payloads["c"]) == "2026-09-10"

    result = build_history(
        catalog,
        payloads,
        start=date(2024, 2, 8),
        end=date(2026, 10, 11),
        step_days=7,
        verbose=False,
        cfg=cfg,
    )
    assert result["history"]["points"][0]["index"] == 1000.0
    assert result["history"]["points"][0]["date"] == "2024-02-08"
    wre = result["history"]["returns"]
    assert wre["1D"]["available"] and wre["5D"]["available"]
    assert wre["30D"]["available"] and wre["YTD"]["available"]

    latest = compose_latest(result, {}, datetime(2026, 10, 11, tzinfo=timezone.utc))
    assert latest["baseDate"] == "2024-02-08"
    assert latest["baseValue"] == 1000.0
    assert latest["id"] == mid.INDEX_ID
    assert latest["archiveStart"] == "2024-02-08"
    # Last two priced snaps fill prevPrice (PR #31 pairwise; never leave null).
    assert result["price_points"] and len(result["price_points"]) >= 2
    assert latest["breadth"]["available"] is True
    priced_rows = [c for c in latest["constituents"] if c.get("price")]
    assert priced_rows and all(c.get("prevPrice") is not None for c in priced_rows)

    live = mid.load_baskets()
    ids = [c["id"] for c in live["index"]["constituents"]]
    assert 45 <= len(ids) <= 55
    assert len(ids) == len(set(ids))

    try:
        run_backfill(
            start=date(2024, 2, 8),
            end=None,
            step_days=7,
            max_ids=None,
            confirm="",
            dry_run=False,
            verbose=False,
            cfg=cfg,
        )
        raise AssertionError("expected --confirm BACKFILL guard")
    except RuntimeError as err:
        assert "Refusing to rebase" in str(err)
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
    parser.add_argument("--sample", action="store_true", help="Fetch a few basket IDs; do not write live JSON")
    parser.add_argument("--backfill", action="store_true", help="Rebuild history.json from TickerMint")
    parser.add_argument("--confirm", default="", help="Must be BACKFILL to write live history.json")
    parser.add_argument("--start", default=None, help=f"First date (default {cfg['archiveStart']})")
    parser.add_argument("--end", default=None, help="Last date (default: latest.json asOfDate)")
    parser.add_argument(
        "--step-days",
        type=int,
        default=int(cfg.get("defaultStepDays") or 7),
        help="7 = weekly (default). 1 = daily",
    )
    parser.add_argument("--max-ids", type=int, default=None, help="Cap basket IDs (debug / smoke)")
    parser.add_argument(
        "--rebuild-movers",
        action="store_true",
        help="Fill Top 10/Bottom 10 from last two priced weekly snaps (does not rebase)",
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
            mid.rebuild_movers()
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
