#!/usr/bin/env python3
"""Fill missing A+PSA / A+CGC inputs on Box EV sets that show 「—」.

Live public sources only. Never invents an ask, a gem rate, or an apparel ID.

* PSA10 ask: SNKRDUNK size-chip ``trading_card_single_psa10`` ``usedMinPrice``
  when ``hasListing`` (a live ask). Else PriceCharting full-guide PSA 10 USD,
  converted with the site FX (USD × 7.80 × 20.3) and stored in yen.
* PSA9 ask: the same SNKRDUNK chip for ``trading_card_single_psa9``, only when
  that chip has a listing. PriceCharting has no separate PSA 9 column.
* CGC10: PriceCharting full-guide row labelled exactly ``CGC 10`` (USD).
  ``CGC 10 Pristine`` is a different grade and is not used. SNKRDUNK has no
  CGC10 chip (other graders sit in one bucket).
* gem_rate: PriceCharting population report. Prefer PSA 10 / PSA total.
  When the PSA census is missing or under 5 cards, use CGC 10 / CGC total
  from the same table. Omitted when neither census reaches 5 graded cards,
  the table does not parse, or the grade-10 count is 0. The source is stored
  on ``gem_src`` (``pc_psa_pop`` or ``pc_cgc_pop``).

Existing positive ``psa10_ask`` / ``gem_rate`` / ``cgc10_usd`` are left as-is.
Zero and null are treated as missing. Cards that still have no source stay
missing. Sets that already show both A+ tiles are not fetched.

  python3 tools/fill_grade_paths.py --self-test
  python3 tools/fill_grade_paths.py --root . --write
  python3 tools/fill_grade_paths.py --root . --sets M6a,M6 --write
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from html import unescape
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent))
from refresh_base_psa_pop import ParseError, parse_pop_table  # noqa: E402
from refresh_box_ev import YEN_PER_USD, snapshot_for  # noqa: E402
from refresh_sealed_boxes import write_data_files  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
MIN_PSA_TOTAL = 5
MAX_ASK_YEN = 2_000_000
MAX_USD = 20_000
SNKR_UA = "Pokemon-Box-EV/1.33.5 (+https://github.com/swishish/Pokemon-Box-EV)"
BROWSER_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36"
)
SKIP_NAME = ("mirror", "monster ball", "マスターボール", "ミラー")
NAME_RE = re.compile(
    r"^(?P<en>.+?)\s+(?P<rarity>MUR|SAR|SSR|ACE|UR|AR|SR)\s*"
    r"\[(?P<code>[A-Za-z0-9]+)\s+(?P<num>\d+)\s*/\s*\d+\]",
)
POOL_RARITY = {
    "SAR": "SAR",
    "sup_SAR": "SAR",
    "MUR": "MUR",
    "poke_sr": "SR",
    "goods_sr": "SR",
}
FULL_PRICE_RE = re.compile(
    r"<td>\s*([^<]+?)\s*</td>\s*<td class=\"price js-price\">\s*([^<]*?)\s*</td>",
    re.I | re.S,
)


def now_shanghai() -> str:
    return datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d %H:%M Asia/Shanghai")


def _num(value) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def positive(value) -> bool:
    n = _num(value)
    return n is not None and n > 0


def needs_psa(card: dict) -> bool:
    return not positive(card.get("psa10_ask"))


def needs_psa9(card: dict) -> bool:
    return not positive(card.get("psa9_ask"))


def needs_gem(card: dict) -> bool:
    return not positive(card.get("gem_rate"))


def needs_cgc(card: dict) -> bool:
    return not positive(card.get("cgc10_usd"))


def psa_count(cards: list[dict]) -> int:
    return sum(1 for c in cards if positive(c.get("psa10_ask")) and positive(c.get("gem_rate")))


def cgc_count(cards: list[dict]) -> int:
    """Match calc.html: CGC tile counts a non-null gem, including a stored 0."""
    return sum(
        1
        for c in cards
        if c.get("cgc10_usd") is not None and c.get("gem_rate") is not None
    )


def grade_cards(data: dict, code: str) -> list[dict]:
    pools = set(data.get("grade_pools") or [])
    return [c for c in data.get("cards") or [] if c.get("set") == code and c.get("pool") in pools]


def dash_sets(data: dict) -> list[str]:
    out = []
    for code in data.get("sets") or {}:
        cards = grade_cards(data, code)
        if not cards:
            continue
        if psa_count(cards) < 3 or cgc_count(cards) < 3:
            out.append(code)
    return out


def sort_sets(codes: list[str], sets: dict) -> list[str]:
    pref = {"M6a": 0, "M6": 1, "M5": 2}

    def key(code: str):
        if code in pref:
            return (pref[code], 0, code)
        rel = str((sets.get(code) or {}).get("release_date") or "0000-00-00")
        parts = []
        for bit in rel.split("-"):
            try:
                parts.append(-int(bit))
            except ValueError:
                parts.append(0)
        return (3, tuple(parts), code)

    return sorted(codes, key=key)


def parse_snkr_name(name: str, set_code: str) -> tuple[str, int] | None:
    text = (name or "").strip()
    low = text.lower()
    if any(tok in low for tok in SKIP_NAME):
        return None
    if text.lower().startswith("[no shrink]") or "シュリンクなし" in text:
        return None
    match = NAME_RE.match(text)
    if not match:
        return None
    if match.group("code").upper() != set_code.upper():
        return None
    return match.group("rarity").upper(), int(match.group("num"))


def index_singles(rows: list[dict], set_code: str) -> tuple[dict[int, tuple[int, str]], set[int]]:
    """num -> (apparel id, rarity). A second different hit marks the number conflicted."""
    found: dict[int, tuple[int, str]] = {}
    conflict: set[int] = set()
    for row in rows:
        parsed = parse_snkr_name(str(row.get("name") or ""), set_code)
        if not parsed:
            continue
        rarity, num = parsed
        try:
            aid = int(row.get("id"))
        except (TypeError, ValueError):
            continue
        prev = found.get(num)
        if prev is None:
            found[num] = (aid, rarity)
        elif prev != (aid, rarity):
            conflict.add(num)
    return found, conflict


def apparel_for(card: dict, found: dict[int, tuple[int, str]], conflict: set[int]) -> int | None:
    rarity = POOL_RARITY.get(card.get("pool") or "")
    if not rarity:
        return None
    try:
        num = int(card.get("num"))
    except (TypeError, ValueError):
        return None
    if num in conflict:
        return None
    hit = found.get(num)
    if not hit or hit[1] != rarity:
        return None
    return hit[0]


def chip_ask(chips: dict | None, condition: str) -> float | None:
    if not chips:
        return None
    for chip in chips.get("chips") or []:
        if chip.get("conditionCode") != condition:
            continue
        if not chip.get("hasListing"):
            return None
        price = _num(chip.get("usedMinPrice"))
        if price is None or not (0 < price <= MAX_ASK_YEN):
            return None
        return price
    return None


def parse_full_prices(html: str) -> dict[str, float]:
    """Label -> USD from the PriceCharting #full-prices table. '-' is omitted."""
    if not html:
        return {}
    block = re.search(r'<div id="full-prices">(.*?)</table>', html, re.I | re.S)
    if not block:
        return {}
    out: dict[str, float] = {}
    for label, raw in FULL_PRICE_RE.findall(block.group(1)):
        name = unescape(label).strip()
        if name in out:
            continue
        text = raw.strip().replace("$", "").replace(",", "").replace("\xa0", "")
        if text in {"", "-", "—", "–"}:
            continue
        try:
            usd = float(text)
        except ValueError:
            continue
        if 0 < usd <= MAX_USD:
            out[name] = usd
    return out


