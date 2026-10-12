#!/usr/bin/env python3
"""Backfill EN market-en history from the TickerMint free API.

Daily "today" snaps still come from TCGCSV *current* dumps
(tools/refresh_market_en.py). TCGCSV's dated archive is still HTTP 403;
this script is the history source Gary chose.

  python3 tools/backfill_tickermint.py --self-test
  python3 tools/backfill_tickermint.py --sample
  python3 tools/backfill_tickermint.py --backfill --confirm BACKFILL

TickerMint (non-commercial, 120 req/min, no key):
  GET https://api.tickermint.cards/products/{tcgplayerId}/prices
  Docs: https://tickermint.cards/developers/pokemon
  Linkback: https://tickermint.cards/card/{id}

Default start = 2024-02-08 (same as TCGCSV/TickerMint history begin).
Optional later cut: --start 2024-02-28.

Does not invent prices: null / missing market that day → card is absent
(no $0 fill). A card enters the index on its first positive market date.
Default sampling is weekly (--step-days 7) so history.json stays small;
pass --step-days 1 for a daily series (~980 points).

--sample fetches a few current-basket IDs (live) and writes nothing to
history.json. --backfill --confirm BACKFILL rebases live history.json
(and latest.json divisor) so 5D/30D/YTD can light up.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import fetch_tcgcsv_archive as arch  # noqa: E402
import refresh_market_en as en  # noqa: E402

CONFIG_PATH = os.path.join(HERE, "tickermint.json")
FIXTURE_DIR = os.path.join(HERE, "testdata", "tickermint")


def load_config() -> dict:
    cfg = {
        "archiveStart": en.ARCHIVE_START,
        "optionalStart": en.OPTIONAL_START,
        "baseUrl": "https://api.tickermint.cards",
        "cardUrlTemplate": "https://tickermint.cards/card/{id}",
        "docsUrl": "https://tickermint.cards/developers/pokemon",
        "sleepSeconds": 0.55,
        "maxPerMinute": 120,
        "sampleIds": 5,
        "defaultStepDays": 7,
        "cacheDir": "data/tickermint-cache",
    }
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, "r", encoding="utf-8") as handle:
            cfg.update(json.load(handle))
    return cfg


def cache_dir(cfg: dict | None = None) -> str:
    cfg = cfg or load_config()
    return os.path.join(en.ROOT, cfg["cacheDir"])


def card_url(pid: str, cfg: dict | None = None) -> str:
    cfg = cfg or load_config()
    return str(cfg["cardUrlTemplate"]).format(id=pid)


def catalog_from_latest(path: str | None = None) -> tuple[dict, dict]:
    latest = en.load_json(path or en.LATEST_PATH, {})
    catalog: dict = {}
    for row in latest.get("constituents") or []:
        pid = str(row.get("id") or "").strip()
        if not pid:
            continue
        catalog[pid] = {
            "id": pid,
            "name": row.get("name") or "Unknown",
            "number": row.get("number") or "",
            "rarity": row.get("rarity") or "",
            "setName": row.get("setName") or "",
            "setId": row.get("setId") or "",
            "image": row.get("image") or "",
        }
    if not catalog:
        raise RuntimeError("latest.json has no constituents — run tools/refresh_market_en.py first")
    return catalog, latest


def representative_on_date(payload: dict, day: date) -> tuple[float, str] | None:
    """Pick the EN representative market price for one calendar day.

    TickerMint payload: { printingName: [{snapshot_date, market, low}, ...] }.
    Null/empty market is skipped (not zero-filled). 1st Edition is ignored
    unless it is the only priced printing — same rule as refresh_market_en.
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
    return en.pick_representative(rows)


def first_priced_date(payload: dict) -> str | None:
    best = None
    if not isinstance(payload, dict):
        return None
    for series in payload.values():
        if not isinstance(series, list):
            continue
        for item in series:
            if en._parse_price((item or {}).get("market")) is None:
                continue
            ds = (item or {}).get("snapshot_date")
            if ds and (best is None or ds < best):
                best = ds
    return best


def _http_get_json(url: str, timeout: int = 60) -> dict:
    req = urllib.request.Request(
        url,
        headers={"User-Agent": en.USER_AGENT, "Accept": "application/json"},
    )
    last: Exception | None = None
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as err:
            if err.code == 404:
                return {}
            if err.code == 429:
                time.sleep(2 ** attempt + 1)
                last = err
                continue
            last = err
        except Exception as err:  # noqa: BLE001
            last = err
            time.sleep(2 ** attempt)
    raise RuntimeError(f"TickerMint request failed: {url}\n{last}")


