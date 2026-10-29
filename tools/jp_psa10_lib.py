#!/usr/bin/env python3
"""Shared JP PSA10 index math + SNK helpers.

Used by the broad-benchmark pipeline (`refresh_market_jp_psa10_bench.py`).
The ~60-card chase sleeve script is retired; do not revive it here.
Do not invent apparel IDs or Chinese names.
"""
from __future__ import annotations

import json
import math
import os
import re
import time
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from typing import Any

SNK = "https://snkrdunk.com"
USER_AGENT = "Pokemon-Box-EV/1.30 (+https://github.com/swishish/Pokemon-Box-EV)"
CONDITION_PSA10 = "trading_card_single_psa10"
BOX_CATEGORY_ID = 14
BASE_INDEX_VALUE = 1000.0
WEIGHT_CAP = 0.03
LIQUIDITY_WINDOW_DAYS = 30
MAX_PRINT_GAP_DAYS = 14
RETURN_CLIP = 0.25
JST = timezone(timedelta(hours=9))

_APPAREL_ID = re.compile(r"/apparels/(\d+)")
_EN_PRINT = re.compile(
    r"\b(English|\bEN\b|\[EN\]|\(EN\)|SVP EN|MEP EN|WCS EN|Pokemon EN|"
    r"\[CN\]|\[KR\]|\[ID\]|\[TH\]|"
    r"インドネシア語|韓国語|中国語|Indonesian|Korean)\b",
    re.I,
)
_BRACKET_SET = re.compile(
    r"\[([A-Za-z][A-Za-z0-9]*(?:-[A-Za-z0-9]+)*)(?:\s+[^\]]+)?\]"
)
_PRODUCT_NUM = re.compile(r"pkmn-tcg-([A-Za-z0-9\-]+)-\d+", re.I)
_PROMO_CODE = re.compile(r"(?:-P|P)$")
_NAMED_PROMO = re.compile(
    r"(Grey Felt Hat|Felt Hat|Van Gogh|Yu Nagaba|NAGABA|Picasso|Munch|"
    r"Mario Pikachu|Luigi Pikachu|poncho|Poncho|"
    r"Extra Battle Day|Battle Day|"
    r"ゴッホ|ムンク|ピカソ|長場|ナガバ|マリオピカチュウ|ルイージピカチュウ|"
    r"ポンチョ|エクストラバトル)",
    re.I,
)
_SEALED = re.compile(
    r"(Booster Box|Special Box|Gift Box|Deck Box|Elite Trainer|"
    r"Collection Box|Card File Set|Complete Set|全種セット|"
    r"未開封|シュリンク付き|"
    r"Pokemon Card Game .+\bBox\s*$|"
    r"\"[^\"]+\"\s*Box\s*$)",
    re.I,
)

# Tag Team GX window (late SM) — sibling thermometer, also lives in MODERN.
_TAGTEAM_SM = {
    "SM8B",
    "SM9",
    "SM9A",
    "SM9B",
    "SM10",
    "SM10A",
    "SM10B",
    "SM11",
    "SM11A",
    "SM11B",
    "SM12",
    "SM12A",
    "SMM",
}

SET_LABEL_ZH = {
    "SV": "朱紫",
    "SV2A": "寶可夢卡牌151",
    "SV4A": "閃色寶藏ex",
    "SV8A": "太晶慶典ex",
    "SV8": "超電突進",
    "M2A": "MEGA 夢境ex",
    "M6A": "30週年 CELEBRATION",
    "SM12A": "Tag All Stars",
    "SM8B": "GX Ultra Shiny",
    "S8B": "VMAX Climax",
    "S12A": "VSTAR Universe",
    "S8A": "25週年 Collection",
    "XY": "XY",
    "BW": "黑白",
    "CP6": "擴充包 20th",
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def ymd(d: date) -> str:
    return d.isoformat()


def parse_ymd(s: str) -> date:
    return date.fromisoformat(str(s)[:10])


def ms_to_ymd_jst(ms: int) -> str:
    """SNK chart timestamps are Japan midnight (UTC 15:00 previous calendar day)."""
    dt = datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc).astimezone(JST)
    return dt.date().isoformat()