def _grader_gem(grader: dict | None, src: str) -> dict | None:
    if not grader or not grader.get("available"):
        return None
    try:
        total_n = int(grader.get("total"))
        gem_n = int((grader.get("counts") or {}).get("10"))
    except (TypeError, ValueError):
        return None
    if total_n < MIN_PSA_TOTAL or gem_n <= 0:
        return None
    rate = gem_n / total_n
    if not (0 < rate <= 1):
        return None
    return {"gem_rate": round(rate, 4), "n10": gem_n, "total": total_n, "src": src}


def gem_from_pop(html: str) -> dict | None:
    """PSA 10 / PSA total, else CGC 10 / CGC total from the same PriceCharting table."""
    if not html or "population-table" not in html:
        return None
    try:
        parsed = parse_pop_table(html)
    except ParseError:
        return None
    graders = parsed.get("graders") or {}
    return _grader_gem(graders.get("psa"), "pc_psa_pop") or _grader_gem(graders.get("cgc"), "pc_cgc_pop")


def normalize_pc_url(url: str) -> str:
    """Turn a stored ``&amp;`` slug into a requestable PriceCharting URL.

    Some console rows saved the HTML entity literally. PriceCharting treats
    ``&amp;`` as a search, not the card. Card URLs have no query string, so a
    literal ``&`` belongs in the path as ``%26``.
    """
    text = unescape(url or "").strip()
    if not text:
        return ""
    path, sep, query = text.partition("?")
    path = path.replace("&", "%26")
    return path + (sep + query if sep else "")