def fetch_product_prices(pid: str, cfg: dict, *, use_cache: bool = True) -> tuple[dict, bool]:
    """Return (payload, cached). cached=True means no HTTP call was made."""
    cache_path = os.path.join(cache_dir(cfg), f"{pid}.json")
    if use_cache and os.path.isfile(cache_path):
        with open(cache_path, "r", encoding="utf-8") as handle:
            return json.load(handle), True
    url = f"{cfg['baseUrl'].rstrip('/')}/products/{pid}/prices"
    payload = _http_get_json(url)
    if use_cache and payload:
        os.makedirs(os.path.dirname(cache_path), exist_ok=True)
        with open(cache_path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle)
            handle.write("\n")
    return payload, False


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


def make_prices_for_day(payloads: dict[str, dict]):
    def prices_for_day(day: date) -> tuple[dict, dict]:
        prices: dict = {}
        subtypes: dict = {}
        for pid, payload in payloads.items():
            picked = representative_on_date(payload, day)
            if not picked:
                continue
            price, sub = picked
            prices[pid] = round(price, 2)
            subtypes[pid] = sub
        return prices, subtypes

    return prices_for_day


def build_history(
    catalog: dict,
    payloads: dict[str, dict],
    *,
    start: date,
    end: date,
    step_days: int,
    basket_size: int,
    verbose: bool = False,
    cfg: dict | None = None,
) -> dict:
    cfg = cfg or load_config()
    dates = arch.daterange(start, end, step_days)
    replayed = en.replay_price_series(
        dates,
        catalog,
        make_prices_for_day(payloads),
        basket_size=basket_size,
        min_priced=2,
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
        "catalog": catalog,
        "firstPriced": {pid: first_priced_date(p) for pid, p in payloads.items()},
    }


def compose_latest(result: dict, prev_latest: dict, now: datetime) -> dict:
    """Rebase latest.json onto the TickerMint-chained divisor.

    Keeps card names/images from the live TCGCSV snapshot. Clears sourceStamp
    so the next daily TCGCSV job still runs.
    """
    history = result["history"]
    last = result["last"]
    last_date = result["last_date"]
    catalog = result["catalog"]
    last_prices = result["last_prices"]
    last_subtypes = result["last_subtypes"]
    points = history["points"]
    prev_index = points[-2]["index"] if len(points) >= 2 else None
    by_id = {str(c.get("id")): c for c in (prev_latest.get("constituents") or [])}
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
    has_prev = prev_index is not None
    index_value = last["index"]
    return {
        "generated": now.isoformat(),
        "sourceStamp": None,
        "asOfDate": last_date,
        "priceSource": "TCGplayer Market Price (TickerMint history + TCGCSV daily snap)",
        "universe": "English raw / ungraded singles (TCGPlayer category 3)",
        "staleDays": en.STALE_DAYS,
        "index": round(index_value, 2),
        "prevIndex": round(prev_index, 2) if has_prev else None,
        "change": round(index_value - prev_index, 2) if has_prev else None,
        "changePct": round((index_value / prev_index - 1) * 100, 2) if has_prev else None,
        "changeLabel": "vs previous snapshot" if has_prev else "first snapshot — daily % after the next refresh",
        "divisor": last["divisor"],
        "baseValue": en.BASE_INDEX_VALUE,
        "baseDate": history["baseDate"],
        "archiveStart": history.get("archiveStart") or en.ARCHIVE_START,
        "constituentCount": len(constituents),
        "totalValue": round(last["sum_today"], 2),
        "pricedToday": sum(1 for c in constituents if not c["carried"]),
        "breadth": {"available": False, "advancing": 0, "declining": 0, "unchanged": 0},
        "gainers": prev_latest.get("gainers") or [],
        "losers": prev_latest.get("losers") or [],
        "top": [
            {k: c[k] for k in ("rank", "id", "name", "number", "image", "setName", "price")}
            for c in constituents[:10]
        ],
        "constituents": constituents,
        "movers": prev_latest.get("movers") or {},
        "snapshotRange": prev_latest.get("snapshotRange"),
        "backfill": {
            "source": "tickermint",
            "start": history["baseDate"],
            "points": len(points),
            "docs": history.get("sourceDocs"),
        },
    }