def daterange(start: date, end: date):
    cur = start
    while cur <= end:
        yield cur
        cur += timedelta(days=1)


def quarter_start(d: date) -> date:
    """Pokeca-like quarterly membership calendar: Jan / Apr / Jul / Oct 1 (JST date)."""
    qmonth = ((d.month - 1) // 3) * 3 + 1
    return date(d.year, qmonth, 1)


def reconstitution_due(last_iso: str | None, as_of: date | None = None) -> bool:
    """True when membership should be rebuilt for the current quarter."""
    today = as_of or datetime.now(JST).date()
    q0 = quarter_start(today)
    if not last_iso:
        return True
    try:
        last = parse_ymd(last_iso)
    except ValueError:
        return True
    return last < q0


def next_reconstitution(as_of: date | None = None) -> str:
    today = as_of or datetime.now(JST).date()
    q0 = quarter_start(today)
    if today < q0:
        return ymd(q0)
    if q0.month == 10:
        nxt = date(q0.year + 1, 1, 1)
    else:
        nxt = date(q0.year, q0.month + 3, 1)
    return ymd(nxt)


def load_json(path: str, default):
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except (ValueError, OSError):
        return default


def write_json(path: str, obj) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(obj, handle, indent=2, ensure_ascii=False)
        handle.write("\n")


def write_js(path: str, global_name: str, obj) -> None:
    payload = json.dumps(obj, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(f"window.{global_name} = " + payload + ";\n")


def get_json(url: str, timeout: int = 30) -> Any:
    req = urllib.request.Request(
        url,
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
    )
    last: Exception | None = None
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception as err:  # noqa: BLE001 — retry transients
            last = err
            time.sleep(2 ** attempt)
    raise RuntimeError(f"SNKRDUNK request failed: {url}\n{last}")


def search_url(keyword: str, page: int = 1, per_page: int = 20) -> str:
    q = urllib.parse.quote(keyword)
    return f"{SNK}/en/v1/search?keyword={q}&perPage={int(per_page)}&page={int(page)}"


def fetch_search(keyword: str, page: int = 1, per_page: int = 20) -> dict:
    return get_json(search_url(keyword, page, per_page))


def fetch_apparel(apparel_id: int) -> dict:
    return get_json(f"{SNK}/v1/apparels/{int(apparel_id)}")


def fetch_psa10_history(catalog_id: int, range_key: str = "all") -> dict:
    q = f"range={range_key}&condition_code={CONDITION_PSA10}"
    return get_json(f"{SNK}/v3/products/{int(catalog_id)}/trading-history?{q}")


_FOREIGN_TAG = re.compile(r"\[(?:EN|CN|KR|ID|TH)\]", re.I)


def is_english_printing(name: str | None, localized: str | None) -> bool:
    blob = f"{name or ''} {localized or ''}"
    return bool(_EN_PRINT.search(blob) or _FOREIGN_TAG.search(blob))


_NON_POKEMON_TCG = re.compile(
    r"(ドラゴンボール|孫悟空|Goku|Dragon Ball|ワンピース|ONE PIECE|遊戯王|Yu-Gi-Oh|"
    r"デュエルマスターズ|ヴァイスシュヴァルツ|Weiss Schwarz|"
    r"Shadowverse|バトルスピリッツ|アルティメットバトル)",
    re.I,
)
_POKEMON_HINT = re.compile(
    r"(ポケモン|Pokemon|Pokémon|Pikachu|ピカチュウ|pkmn-tcg|ポケットモンスター)",
    re.I,
)


def looks_like_pokemon(
    name: str | None,
    localized: str | None = None,
    product_number: str | None = None,
    cls: dict | None = None,
) -> bool:
    """Keep Pokémon TCG singles. Search leakage can include other JP TCGs."""
    blob = f"{name or ''} {localized or ''} {product_number or ''}"
    if _NON_POKEMON_TCG.search(blob):
        return False
    flags = cls or classify_card(name=name, localized=localized, product_number=product_number)
    if flags.get("era") or flags.get("midera"):
        return True
    if flags.get("setCode") and is_promo_code(flags.get("setCode")):
        return True
    if product_number and _PRODUCT_NUM.search(str(product_number)):
        return True
    return bool(_POKEMON_HINT.search(blob) or _NAMED_PROMO.search(blob))


def looks_like_sealed(name: str | None, localized: str | None = None) -> bool:
    blob = f"{name or ''} {localized or ''}"
    return bool(_SEALED.search(blob))


def is_trading_card_single(apparel: dict) -> bool:
    info = apparel.get("apparelInfo") or {}
    if not info.get("isTradingCard"):
        return False
    for cat in apparel.get("categories") or []:
        if int(cat.get("id") or 0) == BOX_CATEGORY_ID:
            return False
    return True


def parse_prints(history: dict) -> list[tuple[str, float]]:
    lines = ((history.get("chart") or {}).get("lines")) or []
    if not lines:
        return []
    points = lines[0].get("points") or []
    out: list[tuple[str, float]] = []
    seen: set[str] = set()
    for pt in points:
        try:
            ts = int(pt.get("timestamp"))
            price = float(pt.get("price"))
        except (TypeError, ValueError, AttributeError):
            continue
        if not (price > 0):
            continue
        day = ms_to_ymd_jst(ts)
        if day in seen:
            continue
        seen.add(day)
        out.append((day, price))
    out.sort(key=lambda x: x[0])
    return out


def parse_trades(history: dict) -> list[dict]:
    rows = []
    for tr in history.get("trades") or []:
        try:
            price = float(tr.get("price"))
        except (TypeError, ValueError):
            continue
        title = str(tr.get("title") or "")
        if title and title.upper().replace(" ", "") not in {"PSA10", "PSA 10"}:
            continue
        if not (price > 0):
            continue
        rows.append(
            {
                "price": price,
                "soldAt": tr.get("soldAt"),
                "title": "PSA10",
            }
        )
    return rows


def locf_prices(prints: list[tuple[str, float]], days: list[str]) -> dict[str, float]:
    """Last observation carried forward. Missing cards stay absent until first print."""
    if not prints:
        return {}
    by = {d: p for d, p in prints}
    last: float | None = None
    out: dict[str, float] = {}
    first = prints[0][0]
    for day in days:
        if day in by:
            last = by[day]
        if last is None or day < first:
            continue
        out[day] = last
    return out


def print_count_between(prints: list[tuple[str, float]], start: str, end: str) -> int:
    n = 0
    for day, _price in prints:
        if start <= day < end:
            n += 1
    return n


def print_yen_between(prints: list[tuple[str, float]], start: str, end: str) -> float:
    total = 0.0
    for day, price in prints:
        if start <= day < end:
            total += price
    return total


def trade_yen_between(trades: list[dict], start: str, end: str) -> tuple[int, float]:
    n = 0
    yen = 0.0
    for tr in trades:
        sold = str(tr.get("soldAt") or "")
        if not sold:
            continue
        try:
            dt = datetime.fromisoformat(sold.replace("Z", "+00:00")).astimezone(JST)
            day = dt.date().isoformat()
        except ValueError:
            continue
        if start <= day < end:
            n += 1
            yen += float(tr["price"])
    return n, yen


def liquidity_score(
    prints: list[tuple[str, float]],
    trades: list[dict],
    as_of: str,
    window_days: int = LIQUIDITY_WINDOW_DAYS,
) -> float:
    end = as_of
    start = ymd(parse_ymd(as_of) - timedelta(days=window_days))
    n_tr, yen_tr = trade_yen_between(trades, start, end)
    if n_tr > 0 and yen_tr > 0:
        return yen_tr
    yen_prints = print_yen_between(prints, start, end)
    if yen_prints > 0:
        return yen_prints
    return 0.0


def pokeca_score(liq_yen: float, last_price: float) -> float:
    """√(sales × price) — Pokeca-style rank, using SNK yen as the sales proxy."""
    if liq_yen > 0 and last_price > 0:
        return math.sqrt(liq_yen * last_price)
    return 0.0


def cap_weights(raw: dict[str, float], cap: float = WEIGHT_CAP) -> dict[str, float]:
    """Cap any name at `cap`, redistribute residual to the rest. Repeat.

    If the basket is so small that 1/n >= cap, the cap is infeasible and we
    keep the raw (renormalized) weights — equal-weight already exceeds 3%.
    cap=1.0 is a no-op overlay (price no-cap).
    """
    keys = [k for k, v in raw.items() if v is not None and v > 0]
    if not keys:
        return {}
    n = len(keys)
    if (1.0 / n) >= cap - 1e-15:
        total = sum(raw[k] for k in keys)
        return {k: raw[k] / total for k in keys}
    weights = {k: float(raw[k]) for k in keys}
    total = sum(weights.values())
    weights = {k: v / total for k, v in weights.items()}
    locked: dict[str, float] = {}
    free = dict(weights)
    for _ in range(n + 2):
        over = [k for k, v in free.items() if v > cap + 1e-12]
        if not over:
            break
        for k in over:
            locked[k] = cap
            del free[k]
        residual = 1.0 - cap * len(locked)
        if residual <= 0 or not free:
            break
        fs = sum(free.values())
        if fs <= 0:
            even = residual / len(free)
            free = {k: even for k in free}
        else:
            free = {k: residual * (v / fs) for k, v in free.items()}
    out = {**locked, **free}
    s = sum(out.values())
    if s <= 0:
        return {k: 1.0 / n for k in keys}
    return {k: v / s for k, v in out.items()}


def daily_return(prev: float | None, cur: float | None) -> float | None:
    if prev is None or cur is None:
        return None
    if not (prev > 0):
        return None
    return cur / prev - 1.0


def clip_return(r: float, clip: float = RETURN_CLIP) -> float:
    if r > clip:
        return clip
    if r < -clip:
        return -clip
    return r


def prev_print_day(print_days: list[str], day: str) -> str | None:
    import bisect

    i = bisect.bisect_left(print_days, day)
    if i <= 0:
        return None
    return print_days[i - 1]


def weighted_return(rets: dict[str, float], weights: dict[str, float]) -> float | None:
    num = 0.0
    den = 0.0
    for kid, r in rets.items():
        w = weights.get(kid)
        if w is None or w <= 0:
            continue
        num += w * r
        den += w
    if den <= 0:
        return None
    return num / den


def card_day_return(
    cid: str,
    day: str,
    price: float,
    prev_price: float | None,
    printed_today: set[str],
    print_days: list[str],
    *,
    max_gap: int = MAX_PRINT_GAP_DAYS,
    clip: float = RETURN_CLIP,
) -> float | None:
    r = daily_return(prev_price, price)
    if r is None:
        return None
    if cid not in printed_today:
        return 0.0
    prev_print = prev_print_day(print_days, day)
    if prev_print is None:
        return 0.0
    gap = (parse_ymd(day) - parse_ymd(prev_print)).days
    if gap > max_gap:
        return 0.0
    return clip_return(r, clip)


def extract_set_code(name: str | None, localized: str | None = None, product_number: str | None = None) -> str | None:
    blob = f"{name or ''} {localized or ''}"
    hits = _BRACKET_SET.findall(blob)
    if hits:
        # Prefer the first bracket that looks like a TPC set, not a random tag.
        for raw in hits:
            code = str(raw).upper()
            if code in {"EN", "JP", "PSA", "PSA10", "CGC", "BGS"}:
                continue
            return code
    if product_number:
        m = _PRODUCT_NUM.search(str(product_number))
        if m:
            return m.group(1).upper()
    return None


def _era_for_code(code: str) -> str | None:
    c = code.upper()
    if c.startswith("SV") or re.match(r"^M\d", c) or c in {"M-P", "SV-P", "SVP"}:
        return "current"
    if c.startswith("SM") or re.match(r"^S\d", c) or c in {"S-P", "SMM", "SMA", "SMP", "SO", "SI"}:
        return "modern"
    if (
        c.startswith("XY")
        or c.startswith("BW")
        or c.startswith("CP")
        or c.startswith("DP")
        or c.startswith("PT")
        or c.startswith("ADV")
        or c.startswith("PCG")
        or c.startswith("PMCG")
        or c.startswith("NEO")
        or c.startswith("HS")
        or re.match(r"^L\d", c)
        or c in {"L-P", "XY-P", "BW-P"}
    ):
        return "vintage"
    return None


def is_promo_code(code: str | None) -> bool:
    if not code:
        return False
    c = code.upper()
    if c.endswith("-P") or c in {"S-P", "SM-P", "SV-P", "M-P", "XY-P", "BW-P", "L-P", "SVP", "SMP"}:
        return True
    return False


def classify_card(
    *,
    name: str | None,
    localized: str | None = None,
    product_number: str | None = None,
) -> dict:
    """Sector flags. Promos are a sibling series; era is still tagged for fill."""
    blob = f"{name or ''} {localized or ''}"
    code = extract_set_code(name, localized, product_number)
    era = _era_for_code(code) if code else None
    promo = is_promo_code(code) or bool(re.search(r"\bPROMO\b|プロモ", blob, re.I))
    if _NAMED_PROMO.search(blob):
        promo = True
    midera = False
    if code and code.upper() in _TAGTEAM_SM:
        midera = True
    if re.search(r"TAG TEAM|タッグチーム", blob, re.I):
        midera = True
        if era is None:
            era = "modern"
    if promo and era is None:
        # Promo prefix already mapped; leftover "PROMO" with no set stays un-era'd.
        pass
    label = None
    if code:
        label = SET_LABEL_ZH.get(code.upper()) or SET_LABEL_ZH.get(re.sub(r"\d.*", "", code.upper()), code)
    return {
        "setCode": code,
        "era": era,
        "promo": bool(promo),
        "midera": bool(midera),
        "setLabelZh": label,
    }


def match_name_zh(name: str | None, localized: str | None, names_map: dict) -> str | None:
    """Official TPC names only. Never invent."""
    species = (names_map or {}).get("species") or {}
    blob = f"{name or ''} {localized or ''}"
    hits = []
    for en, zh in species.items():
        if en and en.lower() in blob.lower():
            hits.append((len(en), zh))
    if not hits:
        return None
    hits.sort(reverse=True)
    return hits[0][1]


def build_index(
    cards: list[dict],
    series: dict[str, dict],
    *,
    cap: float = WEIGHT_CAP,
    window_days: int = LIQUIDITY_WINDOW_DAYS,
    min_constituents: int = 40,
    base: float = BASE_INDEX_VALUE,
    max_gap: int = MAX_PRINT_GAP_DAYS,
    clip: float = RETURN_CLIP,
    prior_day_liquidity: bool = True,
) -> dict:
    """Four chained-return series: equal, liquidity (cap), price (cap), priceNoCap.

    Daily rebalance: equal = 1/n that day; liquidity from *prior* JST day
    (when `prior_day_liquidity`); price from lagged yen. priceNoCap is the
    same lagged yen share with cap=1 (overlay; does not change the capped line).
    """
    all_days: set[str] = set()
    prints_by: dict[str, list[tuple[str, float]]] = {}
    trades_by: dict[str, list[dict]] = {}
    for card in cards:
        cid = str(card["id"])
        blob = series.get(cid) or {}
        prints = [(str(d), float(p)) for d, p in (blob.get("prints") or []) if p and float(p) > 0]
        prints.sort(key=lambda x: x[0])
        prints_by[cid] = prints
        trades_by[cid] = list(blob.get("trades") or [])
        for day, _p in prints:
            all_days.add(day)
    empty = {
        "baseDate": None,
        "points": [],
        "lastPrices": {},
        "lastWeights": {},
        "printedLastDay": set(),
    }
    if not all_days:
        return empty

    days = sorted(all_days)
    start, end = parse_ymd(days[0]), parse_ymd(days[-1])
    calendar = [ymd(d) for d in daterange(start, end)]

    locf: dict[str, dict[str, float]] = {}
    actual: dict[str, set[str]] = {}
    print_days_by: dict[str, list[str]] = {}
    for cid, prints in prints_by.items():
        locf[cid] = locf_prices(prints, calendar)
        actual[cid] = {d for d, _p in prints}
        print_days_by[cid] = sorted(actual[cid])

    eq = liq = px = pxn = None
    base_date = None
    points: list[dict] = []
    prev_prices: dict[str, float] = {}
    last_weights = {"equal": {}, "liquidity": {}, "price": {}, "priceNoCap": {}}
    printed_last: set[str] = set()

    def liq_as_of(day: str) -> str:
        if not prior_day_liquidity:
            return day
        return ymd(parse_ymd(day) - timedelta(days=1))

    for day in calendar:
        today: dict[str, float] = {}
        printed_today: set[str] = set()
        for cid in locf:
            if day in locf[cid]:
                today[cid] = locf[cid][day]
            if day in actual.get(cid, set()):
                printed_today.add(cid)
        volume = len(printed_today)
        n = len(today)
        if n < min_constituents:
            prev_prices = today
            continue
        if base_date is None:
            base_date = day
            eq = liq = px = pxn = base
            equal_w = {cid: 1.0 / n for cid in today}
            asof = day  # no prior day yet
            liq_raw = {
                cid: liquidity_score(prints_by[cid], trades_by[cid], asof, window_days) for cid in today
            }
            liq_w = cap_weights(liq_raw, cap) if sum(liq_raw.values()) > 0 else dict(equal_w)
            px_w = cap_weights(today, cap)
            pxn_w = cap_weights(today, 1.0)
            last_weights = {
                "equal": equal_w,
                "liquidity": liq_w,
                "price": px_w,
                "priceNoCap": pxn_w,
            }
            points.append(
                {
                    "date": day,
                    "equal": round(eq, 2),
                    "liquidity": round(liq, 2),
                    "price": round(px, 2),
                    "priceNoCap": round(pxn, 2),
                    "volume": volume,
                    "count": n,
                }
            )
            prev_prices = today
            printed_last = printed_today
            continue

        rets: dict[str, float] = {}
        for cid, price in today.items():
            r = card_day_return(
                cid,
                day,
                price,
                prev_prices.get(cid),
                printed_today,
                print_days_by.get(cid) or [],
                max_gap=max_gap,
                clip=clip,
            )
            if r is not None:
                rets[cid] = r
        if not rets:
            prev_prices = today
            printed_last = printed_today
            continue

        equal_w = {cid: 1.0 / len(rets) for cid in rets}
        r_eq = weighted_return(rets, equal_w)

        liq_raw = {
            cid: liquidity_score(prints_by[cid], trades_by[cid], liq_as_of(day), window_days)
            for cid in rets
        }
        liq_w = cap_weights(liq_raw, cap) if sum(liq_raw.values()) > 0 else dict(equal_w)
        r_liq = weighted_return(rets, liq_w)

        lagged = {cid: prev_prices[cid] for cid in rets if cid in prev_prices and prev_prices[cid] > 0}
        px_w = cap_weights(lagged, cap) if lagged else dict(equal_w)
        pxn_w = cap_weights(lagged, 1.0) if lagged else dict(equal_w)
        r_px = weighted_return(rets, px_w)
        r_pxn = weighted_return(rets, pxn_w)

        if r_eq is None or r_liq is None or r_px is None or r_pxn is None:
            prev_prices = today
            continue
        assert eq is not None and liq is not None and px is not None and pxn is not None
        eq = eq * (1.0 + r_eq)
        liq = liq * (1.0 + r_liq)
        px = px * (1.0 + r_px)
        pxn = pxn * (1.0 + r_pxn)
        last_weights = {
            "equal": equal_w,
            "liquidity": liq_w,
            "price": px_w,
            "priceNoCap": pxn_w,
        }
        points.append(
            {
                "date": day,
                "equal": round(eq, 2),
                "liquidity": round(liq, 2),
                "price": round(px, 2),
                "priceNoCap": round(pxn, 2),
                "volume": volume,
                "count": n,
            }
        )
        prev_prices = today
        printed_last = printed_today

    return {
        "baseDate": base_date,
        "points": points,
        "lastPrices": prev_prices,
        "lastWeights": last_weights,
        "printedLastDay": printed_last,
    }


def period_returns(points: list[dict], as_of: str, field: str) -> dict:
    pts = [p for p in points if p.get("date") and p.get(field) is not None]
    pts.sort(key=lambda p: p["date"])
    last = None
    for p in pts:
        if p["date"] <= as_of:
            last = p
    empty = {"available": False, "pct": None, "from": None, "to": as_of, "field": field}
    if not last:
        return {k: dict(empty) for k in ("1D", "5D", "30D", "1Y", "YTD")}

    def pack(baseline: dict | None, reason: str) -> dict:
        if not baseline:
            return {
                "available": False,
                "pct": None,
                "from": None,
                "to": last["date"],
                "reason": reason,
                "field": field,
            }
        prev = float(baseline[field])
        cur = float(last[field])
        if not (prev > 0):
            return {
                "available": False,
                "pct": None,
                "from": baseline["date"],
                "to": last["date"],
                "reason": "non-positive baseline",
                "field": field,
            }
        return {
            "available": True,
            "pct": round((cur / prev - 1.0) * 10000) / 100.0,
            "from": baseline["date"],
            "to": last["date"],
            "fromIndex": prev,
            "toIndex": cur,
            "field": field,
        }

    def on_or_before(cutoff: str, before: str) -> dict | None:
        found = None
        for p in pts:
            if p["date"] <= cutoff and p["date"] < before:
                found = p
        return found

    last_d = parse_ymd(last["date"])
    prev_cal = None
    for p in pts:
        if p["date"] < last["date"]:
            prev_cal = p
    cut5 = ymd(last_d - timedelta(days=5))
    cut30 = ymd(last_d - timedelta(days=30))
    cut1y = ymd(last_d - timedelta(days=365))
    cut_ytd = f"{last_d.year}-01-01"
    return {
        "asOf": last["date"],
        "1D": pack(prev_cal, "no previous point"),
        "5D": pack(on_or_before(cut5, last["date"]), "no snapshot on or before " + cut5),
        "30D": pack(on_or_before(cut30, last["date"]), "no snapshot on or before " + cut30),
        "1Y": pack(on_or_before(cut1y, last["date"]), "no snapshot on or before " + cut1y),
        "YTD": pack(on_or_before(cut_ytd, last["date"]), "no snapshot on or before " + cut_ytd),
    }


def movers_from_prices(
    cards: list[dict],
    prev: dict[str, float],
    today: dict[str, float],
    printed: set[str] | None = None,
) -> dict:
    rows = []
    printed_rows = []
    for card in cards:
        cid = str(card["id"])
        p0, p1 = prev.get(cid), today.get(cid)
        if p0 is None or p1 is None or not (p0 > 0):
            continue
        chg = round((p1 / p0 - 1.0) * 10000) / 100.0
        row = {
            "id": cid,
            "name": card.get("localizedName") or card.get("name"),
            "nameZh": card.get("nameZh"),
            "setName": card.get("set") or card.get("setCode"),
            "image": card.get("image") or "",
            "price": p1,
            "prevPrice": p0,
            "changePct": chg,
            "url": card.get("url"),
        }
        rows.append(row)
        if printed is None or cid in printed:
            printed_rows.append(row)
    board = printed_rows if printed is not None else rows
    gainers = sorted(board, key=lambda r: (-r["changePct"], -r["price"]))[:10]
    losers = sorted(board, key=lambda r: (r["changePct"], -r["price"]))[:10]
    adv = sum(1 for r in rows if r["changePct"] > 0)
    dec = sum(1 for r in rows if r["changePct"] < 0)
    unch = len(rows) - adv - dec
    return {
        "gainers": gainers,
        "losers": losers,
        "breadth": {
            "available": len(rows) > 0,
            "advancing": adv,
            "declining": dec,
            "unchanged": unch,
        },
    }


def constituents_payload(cards: list[dict], built: dict, series: dict[str, dict], as_of: str) -> list[dict]:
    prices = built.get("lastPrices") or {}
    weights = built.get("lastWeights") or {}
    rows = []
    for card in cards:
        cid = str(card["id"])
        prints = (series.get(cid) or {}).get("prints") or []
        trades = (series.get(cid) or {}).get("trades") or []
        liq = liquidity_score(prints, trades, as_of)
        rows.append(
            {
                "id": cid,
                "apparelId": card.get("apparelId"),
                "catalogId": card.get("catalogId"),
                "name": card.get("name"),
                "localizedName": card.get("localizedName"),
                "nameZh": card.get("nameZh"),
                "setName": card.get("set") or card.get("setCode"),
                "setCode": card.get("setCode"),
                "era": card.get("era"),
                "promo": card.get("promo"),
                "midera": card.get("midera"),
                "image": card.get("image") or "",
                "url": card.get("url"),
                "price": prices.get(cid),
                "equalWeight": round((weights.get("equal") or {}).get(cid, 0) * 100, 3),
                "liquidityWeight": round((weights.get("liquidity") or {}).get(cid, 0) * 100, 3),
                "priceWeight": round((weights.get("price") or {}).get(cid, 0) * 100, 3),
                "priceNoCapWeight": round((weights.get("priceNoCap") or {}).get(cid, 0) * 100, 3),
                "liquidityScore": round(liq, 1),
                "prints30": print_count_between(
                    prints,
                    ymd(parse_ymd(as_of) - timedelta(days=30)),
                    ymd(parse_ymd(as_of) + timedelta(days=1)),
                )
                if as_of
                else 0,
            }
        )
    rows.sort(key=lambda r: (-(r.get("price") or 0), r.get("id") or ""))
    return rows


def series_from_fixture_cards(fixture_cards: list[dict]) -> tuple[list[dict], dict[str, dict]]:
    cards = []
    series: dict[str, dict] = {}
    for row in fixture_cards:
        cid = str(row["id"])
        cls = classify_card(
            name=row.get("name"),
            localized=row.get("localizedName"),
            product_number=row.get("productNumber"),
        )
        card = {
            "id": cid,
            "apparelId": row.get("apparelId"),
            "catalogId": row.get("catalogId"),
            "name": row.get("name"),
            "localizedName": row.get("localizedName"),
            "nameZh": row.get("nameZh"),
            "productNumber": row.get("productNumber"),
            "set": row.get("set") or cls.get("setCode"),
            "setCode": row.get("setCode") or cls.get("setCode"),
            "era": row.get("era") if "era" in row else cls.get("era"),
            "promo": row.get("promo") if "promo" in row else cls.get("promo"),
            "midera": row.get("midera") if "midera" in row else cls.get("midera"),
            "image": row.get("image") or "",
            "url": row.get("url"),
        }
        cards.append(card)
        series[cid] = {
            "prints": [(str(d), float(p)) for d, p in row.get("prints") or []],
            "trades": list(row.get("trades") or []),
        }
    return cards, series


def load_series_cache(path: str) -> dict[str, dict]:
    raw = load_json(path, {})
    out: dict[str, dict] = {}
    if not isinstance(raw, dict):
        return out
    for cid, blob in raw.items():
        if not isinstance(blob, dict):
            continue
        prints = []
        for row in blob.get("prints") or []:
            if isinstance(row, (list, tuple)) and len(row) >= 2:
                try:
                    prints.append((str(row[0]), float(row[1])))
                except (TypeError, ValueError):
                    continue
        out[str(cid)] = {
            "prints": prints,
            "trades": list(blob.get("trades") or []),
            "fetchedAt": blob.get("fetchedAt"),
        }
    return out


def dump_series_cache(series: dict[str, dict]) -> dict:
    serializable = {}
    for cid, blob in series.items():
        serializable[cid] = {
            "prints": [[d, p] for d, p in blob.get("prints") or []],
            "trades": blob.get("trades") or [],
            "fetchedAt": blob.get("fetchedAt"),
        }
    return serializable