def pop_url(game_url: str) -> str | None:
    game_url = normalize_pc_url(game_url)
    match = re.search(r"pricecharting\.com/game/(.+)$", game_url or "")
    if not match:
        return None
    return "https://www.pricecharting.com/pop/item/" + match.group(1)


def yen_from_usd(usd: float | None) -> int | None:
    if usd is None:
        return None
    yen = int(round(float(usd) * YEN_PER_USD))
    if not (0 < yen <= MAX_ASK_YEN):
        return None
    return yen


def apply_card(
    card: dict,
    *,
    snkr_psa: float | None,
    snkr_psa9: float | None,
    pc_psa_usd: float | None,
    pc_cgc_usd: float | None,
    gem: dict | None,
) -> list[str]:
    """Write only missing fields. Returns the field names that changed."""
    changed: list[str] = []
    if needs_psa(card):
        if snkr_psa is not None:
            card["psa10_ask"] = int(round(snkr_psa))
            card["psa10_src"] = "snkr_psa10_ask"
            changed.append("psa10_ask")
        else:
            yen = yen_from_usd(pc_psa_usd)
            if yen is not None:
                card["psa10_ask"] = yen
                card["psa10_src"] = "pc_psa10"
                changed.append("psa10_ask")
    if needs_psa9(card) and snkr_psa9 is not None:
        card["psa9_ask"] = int(round(snkr_psa9))
        card["psa9_src"] = "snkr_psa9_ask"
        changed.append("psa9_ask")
    if needs_cgc(card) and pc_cgc_usd is not None and 0 < pc_cgc_usd <= MAX_USD:
        card["cgc10_usd"] = round(float(pc_cgc_usd), 2)
        card["cgc10_src"] = "pc_cgc10"
        changed.append("cgc10_usd")
    if needs_gem(card) and gem:
        card["gem_rate"] = gem["gem_rate"]
        card["gem_src"] = gem.get("src") or "pc_psa_pop"
        card["gem_psa10"] = gem["n10"]
        card["gem_psa_total"] = gem["total"]
        changed.append("gem_rate")
    return changed


class Client:
    def __init__(self, cache: Path):
        self.cache = cache
        self.cache.mkdir(parents=True, exist_ok=True)
        self.gate = threading.Lock()
        self.next_at = 0.0
        self.interval = 0.2

    def _wait(self) -> None:
        with self.gate:
            now = time.monotonic()
            delay = self.next_at - now
            self.next_at = max(now, self.next_at) + self.interval
        if delay > 0:
            time.sleep(delay)

    def _read_cache(self, name: str) -> bytes | None:
        path = self.cache / name
        missing = self.cache / (name + ".missing")
        if missing.exists():
            return None
        if path.exists() and path.stat().st_size > 0:
            return path.read_bytes()
        return None

    def _store(self, name: str, data: bytes | None, *, missing: bool) -> None:
        path = self.cache / name
        path.parent.mkdir(parents=True, exist_ok=True)
        if missing or not data:
            (self.cache / (name + ".missing")).write_bytes(b"")
            return
        if b"Just a moment" in data or b"cf-browser-verification" in data:
            return
        path.write_bytes(data)

    def get(self, url: str, *, name: str, ua: str, accept: str) -> bytes | None:
        cached = self._read_cache(name)
        if cached is not None:
            return cached
        if (self.cache / (name + ".missing")).exists():
            return None
        delay = 1.0
        last: Exception | None = None
        for attempt in range(4):
            self._wait()
            req = urllib.request.Request(
                url,
                headers={"User-Agent": ua, "Accept": accept, "Referer": "https://snkrdunk.com/"},
            )
            try:
                with urllib.request.urlopen(req, timeout=40) as resp:
                    data = resp.read()
                if b"Just a moment" in data or b"cf-browser-verification" in data:
                    last = RuntimeError("blocked")
                    if attempt + 1 < 4:
                        time.sleep(delay)
                        delay *= 2
                        continue
                    return None
                self._store(name, data, missing=False)
                return data
            except urllib.error.HTTPError as err:
                last = err
                if err.code == 404:
                    self._store(name, None, missing=True)
                    return None
                if err.code in (429, 500, 502, 503, 504) and attempt + 1 < 4:
                    time.sleep(delay)
                    delay *= 2
                    continue
                return None
            except urllib.error.URLError as err:
                last = err
                if attempt + 1 < 4:
                    time.sleep(delay)
                    delay *= 2
                    continue
                return None
        if last:
            return None
        return None

    def get_json(self, url: str, *, name: str) -> dict | None:
        raw = self.get(url, name=name, ua=SNKR_UA, accept="application/json")
        if not raw:
            return None
        try:
            data = json.loads(raw.decode("utf-8", "replace"))
        except ValueError:
            return None
        if not isinstance(data, dict):
            return None
        return data

    def get_html(self, url: str, *, name: str) -> str | None:
        raw = self.get(url, name=name, ua=BROWSER_UA, accept="text/html")
        if not raw:
            return None
        return raw.decode("utf-8", "replace")