def fetch_payloads(
    pids: list[str],
    cfg: dict,
    *,
    sleep_s: float,
    verbose: bool = False,
    use_cache: bool = True,
) -> dict[str, dict]:
    payloads: dict[str, dict] = {}
    n = len(pids)
    print(f"Fetching TickerMint history for {n} products (sleep {sleep_s}s, ≤{cfg.get('maxPerMinute', 120)}/min) ...", flush=True)
    for i, pid in enumerate(pids, 1):
        cached = False
        try:
            payload, cached = fetch_product_prices(pid, cfg, use_cache=use_cache)
        except Exception as err:  # noqa: BLE001
            print(f"  skip {pid}: {err}", flush=True)
            payload = {}
        if payload:
            payloads[pid] = payload
        if verbose or i == 1 or i == n or i % 25 == 0:
            first = first_priced_date(payload) if payload else None
            src = "cache" if cached else "http"
            print(f"  {i}/{n} id={pid} {src} printings={list(payload.keys()) if payload else []} first={first}", flush=True)
        # Only sleep after a real HTTP call (stay under 120/min). Cache hits are free.
        if sleep_s and i != n and not cached:
            time.sleep(sleep_s)
    print(f"Got history for {len(payloads)}/{n} products.", flush=True)
    return payloads


def run_sample(verbose: bool = True, cfg: dict | None = None) -> dict:
    cfg = cfg or load_config()
    catalog, latest = catalog_from_latest()
    n = int(cfg.get("sampleIds") or 5)
    pids = list(catalog.keys())[:n]
    sleep_s = float(cfg.get("sleepSeconds") or 0.55)
    payloads = fetch_payloads(pids, cfg, sleep_s=sleep_s, verbose=verbose, use_cache=True)
    as_of = en._parse_day(latest.get("asOfDate")) or (datetime.now(timezone.utc).date() - timedelta(days=1))
    start = datetime.strptime(cfg["archiveStart"], "%Y-%m-%d").date()
    result = build_history(
        {pid: catalog[pid] for pid in payloads},
        payloads,
        start=start,
        end=as_of,
        step_days=int(cfg.get("defaultStepDays") or 7),
        basket_size=min(len(payloads), 5) if payloads else 1,
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
    print("Full rebase (rewrites live JSON):\n  python3 tools/backfill_tickermint.py --backfill --confirm BACKFILL", flush=True)
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
    cfg = cfg or load_config()
    catalog, latest = catalog_from_latest()
    pids = list(catalog.keys())
    if max_ids:
        pids = pids[:max_ids]
        catalog = {pid: catalog[pid] for pid in pids}
    sleep_s = float(cfg.get("sleepSeconds") or 0.55)
    payloads = fetch_payloads(pids, cfg, sleep_s=sleep_s, verbose=verbose, use_cache=True)
    if not payloads:
        raise RuntimeError("TickerMint returned no product histories")
    as_of = end or en._parse_day(latest.get("asOfDate")) or (datetime.now(timezone.utc).date() - timedelta(days=1))
    result = build_history(
        catalog,
        payloads,
        start=start,
        end=as_of,
        step_days=step_days,
        basket_size=min(en.TARGET_SIZE, len(catalog)),
        verbose=verbose,
        cfg=cfg,
    )
    hist = result["history"]
    print(
        f"{'DRY RUN' if dry_run else 'Backfill'}: {hist['sampling']['kept']} points, "
        f"{hist['points'][0]['date']} → {hist['points'][-1]['date']}, "
        f"index {hist['points'][-1]['index']:.2f}",
        flush=True,
    )
    for key in ("1D", "5D", "30D", "YTD"):
        r = hist["returns"][key]
        print(f"  {key}: {'available ' + str(r['pct']) + '%' if r['available'] else 'disabled — ' + (r.get('reason') or '')}", flush=True)
    if dry_run:
        return result
    now = datetime.now(timezone.utc)
    latest_out = compose_latest(result, latest, now)
    en.write_json(en.HISTORY_PATH, hist)
    en.write_history_js(hist)
    en.write_json(en.LATEST_PATH, latest_out)
    en.write_latest_js(latest_out)
    print(
        f"Wrote history.json ({len(hist['points'])} points) and latest.json "
        f"asOf {latest_out['asOfDate']} (rebased from {hist['baseDate']}).",
        flush=True,
    )
    print("Daily TCGCSV job can now append on this divisor. Do not mix the old Oct-2026 = 1,000 series.", flush=True)
    return result


def self_test() -> None:
    cfg = load_config()
    assert cfg["archiveStart"] == "2024-02-08"
    assert cfg["optionalStart"] == "2024-02-28"
    catalog = json.loads(open(os.path.join(FIXTURE_DIR, "catalog.json"), encoding="utf-8").read())
    payloads = load_fixture_payloads(FIXTURE_DIR)

    # 1st Edition 999 must lose to Holofoil 100.
    picked = representative_on_date(payloads["a"], date(2024, 2, 8))
    assert picked and picked[0] == 100.0 and picked[1] == "Holofoil"
    # Null market that day → no print (no zero-fill).
    assert representative_on_date(payloads["a"], date(2024, 2, 9)) is None
    # Card c has no 2024 history.
    assert first_priced_date(payloads["c"]) == "2026-09-10"
    assert representative_on_date(payloads["c"], date(2024, 2, 8)) is None

    dates = [
        date(2024, 2, 8),
        date(2024, 2, 9),  # empty / null — skip
        date(2024, 2, 15),
        date(2025, 12, 31),
        date(2026, 9, 10),
        date(2026, 10, 6),
        date(2026, 10, 10),
        date(2026, 10, 11),
    ]

    def prices_for_day(day: date):
        return make_prices_for_day(payloads)(day)

    replayed = en.replay_price_series(
        dates, catalog, prices_for_day, basket_size=2, min_priced=2, verbose=False
    )
    kept = [p["date"] for p in replayed["points"]]
    assert "2024-02-09" not in kept
    assert kept[0] == "2024-02-08"
    assert kept[-1] == "2026-10-11"
    assert replayed["points"][0]["index"] == 1000.0
    # 2024-02-15: a 110 + b 55 on old basket → +10%.
    assert abs(replayed["points"][1]["index"] - 1100.0) < 1e-6
    # c's first priced date is 2026-09-10; after that it can enter (80 > b's 65).
    assert "c" in replayed["last"]["ids"] or replayed["last_prices"].get("c")

    rets = en.period_returns(replayed["points"], "2026-10-11")
    assert rets["1D"]["available"] and rets["1D"]["from"] == "2026-10-10"
    assert rets["5D"]["available"] and rets["5D"]["from"] == "2026-10-06"
    assert rets["30D"]["available"] and rets["30D"]["from"] == "2026-09-10"
    assert rets["YTD"]["available"] and rets["YTD"]["from"] == "2025-12-31"

    # Weekly sampling still includes the end date.
    weekly = arch.daterange(date(2024, 2, 8), date(2024, 2, 28), 7)
    assert weekly[0] == date(2024, 2, 8) and date(2024, 2, 28) in weekly

    result = build_history(
        catalog, payloads,
        start=date(2024, 2, 8), end=date(2026, 10, 11),
        step_days=7, basket_size=2, verbose=False, cfg=cfg,
    )
    assert result["history"]["source"] == "tickermint"
    # Sparse weekly fixtures still light 5D/30D/YTD from real kept points (no interpolation).
    wre = result["history"]["returns"]
    assert wre["1D"]["available"] and wre["5D"]["available"]
    assert wre["30D"]["available"] and wre["YTD"]["available"]
    try:
        run_backfill(
            start=date(2024, 2, 8), end=None, step_days=7,
            max_ids=None, confirm="", dry_run=False, verbose=False, cfg=cfg,
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
    cfg = load_config()
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--sample", action="store_true", help="Fetch a few basket IDs; do not write live JSON")
    parser.add_argument("--backfill", action="store_true", help="Rebuild history.json from TickerMint")
    parser.add_argument("--confirm", default="", help="Must be BACKFILL to write live history.json")
    parser.add_argument("--start", default=None, help=f"First date (default {cfg['archiveStart']})")
    parser.add_argument("--end", default=None, help="Last date (default: latest.json asOfDate)")
    parser.add_argument("--step-days", type=int, default=int(cfg.get("defaultStepDays") or 7),
                        help="7 = weekly (default, smaller history.json). 1 = daily ~980 points")
    parser.add_argument("--max-ids", type=int, default=None, help="Cap basket IDs (debug / smoke)")
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
