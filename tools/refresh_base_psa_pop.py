#!/usr/bin/env python3
"""Refresh English Base Set PSA / CGC population snapshots.

Reads data/base-psa-pop/catalog.json (PriceCharting pop/item slugs) and writes
data/base-psa-pop/latest.json + latest.js for market-base-psa-pop.html.

Public PriceCharting HTML only. No API key. No invented pops or prices.

  python3 tools/refresh_base_psa_pop.py --self-test
  python3 tools/refresh_base_psa_pop.py --root .
  python3 tools/refresh_base_psa_pop.py --from-html-dir /tmp/pc-pop

Parse errors exit non-zero and do not write. Only PSA and CGC columns are
stored. Beckett/BGS columns are ignored (not mixed into PSA). The page adds
PSA+CGC per grade by default and can split the two companies. Half grades
are stored as reported; display always buckets *.5 into the lower whole
grade (8.5→8, 9.5→9). There is no half-grade toggle.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from urllib.parse import quote
from zoneinfo import ZoneInfo

ROOT_DEFAULT = Path(__file__).resolve().parents[1]
TESTDATA = Path(__file__).resolve().parent / "testdata" / "base-psa-pop"
POP_PREFIX = "https://www.pricecharting.com/pop/item/"
GAME_PREFIX = "https://www.pricecharting.com/game/"
# PriceCharting TCG columns reuse video-game condition IDs.
PC_PRICE_MAP = {
    "used": "raw",
    "complete": "7",
    "new": "8",
    "graded": "9",
    "manual_only": "10",
}
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36"
)
APP_UA = "Pokemon-Box-EV/1.31 (+https://github.com/swishish/Pokemon-Box-EV)"
SLEEP_S = 0.45
WHOLE_GRADES = (10, 9, 8, 7, 6, 5, 4, 3, 2, 1)
HALF_GRADES = (9.5, 8.5, 7.5, 6.5, 5.5, 4.5, 3.5, 2.5, 1.5)
PRINT_IDS = ("1st", "shadowless", "unlimited", "base2")
PRINT_LABELS = {
    "1st": "1st Edition",
    "shadowless": "Shadowless",
    "unlimited": "Unlimited",
    "base2": "Base Set 2",
}
GRADER_IDS = ("psa", "cgc")
GRADER_ALIASES = {
    "psa": "psa",
    "cgc": "cgc",
}
SKIP_HEADERS = {
    "bgs",
    "beckett",
    "beckett (bgs)",
    "bgs/beckett",
    "total",
}

TABLE_RE = re.compile(
    r'<table[^>]*id=["\']population-table["\'][^>]*>(.*?)</table>',
    re.I | re.S,
)
THEAD_RE = re.compile(r"<thead[^>]*>(.*?)</thead>", re.I | re.S)
TBODY_RE = re.compile(r"<tbody[^>]*>(.*?)</tbody>", re.I | re.S)
TR_RE = re.compile(r"<tr[^>]*>(.*?)</tr>", re.I | re.S)
TH_RE = re.compile(r"<th[^>]*>(.*?)</th>", re.I | re.S)
TD_RE = re.compile(r"<td[^>]*>(.*?)</td>", re.I | re.S)
TAG_RE = re.compile(r"<[^>]+>")
WS_RE = re.compile(r"\s+")
HALF_NOTE_RE = re.compile(
    r"Half grade populations are included in the nearest whole grade",
    re.I,
)
PRICE_TD_RE = re.compile(
    r'<td[^>]*\bid=["\'](used|complete|new|graded|manual_only)_price["\'][^>]*>(.*?)</td>',
    re.I | re.S,
)
PRICE_SPAN_RE = re.compile(
    r'<span[^>]*\bprice\b[^>]*\bjs-price\b[^>]*>(.*?)</span>|'
    r'<span[^>]*\bjs-price\b[^>]*\bprice\b[^>]*>(.*?)</span>',
    re.I | re.S,
)
MONEY_RE = re.compile(r"\$\s*([0-9][0-9,]*(?:\.[0-9]+)?)")


class ParseError(Exception):
    """HTML was fetched but is not a usable pop table. Fail the job."""


class FetchError(Exception):
    """Network failure. Fail the job; do not invent pops."""


def now_shanghai() -> str:
    return datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d %H:%M Asia/Shanghai")


def today_ymd() -> str:
    return datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d")


def strip_html(raw: str) -> str:
    text = TAG_RE.sub(" ", raw or "")
    return WS_RE.sub(" ", text).strip()


def parse_count(raw: str) -> int | None:
    text = strip_html(raw)
    if not text or text in {"-", "—", "–", "n/a", "N/A", "na", "NA"}:
        return 0
    text = text.replace(",", "").replace("\xa0", "").strip()
    if not re.fullmatch(r"\d+", text):
        return None
    return int(text)


def parse_grade_key(raw: str) -> str | None:
    text = strip_html(raw)
    if not text:
        return None
    if text.lower() == "total":
        return "total"
    if re.fullmatch(r"\d+(?:\.\d+)?", text):
        num = float(text)
        if num == int(num):
            return str(int(num))
        return str(num)
    return None


def detect_grader(header: str) -> str | None:
    key = strip_html(header).lower()
    if key in SKIP_HEADERS:
        return None
    return GRADER_ALIASES.get(key)


def parse_pop_table(html: str, *, url: str = "") -> dict:
    if not html or not html.strip():
        raise ParseError(f"empty HTML{f' from {url}' if url else ''}")
    m = TABLE_RE.search(html)
    if not m:
        raise ParseError(
            f"no #population-table{f' at {url}' if url else ''} — refusing to invent pops"
        )
    table = m.group(1)
    head = THEAD_RE.search(table)
    body = TBODY_RE.search(table)
    if not head or not body:
        raise ParseError(f"population-table missing thead/tbody{f' at {url}' if url else ''}")

    headers = [strip_html(h) for h in TH_RE.findall(head.group(1))]
    if not headers or headers[0].lower() != "grade":
        raise ParseError(f"first column is not Grade{f' at {url}' if url else ''}: {headers}")

    col_map: dict[int, str] = {}
    for idx, header in enumerate(headers[1:], start=1):
        grader = detect_grader(header)
        if grader:
            col_map[idx] = grader
    if "psa" not in col_map.values():
        raise ParseError(f"PSA column missing{f' at {url}' if url else ''}: {headers}")

    graders: dict[str, dict] = {
        gid: {"available": False, "counts": {}, "total": None, "resolution": None}
        for gid in GRADER_IDS
    }
    for gid in col_map.values():
        graders[gid]["available"] = True
        graders[gid]["counts"] = {}
        graders[gid]["total"] = 0

    seen_grades: list[str] = []
    total_row = False
    for row_html in TR_RE.findall(body.group(1)):
        cells = TD_RE.findall(row_html)
        if len(cells) < 2:
            continue
        grade = parse_grade_key(cells[0])
        if grade is None:
            continue
        if grade == "total":
            total_row = True
            for idx, gid in col_map.items():
                if idx >= len(cells):
                    raise ParseError(f"total row short a {gid} cell{f' at {url}' if url else ''}")
                val = parse_count(cells[idx])
                if val is None:
                    raise ParseError(
                        f"unreadable {gid} total {cells[idx]!r}{f' at {url}' if url else ''}"
                    )
                graders[gid]["total"] = val
            continue
        seen_grades.append(grade)
        for idx, gid in col_map.items():
            if idx >= len(cells):
                raise ParseError(f"grade {grade} short a {gid} cell{f' at {url}' if url else ''}")
            val = parse_count(cells[idx])
            if val is None:
                raise ParseError(
                    f"unreadable {gid} count at grade {grade}: {cells[idx]!r}"
                    f"{f' at {url}' if url else ''}"
                )
            graders[gid]["counts"][grade] = val

    if not total_row:
        raise ParseError(f"population-table has no Total row{f' at {url}' if url else ''}")
    if "10" not in seen_grades:
        raise ParseError(f"population-table missing grade 10{f' at {url}' if url else ''}")

    for gid, rec in graders.items():
        if not rec["available"]:
            continue
        summed = sum(int(v) for v in rec["counts"].values())
        if rec["total"] != summed:
            raise ParseError(
                f"{gid} total {rec['total']} != sum of grade rows {summed}"
                f"{f' at {url}' if url else ''} — refusing to invent / patch pops"
            )
        rec["resolution"] = (
            "half" if any(k in rec["counts"] for k in ("9.5", "8.5", "7.5", "6.5")) else "whole"
        )

    half_bucketed = bool(HALF_NOTE_RE.search(html))
    return {
        "graders": graders,
        "half_grades_bucketed_by_source": half_bucketed
        and all(
            rec["resolution"] != "half"
            for rec in graders.values()
            if rec["available"]
        ),
        "grades_reported": seen_grades,
    }


def bucket_lower(counts: dict[str, int]) -> dict[str, int]:
    """Bucket *.5 into the lower whole grade. 10+ specials stay in 10."""
    out = {str(g): 0 for g in WHOLE_GRADES}
    for raw, val in (counts or {}).items():
        try:
            num = float(raw)
            n = int(val)
        except (TypeError, ValueError):
            continue
        if n < 0:
            continue
        if num > 10:
            out["10"] += n
        elif num in WHOLE_GRADES:
            out[str(int(num))] += n
        elif abs(num * 2 - round(num * 2)) < 1e-9 and num != int(num):
            out[str(int(num))] += n
    return out


def grade_share(count: int | None, total: int | None) -> float | None:
    if count is None or total is None or total <= 0:
        return None
    return count / total


def cumulative_shares(counts: dict[str, int], grades: list[str], total: int | None) -> dict[str, float | None]:
    acc = 0
    out: dict[str, float | None] = {}
    for grade in grades:
        acc += int(counts.get(grade) or 0)
        out[grade] = grade_share(acc, total)
    return out


def load_catalog(root: Path) -> dict:
    path = root / "data" / "base-psa-pop" / "catalog.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as err:
        raise ParseError(f"cannot read catalog {path}: {err}") from err
    cards = data.get("cards")
    if not isinstance(cards, list) or not cards:
        raise ParseError(f"{path} has no cards")
    for card in cards:
        if not card.get("id") or not card.get("name") or not card.get("prints"):
            raise ParseError(f"catalog card missing id/name/prints: {card!r}")
        prints = card.get("prints") or {}
        if not prints.get("1st") and not prints.get("unlimited"):
            raise ParseError(f"catalog {card['id']} needs a 1st or Unlimited slug")
    return data


def load_names_zh(root: Path) -> dict[str, str]:
    path = root / "data" / "names_zh.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    species = data.get("species") or {}
    return {str(k): str(v) for k, v in species.items() if k and v}


def pop_url(slug: str) -> str:
    parts = str(slug).lstrip("/").split("/")
    return POP_PREFIX + "/".join(quote(part, safe="-._~") for part in parts)


def game_url(slug: str) -> str:
    parts = str(slug).lstrip("/").split("/")
    return GAME_PREFIX + "/".join(quote(part, safe="-._~") for part in parts)


def parse_money_usd(raw: str) -> float | None:
    text = strip_html(raw)
    if not text or text in {"-", "—", "–", "n/a", "N/A"}:
        return None
    m = MONEY_RE.search(text)
    if not m:
        return None
    return float(m.group(1).replace(",", ""))


def parse_pc_prices(html: str) -> dict[str, float]:
    """used=raw, complete=G7, new=G8, graded=G9, manual_only=PSA10. Missing → omit."""
    out: dict[str, float] = {}
    if not html:
        return out
    for match in PRICE_TD_RE.finditer(html):
        key = PC_PRICE_MAP.get(match.group(1).lower())
        if not key:
            continue
        span = PRICE_SPAN_RE.search(match.group(2) or "")
        blob = (span.group(1) or span.group(2) or "") if span else match.group(2)
        usd = parse_money_usd(blob)
        if usd is not None:
            out[key] = usd
    return out


def fetch_html(url: str, *, timeout: int = 30) -> str:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "en-US,en;q=0.8",
            "From": APP_UA,
        },
    )
    last: Exception | None = None
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                if resp.status != 200:
                    raise FetchError(f"HTTP {resp.status} for {url}")
                raw = resp.read()
            return raw.decode("utf-8", errors="replace")
        except Exception as err:  # noqa: BLE001 — retry transients, then fail loud
            last = err
            if attempt < 3:
                time.sleep(2 ** attempt)
    raise FetchError(f"fetch failed for {url}: {last}")


def empty_grader() -> dict:
    return {"available": False, "counts": {}, "total": None, "resolution": None}


def attach_print(card: dict, print_id: str, slug: str, parsed: dict, html: str) -> dict:
    title = ""
    hm = re.search(r"<h1[^>]*>(.*?)</h1>", html, re.I | re.S)
    if hm:
        title = strip_html(hm.group(1))
    rec = {
        "id": print_id,
        "label": PRINT_LABELS[print_id],
        "slug": slug,
        "pop_url": pop_url(slug),
        "game_url": game_url(slug),
        "page_title": title or None,
        "half_grades_bucketed_by_source": bool(parsed["half_grades_bucketed_by_source"]),
        "grades_reported": parsed["grades_reported"],
        "graders": {
            gid: dict(parsed["graders"].get(gid) or empty_grader()) for gid in GRADER_IDS
        },
    }
    return rec


def load_previous_prices(root: Path) -> dict[tuple[str, str], dict[str, float]]:
    path = root / "data" / "base-psa-pop" / "latest.json"
    try:
        prev = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    out: dict[tuple[str, str], dict[str, float]] = {}
    for card in prev.get("cards") or []:
        cid = card.get("id")
        if not cid:
            continue
        for pid, rec in (card.get("prints") or {}).items():
            prices = (rec or {}).get("pc_prices")
            if isinstance(prices, dict) and prices:
                out[(cid, pid)] = {
                    str(k): float(v)
                    for k, v in prices.items()
                    if v is not None and str(k) in {"raw", "7", "8", "9", "10"}
                }
    return out


def fetch_print_prices(
    spec: dict,
    pid: str,
    slug: str,
    *,
    html_dir: Path | None,
    fetch,
    previous: dict[tuple[str, str], dict[str, float]],
) -> dict[str, float] | None:
    cid = spec["id"]
    try:
        if html_dir is not None:
            local = html_dir / f"{cid}-{pid}-game.html"
            if not local.is_file():
                return previous.get((cid, pid))
            html = local.read_text(encoding="utf-8")
        else:
            html = fetch(game_url(slug))
        parsed = parse_pc_prices(html)
        if parsed:
            return parsed
    except (ParseError, FetchError, OSError) as err:
        print(f"keep previous prices {cid}/{pid}: {err}", file=sys.stderr)
    return previous.get((cid, pid))


def load_previous_prints(root: Path) -> dict[str, dict]:
    path = root / "data" / "base-psa-pop" / "latest.json"
    try:
        prev = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    out: dict[str, dict] = {}
    for card in prev.get("cards") or []:
        cid = card.get("id")
        prints = card.get("prints")
        if cid and isinstance(prints, dict) and prints:
            out[cid] = prints
    return out


def reference_print(prints: dict) -> str:
    for pid in ("unlimited", "1st", "shadowless", "base2"):
        rec = prints.get(pid) or {}
        psa = (rec.get("graders") or {}).get("psa") or {}
        if psa.get("available") and psa.get("total"):
            return pid
    return "unlimited"


def combine_grader(print_rec: dict | None) -> dict:
    """Sum PSA + CGC for labels. Missing company is omitted, not invented as 0."""
    graders = (print_rec or {}).get("graders") or {}
    counts: dict[str, int] = {}
    total = 0
    available = False
    for gid in GRADER_IDS:
        rec = graders.get(gid) or {}
        if not rec.get("available"):
            continue
        available = True
        for key, val in (rec.get("counts") or {}).items():
            try:
                counts[str(key)] = counts.get(str(key), 0) + int(val)
            except (TypeError, ValueError):
                continue
        if rec.get("total") is not None:
            total += int(rec["total"])
    return {"available": available, "counts": counts, "total": total if available else None}


def option_label(card: dict, names_zh: dict[str, str]) -> str:
    name = card["name"]
    zh = names_zh.get(name) or card.get("name_zh")
    number = card.get("number_base")
    prints = card.get("prints") or {}
    ref_id = card.get("reference_print") or reference_print(prints)
    combined = combine_grader(prints.get(ref_id) or {})
    counts = combined.get("counts") or {}
    total = combined.get("total")
    gem = counts.get("10")
    rate = grade_share(gem, total)
    ref_label = PRINT_LABELS.get(ref_id, ref_id)
    name_bit = f"{zh} {name}" if zh else name
    head = f"#{number} {name_bit}" if number is not None else name_bit
    head = " ".join(head.split())
    if gem is None or total is None:
        return f"{head} · {ref_label} PSA+CGC 10 —"
    pct = f"{rate * 100:.2f}%" if rate is not None else "—"
    return f"{head} · {ref_label} PSA+CGC 10 {gem:,} · {pct}"


def build_payload(catalog: dict, fetched: dict[str, dict], *, names_zh: dict[str, str], as_of: str, as_of_date: str) -> dict:
    cards = []
    for spec in catalog["cards"]:
        prints = fetched[spec["id"]]
        card = {
            "id": spec["id"],
            "name": spec["name"],
            "name_zh": names_zh.get(spec["name"]),
            "number_base": spec.get("number_base"),
            "number_base2": spec.get("number_base2"),
            "rarity": spec.get("rarity"),
            "line": spec.get("line"),
            "reference_print": reference_print(prints),
            "prints": prints,
        }
        card["option_label"] = option_label(card, names_zh)
        cards.append(card)
    return {
        "asOfDate": as_of_date,
        "asOf": as_of,
        "staleDays": 14,
        "defaultCardId": catalog.get("default_card_id") or "charizard",
        "source": {
            "name": "PriceCharting public POP report",
            "kind": "pricecharting_pop_html",
            "home": "https://www.pricecharting.com/",
            "cadence": "PriceCharting updates monthly; this site refreshes weekly.",
            "graders": "PSA and CGC columns as published. The page displays PSA+CGC combined per grade. Beckett/BGS is not collected.",
            "half_grades": "PriceCharting already buckets *.5 into the lower whole grade (8.5→8, 9.5→9). Raw half-grade rows are stored only when the HTML lists them.",
            "prices": (
                "Indicative USD from the PriceCharting game page (not the POP table). "
                "used=raw, complete=Grade 7, new=Grade 8, graded=Grade 9, manual_only=PSA 10. "
                "Missing cells omitted, never invented. UI converts USD→HKD ×7.80."
            ),
        },
        "prints": catalog.get("prints") or [],
        "graders": [
            {"id": "psa", "label": "PSA", "default": True},
            {"id": "cgc", "label": "CGC", "default": False},
        ],
        "cards": cards,
    }


def write_outputs(root: Path, payload: dict) -> None:
    data_dir = root / "data" / "base-psa-pop"
    data_dir.mkdir(parents=True, exist_ok=True)
    latest = data_dir / "latest.json"
    latest.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    js = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    (data_dir / "latest.js").write_text("window.BASE_PSA_POP = " + js + ";\n", encoding="utf-8")


def refresh(
    root: Path,
    *,
    html_dir: Path | None = None,
    fetch=fetch_html,
    sleeper=None,
    dry_run: bool = False,
    print_ids: tuple[str, ...] | None = None,
) -> dict:
    wanted = print_ids or PRINT_IDS
    unknown = [pid for pid in wanted if pid not in PRINT_IDS]
    if unknown:
        raise SystemExit(f"unknown print id(s): {unknown}")
    catalog = load_catalog(root)
    names_zh = load_names_zh(root)
    previous = load_previous_prices(root)
    previous_prints = load_previous_prints(root) if print_ids else {}
    fetched: dict[str, dict] = {}
    errors: list[str] = []
    for spec in catalog["cards"]:
        prints: dict[str, dict] = {}
        if print_ids:
            prints.update(previous_prints.get(spec["id"]) or {})
        for pid in wanted:
            slug = (spec.get("prints") or {}).get(pid)
            if not slug:
                continue
            url = pop_url(slug)
            try:
                if html_dir is not None:
                    local = html_dir / f"{spec['id']}-{pid}.html"
                    if not local.is_file():
                        raise ParseError(f"missing fixture {local}")
                    html = local.read_text(encoding="utf-8")
                else:
                    html = fetch(url)
                parsed = parse_pop_table(html, url=url)
                rec = attach_print(spec, pid, slug, parsed, html)
                prices = fetch_print_prices(
                    spec, pid, slug, html_dir=html_dir, fetch=fetch, previous=previous
                )
                if prices:
                    rec["pc_prices"] = prices
                prints[pid] = rec
            except (ParseError, FetchError) as err:
                if spec.get("required") or pid != "base2":
                    errors.append(f"{spec['id']}/{pid}: {err}")
                else:
                    print(f"skip optional {spec['id']}/{pid}: {err}", file=sys.stderr)
            if sleeper and html_dir is None:
                sleeper()
        fetched[spec["id"]] = prints

    if errors:
        raise SystemExit("PARSE/FETCH FAILED (no write, no invented pops):\n  " + "\n  ".join(errors))

    if not print_ids and (
        "charizard" not in fetched or not fetched["charizard"].get("unlimited")
    ):
        raise SystemExit("required card charizard/unlimited missing — no write")
    for spec in catalog["cards"]:
        if not spec.get("required"):
            continue
        for pid, slug in (spec.get("prints") or {}).items():
            if print_ids and pid not in wanted:
                continue
            if slug and pid not in fetched.get(spec["id"], {}):
                raise SystemExit(f"required {spec['id']}/{pid} missing — no write")

    payload = build_payload(
        catalog,
        fetched,
        names_zh=names_zh,
        as_of=now_shanghai(),
        as_of_date=today_ymd(),
    )
    if not dry_run:
        write_outputs(root, payload)
    return payload


def refresh_prices_only(
    root: Path,
    *,
    fetch=fetch_html,
    sleeper=None,
    dry_run: bool = False,
) -> dict:
    """Patch pc_prices onto the existing POP snapshot. Does not re-fetch pops."""
    path = root / "data" / "base-psa-pop" / "latest.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    previous = load_previous_prices(root)
    catalog = {c["id"]: c for c in load_catalog(root)["cards"]}
    updated = 0
    kept = 0
    for card in payload.get("cards") or []:
        spec = catalog.get(card["id"]) or {"id": card["id"], "prints": {}}
        for pid, rec in (card.get("prints") or {}).items():
            slug = rec.get("slug") or (spec.get("prints") or {}).get(pid)
            if not slug:
                continue
            rec["game_url"] = game_url(slug)
            prices = fetch_print_prices(
                {"id": card["id"]}, pid, slug, html_dir=None, fetch=fetch, previous=previous
            )
            if prices:
                rec["pc_prices"] = prices
                updated += 1
            elif rec.get("pc_prices"):
                kept += 1
            if sleeper:
                sleeper()
    notes = payload.setdefault("source", {})
    notes["prices"] = (
        "Indicative USD from the PriceCharting game page (not the POP table). "
        "used=raw, complete=Grade 7, new=Grade 8, graded=Grade 9, manual_only=PSA 10. "
        "Missing cells omitted, never invented. UI converts USD→HKD ×7.80."
    )
    if not dry_run:
        write_outputs(root, payload)
    print(f"prices-only updated={updated} kept={kept} cards={len(payload.get('cards') or [])}")
    return payload


def _assert(cond: bool, msg: str) -> None:
    if not cond:
        raise AssertionError(msg)


def self_test() -> None:
    unl = parse_pop_table(
        (TESTDATA / "charizard-unlimited.html").read_text(encoding="utf-8"),
        url="fixture:unlimited",
    )
    psa = unl["graders"]["psa"]
    cgc = unl["graders"]["cgc"]
    _assert(psa["available"] and psa["counts"]["10"] == 486, psa)
    _assert(psa["total"] == 99246, psa["total"])
    _assert(cgc["available"] and cgc["counts"]["10"] == 4 and cgc["total"] == 9, cgc)
    _assert("bgs" not in unl["graders"], "Beckett/BGS is not stored")
    _assert(unl["half_grades_bucketed_by_source"], "PC note should mark bucketed source")
    _assert(grade_share(486, 99246) is not None, "rate")
    _assert(abs(grade_share(486, 99246) - 486 / 99246) < 1e-12, "rate math")

    first = parse_pop_table(
        (TESTDATA / "charizard-1st.html").read_text(encoding="utf-8"),
        url="fixture:1st",
    )
    _assert(first["graders"]["psa"]["counts"]["10"] == 123, first["graders"]["psa"])
    _assert(first["graders"]["psa"]["total"] == 5383, first["graders"]["psa"]["total"])
    _assert(first["graders"]["cgc"]["total"] == 597, first["graders"]["cgc"]["total"])

    shadowless = parse_pop_table(
        (TESTDATA / "charizard-shadowless.html").read_text(encoding="utf-8"),
        url="fixture:shadowless",
    )
    _assert(shadowless["graders"]["psa"]["counts"]["10"] == 58, shadowless["graders"]["psa"])
    _assert(shadowless["graders"]["psa"]["total"] == 13006, shadowless["graders"]["psa"]["total"])
    _assert(shadowless["graders"]["cgc"]["counts"]["10"] == 38, shadowless["graders"]["cgc"])
    _assert(shadowless["graders"]["cgc"]["total"] == 1855, shadowless["graders"]["cgc"]["total"])

    base2 = parse_pop_table(
        (TESTDATA / "charizard-base2.html").read_text(encoding="utf-8"),
        url="fixture:base2",
    )
    _assert(base2["graders"]["psa"]["counts"]["10"] == 70, base2["graders"]["psa"])
    _assert(base2["graders"]["psa"]["total"] == 21754, base2["graders"]["psa"]["total"])
    _assert(base2["graders"]["cgc"]["total"] == 4047, base2["graders"]["cgc"]["total"])

    half = parse_pop_table(
        (TESTDATA / "half-and-bgs.html").read_text(encoding="utf-8"),
        url="fixture:half",
    )
    _assert("bgs" not in half["graders"], "BGS column must be ignored, not stored")
    _assert(half["graders"]["psa"]["resolution"] == "half", half["graders"]["psa"])
    _assert(half["graders"]["psa"]["counts"]["9.5"] == 3, half["graders"]["psa"]["counts"])
    _assert(half["graders"]["cgc"]["counts"]["8.5"] == 6, half["graders"]["cgc"]["counts"])
    _assert(half["graders"]["cgc"]["total"] == 23, half["graders"]["cgc"])
    bucketed = bucket_lower(half["graders"]["psa"]["counts"])
    _assert(bucketed["10"] == 5, bucketed)
    _assert(bucketed["9"] == 23, bucketed)  # 20 + 3
    _assert(bucketed["8"] == 12, bucketed)  # 10 + 2
    _assert(bucketed["1"] == 1, bucketed)
    cum = cumulative_shares(bucketed, [str(g) for g in WHOLE_GRADES], 41)
    _assert(abs((cum["10"] or 0) - 5 / 41) < 1e-12, cum)
    _assert(abs((cum["9"] or 0) - 28 / 41) < 1e-12, cum)  # 5+23
    _assert(abs((cum["8"] or 0) - 40 / 41) < 1e-12, cum)

    try:
        parse_pop_table((TESTDATA / "missing-table.html").read_text(encoding="utf-8"))
        raise AssertionError("missing table must fail")
    except ParseError:
        pass

    try:
        parse_pop_table((TESTDATA / "total-mismatch.html").read_text(encoding="utf-8"))
        raise AssertionError("total mismatch must fail")
    except ParseError:
        pass

    catalog = load_catalog(ROOT_DEFAULT)
    ids = [c["id"] for c in catalog["cards"]]
    _assert(len(ids) >= 100, f"full Base Set roster expected, got {len(ids)}")
    _assert(ids[0] == "alakazam", ids[:3])
    _assert("charizard" in ids and "machamp" in ids and "clefairy" in ids and "pikachu" in ids, ids)
    print_ids = [p["id"] for p in catalog.get("prints") or []]
    _assert(print_ids == ["1st", "shadowless", "unlimited", "base2"], print_ids)
    charizard = next(c for c in catalog["cards"] if c["id"] == "charizard")
    _assert(charizard["prints"].get("shadowless", "").endswith("charizard-shadowless-4"), charizard)
    pikachu = next(c for c in catalog["cards"] if c["id"] == "pikachu")
    _assert(pikachu["prints"].get("shadowless", "").endswith("pikachu-shadowless-58"), pikachu)
    _assert("red-cheeks" not in (pikachu["prints"].get("shadowless") or ""), pikachu)
    machamp = next(c for c in catalog["cards"] if c["id"] == "machamp")
    _assert(machamp["prints"].get("1st"), machamp)
    _assert("unlimited" not in machamp["prints"], machamp)
    _assert(machamp["prints"].get("shadowless", "").endswith("machamp-shadowless-8"), machamp)
    venusaur = next(c for c in catalog["cards"] if c["id"] == "venusaur")
    _assert(venusaur["number_base"] == 15 and venusaur["number_base2"] == 18, venusaur)
    _assert(venusaur["prints"]["base2"].endswith("venusaur-18"), venusaur["prints"])

    names = load_names_zh(ROOT_DEFAULT)
    _assert(names.get("Charizard") == "噴火龍", names.get("Charizard"))
    card = {
        "name": "Charizard",
        "number_base": 4,
        "reference_print": "unlimited",
        "prints": {
            "unlimited": {
                "graders": {
                    "psa": {"available": True, "counts": {"10": 486}, "total": 99246}
                }
            }
        },
    }
    label = option_label(card, names)
    _assert("#4 噴火龍 Charizard" in label, label)
    _assert("Unlimited PSA+CGC 10 486" in label, label)
    _assert("0.49%" in label, label)
    invent = option_label({"name": "NotAPokemon", "prints": {}}, names)
    _assert("NotAPokemon" in invent and "噴" not in invent, invent)

    sh_prices = parse_pc_prices(
        (TESTDATA / "charizard-shadowless-game.html").read_text(encoding="utf-8")
    )
    _assert(sh_prices.get("raw") == 983.44, sh_prices)
    _assert(sh_prices.get("10") == 24808.28, sh_prices)
    _assert("9.5" not in sh_prices, sh_prices)

    prices = parse_pc_prices((TESTDATA / "charizard-1st-game.html").read_text(encoding="utf-8"))
    _assert(prices.get("raw") == 7691.36, prices)
    _assert(prices.get("7") == 22549.75, prices)
    _assert(prices.get("8") == 29000.0, prices)
    _assert(prices.get("9") == 31900.0, prices)
    _assert(prices.get("10") == 334421.05, prices)
    _assert("9.5" not in prices, prices)
    _assert(parse_pc_prices("<html>no prices</html>") == {}, "empty prices")
    _assert(parse_pc_prices("") == {}, "blank")
    _assert(game_url("pokemon-base-set/charizard-4").endswith("/game/pokemon-base-set/charizard-4"), "game url")

    print("refresh_base_psa_pop self-test OK")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", default=str(ROOT_DEFAULT))
    ap.add_argument("--self-test", action="store_true", help="No network. Fixture checks only.")
    ap.add_argument("--dry-run", action="store_true", help="Fetch and print; do not write JSON")
    ap.add_argument(
        "--prices-only",
        action="store_true",
        help="Fetch PriceCharting game-page prices onto the existing latest.json; do not re-fetch POP",
    )
    ap.add_argument(
        "--from-html-dir",
        default="",
        help="Read {card}-{print}.html from this directory instead of the network",
    )
    ap.add_argument(
        "--prints",
        default="",
        help="Comma-separated print ids to fetch (e.g. shadowless). Merges into existing latest.json.",
    )
    args = ap.parse_args(argv)
    if args.self_test:
        self_test()
        return 0
    print_ids = tuple(p.strip() for p in args.prints.split(",") if p.strip()) or None
    if args.prices_only:
        payload = refresh_prices_only(
            Path(args.root),
            sleeper=(lambda: time.sleep(SLEEP_S)),
            dry_run=args.dry_run,
        )
        print("as_of", payload["asOf"], "cards", len(payload["cards"]))
        return 0
    html_dir = Path(args.from_html_dir) if args.from_html_dir else None
    payload = refresh(
        Path(args.root),
        html_dir=html_dir,
        sleeper=(lambda: time.sleep(SLEEP_S)),
        dry_run=args.dry_run,
        print_ids=print_ids,
    )
    print("as_of", payload["asOf"], "cards", len(payload["cards"]))
    for card in payload["cards"]:
        print(" ", card["option_label"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