def search_set(code: str, client: Client) -> tuple[dict[int, tuple[int, str]], set[int]]:
    found: dict[int, tuple[int, str]] = {}
    conflict: set[int] = set()
    total = None
    page = 1
    while page <= 25:
        url = (
            "https://snkrdunk.com/en/v1/search?keyword="
            + urllib.parse.quote(code)
            + f"&perPage=50&page={page}"
        )
        payload = client.get_json(url, name=f"snkr/search-{code}-p{page}.json")
        if payload is None:
            break
        if total is None:
            try:
                total = int(payload.get("streetwearCount") or 0)
            except (TypeError, ValueError):
                total = 0
        rows = payload.get("streetwears") or []
        if not rows:
            break
        page_found, page_conflict = index_singles(rows, code)
        for num, hit in page_found.items():
            prev = found.get(num)
            if prev is None:
                found[num] = hit
            elif prev != hit:
                conflict.add(num)
        conflict |= page_conflict
        if page * 50 >= (total or 0):
            break
        page += 1
    return found, conflict


def fetch_chips(apparel_id: int, client: Client) -> dict | None:
    apparel = client.get_json(
        f"https://snkrdunk.com/v1/apparels/{int(apparel_id)}",
        name=f"snkr/apparel-{int(apparel_id)}.json",
    )
    if not apparel:
        return None
    try:
        catalog = int(apparel.get("productCatalogId"))
    except (TypeError, ValueError):
        return None
    return client.get_json(
        f"https://snkrdunk.com/v2/products/{catalog}/size-chips",
        name=f"snkr/chips-{catalog}.json",
    )


def gather_one(card: dict, apparel_id: int | None, client: Client, *, want_psa: bool, want_cgc: bool) -> dict:
    out: dict = {"snkr_psa": None, "snkr_psa9": None, "pc_psa_usd": None, "pc_cgc_usd": None, "gem": None}
    need_psa = want_psa and needs_psa(card)
    need_cgc = want_cgc and needs_cgc(card)
    need_gem = (want_psa or want_cgc) and needs_gem(card)
    if apparel_id is not None and (need_psa or (want_psa and needs_psa9(card))):
        chips = fetch_chips(apparel_id, client)
        out["snkr_psa"] = chip_ask(chips, "trading_card_single_psa10")
        out["snkr_psa9"] = chip_ask(chips, "trading_card_single_psa9")
    game = normalize_pc_url(card.get("pc_card_url") or "")
    if game and ((need_psa and out["snkr_psa"] is None) or need_cgc):
        html = client.get_html(game, name=f"pc/game-{card.get('set')}-{card.get('num')}.html")
        prices = parse_full_prices(html or "")
        out["pc_psa_usd"] = prices.get("PSA 10")
        out["pc_cgc_usd"] = prices.get("CGC 10")
    if need_gem and game:
        url = pop_url(game)
        if url:
            html = client.get_html(url, name=f"pc/pop-{card.get('set')}-{card.get('num')}.html")
            out["gem"] = gem_from_pop(html or "")
    return out


def recompute_snapshot(data: dict, code: str) -> dict | None:
    set_obj = data["sets"][code]
    cards = [c for c in data["cards"] if c.get("set") == code]
    snap = snapshot_for(
        set_obj,
        cards,
        data["defaults"]["haircuts"],
        set(data.get("grade_pools") or []),
        data["fees"],
    )
    return snap


