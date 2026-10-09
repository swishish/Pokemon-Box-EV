#!/usr/bin/env python3
"""One-time TCGCSV price-archive backfill for the English market pulse.

Rebuilds data/market-en/history.json (and the matching latest.json divisor
state) from free TCGCSV daily archives starting 2024-02-08, using the *same*
universe, representative-price rule, and S&P-style divisor as
tools/refresh_market_en.py. Basket membership is recomputed on every sample
date. First archived day is rebased to 1,000.

Sampling (S&Poké-500 style): weekly from ARCHIVE_START, then daily for the
most recent 183 days so 1W/1M/6M chart ranges have daily resolution. The
daily GitHub Action then appends/upserts one point and must not wipe this
series.

Archives are PPMd 7z:
  https://tcgcsv.com/archive/tcgplayer/prices-YYYY-MM-DD.ppmd.7z
Need py7zr (`pip install -r tools/requirements-backfill.txt`) or a 7z CLI.

As of 2026-10-09 TCGCSV returns HTTP 403 on /archive/… ("temporarily
removed due to rising server costs"). This script probes first and exits
without touching JSON. Re-run when the FAQ archive URLs work again.

  python3 tools/backfill_market_en.py --self-test
  python3 tools/backfill_market_en.py --probe
  python3 tools/backfill_market_en.py -v
"""
from __future__ import annotations

import argparse
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import refresh_market_en as en  # noqa: E402

STEP_DAYS = 7
DENSE_DAYS = 183  # last six months sampled daily (covers the 6M chart)


class ArchiveUnavailable(RuntimeError):
    """TCGCSV published a takedown / paywall page instead of a 7z."""


def sample_dates(
    latest: date,
    start: date | None = None,
    dense_days: int = DENSE_DAYS,
    step_days: int = STEP_DAYS,
) -> list[date]:
    """Weekly deep history, then daily for the most recent dense_days."""
    start = start or datetime.strptime(en.ARCHIVE_START, "%Y-%m-%d").date()
    if latest < start:
        return []
    dense_start = max(latest - timedelta(days=dense_days), start)
    dates: list[date] = []
    d = start
    while d < dense_start:
        dates.append(d)
        d += timedelta(days=step_days)
    d = dense_start
    while d <= latest:
        dates.append(d)
        d += timedelta(days=1)
    return sorted(set(dates))


def extractor_kind() -> str | None:
    try:
        import py7zr  # noqa: F401

        return "py7zr"
    except ImportError:
        pass
    for name in ("7z", "7zz", "7za"):
        if shutil.which(name):
            return name
    return None


def _http_get(url: str, timeout: int = 180) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": en.USER_AGENT})
    last: Exception | None = None
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except urllib.error.HTTPError as err:
            body = err.read() if err.fp else b""
            text = body.decode("utf-8", "replace")
            if err.code == 403 and "temporarily removed" in text.lower():
                raise ArchiveUnavailable(text.strip() or "TCGCSV archive taken down (HTTP 403)")
            if err.code in (404, 403):
                raise
            last = err
        except Exception as err:  # noqa: BLE001
            last = err
        import time

        time.sleep(2 ** attempt)
    raise RuntimeError(f"TCGCSV request failed: {url}\n{last}")


def fetch_archive_blob(day: date) -> bytes | None:
    url = f"{en.TCGCSV}/archive/tcgplayer/prices-{day.isoformat()}.ppmd.7z"
    try:
        blob = _http_get(url)
    except ArchiveUnavailable:
        raise
    except urllib.error.HTTPError as err:
        if err.code in (404, 403):
            return None
        raise
    except RuntimeError:
        return None
    if blob.lstrip().startswith(b"The price archive has been temporarily removed"):
        raise ArchiveUnavailable(blob.decode("utf-8", "replace").strip())
    if len(blob) < 64 or blob[:2] != b"7z":
        # 7z signature is '7z\xbc\xaf\x27\x1c'
        preview = blob[:200].decode("utf-8", "replace")
        if "temporarily removed" in preview.lower():
            raise ArchiveUnavailable(preview.strip())
        return None
    return blob


def require_extractor() -> str:
    kind = extractor_kind()
    if not kind:
        raise RuntimeError(
            "Need py7zr or a 7z CLI to extract PPMd archives.\n"
            "  pip install -r tools/requirements-backfill.txt\n"
            "  # or: sudo apt-get install p7zip-full"
        )
    return kind


