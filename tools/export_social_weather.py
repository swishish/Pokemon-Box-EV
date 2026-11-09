#!/usr/bin/env python3
"""Poke Social Weather: validate the public JSON shards.

Writes nothing unless a future change wires the workbook. The public page
reads only:

  data/social-weather/daily.json
  data/social-weather/segments.json

Do not merge these into data.json, and do not publish KeywordsDaily,
notable comments, buy CTAs, or private digest Target-hit language.
`read_zh` / `sources` may stay in the JSON for the maintainer; the HTML
page must not render them.

Routine (not a GitHub Action yet)
---------------------------------
Tue / Thu / Sat, Asia/Shanghai. Append the workbook, then overwrite the
two JSON files in a follow-up commit.

Sheet Daily → daily.json ``daily[]``
  date        ISO YYYY-MM-DD, or Excel serial (1899-12-30 epoch)
  score       integer −100…+100 (published composite; do not invent)
  label       text, e.g. 陰／Mild bear
  x           axis −100…+100
  reddit
  yt
  jp_shop
  ext_fg      TCGCharts Fear & Greed, 0–100 (raw, not the ±100 axis)
  buy_pct, sell_pct, bull_pct, bear_pct   optional; page ignores them
  n_posts     integer or blank
  read_zh, sources   optional maintainer notes; do not render

Sheet SegmentDaily → segments.json ``segments[]``
  date, segment, balance, seller_pct, buyer_pct

Segments (stable ids):
  30th_modern_sealed, mid_era_scarce_promo, vintage_lowpop,
  graded_chase_psa10, jp_shop_tape

Balance tags seen so far: seller_heavy, seller_soft, balanced, buyer_heavy.

Composite check (the page displays ``score`` and does not recompute)::

  fg_axis = (ext_fg - 50) * 2
  raw = 0.40*x + 0.25*reddit + 0.15*yt + 0.10*fg_axis + 0.10*jp_shop
  score = round(raw)   # half away from zero only when the sheet says so;
                       # Python round is what --self-test locks to the seed

``--xlsx`` exits without writing. ``--self-test`` checks the committed seed.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(ROOT, "data", "social-weather")
DAILY_JSON = os.path.join(DATA_DIR, "daily.json")
SEGMENTS_JSON = os.path.join(DATA_DIR, "segments.json")

ISO_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
SEGMENTS = (
    "30th_modern_sealed",
    "mid_era_scarce_promo",
    "vintage_lowpop",
    "graded_chase_psa10",
    "jp_shop_tape",
)
WEIGHTS = {
    "x": 0.40,
    "reddit": 0.25,
    "yt": 0.15,
    "tcgcharts_fg": 0.10,
    "jp_shop": 0.10,
}
SEED_DATES = ("2026-10-31", "2026-11-01", "2026-11-02", "2026-11-03")
SEED_SCORES = (-15, -14, -15, -25)


class ExportError(Exception):
    """Refused to treat the shard as publishable."""


def excel_serial_to_iso(value) -> str:
    """Calendar date. Excel 1900 system via the 1899-12-30 epoch.

    Serial 46326 is 2026-10-31. Whole days only — a time fraction is refused
    so a timezone shift cannot move the Shanghai date.
    """
    if isinstance(value, str):
        text = value.strip()
        if ISO_RE.fullmatch(text):
            return text
        if re.fullmatch(r"\d+(\.0+)?", text):
            value = float(text)
        else:
            raise ExportError(f"unrecognized date {value!r}")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ExportError(f"unrecognized date {value!r}")
    if abs(float(value) - round(float(value))) > 1e-6:
        raise ExportError(f"date serial has a time component: {value!r}")
    serial = int(round(float(value)))
    if serial < 1 or serial > 80000:
        raise ExportError(f"date serial out of range: {serial}")
    return (datetime(1899, 12, 30) + timedelta(days=serial)).date().isoformat()


def _num(value, name: str, lo: float, hi: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ExportError(f"{name} is not a number: {value!r}")
    n = float(value)
    if n < lo or n > hi:
        raise ExportError(f"{name} {n} outside {lo}…{hi}")
    return n


def composite_score(row: dict, weights: dict | None = None) -> int:
    """Integer composite from axes + raw F&G. Display still uses published score."""
    w = weights or WEIGHTS
    axes = row["axes"]
    fg_axis = (float(row["ext_fg"]) - 50.0) * 2.0
    raw = (
        float(w["x"]) * float(axes["x"])
        + float(w["reddit"]) * float(axes["reddit"])
        + float(w["yt"]) * float(axes["yt"])
        + float(w["tcgcharts_fg"]) * fg_axis
        + float(w["jp_shop"]) * float(axes["jp_shop"])
    )
    return int(round(raw))


def period_window(period: str, dates: list[str]) -> dict:
    """Same availability rule the page uses. ``dates`` are sorted ISO days."""
    if len(dates) < 2:
        return {"available": False, "reason": "need two points"}
    first, last = dates[0], dates[-1]
    if period == "ALL":
        return {"available": True, "start": first, "end": last}
    if period == "YTD":
        start = last[:4] + "-01-01"
    elif period in ("5D", "10D", "30D"):
        n = {"5D": 5, "10D": 10, "30D": 30}[period]
        start_dt = datetime.strptime(last, "%Y-%m-%d") - timedelta(days=n - 1)
        start = start_dt.date().isoformat()
    else:
        raise ExportError(f"unknown period {period}")
    span = (datetime.strptime(last, "%Y-%m-%d") - datetime.strptime(first, "%Y-%m-%d")).days + 1
    in_win = [d for d in dates if start <= d <= last]
    reaches = first <= start
    short5 = period == "5D" and span <= 5
    available = len(in_win) >= 2 and (reaches or short5)
    return {
        "available": available,
        "start": start,
        "end": last,
        "reason": "" if available else f"history starts {first}, window starts {start}",
    }


def validate(daily_doc: dict, seg_doc: dict) -> None:
    meta = daily_doc.get("meta") or {}
    if meta.get("timezone") != "Asia/Shanghai":
        raise ExportError("meta.timezone must be Asia/Shanghai")
    weights = meta.get("weights") or {}
    for key, exp in WEIGHTS.items():
        if abs(float(weights.get(key, -1)) - exp) > 1e-9:
            raise ExportError(f"weight {key} is {weights.get(key)!r}, expected {exp}")
    rows = daily_doc.get("daily")
    if not isinstance(rows, list) or not rows:
        raise ExportError("daily[] is empty")
    seen = []
    for row in rows:
        iso = excel_serial_to_iso(row.get("date"))
        if iso != row.get("date"):
            raise ExportError(f"daily date must already be ISO, got {row.get('date')!r}")
        seen.append(iso)
        _num(row.get("score"), "score", -100, 100)
        axes = row.get("axes") or {}
        for axis in ("x", "reddit", "yt", "jp_shop"):
            _num(axes.get(axis), axis, -100, 100)
        if row.get("ext_fg") is not None:
            _num(row.get("ext_fg"), "ext_fg", 0, 100)
        n_posts = row.get("n_posts")
        if n_posts is not None:
            if isinstance(n_posts, bool) or not isinstance(n_posts, int) or n_posts < 0:
                raise ExportError(f"n_posts {n_posts!r}")
        if not str(row.get("label") or "").strip():
            raise ExportError(f"missing label on {iso}")
    if seen != sorted(seen) or len(seen) != len(set(seen)):
        raise ExportError("daily dates must be unique and ascending")
    if meta.get("as_of") != seen[-1]:
        raise ExportError(f"meta.as_of {meta.get('as_of')!r} != last day {seen[-1]}")
    segs = seg_doc.get("segments")
    if not isinstance(segs, list):
        raise ExportError("segments[] missing")
    by_date: dict[str, set[str]] = {}
    for row in segs:
        iso = excel_serial_to_iso(row.get("date"))
        if iso != row.get("date"):
            raise ExportError(f"segment date must already be ISO, got {row.get('date')!r}")
        seg = row.get("segment")
        if seg not in SEGMENTS:
            raise ExportError(f"unknown segment {seg!r}")
        _num(row.get("seller_pct"), "seller_pct", 0, 100)
        _num(row.get("buyer_pct"), "buyer_pct", 0, 100)
        if not str(row.get("balance") or "").strip():
            raise ExportError(f"missing balance on {iso} {seg}")
        by_date.setdefault(iso, set()).add(seg)
    for iso in seen:
        have = by_date.get(iso) or set()
        missing = [s for s in SEGMENTS if s not in have]
        if missing:
            raise ExportError(f"{iso} missing segments: {', '.join(missing)}")


def self_test() -> None:
    if excel_serial_to_iso(46326) != "2026-10-31":
        raise ExportError("serial 46326 should be 2026-10-31")
    if excel_serial_to_iso("2026-11-03") != "2026-11-03":
        raise ExportError("ISO passthrough")
    try:
        excel_serial_to_iso(46326.5)
    except ExportError:
        pass
    else:
        raise ExportError("fractional serial should be refused")

    with open(DAILY_JSON, encoding="utf-8") as f:
        daily = json.load(f)
    with open(SEGMENTS_JSON, encoding="utf-8") as f:
        segs = json.load(f)
    validate(daily, segs)
    rows = daily["daily"]
    dates = tuple(r["date"] for r in rows)
    scores = tuple(int(r["score"]) for r in rows)
    if dates != SEED_DATES or scores != SEED_SCORES:
        raise ExportError(f"seed drift: {dates} {scores}")
    for row in rows:
        got = composite_score(row, daily["meta"]["weights"])
        if got != int(row["score"]):
            raise ExportError(f"{row['date']} formula {got} != published {row['score']}")
    periods = {p: period_window(p, list(dates))["available"] for p in ("5D", "10D", "30D", "YTD", "ALL")}
    if periods != {"5D": True, "10D": False, "30D": False, "YTD": False, "ALL": True}:
        raise ExportError(f"short-tape periods {periods}")
    # A tape that reaches the 10D start unlocks 10D and still locks 30D / YTD.
    long_dates = [(datetime(2026, 10, 20) + timedelta(days=i)).date().isoformat() for i in range(15)]
    long = {p: period_window(p, long_dates)["available"] for p in ("5D", "10D", "30D", "YTD", "ALL")}
    if long != {"5D": True, "10D": True, "30D": False, "YTD": False, "ALL": True}:
        raise ExportError(f"15-day periods {long}")
    print("ok", len(rows), "days", scores)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Validate Poke Social Weather JSON shards.")
    ap.add_argument("--self-test", action="store_true", help="check the committed seed and exit")
    ap.add_argument("--xlsx", help="workbook with Daily + SegmentDaily (not wired)")
    args = ap.parse_args(argv)
    if args.self_test:
        try:
            self_test()
        except (ExportError, OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            print(f"self-test failed: {exc}", file=sys.stderr)
            return 1
        return 0
    if args.xlsx:
        print(
            "Sheet→JSON export is not wired yet. Append Daily + SegmentDaily, "
            "map columns as in this file's docstring, write ISO dates, and commit "
            "data/social-weather/daily.json and segments.json. Refusing to write.",
            file=sys.stderr,
        )
        return 2
    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