def repair_pc_urls(data: dict) -> int:
    """Rewrite stored card URLs that saved a literal ``&amp;`` entity."""
    n = 0
    for card in data.get("cards") or []:
        url = card.get("pc_card_url") or ""
        fixed = normalize_pc_url(url)
        if fixed and fixed != url:
            card["pc_card_url"] = fixed
            n += 1
    return n


def fill(data: dict, codes: list[str], client: Client, *, workers: int = 4) -> list[dict]:
    report = []
    ordered = sort_sets(codes, data["sets"])
    for code in ordered:
        if code not in data["sets"]:
            print(f"skip unknown {code}", flush=True)
            continue
        cards = grade_cards(data, code)
        before_psa = psa_count(cards)
        before_cgc = cgc_count(cards)
        before_snap = dict((data["sets"][code].get("snapshot") or {}))
        want_psa = before_psa < 3
        want_cgc = before_cgc < 3
        if not want_psa and not want_cgc:
            print(f"{code} already lit PSA {before_psa} CGC {before_cgc}", flush=True)
            continue
        found, conflict = search_set(code, client)
        targets = []
        for card in cards:
            if not (
                (want_psa and (needs_psa(card) or needs_gem(card)))
                or (want_cgc and (needs_cgc(card) or needs_gem(card)))
            ):
                continue
            targets.append(card)
        print(
            f"{code} grade {len(cards)} fetch {len(targets)} "
            f"PSA {before_psa} CGC {before_cgc} snkr {len(found)} conflicts {len(conflict)}",
            flush=True,
        )
        filled = 0

        def job(card: dict) -> tuple[dict, dict]:
            aid = apparel_for(card, found, conflict)
            return card, gather_one(card, aid, client, want_psa=want_psa, want_cgc=want_cgc)

        if targets:
            with ThreadPoolExecutor(max_workers=workers) as pool:
                futs = [pool.submit(job, card) for card in targets]
                for fut in as_completed(futs):
                    card, got = fut.result()
                    changed = apply_card(
                        card,
                        snkr_psa=got["snkr_psa"] if want_psa else None,
                        snkr_psa9=got["snkr_psa9"] if want_psa else None,
                        pc_psa_usd=got["pc_psa_usd"] if want_psa else None,
                        pc_cgc_usd=got["pc_cgc_usd"] if want_cgc else None,
                        gem=got["gem"] if (want_psa or want_cgc) else None,
                    )
                    if changed:
                        filled += 1
                        print(
                            f"  {code} #{card.get('num')} {card.get('pool')} "
                            + ",".join(changed),
                            flush=True,
                        )
        after_cards = grade_cards(data, code)
        snap = recompute_snapshot(data, code)
        if snap is not None and snap != data["sets"][code].get("snapshot"):
            data["sets"][code]["snapshot"] = snap
        row = {
            "set": code,
            "before_psa": before_psa,
            "before_cgc": before_cgc,
            "after_psa": psa_count(after_cards),
            "after_cgc": cgc_count(after_cards),
            "cards_touched": filled,
            "snapshot_before": before_snap,
            "snapshot_after": data["sets"][code].get("snapshot"),
        }
        report.append(row)
        print(
            f"{code} done PSA {row['before_psa']}→{row['after_psa']} "
            f"CGC {row['before_cgc']}→{row['after_cgc']} touched {filled}",
            flush=True,
        )
    return report


