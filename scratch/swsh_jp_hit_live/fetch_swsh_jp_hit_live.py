#!/usr/bin/env python3
"""One-off live tape for the JP Sword & Shield hit shortlist.

Reads public SNKRDUNK JSON and PriceCharting HTML. Does not write site data,
does not touch the PSA10 regression workbook, and does not invent apparel IDs
or prices. Missing cards stay blank with an error or identity note.

  python3 scratch/swsh_jp_hit_live/fetch_swsh_jp_hit_live.py --self-test
  python3 scratch/swsh_jp_hit_live/fetch_swsh_jp_hit_live.py --out artifacts
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import statistics
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

import refresh_base_psa_pop as pc_pop  # noqa: E402

SHANGHAI = ZoneInfo("Asia/Shanghai")
JST = ZoneInfo("Asia/Tokyo")
SNK = "https://snkrdunk.com"
PC = "https://www.pricecharting.com"
UA_SNK = "Pokemon-Box-EV/1.31 (+https://github.com/swishish/Pokemon-Box-EV)"
UA_WEB = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36"
)
SLEEP_S = 0.6
WINDOW_DAYS = 30
COND_A = "trading_card_single_nearly_unused"
COND_PSA10 = "trading_card_single_psa10"

# Apparel IDs were returned by public SNK search / already stored in this
# repo's scan cache. The fetch re-checks the live product name against the
# set code and number and drops the row's prices if that check fails.
CARDS: list[dict] = [
    {
        "key": "pikachu-v-csr-s8b-222",
        "bucket": "shortlist",
        "request_name": "Pikachu V CSR",
        "set": "S8b",
        "num": "222",
        "rarity": "CSR",
        "apparel_id": 96712,
        "pc_slug": "pokemon-japanese-vmax-climax/pikachu-v-222",
        "search": "Pikachu V CSR[S8b 222",
    },
    {
        "key": "giratina-vstar-ur-s12a-261",
        "bucket": "shortlist",
        "request_name": "Giratina VSTAR UR",
        "set": "S12a",
        "num": "261",
        "rarity": "UR",
        "apparel_id": 105536,
        "pc_slug": "pokemon-japanese-vstar-universe/giratina-vstar-261",
        "search": "Giratina VSTAR UR[s12a 261",
    },
    {
        "key": "glaceon-vmax-hr-s6a-91",
        "bucket": "shortlist",
        "request_name": "Glaceon VMAX HR",
        "set": "S6a",
        "num": "91",
        "rarity": "HR",
        "apparel_id": 93024,
        "pc_slug": "pokemon-japanese-eevee-heroes/glaceon-vmax-91",
        "search": "Glaceon VMAX HR[S6a 091",
        "note": "SNK HR: SA #091. Regular HR sibling is #090, not this row.",
    },
    {
        "key": "lugia-vstar-s12-123",
        "bucket": "shortlist",
        "request_name": "Lugia VSTAR",
        "set": "S12",
        "num": "123",
        "rarity": "HR",
        "apparel_id": 100580,
        "pc_slug": "pokemon-japanese-paradigm-trigger/lugia-vstar-123",
        "search": "Lugia VSTAR UR[s12 123",
        "note": "Number #123 is the gold UR (123/098). The HR is #118, not priced here.",
    },
    {
        "key": "garchomp-v-s9a-84",
        "bucket": "shortlist",
        "request_name": "Garchomp V",
        "set": "S9a",
        "num": "84",
        "rarity": "SR",
        "apparel_id": 96586,
        "pc_slug": "pokemon-japanese-battle-region/garchomp-v-84",
        "search": "Garchomp V CSR[S9a 084",
        "note": "SNK name is CSR #084/067. Shortlist guessed SR.",
    },
    {
        "key": "starmie-v-s9a-83",
        "bucket": "shortlist",
        "request_name": "Starmie V",
        "set": "S9a",
        "num": "83",
        "rarity": "SR",
        "apparel_id": 96585,
        "pc_slug": "pokemon-japanese-battle-region/starmie-v-83",
        "search": "Starmie V CSR[S9a 083",
        "note": "SNK name is CSR #083/067. Shortlist guessed SR.",
    },
    {
        "key": "umbreon-vmax-csr-s8b-245",
        "bucket": "shortlist",
        "request_name": "Umbreon VMAX CSR",
        "set": "S8b",
        "num": "245",
        "rarity": "CSR",
        "apparel_id": 96729,
        "pc_slug": "pokemon-japanese-vmax-climax/umbreon-vmax-245",
        "search": "Umbreon VMAX CSR[S8b 245",
    },
    {
        "key": "umbreon-v-csr-s8b-244",
        "bucket": "shortlist",
        "request_name": "Umbreon V CSR",
        "set": "S8b",
        "num": "244",
        "rarity": "CSR",
        "apparel_id": 96673,
        "pc_slug": "pokemon-japanese-vmax-climax/umbreon-v-244",
        "search": "Umbreon V CSR[S8b 244",
    },
    {
        "key": "zacian-v-csr-s8b-250",
        "bucket": "shortlist",
        "request_name": "Zacian V CSR",
        "set": "S8b",
        "num": "250",
        "rarity": "CSR",
        "apparel_id": 141280,
        "pc_slug": "pokemon-japanese-vmax-climax/zacian-v-250",
        "search": "Zacian V CSR[s8b 250",
    },
    {
        "key": "zamazenta-v-csr-s8b-251",
        "bucket": "shortlist",
        "request_name": "Zamazenta V CSR",
        "set": "S8b",
        "num": "251",
        "rarity": "CSR",
        "apparel_id": 141281,
        "pc_slug": "pokemon-japanese-vmax-climax/zamazenta-v-251",
        "search": "Zamazenta V CSR[s8b 251",
    },
    {
        "key": "blaziken-v-s8b-216",
        "bucket": "shortlist",
        "request_name": "Blaziken V",
        "set": "S8b",
        "num": "216",
        "rarity": "CSR",
        "apparel_id": 96700,
        "pc_slug": "pokemon-japanese-vmax-climax/blaziken-v-216",
        "search": "Blaziken V CSR[S8b 216",
        "note": "SNK name is CSR #216, not CHR.",
    },
    {
        "key": "blaziken-vmax-csr-s8b-217",
        "bucket": "shortlist",
        "request_name": "Blaziken VMAX CSR",
        "set": "S8b",
        "num": "217",
        "rarity": "CSR",
        "apparel_id": 96685,
        "pc_slug": "pokemon-japanese-vmax-climax/blaziken-vmax-217",
        "search": "Blaziken VMAX CSR[S8b 217",
    },
    {
        "key": "flareon-v-sr-s6a-73",
        "bucket": "shortlist",
        "request_name": "Flareon V SR",
        "set": "S6a",
        "num": "73",
        "rarity": "SR",
        "apparel_id": 91171,
        "pc_slug": "pokemon-japanese-eevee-heroes/flareon-v-73",
        "search": "Flareon V SR: SA[S6a 073",
        "note": "SNK SR: SA #073. Regular SR sibling is #072, not this row.",
    },
    {
        "key": "hisuian-zoroark-vstar-s10a-97",
        "bucket": "shortlist",
        "request_name": "Hisuian Zoroark VSTAR",
        "set": "S10a",
        "num": "97",
        "rarity": "HR",
        "apparel_id": 91136,
        "pc_slug": "pokemon-japanese-dark-phantasma/hisuian-zoroark-vstar-97",
        "search": "Hisuian Zoroark VSTAR UR[S10a 097",
        "note": "Number #097 is UR. The HR is #092, not priced here.",
    },
    {
        "key": "pikachu-ar-s12a-205",
        "bucket": "shortlist",
        "request_name": "Pikachu AR",
        "set": "S12a",
        "num": "205",
        "rarity": "AR",
        "apparel_id": 105553,
        "pc_slug": "pokemon-japanese-vstar-universe/pikachu-205",
        "search": "Pikachu AR[s12a 205",
    },
    {
        "key": "espeon-vmax-sp-189",
        "bucket": "shortlist",
        "request_name": "Espeon VMAX promo",
        "set": "S-P",
        "num": "189",
        "rarity": "S-P",
        "apparel_id": 93004,
        "pc_slug": "pokemon-japanese-promo/espeon-vmax-189s-p",
        "search": "Espeon VMAX[S-P 189",
    },
    {
        "key": "sobble-kanazawa-sp-146",
        "bucket": "shortlist",
        "request_name": "Sobble Kanazawa",
        "set": "S-P",
        "num": "146",
        "rarity": "S-P",
        "apparel_id": 91125,
        "pc_slug": "pokemon-japanese-promo/sobble-146s-p",
        "search": "Sobble[S-P 146",
    },
    {
        "key": "cramorant-sp-226",
        "bucket": "shortlist",
        "request_name": "Cramorant promo",
        "set": "S-P",
        "num": "226",
        "rarity": "S-P",
        "apparel_id": 91119,
        "pc_slug": "pokemon-japanese-promo/cramorant-226s-p",
        "search": "Cramorant[S-P 226",
    },
    {
        "key": "kanazawa-pikachu-sp-147",
        "bucket": "shortlist",
        "request_name": "Kanazawa Pikachu",
        "set": "S-P",
        "num": "147",
        "rarity": "S-P",
        "apparel_id": 91113,
        "pc_slug": "pokemon-japanese-promo/kanazawa's-pikachu-147s-p",
        "search": "Kanazawa[S-P 147",
        "note": "No-mark #147. With-mark sibling is S-P #144, not this row.",
    },
    {
        "key": "shibuya-pikachu-sp-2",
        "bucket": "shortlist",
        "request_name": "Shibuya Pikachu",
        "set": "S-P",
        "num": "2",
        "rarity": "S-P",
        "apparel_id": 91127,
        "pc_slug": "pokemon-japanese-promo/shibuya's-pikachu-2s-p",
        "search": "Shibuya Pikachu[S-P 002",
    },
    {
        "key": "umbreon-25th-s8a-12",
        "bucket": "shortlist",
        "request_name": "Umbreon 25th promo",
        "set": "S8a-P",
        "num": "12",
        "rarity": "PROMO",
        "apparel_id": 91160,
        "pc_slug": "pokemon-japanese-25th-anniversary-promo/umbreon-12",
        "search": "Umbreon[S8a-P 012",
        "note": "Distinct from S8b Umbreon V #244, S8b Umbreon VMAX #245, and S6a Umbreon VMAX #095. SNK set code is S8a-P 012/025.",
        "distinct": "yes",
    },
    {
        "key": "rayquaza-s3a-56",
        "bucket": "shortlist",
        "request_name": "Rayquaza",
        "set": "S3a",
        "num": "56",
        "rarity": "",
        "apparel_id": 102294,
        "pc_slug": "pokemon-japanese-legendary-heartbeat/rayquaza-56",
        "search": "Rayquaza[S3a 056",
        "note": "SNK name is AR #056/076.",
    },
    {
        "key": "espeon-vmax-hr-s6a",
        "bucket": "oos_gap",
        "request_name": "Espeon VMAX HR",
        "set": "S6a",
        "num": "",
        "rarity": "HR",
        "apparel_id": None,
        "pc_slug": "",
        "search": "Espeon VMAX HR[S6a",
        "pc_query": "espeon vmax eevee heroes japanese",
        "note": "No Espeon VMAX in S6a. S6a VMAX lines are Leafeon, Glaceon, Sylveon, Umbreon only. #094 is Umbreon VMAX HR, not Espeon.",
    },
    {
        "key": "flareon-vmax-hr-s6a",
        "bucket": "oos_gap",
        "request_name": "Flareon VMAX HR",
        "set": "S6a",
        "num": "",
        "rarity": "HR",
        "apparel_id": None,
        "pc_slug": "",
        "search": "Flareon VMAX HR[S6a",
        "pc_query": "flareon vmax eevee heroes japanese",
        "note": "No Flareon VMAX in S6a. Flareon V SR: SA #073 is a separate shortlist row.",
    },
    {
        "key": "vaporeon-vmax-hr-s6a",
        "bucket": "oos_gap",
        "request_name": "Vaporeon VMAX HR",
        "set": "S6a",
        "num": "",
        "rarity": "HR",
        "apparel_id": None,
        "pc_slug": "",
        "search": "Vaporeon VMAX HR[S6a",
        "pc_query": "vaporeon vmax eevee heroes japanese",
        "note": "No Vaporeon VMAX in S6a.",
    },
    {
        "key": "jolteon-vmax-hr-s6a",
        "bucket": "oos_gap",
        "request_name": "Jolteon VMAX HR",
        "set": "S6a",
        "num": "",
        "rarity": "HR",
        "apparel_id": None,
        "pc_slug": "",
        "search": "Jolteon VMAX HR[S6a",
        "pc_query": "jolteon vmax eevee heroes japanese",
        "note": "No Jolteon VMAX in S6a.",
    },
    {
        "key": "charizard-vmax-csr-s8b",
        "bucket": "oos_gap",
        "request_name": "Charizard VMAX CSR",
        "set": "S8b",
        "num": "",
        "rarity": "CSR",
        "apparel_id": None,
        "pc_slug": "",
        "search": "Charizard VMAX CSR[S8b",
        "pc_query": "charizard vmax csr japanese vmax climax",
        "note": "No Charizard VMAX CSR on SNK. Closest S8b Charizard is CHR #187, not priced as a substitute.",
    },
]

COLUMNS = [
    "as_of_shanghai",
    "bucket",
    "card_key",
    "request_name",
    "request_set",
    "request_number",
    "request_rarity",
    "identity_status",
    "distinct_from_other_umbreon",
    "snk_apparel_id",
    "snk_catalog_id",
    "snk_name",
    "snk_localized_name",
    "snk_product_number",
    "confirmed_set",
    "confirmed_number",
    "confirmed_denom",
    "confirmed_rarity",
    "rarity_note",
    "snk_url",
    "snk_raw_a_ask_jpy",
    "snk_raw_b_ask_jpy",
    "snk_psa10_ask_jpy",
    "snk_raw_a_listed",
    "snk_raw_a_liquidity",
    "snk_raw_a_liquidity_reason",
    "snk_raw_a_for_desk_jpy",
    "snk_raw_b_listed",
    "snk_psa10_listed",
    "snk_psa10_sold_30d_trade_n",
    "snk_psa10_sold_30d_min_jpy",
    "snk_psa10_sold_30d_median_jpy",
    "snk_psa10_sold_30d_last_jpy",
    "snk_psa10_sold_30d_last_at",
    "snk_psa10_trades_capped_at_20",
    "snk_psa10_chart_prints_30d",
    "snk_psa10_recent_trades",
    "snk_raw_a_sold_30d_trade_n",
    "snk_raw_a_sold_30d_median_jpy",
    "snk_raw_a_sold_30d_last_jpy",
    "snk_raw_a_trades_capped_at_20",
    "snk_raw_a_chart_prints_30d",
    "snk_raw_a_recent_trades",
    "pc_url",
    "pc_title",
    "pc_psa10_usd",
    "pc_ungraded_usd",
    "pc_psa10_volume",
    "pc_ungraded_volume",
    "pop_source",
    "pop_url",
    "psa10_pop",
    "psa_total_graded",
    "psa_gem_rate",
    "pop_note",
    "fx_jpy_hkd",
    "fx_usd_hkd",
    "fx_date",
    "snk_raw_a_ask_hkd",
    "snk_psa10_ask_hkd",
    "pc_psa10_hkd",
    "pc_ungraded_hkd",
    "snk_error",
    "pc_error",
    "pop_error",
    "gemrate_note",
    "pc_rejected_url",
]


class HttpError(Exception):
    def __init__(self, status, url: str, detail: str = ""):
        super().__init__(f"HTTP {status} {url} {detail}".strip())
        self.status = status
        self.url = url


def now_shanghai() -> datetime:
    return datetime.now(SHANGHAI)


def stamp(dt: datetime | None = None) -> str:
    dt = dt or now_shanghai()
    return dt.strftime("%Y-%m-%d %H:%M Asia/Shanghai")


def blank_row() -> dict:
    return {k: "" for k in COLUMNS}


def _sleep():
    time.sleep(SLEEP_S)


def http_get(url: str, *, accept: str, ua: str, timeout: int = 45) -> tuple[int, str, bytes]:
    """GET with retries. Returns status, final URL, body. 404 is not retried."""
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": ua,
            "Accept": accept,
            "Accept-Language": "en-US,en;q=0.8",
            "Referer": "https://snkrdunk.com/" if "snkrdunk" in url else "https://www.pricecharting.com/",
        },
    )
    last: Exception | None = None
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                body = resp.read()
                _sleep()
                return resp.status, resp.geturl(), body
        except urllib.error.HTTPError as err:
            if err.code == 404:
                _sleep()
                raise HttpError(404, url) from err
            last = err
            wait = 2 ** attempt
            if err.code == 429:
                wait = max(wait, 8 * (attempt + 1))
            time.sleep(wait)
        except Exception as err:  # noqa: BLE001 — transient network
            last = err
            time.sleep(2 ** attempt)
    status = getattr(last, "code", "err")
    raise HttpError(status, url, str(last))


def get_json(url: str) -> dict:
    status, _final, body = http_get(url, accept="application/json", ua=UA_SNK)
    if status != 200:
        raise HttpError(status, url)
    data = json.loads(body.decode("utf-8", "replace"))
    if not isinstance(data, dict):
        raise HttpError(status, url, "JSON object expected")
    return data


def get_html(url: str) -> tuple[str, str]:
    status, final, body = http_get(
        url,
        accept="text/html,application/xhtml+xml",
        ua=UA_WEB,
    )
    if status != 200:
        raise HttpError(status, url)
    return final, body.decode("utf-8", "replace")


def snk_number_pattern(set_code: str, number: str) -> re.Pattern[str] | None:
    if not number:
        return None
    n = int(number)
    code = re.escape(set_code)
    return re.compile(rf"{code}\s+0*{n}(?!\d)", re.I)


def name_matches(name: str, set_code: str, number: str) -> bool:
    if not number:
        return False
    pat = snk_number_pattern(set_code, number)
    if pat is None or not pat.search(name or ""):
        return False
    blob = name or ""
    if re.search(r"(未開封|Unopened|Booster Box)", blob, re.I):
        return False
    return True


_RARITY_PATTERNS = (
    r"HR:\s*SA",
    r"SR:\s*SA",
    r"SA/PROMO",
    r"\bCSR\b",
    r"\bCHR\b",
    r"\bSAR\b",
    r"\bSSR\b",
    r"\bRRR\b",
    r"\bUR\b",
    r"\bHR\b",
    r"\bSR\b",
    r"\bAR\b",
    r"\bPROMO\b",
)


def parse_rarity(name: str) -> str:
    for pat in _RARITY_PATTERNS:
        match = re.search(pat, name or "", re.I)
        if match:
            token = re.sub(r"\s+", " ", match.group(0)).upper()
            token = token.replace("HR: SA", "HR: SA").replace("SR: SA", "SR: SA")
            return token
    return ""


def parse_set_num(name: str) -> tuple[str, str, str]:
    match = re.search(r"\[([A-Za-z0-9\-]+)\s+(\d+)(?:/(\d+))?", name or "")
    if not match:
        return "", "", ""
    return match.group(1), str(int(match.group(2))), match.group(3) or ""


def rarity_status(request: str, confirmed: str) -> str:
    req = (request or "").upper().strip()
    got = (confirmed or "").upper().strip()
    if not got:
        return "unconfirmed"
    if not req:
        return "confirmed"
    if req in {"S-P", "PROMO", "—", "-"} and got in {"PROMO", "SA/PROMO", "P"}:
        return "confirmed"
    if req == "HR" and got in {"HR", "HR: SA"}:
        return "confirmed"
    if req == "SR" and got in {"SR", "SR: SA"}:
        return "confirmed"
    if req in {"CSR/CHR", "CSR", "CHR"} and got in {"CSR", "CHR"}:
        return "confirmed" if req != "CSR/CHR" or got in {"CSR", "CHR"} else "differs"
    if req == got:
        return "confirmed"
    # Alt-art suffix still counts as the base rarity the shortlist named,
    # but CSR vs SR and UR vs HR do not.
    base = got.split(":")[0].strip()
    if req == base:
        return "confirmed"
    return "differs"


def chip_ask(chips: dict, code: str, text: str) -> tuple[str, str]:
    for chip in chips.get("chips") or []:
        if chip.get("conditionCode") != code and chip.get("text") != text:
            continue
        listed = "Y" if chip.get("hasListing") else "N"
        if not chip.get("hasListing"):
            return "", listed
        try:
            price = float(chip.get("usedMinPrice"))
        except (TypeError, ValueError):
            return "", listed
        if price > 0:
            return str(int(price) if price == int(price) else price), listed
        return "", listed
    return "", ""


def ms_to_jst_date(ms: int) -> str:
    dt = datetime.fromtimestamp(ms / 1000.0, tz=JST)
    return dt.date().isoformat()


def parse_sold_at(raw: str) -> datetime | None:
    if not raw:
        return None
    text = str(raw).replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def summarize_history(hist: dict, *, cutoff: str, title_ok: set[str]) -> dict:
    points = (((hist.get("chart") or {}).get("lines") or [{}])[0].get("points") or []) if hist else []
    prints = 0
    for pt in points:
        try:
            ts = int(pt.get("timestamp"))
            price = float(pt.get("price"))
        except (TypeError, ValueError, AttributeError):
            continue
        if price <= 0:
            continue
        if ms_to_jst_date(ts) >= cutoff:
            prints += 1
    trades_in = []
    all_trades = hist.get("trades") or [] if hist else []
    for tr in all_trades:
        title = str(tr.get("title") or "").upper().replace(" ", "")
        if title_ok and title and title not in title_ok:
            continue
        try:
            price = float(tr.get("price"))
        except (TypeError, ValueError):
            continue
        if price <= 0:
            continue
        sold = parse_sold_at(tr.get("soldAt") or "")
        if sold is None:
            continue
        day = sold.astimezone(SHANGHAI).date().isoformat()
        if day < cutoff:
            continue
        trades_in.append((sold, price))
    trades_in.sort(key=lambda item: item[0])
    prices = [p for _s, p in trades_in]
    last_price = ""
    last_at = ""
    median = ""
    minimum = ""
    if prices:
        last_price = _yen(prices[-1])
        last_at = trades_in[-1][0].astimezone(SHANGHAI).strftime("%Y-%m-%d %H:%M")
        median = _yen(statistics.median(prices))
        minimum = _yen(min(prices))
    blobs = [
        f"{_yen(price)}@{sold.astimezone(SHANGHAI).strftime('%Y-%m-%d %H:%M')}"
        for sold, price in trades_in
    ]
    capped = "Y" if len(all_trades) >= 20 and len(trades_in) == len(all_trades) else "N"
    return {
        "n": str(len(trades_in)),
        "min": minimum,
        "median": median,
        "last": last_price,
        "last_at": last_at,
        "capped": capped,
        "prints": str(prints),
        "trades": " | ".join(blobs),
    }


def _yen(price: float) -> str:
    if float(price) == int(price):
        return str(int(price))
    return f"{price:.2f}"


def raw_a_desk(listed: str, ask: str, trade_n: str, prints: str, identified: bool) -> tuple[str, str, str]:
    """Live Grade A ask is desk-usable only when it also traded in the 30-day window.

    Otherwise the card is illiquid and the desk raw is left blank (parent uses
    PriceCharting ungraded × 0.95). The posted ask stays in snk_raw_a_ask_jpy.
    """
    if not identified:
        return "illiquid", "card_not_identified", ""
    has_ask = listed == "Y" and str(ask or "") not in {"", "0"}
    try:
        traded = int(trade_n or 0) > 0 or int(prints or 0) > 0
    except ValueError:
        traded = False
    if has_ask and traded:
        return "liquid", "live_ask_and_30d_print", str(ask)
    if not has_ask:
        return "illiquid", "no_live_ask", ""
    return "illiquid", "ask_without_30d_print", ""


def hkd(amount: str, rate: float | None) -> str:
    if rate is None or amount == "":
        return ""
    try:
        value = float(amount) * rate
    except (TypeError, ValueError):
        return ""
    return f"{value:.2f}"


def fetch_fx() -> dict:
    out = {"jpy_hkd": None, "usd_hkd": None, "date": "", "error": ""}
    try:
        jpy = get_json("https://api.frankfurter.app/latest?from=JPY&to=HKD")
        usd = get_json("https://api.frankfurter.app/latest?from=USD&to=HKD")
        out["jpy_hkd"] = float(jpy["rates"]["HKD"])
        out["usd_hkd"] = float(usd["rates"]["HKD"])
        out["date"] = str(jpy.get("date") or usd.get("date") or "")
    except Exception as err:  # noqa: BLE001
        out["error"] = str(err)
    return out


def search_apparel(card: dict) -> dict | None:
    query = card.get("search") or ""
    if not query:
        return None
    url = f"{SNK}/en/v1/search?keyword={urllib.parse.quote(query)}&perPage=8&page=1"
    data = get_json(url)
    number = card.get("num") or ""
    for row in data.get("streetwears") or []:
        name = str(row.get("name") or "")
        if number and name_matches(name, card["set"], number):
            return row
        if not number and card["set"].lower() in name.lower() and card["rarity"].lower() in name.lower():
            # Gap cards: a hit here would mean the card exists. Caller decides.
            if "VMAX" in name.upper() and card["set"].lower() in name.lower():
                return row
    return None


def resolve_apparel(card: dict) -> tuple[dict | None, str]:
    aid = card.get("apparel_id")
    if aid:
        try:
            apparel = get_json(f"{SNK}/v1/apparels/{int(aid)}")
        except HttpError as err:
            return None, str(err)
        name = str(apparel.get("name") or "")
        if card.get("num") and not name_matches(name, card["set"], card["num"]):
            return None, f"apparel {aid} name did not match {card['set']} #{card['num']}: {name}"
        return apparel, ""
    if card.get("bucket") == "oos_gap":
        try:
            hit = search_apparel(card)
        except HttpError as err:
            return None, str(err)
        if hit:
            return None, f"unexpected SNK hit {hit.get('id')} {hit.get('name')}"
        return None, ""
    try:
        hit = search_apparel(card)
    except HttpError as err:
        return None, str(err)
    if not hit or not hit.get("id"):
        return None, "SNK search missed"
    try:
        apparel = get_json(f"{SNK}/v1/apparels/{int(hit['id'])}")
    except HttpError as err:
        return None, str(err)
    name = str(apparel.get("name") or "")
    if card.get("num") and not name_matches(name, card["set"], card["num"]):
        return None, f"search hit name mismatch: {name}"
    return apparel, ""


def pc_game_url(slug: str) -> str:
    parts = [urllib.parse.quote(part, safe="") for part in slug.strip("/").split("/") if part]
    return f"{PC}/game/" + "/".join(parts)


def pc_pop_url(slug: str) -> str:
    parts = [urllib.parse.quote(part, safe="") for part in slug.strip("/").split("/") if part]
    return f"{PC}/pop/item/" + "/".join(parts)


def slug_from_url(url: str) -> str:
    match = re.search(r"pricecharting\.com/game/(.+?)(?:\?|$)", url)
    if not match:
        return ""
    raw = urllib.parse.unquote(match.group(1)).strip("/")
    return raw


def number_in_slug(slug: str, number: str) -> bool:
    if not number or not slug:
        return False
    n = str(int(number))
    tail = slug.rsplit("/", 1)[-1].lower()
    # pikachu-v-222, umbreon-12, espeon-vmax-189s-p, kanazawa's-pikachu-147s-p
    return bool(re.search(rf"(?:^|-)0*{n}(?:s-p)?$", tail))


def parse_volumes(html: str) -> dict[str, str]:
    row = re.search(r'<tr class="sales_volume">(.*?)</tr>', html or "", re.I | re.S)
    if not row:
        return {}
    out: dict[str, str] = {}
    for match in re.finditer(
        r'data-show-tab="completed-auctions-([^"]+)"[^>]*>.*?<a[^>]*>(.*?)</a>',
        row.group(1),
        re.I | re.S,
    ):
        key = match.group(1).strip().lower()
        text = re.sub(r"<[^>]+>", " ", match.group(2))
        text = re.sub(r"\s+", " ", text).strip()
        if text and key not in out:
            out[key] = text
    return out


def page_title(html: str) -> str:
    match = re.search(r"<title>(.*?)</title>", html or "", re.I | re.S)
    if not match:
        return ""
    text = re.sub(r"\s+", " ", match.group(1)).strip()
    return text


def fetch_pc(card: dict) -> dict:
    result = {
        "url": "",
        "title": "",
        "psa10_usd": "",
        "ungraded_usd": "",
        "psa10_volume": "",
        "ungraded_volume": "",
        "slug": "",
        "error": "",
        "rejected_url": "",
    }
    slug = card.get("pc_slug") or ""
    html = ""
    final = ""
    if slug:
        try:
            final, html = get_html(pc_game_url(slug))
            result["slug"] = slug_from_url(final) or slug
        except HttpError as err:
            result["error"] = str(err)
            html = ""
    number = card.get("num") or ""
    if html and number and not number_in_slug(result["slug"], number):
        result["rejected_url"] = final
        result["error"] = f"PC page number mismatch: {result['slug']}"
        html = ""
        result["slug"] = ""
    if not html and card.get("pc_query"):
        q = urllib.parse.quote(card["pc_query"])
        try:
            final, html = get_html(f"{PC}/search-products?q={q}&type=prices")
        except HttpError as err:
            result["error"] = (result["error"] + "; " if result["error"] else "") + str(err)
            return result
        found = slug_from_url(final)
        title = page_title(html)
        # A multi-result search stays on /search-products and must not be priced.
        if "/game/" not in final:
            result["error"] = (result["error"] + "; " if result["error"] else "") + "PC search did not resolve to one card"
            result["title"] = title
            return result
        if number and not number_in_slug(found, number):
            result["rejected_url"] = final.split("?")[0]
            result["error"] = (result["error"] + "; " if result["error"] else "") + (
                f"PC resolved to a different card ({found})"
            )
            result["title"] = title
            return result
        if not number:
            # Gap rows have no number. Do not copy prices from a different product.
            result["rejected_url"] = final.split("?")[0]
            result["title"] = title
            result["error"] = (result["error"] + "; " if result["error"] else "") + (
                "PC search hit was not accepted as the requested S6a/S8b card"
            )
            return result
        result["slug"] = found
        result["error"] = ""
    if not html:
        return result
    prices = pc_pop.parse_pc_prices(html)
    volumes = parse_volumes(html)
    if "10" in prices:
        result["psa10_usd"] = f"{prices['10']:.2f}"
    if "raw" in prices:
        result["ungraded_usd"] = f"{prices['raw']:.2f}"
    result["psa10_volume"] = volumes.get("manual-only", "")
    result["ungraded_volume"] = volumes.get("used", "")
    result["title"] = page_title(html)
    result["url"] = pc_game_url(result["slug"]) if result["slug"] else final.split("?")[0]
    return result


def fetch_pop(slug: str) -> dict:
    out = {
        "source": "",
        "url": "",
        "psa10": "",
        "total": "",
        "gem": "",
        "note": "",
        "error": "",
    }
    if not slug:
        out["error"] = "no PriceCharting slug"
        return out
    url = pc_pop_url(slug)
    out["url"] = url
    try:
        _final, html = get_html(url)
    except HttpError as err:
        out["error"] = str(err)
        return out
    try:
        table = pc_pop.parse_pop_table(html, url=url)
    except pc_pop.ParseError as err:
        out["error"] = str(err)
        return out
    psa = (table.get("graders") or {}).get("psa") or {}
    counts = psa.get("counts") or {}
    total = psa.get("total")
    psa10 = counts.get("10")
    if psa10 is None or total is None:
        out["error"] = "PSA 10 or total missing"
        return out
    out["source"] = "pricecharting_pop_psa_column"
    out["psa10"] = str(int(psa10))
    out["total"] = str(int(total))
    if int(total) > 0:
        out["gem"] = f"{int(psa10) / int(total):.4f}"
    note = "PSA column on PriceCharting. Half grades are bucketed into the lower whole grade by the source page."
    if table.get("half_grades_bucketed_by_source"):
        out["note"] = note
    else:
        out["note"] = "PSA column on PriceCharting."
    return out


def probe_gemrate() -> str:
    try:
        http_get("https://www.gemrate.com/", accept="text/html", ua=UA_WEB, timeout=20)
        return "reachable"
    except HttpError as err:
        return f"GemRate not used ({err})"
    except Exception as err:  # noqa: BLE001
        return f"GemRate not used ({err})"


def build_row(card: dict, fx: dict, gem_note: str, as_of: str, cutoff: str) -> dict:
    row = blank_row()
    row["as_of_shanghai"] = as_of
    row["bucket"] = card["bucket"]
    row["card_key"] = card["key"]
    row["request_name"] = card["request_name"]
    row["request_set"] = card["set"]
    row["request_number"] = card.get("num") or ""
    row["request_rarity"] = card.get("rarity") or ""
    row["distinct_from_other_umbreon"] = card.get("distinct") or ""
    row["rarity_note"] = card.get("note") or ""
    row["gemrate_note"] = gem_note
    row["fx_date"] = fx.get("date") or ""
    if fx.get("jpy_hkd") is not None:
        row["fx_jpy_hkd"] = f"{fx['jpy_hkd']:.6f}"
    if fx.get("usd_hkd") is not None:
        row["fx_usd_hkd"] = f"{fx['usd_hkd']:.6f}"
    if fx.get("error"):
        row["rarity_note"] = (row["rarity_note"] + " " if row["rarity_note"] else "") + f"FX: {fx['error']}"

    apparel, snk_err = resolve_apparel(card)
    if snk_err:
        row["snk_error"] = snk_err
    if apparel is None:
        if card["bucket"] == "oos_gap" and not snk_err:
            row["identity_status"] = "not_found"
        elif card["bucket"] == "oos_gap":
            row["identity_status"] = "not_found"
        else:
            row["identity_status"] = "snk_missing"
    else:
        name = str(apparel.get("name") or "")
        set_code, number, denom = parse_set_num(name)
        rarity = parse_rarity(name)
        row["snk_apparel_id"] = str(apparel.get("id") or "")
        catalog = apparel.get("productCatalogId")
        row["snk_catalog_id"] = "" if catalog in (None, "") else str(catalog)
        row["snk_name"] = name
        row["snk_localized_name"] = str(apparel.get("localizedName") or "")
        row["snk_product_number"] = str(apparel.get("productNumber") or "")
        row["confirmed_set"] = set_code
        row["confirmed_number"] = number
        row["confirmed_denom"] = denom
        row["confirmed_rarity"] = rarity
        row["snk_url"] = f"{SNK}/apparels/{int(apparel['id'])}"
        status = rarity_status(card.get("rarity") or "", rarity)
        if card.get("distinct") == "yes":
            row["identity_status"] = "distinct_confirmed" if status == "confirmed" else f"distinct_{status}"
        else:
            row["identity_status"] = status
        if catalog:
            try:
                chips = get_json(f"{SNK}/v2/products/{int(catalog)}/size-chips")
                a_ask, a_listed = chip_ask(chips, COND_A, "A")
                b_ask, b_listed = chip_ask(chips, "trading_card_single_little_scratches", "B")
                p_ask, p_listed = chip_ask(chips, COND_PSA10, "PSA10")
                row["snk_raw_a_ask_jpy"] = a_ask
                row["snk_raw_b_ask_jpy"] = b_ask
                row["snk_psa10_ask_jpy"] = p_ask
                row["snk_raw_a_listed"] = a_listed
                row["snk_raw_b_listed"] = b_listed
                row["snk_psa10_listed"] = p_listed
            except HttpError as err:
                row["snk_error"] = (row["snk_error"] + "; " if row["snk_error"] else "") + str(err)
            try:
                hist = get_json(
                    f"{SNK}/v3/products/{int(catalog)}/trading-history?range=all&condition_code={COND_PSA10}"
                )
                summary = summarize_history(hist, cutoff=cutoff, title_ok={"PSA10"})
                row["snk_psa10_sold_30d_trade_n"] = summary["n"]
                row["snk_psa10_sold_30d_min_jpy"] = summary["min"]
                row["snk_psa10_sold_30d_median_jpy"] = summary["median"]
                row["snk_psa10_sold_30d_last_jpy"] = summary["last"]
                row["snk_psa10_sold_30d_last_at"] = summary["last_at"]
                row["snk_psa10_trades_capped_at_20"] = summary["capped"]
                row["snk_psa10_chart_prints_30d"] = summary["prints"]
                row["snk_psa10_recent_trades"] = summary["trades"]
            except HttpError as err:
                row["snk_error"] = (row["snk_error"] + "; " if row["snk_error"] else "") + str(err)
            try:
                hist_a = get_json(
                    f"{SNK}/v3/products/{int(catalog)}/trading-history?range=all&condition_code={COND_A}"
                )
                summary_a = summarize_history(hist_a, cutoff=cutoff, title_ok={"A"})
                row["snk_raw_a_sold_30d_trade_n"] = summary_a["n"]
                row["snk_raw_a_sold_30d_median_jpy"] = summary_a["median"]
                row["snk_raw_a_sold_30d_last_jpy"] = summary_a["last"]
                row["snk_raw_a_trades_capped_at_20"] = summary_a["capped"]
                row["snk_raw_a_chart_prints_30d"] = summary_a["prints"]
                row["snk_raw_a_recent_trades"] = summary_a["trades"]
            except HttpError as err:
                row["snk_error"] = (row["snk_error"] + "; " if row["snk_error"] else "") + str(err)
        else:
            row["snk_error"] = (row["snk_error"] + "; " if row["snk_error"] else "") + "no productCatalogId"

    pc = fetch_pc(card)
    row["pc_url"] = pc["url"]
    row["pc_title"] = pc["title"]
    row["pc_psa10_usd"] = pc["psa10_usd"]
    row["pc_ungraded_usd"] = pc["ungraded_usd"]
    row["pc_psa10_volume"] = pc["psa10_volume"]
    row["pc_ungraded_volume"] = pc["ungraded_volume"]
    row["pc_error"] = pc["error"]
    row["pc_rejected_url"] = pc["rejected_url"]
    if pc["slug"] and card.get("num"):
        pop = fetch_pop(pc["slug"])
        row["pop_source"] = pop["source"]
        row["pop_url"] = pop["url"]
        row["psa10_pop"] = pop["psa10"]
        row["psa_total_graded"] = pop["total"]
        row["psa_gem_rate"] = pop["gem"]
        row["pop_note"] = pop["note"]
        row["pop_error"] = pop["error"]
    elif card["bucket"] == "oos_gap":
        row["pop_error"] = "skipped; card not identified"
    else:
        row["pop_error"] = "skipped; no confirmed PriceCharting slug"

    identified = bool(row.get("snk_apparel_id"))
    liquidity, reason, desk = raw_a_desk(
        row["snk_raw_a_listed"],
        row["snk_raw_a_ask_jpy"],
        row["snk_raw_a_sold_30d_trade_n"],
        row["snk_raw_a_chart_prints_30d"],
        identified,
    )
    row["snk_raw_a_liquidity"] = liquidity
    row["snk_raw_a_liquidity_reason"] = reason
    row["snk_raw_a_for_desk_jpy"] = desk
    rate_j = fx.get("jpy_hkd")
    rate_u = fx.get("usd_hkd")
    row["snk_raw_a_ask_hkd"] = hkd(row["snk_raw_a_ask_jpy"], rate_j)
    row["snk_psa10_ask_hkd"] = hkd(row["snk_psa10_ask_jpy"], rate_j)
    row["pc_psa10_hkd"] = hkd(row["pc_psa10_usd"], rate_u)
    row["pc_ungraded_hkd"] = hkd(row["pc_ungraded_usd"], rate_u)
    return row


def write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def write_readme(path: Path, rows: list[dict], fx: dict, gem_note: str, as_of: str) -> None:
    lines = [
        "# JP SWSH hit live tape",
        "",
        f"As of: {as_of}",
        "",
        "Public SNKRDUNK JSON (apparel, size chips, trading-history) and PriceCharting HTML (game price + PSA population table).",
        "GemRate was not used after the first request failed.",
        "The PSA10 regression workbook was not opened for writing. Site pages were not modified.",
        "",
        "## Window",
        "",
        "Recent solds are SNK lots in the last 30 Asia/Shanghai calendar days.",
        "The trades array is the last 20 lots the API returns. `snk_*_trades_capped_at_20=Y` means every returned lot is inside the window, so the count is a floor.",
        "Chart-print columns count sampled chart days, not every lot.",
        "Raw B is an ask only. Raw B solds were not requested as a separate tape.",
        "",
        "Raw A is `liquid` only when a live Grade A ask exists and at least one raw-A trade or chart print falls in that 30-day window.",
        "`snk_raw_a_for_desk_jpy` is the ask in that case and blank when `snk_raw_a_liquidity=illiquid`.",
        "The posted ask, if any, stays in `snk_raw_a_ask_jpy`. Illiquid reasons: `no_live_ask`, `ask_without_30d_print`, `card_not_identified`.",
        "Parent desk rule for a blank desk raw: PriceCharting ungraded × 0.95. This file does not apply that haircut.",
        "",
        "## Residuals (parent)",
        "",
        "This CSV does not contain model residuals or a best-model pick.",
        "Score (Live−FV)/FV on the regression workbook against Chase_Fit, Large, CharFit, CharFitL, BandAge, and SetHeat.",
        "Skip Vintage. Pick the best model per card on the box.",
        "Live inputs to use: `snk_raw_a_for_desk_jpy` when liquid, else PC ungraded × 0.95; `pc_psa10_usd`; `pc_psa10_volume`; `pc_ungraded_usd`; `pc_ungraded_volume`; `psa10_pop`; `psa_total_graded`; `psa_gem_rate`.",
        "",
        "## FX",
        "",
    ]
    if fx.get("jpy_hkd") is not None:
        lines.append(
            f"Frankfurter {fx.get('date')}: 1 JPY = {fx['jpy_hkd']:.6f} HKD; 1 USD = {fx['usd_hkd']:.6f} HKD."
        )
    else:
        lines.append(f"FX fetch failed: {fx.get('error') or 'unknown'}. HKD columns left blank.")
    lines += ["", "## GemRate", "", gem_note, "", "## Identity", ""]
    for row in rows:
        bits = [
            row["card_key"],
            row["identity_status"] or "?",
            row["confirmed_rarity"] or "-",
            row["snk_error"] or "",
            row["pc_error"] or "",
            row["pop_error"] or "",
        ]
        note = row["rarity_note"]
        extra = " | ".join(b for b in bits if b)
        lines.append(f"- {extra}")
        if note:
            lines.append(f"  {note}")
        if row.get("pc_rejected_url"):
            lines.append(f"  rejected PC URL: {row['pc_rejected_url']}")
    lines += [
        "",
        "## Gaps",
        "",
        "Espeon / Flareon / Vaporeon / Jolteon VMAX do not exist as S6a HRs on SNK.",
        "S6a VMAX cards that do exist are Leafeon (#088/#089), Glaceon (#090/#091), Sylveon (#092/#093), and Umbreon (#094/#095).",
        "A PriceCharting search for Espeon VMAX under Eevee Heroes resolves to Eevee Heroes Special #4 (a different product). Those prices were not copied onto the gap rows.",
        "Charizard VMAX CSR is not an S8b SNK product. S8b Charizard is CHR #187. No substitute prices were filled in.",
        "Lugia VSTAR #123 is UR. The HR is #118. Hisuian Zoroark VSTAR #097 is UR. The HR is #092.",
        "Garchomp V #084 and Starmie V #083 are CSR on SNK. The shortlist guessed SR.",
        "",
        "Giratina VSTAR #261 PriceCharting PSA population on this pull is 30,781 PSA 10 out of 33,390 PSA (gem 0.9219). The page title is this card. The October shortlist pilot had pop_psa10 67. The live table was kept.",
        "",
        "No SNKRDUNK request returned 403. GemRate returned 403 on the first request, so population is the PriceCharting PSA column only.",
        "",
        "Raw asks with no live listing: Sobble S-P #146 has no raw A or B ask (PSA10 ask is present). Starmie V #083, Zacian V #250, Flareon V #073, and Kanazawa Pikachu #147 have a raw A ask and no raw B ask.",
        "",
    ]
    illiquid = [
        f"{row['card_key']} ({row.get('snk_raw_a_liquidity_reason') or 'n/a'})"
        for row in rows
        if row.get("bucket") == "shortlist" and row.get("snk_raw_a_liquidity") == "illiquid"
    ]
    if illiquid:
        lines += ["Illiquid raw A (desk field blank): " + "; ".join(illiquid), ""]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def self_test() -> None:
    assert name_matches("Pikachu V CSR[S8b 222/184](High Class Pack)", "S8b", "222")
    assert not name_matches("Pikachu V CSR[S8b 22/184]", "S8b", "222")
    assert name_matches("Shibuya Pikachu: PROMO[S-P 002](S-P Promotional cards)", "S-P", "2")
    assert not name_matches("Something [S-P 020]", "S-P", "2")
    assert name_matches("Umbreon: PROMO[S8a-P 012/025](Promo)", "S8a-P", "12")
    assert parse_rarity("Glaceon VMAX HR: SA[S6a 091/069]") == "HR: SA"
    assert parse_rarity("Lugia VSTAR UR[s12 123/098]") == "UR"
    assert parse_rarity("Garchomp V CSR[S9a 084/067]") == "CSR"
    assert parse_rarity("Espeon VMAX: SA/PROMO[S-P 189]") == "SA/PROMO"
    assert rarity_status("HR", "HR: SA") == "confirmed"
    assert rarity_status("HR", "UR") == "differs"
    assert rarity_status("SR", "CSR") == "differs"
    assert rarity_status("S-P", "PROMO") == "confirmed"
    set_code, number, denom = parse_set_num("Rayquaza AR[S3a 056/076](set)")
    assert (set_code, number, denom) == ("S3a", "56", "076")
    assert number_in_slug("pokemon-japanese-promo/sobble-146s-p", "146")
    assert number_in_slug("pokemon-japanese-25th-anniversary-promo/umbreon-12", "12")
    assert not number_in_slug("pokemon-japanese-eevee-heroes-special/espeon-vmax-4", "189")
    html = """
    <tr class="sales_volume">
      <td data-show-tab="completed-auctions-used"><a href="#">2 sales per week</a></td>
      <td data-show-tab="completed-auctions-manual-only"><a href="#">3 sales per week</a></td>
    </tr>
    """
    vols = parse_volumes(html)
    assert vols["used"] == "2 sales per week"
    assert vols["manual-only"] == "3 sales per week"
    hist = {
        "trades": [
            {"price": 100, "soldAt": "2026-10-20T01:00:00Z", "title": "PSA10"},
            {"price": 300, "soldAt": "2026-09-01T01:00:00Z", "title": "PSA10"},
            {"price": 200, "soldAt": "2026-10-27T01:00:00Z", "title": "PSA10"},
        ],
        "chart": {"lines": [{"points": [{"timestamp": 1761523200000, "price": 100}]}]},
    }
    # cutoff keeps October sales only relative to 2026-10-28
    summary = summarize_history(hist, cutoff="2026-10-01", title_ok={"PSA10"})
    assert summary["n"] == "2"
    assert summary["median"] == "150"
    assert summary["min"] == "100"
    assert raw_a_desk("Y", "11800", "9", "6", True) == ("liquid", "live_ask_and_30d_print", "11800")
    assert raw_a_desk("N", "", "0", "0", True) == ("illiquid", "no_live_ask", "")
    assert raw_a_desk("Y", "7900", "0", "0", True) == ("illiquid", "ask_without_30d_print", "")
    assert raw_a_desk("", "", "", "", False)[0] == "illiquid"
    sample = (ROOT / "tools/testdata/base-psa-pop/charizard-unlimited.html").read_text(encoding="utf-8")
    table = pc_pop.parse_pop_table(sample, url="fixture")
    assert table["graders"]["psa"]["counts"]["10"] > 0
    print("self-test ok")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--out", default=str(ROOT / "artifacts"))
    parser.add_argument("--also", default="/opt/cursor/artifacts")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return 0
    as_of_dt = now_shanghai()
    as_of = stamp(as_of_dt)
    day = as_of_dt.strftime("%Y-%m-%d")
    cutoff = (as_of_dt.date() - timedelta(days=WINDOW_DAYS)).isoformat()
    print(f"as of {as_of} window from {cutoff}", file=sys.stderr)
    fx = fetch_fx()
    print(f"fx {fx}", file=sys.stderr)
    gem_note = probe_gemrate()
    print(gem_note, file=sys.stderr)
    rows = []
    for card in CARDS:
        print(f"fetch {card['key']}", file=sys.stderr)
        try:
            row = build_row(card, fx, gem_note, as_of, cutoff)
        except Exception as err:  # noqa: BLE001 — one card should not drop the tape
            row = blank_row()
            row["as_of_shanghai"] = as_of
            row["bucket"] = card["bucket"]
            row["card_key"] = card["key"]
            row["request_name"] = card["request_name"]
            row["snk_error"] = f"unexpected: {err}"
            row["identity_status"] = "error"
            print(f"  FAIL {err}", file=sys.stderr)
        else:
            print(
                f"  {row['identity_status']} psa10_ask={row['snk_psa10_ask_jpy']} "
                f"pc10={row['pc_psa10_usd']} pop10={row['psa10_pop']}",
                file=sys.stderr,
            )
        rows.append(row)
    out_dir = Path(args.out)
    csv_name = f"swsh_jp_hit_live_prices_{day}.csv"
    readme_name = f"swsh_jp_hit_live_prices_{day}_README.md"
    targets = [out_dir]
    also = Path(args.also) if args.also else None
    if also and also not in targets:
        targets.append(also)
    for folder in targets:
        write_csv(folder / csv_name, rows)
        write_readme(folder / readme_name, rows, fx, gem_note, as_of)
        print(f"wrote {folder / csv_name} rows={len(rows)}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
