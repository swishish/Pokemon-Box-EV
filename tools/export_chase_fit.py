#!/usr/bin/env python3
"""Export Gary's weekly PSA10 regression workbook to the Chase 公允值 page.

Reads sheet Chase_Fit (live Strong Buy model — not Chase_Fit_Large) plus
Raw_Data URLs, then writes:

  data/chase-fit/latest.json
  data/chase-fit/latest.js          window.CHASE_FIT fallback
  data/chase-fit/archive/YYYY-MM-DD.json   previous latest, before overwrite
  data/chase-fit/thumbs.json        JP local/SNK / EN TCGPlayer / public card art (after write)

Stdlib only (zipfile + xml) — no openpyxl / pandas. GitHub Actions can
`--self-test` without the workbook. Publishing still needs the dated xlsx
from Gary (Google Drive); there is no public dump to fetch.

Refuse to publish if required meta / coefficient / card columns are missing.
Never invent prices or Strong Buy labels.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
import zipfile
from xml.etree import ElementTree as ET

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(ROOT, "data", "chase-fit")
LATEST_JSON = os.path.join(DATA_DIR, "latest.json")
LATEST_JS = os.path.join(DATA_DIR, "latest.js")
ARCHIVE_DIR = os.path.join(DATA_DIR, "archive")

NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
PKG_NS = {"r": "http://schemas.openxmlformats.org/package/2006/relationships"}
OFFICE_REL = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"

LIVE_SHEET = "Chase_Fit"
FX_DEFAULT = 7.8
FORMULA_DEFAULT = "log_psa10 ~ gem_rate + log_pop + log_raw + C(language) + promo_flag"

REQUIRED_COEF_TERMS = (
    "Intercept",
    "C(language)[T.JP]",
    "gem_rate",
    "log_pop",
    "log_raw",
    "promo_flag",
)
REQUIRED_CARD_COLS = (
    "card_id",
    "name",
    "language",
    "promo_flag",
    "raw_usd",
    "psa10_usd",
    "gem_rate",
    "pop_psa10",
    "pred_usd",
    "residual_pct",
    "fv_hkd",
    "label",
)
REQUIRED_COEF_COLS = ("term", "coef")

_DATE_RE = re.compile(r"(20\d{2}-\d{2}-\d{2})")
_HTTP_RE = re.compile(r"https?://[^\s|]+")


class ExportError(Exception):
    """User-facing refusal (missing columns / empty sheet)."""


def _col_row(ref: str) -> tuple[int, int]:
    m = re.match(r"^([A-Z]+)(\d+)$", ref.upper())
    if not m:
        raise ExportError(f"bad cell ref {ref!r}")
    col = 0
    for ch in m.group(1):
        col = col * 26 + (ord(ch) - 64)
    return col - 1, int(m.group(2)) - 1


def _xml(z: zipfile.ZipFile, name: str) -> ET.Element:
    with z.open(name) as fh:
        return ET.parse(fh).getroot()


def _shared_strings(z: zipfile.ZipFile) -> list[str]:
    names = [n for n in z.namelist() if n.lower().endswith("sharedstrings.xml")]
    if not names:
        return []
    root = _xml(z, names[0])
    out: list[str] = []
    for si in root.findall("m:si", NS):
        texts = [t.text or "" for t in si.findall(".//m:t", NS)]
        out.append("".join(texts))
    return out


def _cell_value(cell: ET.Element, strings: list[str]):
    t = cell.get("t")
    if t == "s":
        v = cell.find("m:v", NS)
        if v is None or v.text is None:
            return None
        idx = int(v.text)
        return strings[idx] if 0 <= idx < len(strings) else None
    if t == "inlineStr":
        texts = [n.text or "" for n in cell.findall(".//m:t", NS)]
        return "".join(texts) if texts else None
    if t == "b":
        v = cell.find("m:v", NS)
        return None if v is None or v.text is None else (v.text == "1")
    if t == "e":
        return None
    v = cell.find("m:v", NS)
    if v is None or v.text is None:
        return None
    raw = v.text.strip()
    if raw == "":
        return None
    try:
        if "." not in raw and "e" not in raw.lower():
            return int(raw)
        return float(raw)
    except ValueError:
        return raw


def _sheet_path_map(z: zipfile.ZipFile) -> dict[str, str]:
    wb = _xml(z, "xl/workbook.xml")
    rels = _xml(z, "xl/_rels/workbook.xml.rels")
    rid_to_target = {}
    for rel in rels:
        rid = rel.get("Id")
        target = rel.get("Target")
        if rid and target:
            target = target.lstrip("/")
            if not target.startswith("xl/"):
                target = "xl/" + target
            rid_to_target[rid] = target
    out = {}
    for sh in wb.findall("m:sheets/m:sheet", NS):
        name = sh.get("name")
        rid = sh.get(f"{OFFICE_REL}id")
        if name and rid and rid in rid_to_target:
            out[name] = rid_to_target[rid]
    return out


def read_sheet_rows(xlsx_path: str, sheet_name: str) -> list[list]:
    """Return a dense list-of-lists for `sheet_name` (None-padded)."""
    with zipfile.ZipFile(xlsx_path) as z:
        paths = _sheet_path_map(z)
        if sheet_name not in paths:
            raise ExportError(
                f"workbook has no sheet {sheet_name!r}. sheets={sorted(paths)}"
            )
        strings = _shared_strings(z)
        root = _xml(z, paths[sheet_name])
        max_r = max_c = 0
        cells: dict[tuple[int, int], object] = {}
        for c in root.findall("m:sheetData/m:row/m:c", NS):
            ref = c.get("r")
            if not ref:
                continue
            col, row = _col_row(ref)
            cells[(row, col)] = _cell_value(c, strings)
            if row > max_r:
                max_r = row
            if col > max_c:
                max_c = col
        dim = root.find("m:dimension", NS)
        if dim is not None:
            ref = dim.get("ref") or ""
            if ":" in ref:
                _, end = ref.split(":", 1)
                try:
                    c2, r2 = _col_row(end)
                    max_c = max(max_c, c2)
                    max_r = max(max_r, r2)
                except ExportError:
                    pass
        rows = [[None] * (max_c + 1) for _ in range(max_r + 1)]
        for (r, c), v in cells.items():
            rows[r][c] = v
        return rows


def _as_str(v) -> str:
    if v is None:
        return ""
    if isinstance(v, float) and v == int(v):
        return str(int(v))
    return str(v).strip()


def _as_float(v):
    if v is None or v == "":
        return None
    if isinstance(v, bool):
        return float(int(v))
    if isinstance(v, (int, float)):
        if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
            return None
        return float(v)
    s = str(v).strip().replace(",", "")
    if s == "":
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _as_int(v):
    f = _as_float(v)
    if f is None:
        return None
    return int(round(f))


def _first_date(*texts: str) -> str | None:
    for t in texts:
        if not t:
            continue
        m = _DATE_RE.search(t)
        if m:
            return m.group(1)
    return None


def _parse_kv_row(cell0: str) -> tuple[str, str] | None:
    if not cell0 or ":" not in cell0:
        return None
    key, val = cell0.split(":", 1)
    return key.strip(), val.strip()


def find_header_row(rows: list[list], required: tuple[str, ...]) -> int:
    need = {c.lower() for c in required}
    for i, row in enumerate(rows):
        keys = {str(c).strip().lower() for c in row if c not in (None, "")}
        if need.issubset(keys):
            return i
    raise ExportError(
        "missing header row with columns: " + ", ".join(required)
    )


def rows_to_dicts(rows: list[list], header_i: int) -> list[dict]:
    headers = [_as_str(h) for h in rows[header_i]]
    out = []
    for row in rows[header_i + 1 :]:
        if not row or all(c in (None, "") for c in row):
            # blank separator — stop if we already collected some rows
            if out:
                break
            continue
        d = {}
        for i, key in enumerate(headers):
            if not key:
                continue
            d[key] = row[i] if i < len(row) else None
        # skip leftover chart helper rows (yx_x / zeros)
        first = next((d[k] for k in d if d[k] not in (None, "")), None)
        if first is None:
            if out:
                break
            continue
        out.append(d)
    return out


def extract_chase_fit(rows: list[list]) -> tuple[dict, list[dict], list[dict]]:
    if not rows:
        raise ExportError("Chase_Fit sheet is empty")
    title = _as_str(rows[0][0] if rows[0] else "")
    generated = _as_str(rows[1][0] if len(rows) > 1 and rows[1] else "")
    formula_line = _as_str(rows[2][0] if len(rows) > 2 and rows[2] else "")
    formula = formula_line
    if formula.lower().startswith("formula:"):
        formula = formula.split(":", 1)[1].strip()
    if not formula:
        formula = FORMULA_DEFAULT

    meta = {
        "model": LIVE_SHEET,
        "title": title,
        "generated": generated,
        "formula": formula,
        "fx_hkd_per_usd": FX_DEFAULT,
        "notes": "",
        "n": None,
        "r2": None,
        "adj_r2": None,
    }
    fx_m = re.search(r"US\$1\s*=\s*HK\$([0-9.]+)", generated, re.I)
    if fx_m:
        meta["fx_hkd_per_usd"] = float(fx_m.group(1))
    meta["data_date"] = _first_date(title, generated) or ""

    for row in rows[:20]:
        cell0 = _as_str(row[0] if row else "")
        kv = _parse_kv_row(cell0)
        if kv:
            key, val = kv
            if key.lower().startswith("jp-first"):
                meta["notes"] = f"{key}: {val}".strip() if val else key
                continue
        else:
            if cell0.lower().startswith("jp-first"):
                meta["notes"] = cell0
            continue
        key, val = kv
        kl = key.lower().replace("r2", "r²")
        if kl.startswith("n (pooled") or kl == "n":
            meta["n"] = _as_int(val)
        elif "adj" in kl and "r²" in kl and "en-only" not in kl:
            meta["adj_r2"] = _as_float(val)
        elif kl.startswith("r²") and "en-only" not in kl:
            meta["r2"] = _as_float(val)

    coef_header_i = None
    for i, row in enumerate(rows[:40]):
        keys = [_as_str(c).lower() for c in row[:6]]
        if keys[:2] == ["term", "coef"]:
            coef_header_i = i
            break
    if coef_header_i is None:
        raise ExportError("Chase_Fit is missing the Coefficients table (term/coef)")
    coef_dicts = rows_to_dicts(rows, coef_header_i)
    coefficients = []
    for d in coef_dicts:
        term = _as_str(d.get("term"))
        if not term or term.lower() in ("term",):
            continue
        # stop when we hit the per-row section title
        if term.lower().startswith("per-row") or term == "card_id":
            break
        coef = _as_float(d.get("coef"))
        if coef is None:
            continue
        coefficients.append(
            {
                "term": term,
                "coef": coef,
                "std_err": _as_float(d.get("std_err")),
                "t": _as_float(d.get("t")),
                "p": _as_float(d.get("p")),
            }
        )

    card_header_i = find_header_row(rows, ("card_id", "name", "pred_usd", "residual_pct"))
    cards = rows_to_dicts(rows, card_header_i)
    return meta, coefficients, cards


def extract_raw_by_id(rows: list[list]) -> dict[str, dict]:
    if not rows:
        return {}
    header_i = find_header_row(rows, ("card_id", "name"))
    out = {}
    headers = [_as_str(h) for h in rows[header_i]]
    for row in rows[header_i + 1 :]:
        if not row or all(c in (None, "") for c in row):
            continue
        d = {}
        for i, key in enumerate(headers):
            if not key:
                continue
            d[key] = row[i] if i < len(row) else None
        cid = _as_str(d.get("card_id"))
        if cid:
            out[cid] = d
    return out


def workbook_filename(xlsx_path: str) -> str:
    base = os.path.basename(xlsx_path)
    m = re.match(r"(pokemon_psa10_regression_\d{4}-\d{2}-\d{2})", base)
    return f"{m.group(1)}.xlsx" if m else base


def _source_urls(raw: dict | None, pc_url: str) -> list[str]:
    urls = []
    seen = set()
    blob = ""
    if raw:
        blob = " ".join(_as_str(raw.get(k)) for k in ("url", "sources", "jp_twin_url"))
    for u in _HTTP_RE.findall(blob) + ([pc_url] if pc_url else []):
        u = u.rstrip(").,;")
        if u not in seen:
            seen.add(u)
            urls.append(u)
    return urls


def _primary_market(language: str) -> str:
    if language == "JP":
        return "SNKRDUNK Grade A + PriceCharting PSA10"
    return "PriceCharting / TCGPlayer"


def _ln(v):
    f = _as_float(v)
    if f is None or f <= 0:
        return None
    return math.log(f)


def normalize_card(row: dict, raw: dict | None, fx: float) -> dict:
    missing = [c for c in REQUIRED_CARD_COLS if c not in row]
    if missing:
        raise ExportError("card row missing columns: " + ", ".join(missing))
    cid = _as_str(row.get("card_id"))
    name = _as_str(row.get("name"))
    if not cid or not name:
        raise ExportError("card row missing card_id or name")
    language = _as_str(row.get("language")).upper() or None
    raw_usd = _as_float(row.get("raw_usd"))
    psa10_usd = _as_float(row.get("psa10_usd"))
    pred_usd = _as_float(row.get("pred_usd"))
    gem_rate = _as_float(row.get("gem_rate"))
    pop = _as_float(row.get("pop_psa10"))
    residual_pct = _as_float(row.get("residual_pct"))
    fv_hkd = _as_float(row.get("fv_hkd"))
    residual_usd = _as_float(row.get("residual_usd"))
    residual_hkd = _as_float(row.get("residual_hkd"))
    if residual_usd is None and psa10_usd is not None and pred_usd is not None:
        residual_usd = psa10_usd - pred_usd
    if fv_hkd is None and pred_usd is not None:
        fv_hkd = pred_usd * fx
    if residual_hkd is None and residual_usd is not None:
        residual_hkd = residual_usd * fx
    multiple = None
    if raw:
        multiple = _as_float(raw.get("psa10_raw_multiple"))
    if multiple is None:
        multiple = _as_float(row.get("psa10_raw_multiple"))
    if multiple is None and psa10_usd and raw_usd and raw_usd > 0:
        multiple = psa10_usd / raw_usd
    pc_url = ""
    if raw:
        pc_url = _as_str(raw.get("url"))
    if not pc_url:
        pc_url = _as_str(row.get("url") or row.get("pricecharting_url"))
    label = row.get("label")
    if label in ("", None):
        label = None
    else:
        label = _as_str(label) or None
    log_raw = _as_float(row.get("log_raw"))
    if log_raw is None:
        log_raw = _ln(raw_usd)
    log_psa10 = _as_float(row.get("log_psa10"))
    if log_psa10 is None:
        log_psa10 = _ln(psa10_usd)
    log_pred = _as_float(row.get("log_pred"))
    if log_pred is None:
        log_pred = _ln(pred_usd)
    log_pop = _as_float(row.get("log_pop"))
    if log_pop is None and pop is not None and pop >= 0:
        log_pop = math.log(pop + 1.0)
    pop_flag = row.get("pop_flag")
    if pop_flag in ("", None) and raw:
        pop_flag = raw.get("pop_flag")
    if pop_flag == "":
        pop_flag = None
    else:
        pop_flag = _as_str(pop_flag) or None
    card = {
        "card_id": cid,
        "name": name,
        "language": language,
        "promo_flag": _as_int(row.get("promo_flag")) or 0,
        "raw_usd": raw_usd,
        "psa10_usd": psa10_usd,
        "gem_rate": gem_rate,
        "pop_psa10": _as_int(pop),
        "sales_per_year": _as_int(row.get("sales_per_year")),
        "n_sold_note": _as_str(row.get("n_sold_note")) or None,
        "low_liquidity": _as_str(row.get("low_liquidity")) or None,
        "pred_usd": pred_usd,
        "residual_usd": residual_usd,
        "residual_pct": residual_pct,
        "residual_hkd": residual_hkd,
        "fv_hkd": fv_hkd,
        "label": label,
        "old_residual_pct_prior": _as_float(row.get("old_residual_pct_prior")),
        "psa10_chg_pct_vs_prior": _as_float(row.get("psa10_chg_pct_vs_prior")),
        "pop_flag": pop_flag,
        "psa10_raw_multiple": multiple,
        "log_psa10": log_psa10,
        "log_pred": log_pred,
        "log_raw": log_raw,
        "log_pop": log_pop,
        "pricecharting_url": pc_url or None,
        "source_urls": _source_urls(raw, pc_url),
        "primary_market": _primary_market(language or ""),
    }
    return card


def validate_publish(meta: dict, coefficients: list[dict], cards: list[dict]) -> None:
    missing_meta = [k for k in ("model", "data_date", "formula", "n", "r2") if not meta.get(k)]
    if missing_meta:
        raise ExportError("missing meta fields: " + ", ".join(missing_meta))
    if meta.get("model") != LIVE_SHEET:
        raise ExportError(f"refusing to publish model {meta.get('model')!r}; expected {LIVE_SHEET}")
    terms = {c.get("term") for c in coefficients}
    missing_terms = [t for t in REQUIRED_COEF_TERMS if t not in terms]
    if missing_terms:
        raise ExportError("missing coefficient terms: " + ", ".join(missing_terms))
    if not cards:
        raise ExportError("Chase_Fit has no card rows")
    declared = meta.get("n")
    if declared is not None and int(declared) != len(cards):
        raise ExportError(
            f"meta n={declared} does not match card rows {len(cards)} — refusing to publish"
        )
    for i, card in enumerate(cards):
        for col in ("card_id", "name", "pred_usd", "psa10_usd", "residual_pct"):
            if card.get(col) in (None, ""):
                raise ExportError(f"card[{i}] missing {col}")
    # residual_pct on this workbook is percent (−80.7), not a fraction.
    abs_r = [abs(c["residual_pct"]) for c in cards if c.get("residual_pct") is not None]
    if abs_r:
        med = sorted(abs_r)[len(abs_r) // 2]
        if med < 1.5:
            raise ExportError(
                f"residual_pct looks like a fraction (median abs={med:.4f}); "
                "refusing to publish (page expects percent units)"
            )


def strong_buy_entries(cards: list[dict]) -> list[dict]:
    out = []
    for c in cards:
        if c.get("label") != "Strong Buy":
            continue
        out.append(
            {
                "card_id": c.get("card_id"),
                "name": c.get("name"),
                "language": c.get("language"),
                "residual_pct": c.get("residual_pct"),
            }
        )
    out.sort(key=lambda x: (x.get("residual_pct") is None, x.get("residual_pct") or 0))
    return out


def diff_strong_buys(prior_cards: list[dict], new_cards: list[dict]) -> dict:
    prior = {e["card_id"]: e for e in strong_buy_entries(prior_cards)}
    now = {e["card_id"]: e for e in strong_buy_entries(new_cards)}
    enter = [now[k] for k in now if k not in prior]
    exit_ = [prior[k] for k in prior if k not in now]
    still = [now[k] for k in now if k in prior]
    enter.sort(key=lambda x: (x.get("residual_pct") is None, x.get("residual_pct") or 0))
    exit_.sort(key=lambda x: (x.get("residual_pct") is None, x.get("residual_pct") or 0))
    still.sort(key=lambda x: (x.get("residual_pct") is None, x.get("residual_pct") or 0))
    return {
        "enter": enter,
        "exit": exit_,
        "still": still,
        "counts": {"enter": len(enter), "exit": len(exit_), "still": len(still), "strong_buy": len(now)},
    }


def load_json(path: str) -> dict | None:
    if not os.path.isfile(path):
        return None
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def write_json(path: str, data: dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
        fh.write("\n")


def write_js(path: str, data: dict) -> None:
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    payload = payload.replace("</", "<\\/")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("window.CHASE_FIT = ")
        fh.write(payload)
        fh.write(";\n")


def refresh_thumbs() -> str | None:
    """Rebuild thumbs.json from existing site image sources. Non-fatal."""
    path = os.path.join(ROOT, "tools", "build_chase_thumbs.py")
    if not os.path.isfile(path):
        print("WARN: tools/build_chase_thumbs.py missing; skip thumbs", file=sys.stderr)
        return None
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location("build_chase_thumbs", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        thumbs = mod.build_from_repo()
        dest = mod.write_thumbs(thumbs)
        counts = thumbs.get("counts") or {}
        print(
            "wrote",
            os.path.relpath(dest, ROOT),
            f"local={counts.get('local', 0)} tcgplayer={counts.get('tcgplayer', 0)} "
            f"snkrdunk={counts.get('snkrdunk', 0)} pokemontcg={counts.get('pokemontcg', 0)} "
            f"tcgdex={counts.get('tcgdex', 0)} none={counts.get('none', 0)}",
        )
        return dest
    except Exception as err:
        print(f"WARN: thumbs rebuild failed (non-fatal): {err}", file=sys.stderr)
        return None


def archive_previous(latest: dict | None) -> str | None:
    if not latest or not isinstance(latest, dict):
        return None
    prior_date = (latest.get("meta") or {}).get("data_date")
    if not prior_date:
        return None
    os.makedirs(ARCHIVE_DIR, exist_ok=True)
    dest = os.path.join(ARCHIVE_DIR, f"{prior_date}.json")
    write_json(dest, latest)
    return dest


def build_payload(
    xlsx_path: str,
    *,
    sheet: str = LIVE_SHEET,
    prior: dict | None = None,
) -> dict:
    if sheet != LIVE_SHEET:
        raise ExportError(
            f"refusing sheet {sheet!r}; public page uses {LIVE_SHEET} "
            "(Chase_Fit_Large is not the live Strong Buy model)"
        )
    chase_rows = read_sheet_rows(xlsx_path, sheet)
    meta, coefficients, raw_cards = extract_chase_fit(chase_rows)
    try:
        raw_map = extract_raw_by_id(read_sheet_rows(xlsx_path, "Raw_Data"))
    except ExportError:
        raw_map = {}
    meta["workbook"] = workbook_filename(xlsx_path)
    fx = float(meta.get("fx_hkd_per_usd") or FX_DEFAULT)
    cards = [normalize_card(r, raw_map.get(_as_str(r.get("card_id"))), fx) for r in raw_cards]
    cards.sort(key=lambda c: (c.get("residual_pct") is None, c.get("residual_pct") or 0, c.get("name") or ""))
    missing_url = [c["card_id"] for c in cards if not c.get("pricecharting_url")]
    if missing_url:
        raise ExportError(
            "refusing to publish: missing PriceCharting URL for "
            + ", ".join(missing_url[:8])
            + ("…" if len(missing_url) > 8 else "")
        )
    validate_publish(meta, coefficients, cards)
    prior_cards = (prior or {}).get("cards") or []
    prior_date = ((prior or {}).get("meta") or {}).get("data_date")
    weekly = diff_strong_buys(prior_cards, cards)
    weekly["prior_date"] = prior_date
    weekly["current_date"] = meta["data_date"]
    return {
        "meta": meta,
        "coefficients": coefficients,
        "cards": cards,
        "weekly_changes": weekly,
    }


def export(xlsx_path: str, *, sheet: str = LIVE_SHEET, dry_run: bool = False) -> dict:
    if not os.path.isfile(xlsx_path):
        raise ExportError(f"workbook not found: {xlsx_path}")
    prior = load_json(LATEST_JSON)
    payload = build_payload(xlsx_path, sheet=sheet, prior=prior)
    if dry_run:
        return payload
    archived = archive_previous(prior)
    write_json(LATEST_JSON, payload)
    write_js(LATEST_JS, payload)
    payload["_archived"] = archived
    payload["_thumbs"] = refresh_thumbs()
    return payload


# ----- self-test (no workbook required) ------------------------------------

def _assert(cond: bool, msg: str) -> None:
    if not cond:
        raise AssertionError(msg)


def self_test() -> None:
    prior = [
        {"card_id": "A", "name": "Pikachu ex #179", "language": "EN", "label": "Strong Buy", "residual_pct": -45.4},
        {"card_id": "B", "name": "Sobble", "language": "JP", "label": "Strong Buy", "residual_pct": -83.1},
        {"card_id": "C", "name": "Other", "language": "EN", "label": "Watch / Second Opinion", "residual_pct": -20},
    ]
    new = [
        {"card_id": "B", "name": "Sobble", "language": "JP", "label": "Strong Buy", "residual_pct": -80.7},
        {"card_id": "D", "name": "Darkrai", "language": "JP", "label": "Strong Buy", "residual_pct": -71.7},
        {"card_id": "A", "name": "Pikachu ex #179", "language": "EN", "label": None, "residual_pct": -45.6},
    ]
    d = diff_strong_buys(prior, new)
    _assert([e["card_id"] for e in d["enter"]] == ["D"], f"enter {d['enter']}")
    _assert([e["card_id"] for e in d["exit"]] == ["A"], f"exit {d['exit']}")
    _assert([e["card_id"] for e in d["still"]] == ["B"], f"still {d['still']}")
    _assert(d["counts"]["strong_buy"] == 2, f"counts {d['counts']}")

    # missing columns refuse
    try:
        validate_publish({"model": LIVE_SHEET}, [], [])
        raise AssertionError("expected refuse on empty")
    except ExportError:
        pass

    # fraction residual refuse
    meta = {
        "model": LIVE_SHEET,
        "data_date": "2026-10-16",
        "formula": FORMULA_DEFAULT,
        "n": 1,
        "r2": 0.8,
    }
    coefs = [{"term": t, "coef": 0.1} for t in REQUIRED_COEF_TERMS]
    cards = [
        {
            "card_id": "X",
            "name": "Test",
            "language": "JP",
            "pred_usd": 100,
            "psa10_usd": 50,
            "residual_pct": -0.5,
        }
    ]
    try:
        validate_publish(meta, coefs, cards)
        raise AssertionError("expected refuse on fraction residual")
    except ExportError as err:
        _assert("fraction" in str(err), str(err))

    cards[0]["residual_pct"] = -50.0
    validate_publish(meta, coefs, cards)

    wrapped = {"meta": meta, "cards": cards}
    tmp = os.path.join(os.path.dirname(__file__), "testdata", "_chase_fit_selftest.js")
    os.makedirs(os.path.dirname(tmp), exist_ok=True)
    write_js(tmp, wrapped)
    text = open(tmp, encoding="utf-8").read()
    _assert(text.startswith("window.CHASE_FIT = "), text[:40])
    _assert(text.endswith(";\n"), repr(text[-10:]))
    os.remove(tmp)
    print("export_chase_fit self-test ok")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("xlsx", nargs="?", help="Path to pokemon_psa10_regression_YYYY-MM-DD.xlsx")
    ap.add_argument("--sheet", default=LIVE_SHEET, help=f"Sheet to publish (must be {LIVE_SHEET})")
    ap.add_argument("--dry-run", action="store_true", help="Parse and validate; do not write files")
    ap.add_argument("--self-test", action="store_true", help="Run unit checks and exit")
    args = ap.parse_args(argv)
    if args.self_test:
        self_test()
        return 0
    if not args.xlsx:
        print("ERROR: pass the weekly workbook path (or --self-test).", file=sys.stderr)
        return 2
    try:
        payload = export(args.xlsx, sheet=args.sheet, dry_run=args.dry_run)
    except ExportError as err:
        print(f"ERROR: {err}", file=sys.stderr)
        return 1
    meta = payload["meta"]
    weekly = payload["weekly_changes"]
    sbs = [c for c in payload["cards"] if c.get("label") == "Strong Buy"]
    print(
        f"{'DRY ' if args.dry_run else ''}Chase_Fit {meta['data_date']} "
        f"n={meta['n']} R²={meta['r2']:.4f} Strong Buy={len(sbs)} "
        f"ENTER={weekly['counts']['enter']} EXIT={weekly['counts']['exit']} "
        f"still={weekly['counts']['still']}"
    )
    for lab, key in (("ENTER", "enter"), ("EXIT", "exit"), ("維持", "still")):
        rows = weekly[key]
        if not rows:
            print(f"  {lab}: （無）")
            continue
        print(f"  {lab}:")
        for e in rows:
            pct = e.get("residual_pct")
            pct_s = "—" if pct is None else f"{pct:+.1f}%"
            print(f"    {pct_s} {e.get('language')} {e.get('name')}")
    if not args.dry_run:
        print("wrote", os.path.relpath(LATEST_JSON, ROOT))
        print("wrote", os.path.relpath(LATEST_JS, ROOT))
        if payload.get("_archived"):
            print("archived", os.path.relpath(payload["_archived"], ROOT))
    return 0


if __name__ == "__main__":
    sys.exit(main())