def write_note(data: dict, report: list[dict], as_of: str) -> None:
    gained_psa = [r["set"] for r in report if r["before_psa"] < 3 <= r["after_psa"]]
    gained_cgc = [r["set"] for r in report if r["before_cgc"] < 3 <= r["after_cgc"]]
    still_psa = [r["set"] for r in report if r["after_psa"] < 3]
    still_cgc = [r["set"] for r in report if r["after_cgc"] < 3]
    untouched = [r["set"] for r in report if r["cards_touched"] == 0 and r["after_psa"] < 3 and r["after_cgc"] < 3]
    data["notes"]["grade_paths"] = (
        f"{as_of} A+ fill for sets whose A+PSA or A+CGC tile was —. "
        "PSA10 ask = SNKRDUNK size-chip trading_card_single_psa10 usedMinPrice when hasListing; "
        "else PriceCharting full-guide PSA 10 USD × 7.80 × 20.3. "
        "PSA9 ask = SNKRDUNK trading_card_single_psa9 when hasListing. "
        "CGC10 USD = PriceCharting full-guide row CGC 10 (not CGC 10 Pristine); SNKRDUNK has no CGC10 chip. "
        f"gem_rate = PriceCharting POP grade 10 / total, PSA census when total >= {MIN_PSA_TOTAL}, else CGC census on the same table. "
        "Existing positive asks and gem rates were not overwritten. Missing sources stayed missing. "
        f"Gained A+PSA: {', '.join(gained_psa) or '—'}. "
        f"Gained A+CGC: {', '.join(gained_cgc) or '—'}. "
        f"Still A+PSA —: {', '.join(still_psa) or '—'}. "
        f"Still A+CGC —: {', '.join(still_cgc) or '—'}. "
        f"No new fields: {', '.join(untouched) or '—'}."
    )


def _assert(cond: bool, msg: str) -> None:
    if not cond:
        raise AssertionError(msg)


