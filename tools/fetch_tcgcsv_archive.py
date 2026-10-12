#!/usr/bin/env python3
"""Download TCGCSV daily price archives and (optionally) replay them into EN history.

Incremental path (preferred while full history is huge / archives are flaky):

  python3 tools/fetch_tcgcsv_archive.py --self-test
  python3 tools/fetch_tcgcsv_archive.py --probe
  python3 tools/fetch_tcgcsv_archive.py --sample          # last 5 days → cache
  python3 tools/fetch_tcgcsv_archive.py --replay --from-dir data/tcgcsv-archive

Full backfill (rebases the index to 1,000 on the first archived day):

  python3 tools/fetch_tcgcsv_archive.py --backfill --confirm BACKFILL
  python3 tools/fetch_tcgcsv_archive.py --backfill --start 2024-02-28 --confirm BACKFILL

Start date: default 2024-02-08 (TCGCSV FAQ archive begin). Gary's 2024-02-28 is
an optional later cut, not the archive's real start — pass --start 2024-02-28.

Archives: https://tcgcsv.com/archive/tcgplayer/prices-YYYY-MM-DD.ppmd.7z
Pokemon = categoryId 3 = extracted path {date}/3/{groupId}/prices
Extractor: 7z / 7zz / 7za (PPMd). py7zr is an optional fallback.

Does not invent prices: HTTP 404 / empty / unreadable days are skipped.
As of 2026-10-12 TCGCSV still returns HTTP 403 ("temporarily removed") on
/archive/ — --probe / --sample / --backfill then exit 2 without writing JSON.
Prefer tools/backfill_tickermint.py for EN history (Gary, 2026-10-12).
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import refresh_market_en as en  # noqa: E402

CONFIG_PATH = os.path.join(HERE, "tcgcsv_archive.json")
FIXTURE_DIR = os.path.join(HERE, "testdata", "tcgcsv-archive")


class ArchiveUnavailable(RuntimeError):
    """TCGCSV published a takedown / paywall page instead of a 7z."""


def load_config() -> dict:
    cfg = {
        "archiveStart": en.ARCHIVE_START,
        "optionalStart": en.OPTIONAL_START,
        "categoryId": en.CATEGORY,
        "sleepSeconds": 0.5,
        "sampleDays": 5,
        "cacheDir": "data/tcgcsv-archive",
        "urlTemplate": "https://tcgcsv.com/archive/tcgplayer/prices-{date}.ppmd.7z",
    }
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, "r", encoding="utf-8") as handle:
            cfg.update(json.load(handle))
    return cfg


def cache_root(cfg: dict | None = None) -> str:
    cfg = cfg or load_config()
    return os.path.join(en.ROOT, cfg["cacheDir"])


def compact_dir(cfg: dict | None = None) -> str:
    return os.path.join(cache_root(cfg), "pokemon-en")


def archive_url(day: date, cfg: dict | None = None) -> str:
    cfg = cfg or load_config()
    return str(cfg["urlTemplate"]).format(date=day.isoformat())


def daterange(start: date, end: date, step_days: int = 1) -> list[date]:
    if step_days < 1:
        raise ValueError("step_days must be >= 1")
    out: list[date] = []
    d = start
    while d <= end:
        out.append(d)
        d += timedelta(days=step_days)
    if out and out[-1] != end:
        out.append(end)
    return out


def extractor_kind() -> str | None:
    for name in ("7z", "7zz", "7za"):
        if shutil.which(name):
            return name
    try:
        import py7zr  # noqa: F401

        return "py7zr"
    except ImportError:
        return None


def require_extractor() -> str:
    kind = extractor_kind()
    if not kind:
        raise RuntimeError(
            "Need a 7z CLI (7z / 7zz / 7za) that can extract PPMd archives.\n"
            "  sudo apt-get install p7zip-full\n"
            "Optional Python fallback: pip install py7zr"
        )
    return kind


def _http_get(url: str, timeout: int = 180, extra_headers: dict | None = None) -> bytes:
    headers = {"User-Agent": en.USER_AGENT}
    if extra_headers:
        headers.update(extra_headers)
    req = urllib.request.Request(url, headers=headers)
    last: Exception | None = None
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except urllib.error.HTTPError as err:
            body = err.read() if err.fp else b""
            text = body.decode("utf-8", "replace")
            low = text.lower()
            if err.code == 403 and "temporarily removed" in low:
                raise ArchiveUnavailable(text.strip() or "TCGCSV archive taken down (HTTP 403)")
            if err.code in (404, 403):
                raise
            last = err
        except Exception as err:  # noqa: BLE001
            last = err
        time.sleep(2 ** attempt)
    raise RuntimeError(f"TCGCSV request failed: {url}\n{last}")


def _looks_like_takedown(blob: bytes) -> bool:
    preview = blob[:400].decode("utf-8", "replace").lower()
    return "temporarily removed" in preview or preview.lstrip().startswith("the price archive has been")


def peek_archive(day: date, nbytes: int = 1024, cfg: dict | None = None) -> bytes | None:
    url = archive_url(day, cfg)
    try:
        blob = _http_get(url, timeout=30, extra_headers={"Range": f"bytes=0-{nbytes - 1}"})
    except ArchiveUnavailable:
        raise
    except urllib.error.HTTPError as err:
        if err.code in (404, 403):
            return None
        raise
    if _looks_like_takedown(blob):
        raise ArchiveUnavailable(blob[:400].decode("utf-8", "replace").strip())
    return blob


def fetch_archive_blob(day: date, cfg: dict | None = None) -> bytes | None:
    url = archive_url(day, cfg)
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
    if _looks_like_takedown(blob):
        raise ArchiveUnavailable(blob[:400].decode("utf-8", "replace").strip())
    if len(blob) < 64 or blob[:2] != b"7z":
        return None
    return blob


def probe_archive(verbose: bool = False, cfg: dict | None = None) -> str:
    cfg = cfg or load_config()
    start = datetime.strptime(cfg["archiveStart"], "%Y-%m-%d").date()
    url = archive_url(start, cfg)
    if verbose:
        print(f"Probing {url} ...", flush=True)
    blob = peek_archive(start, cfg=cfg)
    if blob is None:
        raise RuntimeError(
            f"No TCGCSV archive at {cfg['archiveStart']} (HTTP 404/empty). "
            "See https://tcgcsv.com/faq"
        )
    if blob[:2] != b"7z":
        preview = blob[:200].decode("utf-8", "replace")
        raise RuntimeError(f"Archive URL did not return a 7z file: {preview!r}")
    kind = require_extractor()
    if verbose:
        print(f"  ok, 7z signature · extractor={kind}", flush=True)
    return kind


def extract_category3(blob: bytes, day: date, dest: str, kind: str) -> list[str]:
    """Extract `{date}/3/*/prices` from a PPMd 7z blob into dest. Returns file paths."""
    prefix = f"{day.isoformat()}/{en.CATEGORY}/"
    os.makedirs(dest, exist_ok=True)
    if kind == "py7zr":
        import py7zr

        with py7zr.SevenZipFile(io.BytesIO(blob), "r") as archive:
            targets = [
                n for n in archive.getnames()
                if n.startswith(prefix) and n.rstrip("/").endswith("prices")
            ]
            if not targets:
                return []
            archive.extract(path=dest, targets=targets)
    else:
        archive_path = os.path.join(dest, f"prices-{day.isoformat()}.ppmd.7z")
        with open(archive_path, "wb") as handle:
            handle.write(blob)
        pattern = f"{day.isoformat()}/{en.CATEGORY}/*/prices"
        proc = subprocess.run(
            [kind, "x", f"-o{dest}", "-y", archive_path, pattern],
            check=False,
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0:
            proc = subprocess.run(
                [kind, "x", f"-o{dest}", "-y", archive_path, f"{day.isoformat()}/{en.CATEGORY}"],
                check=False,
                capture_output=True,
                text=True,
            )
            if proc.returncode != 0:
                raise RuntimeError(f"{kind} extract failed: {proc.stderr or proc.stdout}")
        try:
            os.remove(archive_path)
        except OSError:
            pass
    root = os.path.join(dest, day.isoformat(), str(en.CATEGORY))
    paths: list[str] = []
    if os.path.isdir(root):
        for dirpath, _, filenames in os.walk(root):
            if "prices" in filenames:
                paths.append(os.path.join(dirpath, "prices"))
    return paths


def rows_from_price_file(path: str) -> list[dict]:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (ValueError, OSError):
        return []
    if isinstance(payload, dict):
        return list(payload.get("results") or [])
    if isinstance(payload, list):
        return payload
    return []


def prices_from_extracted(day: date, extracted_root: str) -> tuple[dict, dict]:
    root = os.path.join(extracted_root, day.isoformat(), str(en.CATEGORY))
    rows: list[dict] = []
    if os.path.isdir(root):
        for dirpath, _, filenames in os.walk(root):
            if "prices" in filenames:
                rows.extend(rows_from_price_file(os.path.join(dirpath, "prices")))
    if not rows:
        return {}, {}
    return en.prices_from_rows(rows)


def write_compact_csv(path: str, prices: dict, subtypes: dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(("productId", "marketPrice", "subTypeName"))
        for pid in sorted(prices, key=lambda x: int(x) if str(x).isdigit() else str(x)):
            writer.writerow((pid, prices[pid], subtypes.get(pid) or ""))


def read_compact_csv(path: str) -> tuple[dict, dict]:
    prices: dict = {}
    subtypes: dict = {}
    with open(path, "r", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            pid = (row.get("productId") or "").strip()
            val = en._parse_price(row.get("marketPrice"))
            if not pid or val is None:
                continue
            prices[pid] = round(val, 2)
            subtypes[pid] = (row.get("subTypeName") or "").strip()
    return prices, subtypes


def compact_path_for(day: date, cfg: dict | None = None) -> str:
    return os.path.join(compact_dir(cfg), f"{day.isoformat()}.csv")


def load_day_prices(day: date, from_dir: str | None, cfg: dict | None = None) -> tuple[dict, dict]:
    """Load representative prices for one day: compact CSV, else extracted JSON."""
    compact = compact_path_for(day, cfg) if from_dir is None else os.path.join(
        from_dir, "pokemon-en", f"{day.isoformat()}.csv"
    )
    if os.path.isfile(compact):
        return read_compact_csv(compact)
    root = from_dir or cache_root(cfg)
    return prices_from_extracted(day, root)


def download_day(
    day: date,
    *,
    kind: str,
    cfg: dict,
    keep_raw: bool = False,
    verbose: bool = False,
) -> dict | None:
    """Fetch one archive, extract category 3, write compact CSV. Skip missing days."""
    blob = fetch_archive_blob(day, cfg)
    if not blob:
        if verbose:
            print(f"  {day.isoformat()}: skip (missing / not a 7z)", flush=True)
        return None
    dest = cache_root(cfg)
    os.makedirs(dest, exist_ok=True)
    if keep_raw:
        raw_7z = os.path.join(dest, "7z", f"prices-{day.isoformat()}.ppmd.7z")
        os.makedirs(os.path.dirname(raw_7z), exist_ok=True)
        with open(raw_7z, "wb") as handle:
            handle.write(blob)
    with tempfile.TemporaryDirectory(prefix="tcgcsv-") as tmp:
        paths = extract_category3(blob, day, tmp, kind)
        if not paths:
            if verbose:
                print(f"  {day.isoformat()}: skip (no category {en.CATEGORY} prices)", flush=True)
            return None
        prices, subtypes = prices_from_extracted(day, tmp)
        if keep_raw:
            raw_root = os.path.join(dest, day.isoformat(), str(en.CATEGORY))
            src = os.path.join(tmp, day.isoformat(), str(en.CATEGORY))
            if os.path.isdir(src):
                if os.path.isdir(raw_root):
                    shutil.rmtree(raw_root)
                shutil.copytree(src, raw_root)
    if not prices:
        if verbose:
            print(f"  {day.isoformat()}: skip (no priced rows)", flush=True)
        return None
    write_compact_csv(compact_path_for(day, cfg), prices, subtypes)
    if verbose:
        print(f"  {day.isoformat()}: {len(prices)} priced products → compact CSV", flush=True)
    return prices


def download_range(
    start: date,
    end: date,
    *,
    step_days: int = 1,
    keep_raw: bool = False,
    verbose: bool = False,
    cfg: dict | None = None,
) -> list[date]:
    cfg = cfg or load_config()
    kind = require_extractor()
    sleep_s = float(cfg.get("sleepSeconds") or 0.5)
    saved: list[date] = []
    dates = daterange(start, end, step_days)
    print(
        f"Downloading {len(dates)} archive(s) {start} → {end} "
        f"(step {step_days}d, sleep {sleep_s}s) ...",
        flush=True,
    )
    for i, day in enumerate(dates):
        try:
            got = download_day(day, kind=kind, cfg=cfg, keep_raw=keep_raw, verbose=verbose)
        except ArchiveUnavailable:
            raise
        if got is not None:
            saved.append(day)
        if sleep_s and i != len(dates) - 1:
            time.sleep(sleep_s)
    print(f"Saved {len(saved)}/{len(dates)} days (skipped {len(dates) - len(saved)} missing).", flush=True)
    return saved


def catalog_from_fixture(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as handle:
        raw = json.load(handle)
    return {str(k): v for k, v in raw.items()}


def replay_days(
    dates: list[date],
    catalog: dict,
    *,
    from_dir: str | None,
    basket_size: int,
    verbose: bool = False,
    cfg: dict | None = None,
) -> dict:
    """Walk dated compact/extracted prices through the live index math.

    Missing / thin days are skipped (no interpolated prices). First kept day
    is rebased to 1,000 — this is a *new* series, not mixed with the live
    Oct-2026 = 1,000 chain unless the caller replaces history.json on purpose.
    """
    def prices_for_day(d):
        return load_day_prices(d, from_dir, cfg)

    replayed = en.replay_price_series(
        dates, catalog, prices_for_day, basket_size=basket_size, verbose=verbose
    )
    now = datetime.now(timezone.utc)
    cfg = cfg or load_config()
    points = replayed["points"]
    history = {
        "generated": now.isoformat(),
        "baseDate": points[0]["date"],
        "baseValue": en.BASE_INDEX_VALUE,
        "archiveStart": cfg.get("archiveStart") or en.ARCHIVE_START,
        "optionalStart": cfg.get("optionalStart") or en.OPTIONAL_START,
        "sampling": {
            "start": dates[0].isoformat(),
            "end": dates[-1].isoformat(),
            "kept": len(points),
            "skipped": replayed["skipped"],
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
    }


def write_history(history: dict, path: str | None = None) -> None:
    dest = path or en.HISTORY_PATH
    en.write_json(dest, history)
    if dest == en.HISTORY_PATH:
        en.write_history_js(history)


def run_sample(verbose: bool = False, cfg: dict | None = None) -> dict:
    cfg = cfg or load_config()
    probe_archive(verbose=verbose, cfg=cfg)
    sample_days = int(cfg.get("sampleDays") or 5)
    end = datetime.now(timezone.utc).date() - timedelta(days=1)
    start = end - timedelta(days=sample_days - 1)
    saved = download_range(start, end, step_days=1, verbose=verbose, cfg=cfg)
    return {"start": start.isoformat(), "end": end.isoformat(), "saved": [d.isoformat() for d in saved]}


def run_replay(
    *,
    start: date,
    end: date,
    step_days: int,
    from_dir: str | None,
    catalog_path: str | None,
    basket_size: int,
    out_path: str | None,
    write_latest: bool,
    dry_run: bool,
    verbose: bool,
    cfg: dict | None = None,
) -> dict:
    cfg = cfg or load_config()
    dates = daterange(start, end, step_days)
    if catalog_path:
        catalog = catalog_from_fixture(catalog_path)
    else:
        print("Building English Pokemon singles catalog (same dumps as daily) ...", flush=True)
        catalog, _, _ = en.fetch_universe(verbose=verbose)
        print(f"  catalog {len(catalog)} singles", flush=True)
    result = replay_days(
        dates, catalog, from_dir=from_dir, basket_size=basket_size, verbose=verbose, cfg=cfg
    )
    history = result["history"]
    if dry_run:
        print(
            f"DRY RUN: {len(history['points'])} points, "
            f"{history['points'][0]['date']} → {history['points'][-1]['date']}, "
            f"index {history['points'][-1]['index']:.2f}",
            flush=True,
        )
        return result
    dest = out_path or (
        en.HISTORY_PATH if write_latest else os.path.join(en.DATA_DIR, "history-sample.json")
    )
    write_history(history, dest)
    print(f"Wrote {len(history['points'])} points → {dest}", flush=True)
    if dest != en.HISTORY_PATH:
        print("Not the live history.json (pass --out data/market-en/history.json or --backfill to rebase).", flush=True)
    if write_latest:
        now = datetime.now(timezone.utc)
        latest = _compose_latest_from_replay(result, now)
        en.write_json(en.LATEST_PATH, latest)
        en.write_latest_js(latest)
        print(f"Wrote latest.json asOf {latest['asOfDate']} (index rebased from {history['baseDate']})", flush=True)
    return result


def _compose_latest_from_replay(result: dict, now: datetime) -> dict:
    history = result["history"]
    last = result["last"]
    last_date = result["last_date"]
    catalog = result["catalog"]
    last_prices = result["last_prices"]
    last_subtypes = result["last_subtypes"]
    points = history["points"]
    prev_index = points[-2]["index"] if len(points) >= 2 else None
    constituents = []
    for rank, (pid, price) in enumerate(last["basket"], start=1):
        meta = catalog.get(pid) or {
            "id": pid, "name": "Unknown", "number": "", "rarity": "",
            "setName": "", "setId": "", "image": "",
        }
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
                "prevPrice": None,
                "changePct": None,
                "printing": last_subtypes.get(pid) or None,
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
        "priceSource": "TCGplayer Market Price (via tcgcsv.com daily snapshot)",
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
        "gainers": [],
        "losers": [],
        "top": [
            {k: c[k] for k in ("rank", "id", "name", "number", "image", "setName", "price")}
            for c in constituents[:10]
        ],
        "constituents": constituents,
        "backfill": {
            "source": "tcgcsv-archive",
            "start": history["baseDate"],
            "points": len(points),
        },
    }


def self_test() -> None:
    cfg = load_config()
    assert cfg["archiveStart"] == "2024-02-08"
    assert cfg["optionalStart"] == "2024-02-28"
    days = daterange(date(2024, 2, 8), date(2024, 2, 28), 7)
    assert days[0] == date(2024, 2, 8)
    assert date(2024, 2, 28) in days  # end date always kept
    # Missing days are skipped, not interpolated.
    catalog = catalog_from_fixture(os.path.join(FIXTURE_DIR, "catalog.json"))
    result = replay_days(
        daterange(date(2024, 2, 8), date(2024, 2, 12), 1),
        catalog,
        from_dir=FIXTURE_DIR,
        basket_size=2,
        verbose=False,
        cfg=cfg,
    )
    dates = [p["date"] for p in result["history"]["points"]]
    assert dates == ["2024-02-08", "2024-02-10"]  # 02-09 missing in fixtures
    assert result["history"]["points"][0]["index"] == 1000.0
    assert abs(result["history"]["points"][1]["index"] - 1100.0) < 1e-6
    rets = result["history"]["returns"]
    assert rets["1D"]["available"]
    assert not rets["5D"]["available"]  # cutoff 2024-02-05, no point that early

    # 7z extract round-trip when a CLI is present.
    kind = extractor_kind()
    if kind and kind != "py7zr":
        fixture_7z = os.path.join(FIXTURE_DIR, "prices-2024-02-08.ppmd.7z")
        assert os.path.isfile(fixture_7z), "committed sample 7z missing"
        with open(fixture_7z, "rb") as handle:
            blob = handle.read()
        assert blob[:2] == b"7z"
        with tempfile.TemporaryDirectory(prefix="tcgcsv-7z-") as tmp:
            paths = extract_category3(blob, date(2024, 2, 8), tmp, kind)
            assert paths, "7z extract produced no category-3 price files"
            prices, _ = prices_from_extracted(date(2024, 2, 8), tmp)
            assert prices.get("1") == 100.0 and prices.get("2") == 50.0
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
    parser.add_argument("--probe", action="store_true", help="Check archive availability + 7z extractor")
    parser.add_argument("--sample", action="store_true", help=f"Download last {cfg.get('sampleDays', 5)} days into the cache")
    parser.add_argument("--download", action="store_true", help="Download --start/--end into the cache")
    parser.add_argument("--replay", action="store_true", help="Rebuild history points from cache / --from-dir")
    parser.add_argument("--backfill", action="store_true", help="Download+replay and replace history.json (needs --confirm BACKFILL)")
    parser.add_argument("--confirm", default="", help="Must be BACKFILL to write live history.json from --backfill")
    parser.add_argument("--start", default=None, help=f"First date (default {cfg['archiveStart']})")
    parser.add_argument("--end", default=None, help="Last date (default: yesterday UTC)")
    parser.add_argument("--step-days", type=int, default=1, help="1 = daily, 7 = weekly cheaper backfill")
    parser.add_argument("--from-dir", default=None, help="Replay from this extracted/compact tree")
    parser.add_argument("--catalog", default=None, help="JSON catalog (fixture). Default: live TCGCSV Groups.csv")
    parser.add_argument("--basket-size", type=int, default=en.TARGET_SIZE)
    parser.add_argument("--out", default=None, help="Write history JSON here (default data/market-en/history.json)")
    parser.add_argument("--write-latest", action="store_true", help="Also rewrite latest.json (rebases the live index)")
    parser.add_argument("--keep-raw", action="store_true", help="Keep 7z + extracted JSON under the cache dir")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    if args.self_test:
        self_test()
        return 0

    start = _parse_day(args.start, cfg["archiveStart"])
    end = _parse_day(args.end, datetime.now(timezone.utc).date().isoformat()) if args.end else (
        datetime.now(timezone.utc).date() - timedelta(days=1)
    )

    try:
        if args.probe:
            probe_archive(verbose=True, cfg=cfg)
            print("Archives reachable. Ready to --sample / --download / --backfill.", flush=True)
            print(
                f"Start-date config: default {cfg['archiveStart']} "
                f"(optional later cut {cfg['optionalStart']})",
                flush=True,
            )
            return 0
        if args.sample:
            probe_archive(verbose=args.verbose, cfg=cfg)
            info = run_sample(verbose=True, cfg=cfg)
            print(json.dumps(info, indent=2), flush=True)
            print(
                "Cache only. Replay with:\n"
                "  python3 tools/fetch_tcgcsv_archive.py --replay "
                "--from-dir data/tcgcsv-archive --out data/market-en/history-sample.json",
                flush=True,
            )
            return 0
        if args.backfill:
            if args.confirm != "BACKFILL":
                print(
                    "Refusing to rebase history.json. Re-run with --confirm BACKFILL "
                    "(this replaces the live index level).",
                    file=sys.stderr,
                )
                return 2
            probe_archive(verbose=True, cfg=cfg)
            download_range(
                start, end, step_days=args.step_days,
                keep_raw=args.keep_raw, verbose=True, cfg=cfg,
            )
            run_replay(
                start=start, end=end, step_days=args.step_days,
                from_dir=args.from_dir or cache_root(cfg),
                catalog_path=args.catalog,
                basket_size=args.basket_size,
                out_path=args.out,
                write_latest=True,
                dry_run=args.dry_run,
                verbose=True,
                cfg=cfg,
            )
            return 0
        if args.download:
            probe_archive(verbose=args.verbose, cfg=cfg)
            download_range(
                start, end, step_days=args.step_days,
                keep_raw=args.keep_raw, verbose=True, cfg=cfg,
            )
            return 0
        if args.replay:
            run_replay(
                start=start, end=end, step_days=args.step_days,
                from_dir=args.from_dir,
                catalog_path=args.catalog,
                basket_size=args.basket_size,
                out_path=args.out,
                write_latest=args.write_latest,
                dry_run=args.dry_run,
                verbose=args.verbose or True,
                cfg=cfg,
            )
            return 0
        parser.print_help()
        return 0
    except ArchiveUnavailable as err:
        print("ERROR: TCGCSV price archive is not publicly downloadable right now.", file=sys.stderr)
        print(str(err), file=sys.stderr)
        print(
            "Live category dumps still power the daily pulse. Re-run --sample / --backfill "
            "when https://tcgcsv.com/faq archive URLs return .ppmd.7z files again.",
            file=sys.stderr,
        )
        return 2
    except Exception as err:  # noqa: BLE001
        print(f"ERROR: {err}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