def peek_archive(day: date, nbytes: int = 1024) -> bytes | None:
    """Read the first bytes of an archive (Range) so --probe stays cheap."""
    url = f"{en.TCGCSV}/archive/tcgplayer/prices-{day.isoformat()}.ppmd.7z"
    req = urllib.request.Request(
        url,
        headers={"User-Agent": en.USER_AGENT, "Range": f"bytes=0-{nbytes - 1}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            blob = resp.read(nbytes)
    except urllib.error.HTTPError as err:
        body = err.read() if err.fp else b""
        text = body.decode("utf-8", "replace")
        if err.code == 403 and "temporarily removed" in text.lower():
            raise ArchiveUnavailable(text.strip() or "TCGCSV archive taken down (HTTP 403)")
        if err.code in (404, 403):
            return None
        raise
    if blob.lstrip().startswith(b"The price archive has been temporarily removed"):
        raise ArchiveUnavailable(blob.decode("utf-8", "replace").strip())
    if b"temporarily removed" in blob.lower():
        raise ArchiveUnavailable(blob.decode("utf-8", "replace").strip())
    return blob


def probe_archive(verbose: bool = False) -> str:
    """Hit the FAQ start-date URL. Returns extractor kind, or raises."""
    start = datetime.strptime(en.ARCHIVE_START, "%Y-%m-%d").date()
    url = f"{en.TCGCSV}/archive/tcgplayer/prices-{start.isoformat()}.ppmd.7z"
    if verbose:
        print(f"Probing {url} ...", flush=True)
    blob = peek_archive(start)
    if blob is None:
        raise RuntimeError(
            f"No TCGCSV archive at {en.ARCHIVE_START} (HTTP 404/empty). "
            "See https://tcgcsv.com/faq"
        )
    if blob[:2] != b"7z":
        preview = blob[:200].decode("utf-8", "replace")
        raise RuntimeError(f"Archive URL did not return a 7z file: {preview!r}")
    if verbose:
        print("  ok, 7z signature", flush=True)
    kind = require_extractor()
    if verbose:
        print(f"  extractor={kind}", flush=True)
    return kind


def latest_archive_date(verbose: bool = False) -> date:
    today = datetime.now(timezone.utc).date()
    last_err: Exception | None = None
    for back in range(0, 10):
        d = today - timedelta(days=back)
        try:
            blob = peek_archive(d)
        except ArchiveUnavailable:
            raise
        except Exception as err:  # noqa: BLE001
            last_err = err
            continue
        if blob and blob[:2] == b"7z":
            if verbose:
                print(f"Latest archive: {d.isoformat()}", flush=True)
            return d
    raise RuntimeError(f"No recent TCGCSV price archive found ({last_err})")


def _iter_price_files_py7zr(blob: bytes, day: date, tmp: str) -> list[str]:
    import py7zr

    prefix = f"{day.isoformat()}/{en.CATEGORY}/"
    with py7zr.SevenZipFile(io.BytesIO(blob), "r") as archive:
        targets = [
            n for n in archive.getnames()
            if n.startswith(prefix) and n.endswith("/prices")
        ]
        if not targets:
            return []
        archive.extract(path=tmp, targets=targets)
        return [os.path.join(tmp, *n.split("/")) for n in targets]


def _iter_price_files_7z(blob: bytes, day: date, tmp: str, bin_name: str) -> list[str]:
    archive_path = os.path.join(tmp, f"prices-{day.isoformat()}.ppmd.7z")
    with open(archive_path, "wb") as handle:
        handle.write(blob)
    pattern = f"{day.isoformat()}/{en.CATEGORY}/*/prices"
    proc = subprocess.run(
        [bin_name, "x", f"-o{tmp}", "-y", archive_path, pattern],
        check=False,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        # Some 7z builds want the glob quoted differently; extract the day folder.
        proc = subprocess.run(
            [bin_name, "x", f"-o{tmp}", "-y", archive_path, f"{day.isoformat()}/{en.CATEGORY}"],
            check=False,
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0:
            raise RuntimeError(f"{bin_name} extract failed: {proc.stderr or proc.stdout}")
    root = os.path.join(tmp, day.isoformat(), str(en.CATEGORY))
    paths = []
    if os.path.isdir(root):
        for dirpath, _, filenames in os.walk(root):
            if "prices" in filenames:
                paths.append(os.path.join(dirpath, "prices"))
    return paths


def archive_prices(day: date, kind: str) -> tuple[dict, dict]:
    """Representative {productId: price} for one archived date (category 3)."""
    blob = fetch_archive_blob(day)
    if not blob:
        return {}, {}
    with tempfile.TemporaryDirectory(prefix="tcgcsv-") as tmp:
        if kind == "py7zr":
            paths = _iter_price_files_py7zr(blob, day, tmp)
        else:
            paths = _iter_price_files_7z(blob, day, tmp, kind)
        rows: list[dict] = []
        for path in paths:
            if not os.path.exists(path):
                continue
            with open(path, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
            rows.extend(payload.get("results") or [])
        if not rows:
            return {}, {}
        return en.prices_from_rows(rows)


def compose_latest(
    *,
    catalog: dict,
    last: dict,
    last_date: str,
    last_prices: dict,
    last_subtypes: dict,
    prev_eff: dict | None,
    points: list[dict],
    stamp: str | None,
    now: datetime,
) -> dict:
    """Build a latest.json the daily Action can continue from."""
    prev_index = points[-2]["index"] if len(points) >= 2 else None
    constituents = []
    advancing = declining = unchanged = 0
    for rank, (pid, price) in enumerate(last["basket"], start=1):
        meta = catalog.get(pid) or {
            "id": pid,
            "name": "Unknown",
            "number": "",
            "rarity": "",
            "setName": "",
            "setId": "",
            "image": "",
        }
        prior = (prev_eff or {}).get(pid)
        live_today = pid in last_prices
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
                "printing": (last_subtypes.get(pid) if live_today else None),
                "pricedAsOf": last_date if live_today else None,
                "carried": not live_today,
            }
        )

    movable = [c for c in constituents if c["changePct"] is not None]
    movable.sort(key=lambda c: c["changePct"], reverse=True)
    fields = ("id", "name", "number", "image", "setName", "price", "prevPrice", "changePct")
    gainers = [{k: c[k] for k in fields} for c in movable[: en.TOP_MOVERS] if c["changePct"] > 0]
    losers = [{k: c[k] for k in fields} for c in reversed(movable[-en.TOP_MOVERS :]) if c["changePct"] < 0]

    has_prev = prev_index is not None
    change_abs = round(last["index"] - prev_index, 2) if has_prev else None
    change_pct = round((last["index"] / prev_index - 1) * 100, 2) if has_prev else None
    stamp_day = (stamp or "")[:10]
    if stamp_day and stamp_day != last_date:
        # Don't store a live stamp that belongs to a different calendar day —
        # the daily job would skip a real refresh.
        stamp = None

    return {
        "generated": now.isoformat(),
        "sourceStamp": stamp,
        "asOfDate": last_date,
        "priceSource": "TCGplayer Market Price (via tcgcsv.com daily snapshot)",
        "universe": "English raw / ungraded singles (TCGPlayer category 3)",
        "staleDays": en.STALE_DAYS,
        "index": round(last["index"], 2),
        "prevIndex": round(prev_index, 2) if has_prev else None,
        "change": change_abs,
        "changePct": change_pct,
        "changeLabel": "vs previous snapshot" if has_prev else "first snapshot — daily % after the next refresh",
        "divisor": last["divisor"],
        "baseValue": en.BASE_INDEX_VALUE,
        "baseDate": en.ARCHIVE_START,
        "constituentCount": len(constituents),
        "totalValue": round(last["sum_today"], 2),
        "pricedToday": sum(1 for c in constituents if not c["carried"]),
        "breadth": {
            "available": bool(movable),
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
        "backfill": {
            "source": "tcgcsv-archive",
            "start": en.ARCHIVE_START,
            "points": len(points),
        },
    }


def run_backfill(
    *,
    verbose: bool = False,
    start: str | None = None,
    end: str | None = None,
    dense_days: int = DENSE_DAYS,
    step_days: int = STEP_DAYS,
    max_dates: int | None = None,
    dry_run: bool = False,
) -> dict:
    probe_archive(verbose=verbose)
    kind = require_extractor()
    start_d = datetime.strptime(start or en.ARCHIVE_START, "%Y-%m-%d").date()
    end_d = datetime.strptime(end, "%Y-%m-%d").date() if end else latest_archive_date(verbose=verbose)
    dates = sample_dates(end_d, start=start_d, dense_days=dense_days, step_days=step_days)
    if max_dates:
        dates = dates[:max_dates]
    if not dates:
        raise RuntimeError("No sample dates in range")

    print("Building English Pokemon singles catalog (same dumps as daily) ...", flush=True)
    catalog, _, _ = en.fetch_universe(verbose=verbose)
    print(f"  catalog {len(catalog)} singles", flush=True)
    print(
        f"Reconstructing {len(dates)} points ({dates[0]} → {dates[-1]}; "
        f"weekly then last {dense_days}d daily) ...",
        flush=True,
    )

    points: list[dict] = []
    prev_ids = None
    prev_divisor = None
    prev_eff: dict | None = None
    last = None
    last_date = None
    last_prices: dict = {}
    last_subtypes: dict = {}
    carry: dict = {}
    carry_dt: dict = {}

    for i, d in enumerate(dates):
        ds = d.isoformat()
        try:
            prices, subtypes = archive_prices(d, kind)
        except ArchiveUnavailable:
            raise
        if len(prices) < en.TARGET_SIZE:
            if verbose:
                print(f"  {ds}: only {len(prices)} priced — skip", flush=True)
            continue
        step = en.step_index(prices, catalog, prev_ids, prev_divisor, carry=carry)
        points.append(
            {
                "date": ds,
                "index": round(step["index"], 2),
                "totalValue": round(step["sum_today"], 2),
                "count": len(step["ids"]),
            }
        )
        prev_eff = dict(carry)
        for pid, pr in prices.items():
            carry[pid] = pr
            carry_dt[pid] = d
        carry = {
            pid: p for pid, p in carry.items()
            if (d - carry_dt[pid]).days <= en.STALE_DAYS
        }
        prev_ids = step["ids"]
        prev_divisor = step["divisor"]
        last = step
        last_date = ds
        last_prices = prices
        last_subtypes = subtypes
        if verbose or i % 10 == 0 or i == len(dates) - 1:
            print(
                f"  {ds}: index {step['index']:.2f} "
                f"(basket {len(step['ids'])}, ${step['sum_today']:,.0f})",
                flush=True,
            )

    if not last or not last_date:
        raise RuntimeError("No points reconstructed — every archive day was empty or skipped")

    now = datetime.now(timezone.utc)
    stamp = en.source_stamp()
    latest = compose_latest(
        catalog=catalog,
        last=last,
        last_date=last_date,
        last_prices=last_prices,
        last_subtypes=last_subtypes,
        prev_eff=prev_eff,
        points=points,
        stamp=stamp,
        now=now,
    )
    history = {
        "generated": now.isoformat(),
        "baseDate": en.ARCHIVE_START,
        "baseValue": en.BASE_INDEX_VALUE,
        "sampling": {
            "stepDays": step_days,
            "denseDays": dense_days,
            "start": dates[0].isoformat(),
            "end": last_date,
        },
        "points": points,
    }
    if dry_run:
        print(f"DRY RUN: would write {len(points)} points, index {latest['index']:.2f}", flush=True)
        return {"latest": latest, "history": history}

    en.write_json(en.LATEST_PATH, latest)
    en.write_json(en.HISTORY_PATH, history)
    en.write_latest_js(latest)
    en.write_history_js(history)

    ch = latest["changePct"]
    ch_s = "n/a" if ch is None else f"{ch:+.2f}%"
    print(
        f"\nDone. {len(points)} history points, EN pulse = {latest['index']:.2f} "
        f"({ch_s} 1D) · asOf {latest['asOfDate']} · base {en.BASE_INDEX_VALUE} @ {en.ARCHIVE_START}",
        flush=True,
    )
    print(
        "Daily Action can now append via tools/refresh_market_en.py "
        "(stdlib only; do not re-run this backfill casually — it rebases the level).",
        flush=True,
    )
    return {"latest": latest, "history": history}


def self_test() -> None:
    start = date(2024, 2, 8)
    latest = date(2024, 8, 20)
    dates = sample_dates(latest, start=start, dense_days=10, step_days=7)
    assert dates[0] == start
    assert dates[-1] == latest
    # Deep history is weekly until the dense window.
    dense_start = latest - timedelta(days=10)
    weekly = [d for d in dates if d < dense_start]
    daily = [d for d in dates if d >= dense_start]
    gaps = [(b - a).days for a, b in zip(weekly, weekly[1:])]
    assert gaps and all(g == 7 for g in gaps)
    assert daily == [dense_start + timedelta(days=i) for i in range((latest - dense_start).days + 1)]

    catalog = {
        "a": {"id": "a", "name": "A", "number": "1", "rarity": "", "setName": "S", "setId": "1", "image": ""},
        "b": {"id": "b", "name": "B", "number": "2", "rarity": "", "setName": "S", "setId": "1", "image": ""},
        "c": {"id": "c", "name": "C", "number": "3", "rarity": "", "setName": "S", "setId": "1", "image": ""},
    }
    d1 = {"a": 100.0, "b": 50.0, "c": 10.0}
    s1 = en.step_index(d1, catalog, None, None, size=2, base=1000.0)
    d2 = {"a": 110.0, "b": 55.0, "c": 10.0}
    s2 = en.step_index(d2, catalog, s1["ids"], s1["divisor"], size=2, base=1000.0)
    points = [
        {"date": "2024-02-08", "index": round(s1["index"], 2), "totalValue": s1["sum_today"], "count": 2},
        {"date": "2024-02-09", "index": round(s2["index"], 2), "totalValue": s2["sum_today"], "count": 2},
    ]
    now = datetime(2026, 10, 9, tzinfo=timezone.utc)
    latest = compose_latest(
        catalog=catalog,
        last=s2,
        last_date="2024-02-09",
        last_prices=d2,
        last_subtypes={"a": "Holofoil", "b": "Normal"},
        prev_eff=d1,
        points=points,
        stamp="2024-02-09T20:05:00+0000",
        now=now,
    )
    assert latest["index"] == 1100.0
    assert latest["baseDate"] == en.ARCHIVE_START
    assert latest["baseValue"] == 1000.0
    assert latest["changePct"] == 10.0
    assert latest["breadth"]["available"] is True
    assert latest["gainers"] and latest["gainers"][0]["changePct"] == 10.0
    assert latest["divisor"] == s2["divisor"]
    # Same-day live stamp kept so the daily job can skip an identical dump.
    assert latest["sourceStamp"] == "2024-02-09T20:05:00+0000"
    # A live stamp from a *later* day must not be stored (would skip a real refresh).
    latest2 = compose_latest(
        catalog=catalog, last=s2, last_date="2024-02-09", last_prices=d2,
        last_subtypes={}, prev_eff=d1, points=points,
        stamp="2024-02-10T20:05:00+0000", now=now,
    )
    assert latest2["sourceStamp"] is None

    merged = en.merge_history_point(
        {"baseDate": en.ARCHIVE_START, "sampling": {"denseDays": 183}, "points": points},
        {"asOfDate": "2024-02-10", "index": 1110.0, "totalValue": 1, "constituentCount": 2,
         "baseValue": 1000, "baseDate": en.ARCHIVE_START},
        now,
    )
    assert [p["date"] for p in merged["points"]] == ["2024-02-08", "2024-02-09", "2024-02-10"]
    assert merged["sampling"]["denseDays"] == 183
    print("self-test ok")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--probe", action="store_true", help="Check archive availability and extractor, then exit")
    parser.add_argument("--start", default=None, help="First date (default 2024-02-08)")
    parser.add_argument("--end", default=None, help="Last date (default: latest published archive)")
    parser.add_argument("--dense-days", type=int, default=DENSE_DAYS)
    parser.add_argument("--step-days", type=int, default=STEP_DAYS)
    parser.add_argument("--max-dates", type=int, default=None, help="Cap sample dates (debug)")
    parser.add_argument("--dry-run", action="store_true", help="Reconstruct but do not write JSON")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)
    if args.self_test:
        self_test()
        return 0
    try:
        if args.probe:
            probe_archive(verbose=True)
            print(f"Latest published day: {latest_archive_date(verbose=True).isoformat()}", flush=True)
            print("Archives reachable. Ready to backfill.", flush=True)
            return 0
        run_backfill(
            verbose=args.verbose,
            start=args.start,
            end=args.end,
            dense_days=args.dense_days,
            step_days=args.step_days,
            max_dates=args.max_dates,
            dry_run=args.dry_run,
        )
    except ArchiveUnavailable as err:
        print("ERROR: TCGCSV price archive is not publicly downloadable right now.", file=sys.stderr)
        print(str(err), file=sys.stderr)
        print(
            "Live category dumps still work (daily pulse). Re-run this backfill when "
            "https://tcgcsv.com/faq archive URLs return .ppmd.7z files again.",
            file=sys.stderr,
        )
        return 2
    except Exception as err:  # noqa: BLE001
        print(f"ERROR: {err}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