def self_test() -> None:
    assert parse_snkr_name(
        'MEGA Darkrai ex MUR [M5 118/081](Expansion Pack "Abyss Eye")', "M5"
    ) == ("MUR", 118)
    assert parse_snkr_name(
        'Pikachu ex SAR [M6a 127/103](Expansion Pack "30th CELEBRATION")', "M6a"
    ) == ("SAR", 127)
    assert parse_snkr_name(
        'Forretress ex SR[SV2D 084/071](Expansion Pack "Clay burst")', "SV2D"
    ) == ("SR", 84)
    assert parse_snkr_name(
        'Pikachu ex SAR [M6a 127/103](Expansion Pack "30th CELEBRATION")', "M6"
    ) is None
    assert parse_snkr_name('Gengar R: Monster Ball Mirror[SV2a 094/165](x)', "SV2a") is None
    rows = [
        {"id": 10, "name": 'Pikachu ex SAR [M6a 127/103](Expansion Pack "30th CELEBRATION")'},
        {"id": 11, "name": 'Pikachu ex SAR [M6a 126/103](Expansion Pack "30th CELEBRATION")'},
        {"id": 99, "name": 'Pikachu ex SAR [M6 010/076](other)'},
    ]
    found, conflict = index_singles(rows, "M6a")
    assert found[127] == (10, "SAR") and 10 not in found and not conflict
    dup, dup_conflict = index_singles(
        rows + [{"id": 12, "name": 'Pikachu ex SR [M6a 127/103](x)'}],
        "M6a",
    )
    assert 127 in dup_conflict
    assert apparel_for({"pool": "SAR", "num": 127}, found, conflict) == 10
    assert apparel_for({"pool": "poke_sr", "num": 127}, found, conflict) is None
    assert apparel_for({"pool": "SAR", "num": 127}, dup, dup_conflict) is None

    chips = {
        "chips": [
            {
                "conditionCode": "trading_card_single_psa10",
                "text": "PSA10",
                "hasListing": True,
                "usedMinPrice": 4700,
            },
            {
                "conditionCode": "trading_card_single_psa9",
                "text": "PSA9",
                "hasListing": False,
                "usedMinPrice": 2000,
            },
        ]
    }
    assert chip_ask(chips, "trading_card_single_psa10") == 4700
    assert chip_ask(chips, "trading_card_single_psa9") is None
    assert chip_ask({"chips": [{"conditionCode": "trading_card_single_psa10", "hasListing": True, "usedMinPrice": 9_000_000}]}, "trading_card_single_psa10") is None

    html = """
    <div id="full-prices"><table>
      <tr><td>CGC 10 Pristine</td><td class="price js-price">$21.55</td></tr>
      <tr><td>CGC 10</td><td class="price js-price">$10.28</td></tr>
      <tr><td>PSA 10</td><td class="price js-price">$37.68</td></tr>
      <tr><td>Grade 9</td><td class="price js-price">-</td></tr>
    </table></div>
    """
    prices = parse_full_prices(html)
    assert prices["CGC 10"] == 10.28 and prices["PSA 10"] == 37.68
    assert "CGC 10 Pristine" in prices and prices["CGC 10 Pristine"] != prices["CGC 10"]
    assert "Grade 9" not in prices
    assert yen_from_usd(37.68) == int(round(37.68 * YEN_PER_USD))
    assert yen_from_usd(37.68) == 5966

    pop = """
    <table id="population-table"><thead><tr><th>Grade</th><th>PSA</th><th>CGC</th></tr></thead>
    <tbody>
      <tr><td>9</td><td>20</td><td>5</td></tr>
      <tr><td>10</td><td>80</td><td>10</td></tr>
      <tr><td>Total</td><td>100</td><td>15</td></tr>
    </tbody></table>
    """
    gem = gem_from_pop(pop)
    assert gem == {"gem_rate": 0.8, "n10": 80, "total": 100, "src": "pc_psa_pop"}
    cgc_only = """
    <table id="population-table"><thead><tr><th>Grade</th><th>PSA</th><th>CGC</th></tr></thead>
    <tbody>
      <tr><td>9</td><td>-</td><td>20</td></tr>
      <tr><td>10</td><td>-</td><td>80</td></tr>
      <tr><td>Total</td><td>-</td><td>100</td></tr>
    </tbody></table>
    """
    cgc_gem = gem_from_pop(cgc_only)
    assert cgc_gem["src"] == "pc_cgc_pop" and cgc_gem["gem_rate"] == 0.8 and cgc_gem["total"] == 100
    thin = pop.replace("<td>100</td>", "<td>4</td>").replace("<td>20</td>", "<td>1</td>").replace("<td>80</td>", "<td>3</td>")
    # total 4 fails the floor even though the table parses — rebuild a consistent thin table
    thin = """
    <table id="population-table"><thead><tr><th>Grade</th><th>PSA</th></tr></thead>
    <tbody>
      <tr><td>9</td><td>1</td></tr>
      <tr><td>10</td><td>3</td></tr>
      <tr><td>Total</td><td>4</td></tr>
    </tbody></table>
    """
    assert gem_from_pop(thin) is None
    assert gem_from_pop("<html>no table</html>") is None
    assert pop_url("https://www.pricecharting.com/game/pokemon-japanese-clay-burst/forretress-ex-84") == (
        "https://www.pricecharting.com/pop/item/pokemon-japanese-clay-burst/forretress-ex-84"
    )
    fixed = normalize_pc_url(
        "https://www.pricecharting.com/game/pokemon-japanese-scarlet-&amp;-violet-151/venusaur-ex-184"
    )
    assert fixed == "https://www.pricecharting.com/game/pokemon-japanese-scarlet-%26-violet-151/venusaur-ex-184"
    assert normalize_pc_url(
        "https://www.pricecharting.com/game/pokemon-japanese-mega-brave/night-stretcher-83"
    ).endswith("/night-stretcher-83")
    assert pop_url(
        "https://www.pricecharting.com/game/pokemon-japanese-scarlet-&amp;-violet-151/venusaur-ex-184"
    ) == "https://www.pricecharting.com/pop/item/pokemon-japanese-scarlet-%26-violet-151/venusaur-ex-184"

    kept = {"psa10_ask": 5000, "psa9_ask": None, "gem_rate": 0.5, "cgc10_usd": 15}
    assert apply_card(kept, snkr_psa=4700, snkr_psa9=None, pc_psa_usd=10, pc_cgc_usd=99, gem={"gem_rate": 0.2, "n10": 2, "total": 10}) == []
    assert kept["psa10_ask"] == 5000 and kept["cgc10_usd"] == 15 and kept["gem_rate"] == 0.5

    blank = {"psa10_ask": 0, "psa9_ask": 0, "gem_rate": 0, "cgc10_usd": None}
    changed = apply_card(
        blank,
        snkr_psa=4700,
        snkr_psa9=2100,
        pc_psa_usd=37.68,
        pc_cgc_usd=10.28,
        gem={"gem_rate": 0.8, "n10": 80, "total": 100},
    )
    assert "psa10_ask" in changed and blank["psa10_ask"] == 4700 and blank["psa10_src"] == "snkr_psa10_ask"
    assert blank["psa9_ask"] == 2100
    assert blank["cgc10_usd"] == 10.28 and blank["cgc10_src"] == "pc_cgc10"
    assert blank["gem_rate"] == 0.8 and blank["gem_src"] == "pc_psa_pop"

    fallback = {"psa10_ask": None, "gem_rate": None, "cgc10_usd": None}
    apply_card(fallback, snkr_psa=None, snkr_psa9=None, pc_psa_usd=37.68, pc_cgc_usd=10.28, gem=None)
    assert fallback["psa10_ask"] == 5966 and fallback["psa10_src"] == "pc_psa10"
    assert "gem_rate" not in fallback or fallback["gem_rate"] is None

    none_card = {"psa10_ask": None, "gem_rate": None, "cgc10_usd": None}
    assert apply_card(none_card, snkr_psa=None, snkr_psa9=None, pc_psa_usd=None, pc_cgc_usd=None, gem=None) == []

    data = {
        "fees": {"psa_usd": 60, "cgc_usd": 20, "fx_usdjpy": 158},
        "defaults": {"haircuts": {"SAR": 0.1, "poke_sr": 0.25, "goods_sr": 0.25, "MUR": 0.1}},
        "grade_pools": ["SAR", "MUR", "poke_sr", "goods_sr", "sup_SAR"],
        "sets": {
            "M6a": {"code": "M6a", "rates": {"SAR": 1.0}, "snapshot": {"GA": 1, "HC": 1, "PSA": None, "CGC": None}, "release_date": "2026-09-16"},
            "SV9": {"code": "SV9", "rates": {"SAR": 1.0}, "release_date": "2025-01-24"},
            "M5": {"code": "M5", "rates": {"SAR": 1.0}, "release_date": "2026-05-22"},
        },
        "cards": [],
    }
    for i, num in enumerate((124, 125, 126)):
        data["cards"].append(
            {
                "set": "M6a",
                "pool": "SAR",
                "num": num,
                "gradeA": 1000 + i,
                "psa10_ask": None,
                "gem_rate": None,
                "cgc10_usd": None,
            }
        )
    assert dash_sets(data) == ["M6a"] or set(dash_sets(data)) == {"M6a"}
    # M5 with three complete cards is not a dash set
    for num in (1, 2, 3):
        data["cards"].append(
            {
                "set": "M5",
                "pool": "SAR",
                "num": num,
                "gradeA": 1000,
                "psa10_ask": 5000,
                "gem_rate": 0.8,
                "cgc10_usd": 40,
            }
        )
    assert "M5" not in dash_sets(data)
    assert sort_sets(["SV9", "M6a", "M6"], data["sets"])[0] == "M6a"

    for card in data["cards"]:
        if card["set"] != "M6a":
            continue
        apply_card(
            card,
            snkr_psa=8000,
            snkr_psa9=None,
            pc_psa_usd=None,
            pc_cgc_usd=50,
            gem={"gem_rate": 0.9, "n10": 90, "total": 100},
        )
    snap = recompute_snapshot(data, "M6a")
    assert snap["PSA"] is not None and snap["CGC"] is not None and snap["GA"] is not None
    assert psa_count(grade_cards(data, "M6a")) == 3
    assert cgc_count(grade_cards(data, "M6a")) == 3
    print("fill_grade_paths self-test ok")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", default=str(ROOT))
    ap.add_argument("--sets", default="", help="Comma-separated set codes. Default: sets whose A+ tile is —.")
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--cache", default="/tmp/grade-paths-cache")
    ap.add_argument("--report", default="/tmp/grade-paths-report.json")
    ap.add_argument("--workers", type=int, default=4)
    args = ap.parse_args()
    if args.self_test:
        self_test()
        return
    root = Path(args.root)
    data = json.loads((root / "data.json").read_text(encoding="utf-8"))
    if args.sets.strip():
        codes = [c.strip() for c in args.sets.split(",") if c.strip()]
    else:
        codes = dash_sets(data)
    codes = [c for c in codes if c in data["sets"]]
    print("sets", " ".join(sort_sets(codes, data["sets"])), flush=True)
    repaired = repair_pc_urls(data)
    if repaired:
        print(f"repaired {repaired} PriceCharting URLs that stored &amp;", flush=True)
    if not args.write:
        print("dry run: pass --write to fetch and update data.json", flush=True)
        return
    if not codes and not repaired:
        print("no sets with an A+ tile of — and no URL repairs; leaving data.json unchanged", flush=True)
        return
    client = Client(Path(args.cache))
    report = fill(data, codes, client, workers=max(1, args.workers))
    as_of = now_shanghai()
    if any(r["cards_touched"] for r in report):
        data["as_of"] = as_of
    write_note(data, report, as_of)
    write_data_files(root, data)
    Path(args.report).write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(data["notes"]["grade_paths"], flush=True)


if __name__ == "__main__":
    main()
