#!/usr/bin/env python3
"""Refresh JP sealed-box market prices into data.json / data.js.

View-only public fetches only:
  * SNKRDUNK JP apparel JSON (known snkrdunk_shrink_id, optional discovered
    "[No shrink]" sibling). No login, buy, bid, or messages.
  * PriceCharting booster-box HTML already linked from each set (ungraded avg).

Writes the landed.shrink / landed.ns fields calc.html already uses:
  landed_yen = unit_yen × 1.035 + (HK$173 / 4) × 20.3
  unit_yen   = lot ask ÷ qty  (prefer 4 then 3 then 2 then 1 box)

Never invents apparel IDs or prices. A failed fetch keeps the previous
snapshot. Does not refresh card Grade A / pity rates (those still need
tools/refresh_box_ev.py + hand-saved PriceCharting card HTML).
Does not bump data.as_of (that date is the card snapshot).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT_DEFAULT = Path(__file__).resolve().parents[1]
TESTDATA = Path(__file__).resolve().parent / "testdata" / "sealed-boxes"

USER_AGENT = "Pokemon-Box-EV/1.28.11 (+https://github.com/swishish/Pokemon-Box-EV)"
PC_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36"
)

USD_HKD = 7.80
JPY_PER_HKD = 20.3
SHIP_HKD_PER_CARTON = 173.0  # HK$173 / 4 boxes
FEE_MULT = 1.035
PREFERRED_QTY = (4, 3, 2, 1)
MIN_UNIT_YEN = 1000.0
MAX_UNIT_YEN = 200000.0
UNIT_VS_SINGLE_LO = 0.55
UNIT_VS_SINGLE_HI = 1.45
# 2–4 box lots need at least two asks; a single 4-box listing can be a junk premium.
MIN_MULTI_LISTINGS = 2
SLEEP_S = 0.35

NO_SHRINK_PREFIXES = (
    "[No shrink] ",
    "[No shrink]",
    "【シュリンクなし】",
)

QTY_RE = re.compile(r"^(\d+)個$")
QUOTED_RE = re.compile(r'"([^"]+)"')
PC_UNGRADED_RE = re.compile(
    r"Ungraded</t[dh]>\s*<t[dh][^>]*>\s*\$([0-9][0-9,]*(?:\.[0-9]+)?)",
    re.I | re.S,
)
PC_JS_PRICE_RE = re.compile(r'class="js-price">\s*\$([0-9][0-9,]*(?:\.[0-9]+)?)', re.I)


class FetchError(Exception):
    """Network or parse failure; caller keeps the previous snapshot."""


def now_shanghai() -> str:
    return datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d %H:%M Asia/Shanghai")


def landed_yen(unit_yen: float) -> float:
    ship = (SHIP_HKD_PER_CARTON / 4.0) * JPY_PER_HKD
    return round(float(unit_yen) * FEE_MULT + ship, 2)


def parse_qty(label: str | None) -> int | None:
    if not label:
        return None
    m = QTY_RE.match(str(label).strip())
    return int(m.group(1)) if m else None


def _positive_price(row: dict) -> float | None:
    for key in ("minNewListingPrice", "minListingPrice"):
        raw = row.get(key)
        try:
            val = float(raw)
        except (TypeError, ValueError):
            continue
        if val > 0:
            return val
    return None


def pick_lot(size_prices: list[dict]) -> dict | None:
    """Prefer a 4/3/2/1-box new-listing lot. Reject empty or garbage asks."""
    by_qty: dict[int, dict] = {}
    for row in size_prices or []:
        size = row.get("size") or {}
        qty = parse_qty(size.get("localizedName"))
        price = _positive_price(row)
        try:
            count = int(row.get("listingItemCount") or 0)
        except (TypeError, ValueError):
            count = 0
        if qty is None or price is None or count <= 0:
            continue
        if qty in by_qty and by_qty[qty]["lot_yen"] <= price:
            continue
        by_qty[qty] = {
            "qty": qty,
            "qty_label": f"{qty}個",
            "lot_yen": price,
            "unit_yen": price / qty,
            "listing_count": count,
        }

    single = by_qty.get(1, {}).get("unit_yen")

    def usable(rec: dict, *, need_depth: bool) -> bool:
        unit = rec["unit_yen"]
        if unit < MIN_UNIT_YEN or unit > MAX_UNIT_YEN:
            return False
        if rec["qty"] > 1 and rec["listing_count"] < MIN_MULTI_LISTINGS and need_depth:
            return False
        if single is not None and rec["qty"] > 1:
            if unit < single * UNIT_VS_SINGLE_LO or unit > single * UNIT_VS_SINGLE_HI:
                return False
        return True

    def finish(rec: dict) -> dict:
        out = dict(rec)
        out["unit_yen"] = round(out["unit_yen"], 2)
        out["landed_yen"] = landed_yen(out["unit_yen"])
        return out

    # Prefer a real 4/3/2-box book, then any remaining 4/3/2, then 1-box.
    for need_depth in (True, False):
        for qty in PREFERRED_QTY:
            rec = by_qty.get(qty)
            if rec and usable(rec, need_depth=need_depth and qty > 1):
                return finish(rec)
    return None


def parse_pc_ungraded(html: str) -> float | None:
    if not html:
        return None
    for cre in (PC_UNGRADED_RE, PC_JS_PRICE_RE):
        m = cre.search(html)
        if not m:
            continue
        raw = m.group(1).replace(",", "")
        try:
            usd = float(raw)
        except ValueError:
            continue
        if usd > 0:
            return usd
    return None


def search_queries(shrink_name: str) -> list[str]:
    name = (shrink_name or "").strip()
    out: list[str] = []
    quoted = QUOTED_RE.findall(name)
    if quoted:
        out.append(f"{quoted[-1]} Box")
        out.append(quoted[-1])
    if name:
        out.append(name)
    # de-dupe, keep order
    seen: set[str] = set()
    uniq = []
    for q in out:
        if q not in seen:
            seen.add(q)
            uniq.append(q)
    return uniq


def match_no_shrink(shrink_name: str, streetwears: list[dict]) -> int | None:
    """Return a fetched streetwear id only when the EN name is an exact sibling."""
    want = (shrink_name or "").strip()
    if not want:
        return None
    for row in streetwears or []:
        name = str(row.get("name") or "").strip()
        rid = row.get("id")
        try:
            rid_i = int(rid)
        except (TypeError, ValueError):
            continue
        remainder = name
        matched_prefix = False
        for prefix in NO_SHRINK_PREFIXES:
            if remainder.startswith(prefix):
                remainder = remainder[len(prefix) :].strip()
                matched_prefix = True
                break
        if matched_prefix and remainder == want:
            return rid_i
    return None


def http_get(url: str, *, ua: str = USER_AGENT, accept: str = "application/json") -> bytes:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": ua,
            "Accept": accept,
            "Referer": "https://snkrdunk.com/",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.read()
    except urllib.error.HTTPError as err:
        raise FetchError(f"HTTP {err.code} {url}") from err
    except urllib.error.URLError as err:
        raise FetchError(f"URL error {url}: {err.reason}") from err


def fetch_json(url: str, sleeper=None) -> dict:
    raw = http_get(url, ua=USER_AGENT, accept="application/json")
    if sleeper:
        sleeper()
    try:
        data = json.loads(raw.decode("utf-8", "replace"))
    except json.JSONDecodeError as err:
        raise FetchError(f"bad JSON {url}: {err}") from err
    if not isinstance(data, dict):
        raise FetchError(f"JSON object expected from {url}")
    return data


def fetch_html(url: str, sleeper=None) -> str:
    raw = http_get(url, ua=PC_USER_AGENT, accept="text/html")
    if sleeper:
        sleeper()
    return raw.decode("utf-8", "replace")


def apparel_url(apparel_id: int) -> str:
    return f"https://snkrdunk.com/v1/apparels/{int(apparel_id)}"


def sizes_url(apparel_id: int) -> str:
    return f"https://snkrdunk.com/v1/apparels/{int(apparel_id)}/sizes"


def search_url(keyword: str) -> str:
    q = urllib.parse.quote(keyword)
    return f"https://snkrdunk.com/en/v1/search?keyword={q}&perPage=20&page=1"


def shrink_page_url(apparel_id: int) -> str:
    return f"https://snkrdunk.com/en/trading-cards/{int(apparel_id)}"


def lot_from_apparel(apparel: dict, sizes: dict) -> dict | None:
    lot = pick_lot(sizes.get("sizePrices") or [])
    if not lot:
        return None
    lot["apparel_id"] = int(apparel["id"])
    lot["name"] = apparel.get("name") or ""
    lot["localized_name"] = apparel.get("localizedName") or ""
    lot["product_number"] = apparel.get("productNumber") or ""
    lot["min_price"] = apparel.get("minPriceOfNewListing") or apparel.get("minPrice")
    return lot


def discover_no_shrink_id(
    shrink_name: str,
    *,
    fetch_search,
    sleeper=None,
) -> tuple[int | None, str]:
    for query in search_queries(shrink_name):
        try:
            payload = fetch_search(query)
        except FetchError:
            continue
        if sleeper:
            sleeper()
        found = match_no_shrink(shrink_name, payload.get("streetwears") or [])
        if found is not None:
            return found, query
    return None, ""


def apply_lot(set_obj: dict, kind: str, lot: dict, as_of: str) -> None:
    landed = set_obj.setdefault("landed", {})
    landed[kind] = {
        "landed_yen": lot["landed_yen"],
        "unit_yen": lot["unit_yen"],
        "qty_label": lot["qty_label"],
    }
    rec = {
        "as_of": as_of,
        "source": "snkrdunk_public_new_listing",
        "apparel_id": lot["apparel_id"],
        "lot_yen": lot["lot_yen"],
        "qty": lot["qty"],
        "qty_label": lot["qty_label"],
        "unit_yen": lot["unit_yen"],
        "listing_count": lot["listing_count"],
    }
    sealed = set_obj.setdefault("sealed_box", {})
    sealed[kind] = rec
    if kind == "shrink":
        set_obj["snkrdunk_shrink_id"] = lot["apparel_id"]
        set_obj["snkrdunk_shrink_url"] = shrink_page_url(lot["apparel_id"])
    else:
        set_obj["snkrdunk_ns_id"] = lot["apparel_id"]
        set_obj["snkrdunk_ns_url"] = shrink_page_url(lot["apparel_id"])


def apply_pc(set_obj: dict, usd: float, as_of: str) -> None:
    set_obj["pc_avg_usd"] = usd
    set_obj["pc_avg_hkd"] = round(usd * USD_HKD, 2)
    set_obj["pc_avg_as_of"] = as_of
    set_obj["pc_avg_source"] = "pricecharting_ungraded_loose"
    sealed = set_obj.setdefault("sealed_box", {})
    sealed["pc"] = {"as_of": as_of, "usd": usd, "source": "pricecharting_public_html"}


def write_data_files(root: Path, data: dict) -> None:
    (root / "data.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    js_json = json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    (root / "data.js").write_text("window.EV_DATA = " + js_json + ";\n", encoding="utf-8")


def _assert(cond: bool, msg: str) -> None:
    if not cond:
        raise AssertionError(msg)


def self_test() -> None:
    _assert(abs(landed_yen(10625.0) - 11874.85) < 0.02, "landed formula vs M6 snapshot")
    _assert(parse_qty("4個") == 4, "qty 4")
    _assert(parse_qty("pack") is None, "qty reject")

    sizes = json.loads((TESTDATA / "sizes-846048.json").read_text(encoding="utf-8"))
    apparel = json.loads((TESTDATA / "apparel-846048.json").read_text(encoding="utf-8"))
    lot = lot_from_apparel(apparel, sizes)
    _assert(lot is not None, "M6 shrink lot")
    _assert(lot["qty"] == 4, f"prefer 4個, got {lot}")
    _assert(abs(lot["unit_yen"] - 10875.0) < 0.01, lot)
    _assert(abs(lot["landed_yen"] - landed_yen(10875.0)) < 0.01, lot)

    # garbage 36個 888888 and empty 19個 must not win
    garbage_sizes = {
        "sizePrices": [
            {
                "size": {"localizedName": "1個"},
                "minNewListingPrice": 9900,
                "listingItemCount": 10,
            },
            {
                "size": {"localizedName": "36個"},
                "minNewListingPrice": 888888,
                "listingItemCount": 1,
            },
            {
                "size": {"localizedName": "4個"},
                "minNewListingPrice": 0,
                "listingItemCount": 0,
            },
        ]
    }
    g = pick_lot(garbage_sizes["sizePrices"])
    _assert(g is not None and g["qty"] == 1, f"fall back to 1個, got {g}")

    thin = pick_lot(
        [
            {
                "size": {"localizedName": "1個"},
                "minNewListingPrice": 22499,
                "listingItemCount": 20,
            },
            {
                "size": {"localizedName": "4個"},
                "minNewListingPrice": 119999,
                "listingItemCount": 1,
            },
        ]
    )
    _assert(thin is not None and thin["qty"] == 1, f"thin 4-box premium → 1個, got {thin}")

    ns_sizes = json.loads((TESTDATA / "sizes-846050.json").read_text(encoding="utf-8"))
    ns_app = json.loads((TESTDATA / "apparel-846050.json").read_text(encoding="utf-8"))
    ns_lot = lot_from_apparel(ns_app, ns_sizes)
    _assert(ns_lot is not None and ns_lot["qty"] == 4, ns_lot)
    _assert(abs(ns_lot["unit_yen"] - 8750.0) < 0.01, ns_lot)

    search = json.loads((TESTDATA / "search-storm-emeralda.json").read_text(encoding="utf-8"))
    found = match_no_shrink(apparel["name"], search["streetwears"])
    _assert(found == 846050, f"no-shrink id {found}")
    _assert(match_no_shrink(apparel["name"], []) is None, "empty search invents nothing")
    # Deluxe / unrelated must not match
    _assert(
        match_no_shrink(
            apparel["name"],
            [{"id": 9, "name": "[No shrink] Pokemon Card Game MEGA Special Box"}],
        )
        is None,
        "reject non-sibling no-shrink",
    )

    html = (TESTDATA / "pc-mega-brave.html").read_text(encoding="utf-8")
    _assert(parse_pc_ungraded(html) == 81.95, parse_pc_ungraded(html))
    _assert(parse_pc_ungraded("<html>no prices</html>") is None, "missing PC")

    sample = {
        "as_of": "2026-10-07 18:54 Asia/Shanghai",
        "notes": {},
        "fx": {},
        "sets": {
            "M6": {
                "code": "M6",
                "landed": {
                    "shrink": {"landed_yen": 1, "unit_yen": 1, "qty_label": "1個"},
                    "ns": {"landed_yen": 2, "unit_yen": 2, "qty_label": "1個"},
                },
                "snkrdunk_shrink_id": 846048,
            }
        },
    }
    as_of = "2026-10-19 00:00 Asia/Shanghai"
    apply_lot(sample["sets"]["M6"], "shrink", lot, as_of)
    apply_lot(sample["sets"]["M6"], "ns", ns_lot, as_of)
    apply_pc(sample["sets"]["M6"], 81.95, as_of)
    _assert(sample["as_of"] == "2026-10-07 18:54 Asia/Shanghai", "must not bump card as_of")
    _assert(sample["sets"]["M6"]["snkrdunk_ns_id"] == 846050, "store fetched ns id")
    _assert(sample["sets"]["M6"]["landed"]["shrink"]["qty_label"] == "4個", sample)

    write_data_files(TESTDATA, {"as_of": "x", "sets": {}})
    stray_json = TESTDATA / "data.json"
    stray_js = TESTDATA / "data.js"
    text = stray_js.read_text(encoding="utf-8")
    _assert(text.startswith("window.EV_DATA = "), text[:40])
    _assert(text.endswith(";\n"), repr(text[-10:]))
    stray_json.unlink()
    stray_js.unlink()

    print("refresh_sealed_boxes self-test ok")


def refresh(
    root: Path,
    *,
    set_codes: list[str] | None,
    skip_pc: bool,
    dry_run: bool,
    fetch_apparel,
    fetch_sizes,
    fetch_search,
    fetch_pc_html,
    sleeper=None,
) -> dict:
    data = json.loads((root / "data.json").read_text(encoding="utf-8"))
    sets = data.get("sets") or {}
    codes = set_codes or list(sets)
    as_of = now_shanghai()
    stats = {
        "as_of": as_of,
        "ok_shrink": 0,
        "ok_ns": 0,
        "ok_pc": 0,
        "kept_shrink": 0,
        "kept_ns": 0,
        "kept_pc": 0,
        "skipped": [],
        "rows": [],
    }

    for code in codes:
        set_obj = sets.get(code)
        if not set_obj:
            stats["skipped"].append((code, "unknown set"))
            continue
        sid = set_obj.get("snkrdunk_shrink_id")
        try:
            apparel_id = int(sid)
        except (TypeError, ValueError):
            stats["skipped"].append((code, "no snkrdunk_shrink_id"))
            stats["kept_shrink"] += 1
            continue

        shrink_lot = None
        try:
            apparel = fetch_apparel(apparel_id)
            sizes = fetch_sizes(apparel_id)
            if sleeper:
                sleeper()
            if int(apparel.get("id") or 0) != apparel_id:
                raise FetchError(f"{code}: apparel id mismatch")
            shrink_lot = lot_from_apparel(apparel, sizes)
            if not shrink_lot:
                raise FetchError(f"{code}: no usable 1–4 box lot")
            apply_lot(set_obj, "shrink", shrink_lot, as_of)
            stats["ok_shrink"] += 1
        except FetchError as err:
            stats["kept_shrink"] += 1
            stats["skipped"].append((code, f"shrink kept: {err}"))
            apparel = None

        ns_lot = None
        if apparel is not None:
            known_ns = set_obj.get("snkrdunk_ns_id")
            ns_id = None
            try:
                if known_ns:
                    ns_id = int(known_ns)
            except (TypeError, ValueError):
                ns_id = None
            if ns_id is None:
                ns_id, _q = discover_no_shrink_id(
                    apparel.get("name") or "",
                    fetch_search=fetch_search,
                    sleeper=sleeper,
                )
            if ns_id is not None:
                try:
                    ns_app = fetch_apparel(ns_id)
                    ns_sizes = fetch_sizes(ns_id)
                    if sleeper:
                        sleeper()
                    loc = str(ns_app.get("localizedName") or "")
                    en = str(ns_app.get("name") or "")
                    if "シュリンクなし" not in loc and "[No shrink]" not in en:
                        raise FetchError(f"{code}: id {ns_id} is not a no-shrink box")
                    ns_lot = lot_from_apparel(ns_app, ns_sizes)
                    if not ns_lot:
                        raise FetchError(f"{code}: no-shrink has no usable lot")
                    apply_lot(set_obj, "ns", ns_lot, as_of)
                    stats["ok_ns"] += 1
                except FetchError as err:
                    stats["kept_ns"] += 1
                    stats["skipped"].append((code, f"ns kept: {err}"))
            else:
                stats["kept_ns"] += 1
        else:
            stats["kept_ns"] += 1

        pc_usd = None
        pc_url = set_obj.get("pricecharting_box_url") or set_obj.get("pc_url")
        if not skip_pc and pc_url:
            try:
                html = fetch_pc_html(pc_url)
                if sleeper:
                    sleeper()
                pc_usd = parse_pc_ungraded(html)
                if pc_usd is None:
                    raise FetchError("no ungraded price")
                apply_pc(set_obj, pc_usd, as_of)
                stats["ok_pc"] += 1
            except FetchError as err:
                stats["kept_pc"] += 1
                stats["skipped"].append((code, f"pc kept: {err}"))
        else:
            stats["kept_pc"] += 1

        stats["rows"].append(
            {
                "code": code,
                "shrink": shrink_lot,
                "ns": ns_lot,
                "pc_usd": pc_usd,
            }
        )

    notes = data.setdefault("notes", {})
    notes["sealed_box_as_of"] = as_of
    notes["method_sealed_box"] = (
        "HK-landed shrink / no-shrink from public SNKRDUNK new-listing lot asks "
        "(prefer 4 then 3 then 2 then 1 box). unit_yen = lot ÷ qty. "
        "landed_yen = unit×1.035 + (HK$173/4)×20.3. View-only public JSON; "
        "no login / buy / bid. Missing fetch keeps the previous snapshot; "
        "never invent IDs or prices. PriceCharting booster-box ungraded avg is display-only."
    )
    data.setdefault("fx", {})
    data["fx"]["jpy_per_hkd"] = JPY_PER_HKD
    data["fx"]["usd_hkd"] = USD_HKD

    if stats["ok_shrink"] == 0 and stats["ok_ns"] == 0 and stats["ok_pc"] == 0:
        raise SystemExit(
            "ERROR: no sealed-box prices were fetched. "
            "Keeping data.json unchanged (will not invent prices)."
        )

    if not dry_run:
        write_data_files(root, data)
    stats["wrote"] = not dry_run
    return stats


def live_sleeper() -> None:
    time.sleep(SLEEP_S)


def live_fetch_apparel(apparel_id: int) -> dict:
    return fetch_json(apparel_url(apparel_id))


def live_fetch_sizes(apparel_id: int) -> dict:
    return fetch_json(sizes_url(apparel_id))


def live_fetch_search(keyword: str) -> dict:
    return fetch_json(search_url(keyword))


def live_fetch_pc(url: str) -> str:
    return fetch_html(url)


def print_report(stats: dict) -> None:
    print("sealed_box_as_of", stats["as_of"])
    print(
        "ok shrink",
        stats["ok_shrink"],
        "ns",
        stats["ok_ns"],
        "pc",
        stats["ok_pc"],
        "kept shrink",
        stats["kept_shrink"],
        "ns",
        stats["kept_ns"],
        "pc",
        stats["kept_pc"],
        "wrote",
        stats.get("wrote"),
    )
    for row in stats["rows"]:
        sh = row["shrink"]
        ns = row["ns"]
        sh_s = (
            f"¥{sh['unit_yen']:.0f}/{sh['qty_label']} → landed ¥{sh['landed_yen']:.0f}"
            if sh
            else "kept"
        )
        ns_s = (
            f"¥{ns['unit_yen']:.0f}/{ns['qty_label']}"
            if ns
            else "kept"
        )
        pc_s = f"${row['pc_usd']:.2f}" if row["pc_usd"] is not None else "kept"
        print(f"  {row['code']:6} shrink {sh_s:40} ns {ns_s:20} pc {pc_s}")
    if stats["skipped"]:
        print("notes:")
        for code, why in stats["skipped"]:
            print(f"  {code}: {why}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", default=str(ROOT_DEFAULT))
    ap.add_argument("--self-test", action="store_true", help="No network. Fixture checks only.")
    ap.add_argument("--dry-run", action="store_true", help="Fetch and print; do not write data.json")
    ap.add_argument("--skip-pc", action="store_true", help="Skip PriceCharting HTML (SNKR only)")
    ap.add_argument(
        "--sets",
        default="",
        help="Comma-separated set codes (default: every set in data.json with a shrink id)",
    )
    args = ap.parse_args(argv)
    if args.self_test:
        self_test()
        return 0
    codes = [c.strip() for c in args.sets.split(",") if c.strip()] or None
    stats = refresh(
        Path(args.root),
        set_codes=codes,
        skip_pc=args.skip_pc,
        dry_run=args.dry_run,
        fetch_apparel=live_fetch_apparel,
        fetch_sizes=live_fetch_sizes,
        fetch_search=live_fetch_search,
        fetch_pc_html=live_fetch_pc,
        sleeper=live_sleeper,
    )
    print_report(stats)
    return 0


if __name__ == "__main__":
    sys.exit(main())
