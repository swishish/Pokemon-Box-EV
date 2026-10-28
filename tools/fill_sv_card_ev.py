#!/usr/bin/env python3
"""Fill card-level Box EV for the SV sets added without Grade A rows.

Live sources only (no invented prices or apparel IDs):

* PriceCharting console HTML — ungraded completed-sale USD, same parser as
  tools/refresh_box_ev.py. Saved under /tmp/pc-sets/{CODE}.html as a fetch
  cache, not a hand-off to a person.
* SNKRDUNK public apparel JSON + product size-chips. Grade A ask is the
  chip text "A" / condition trading_card_single_nearly_unused usedMinPrice
  when hasListing is true.
* Official Taiwan trainer-site card pages for Traditional Chinese names and
  for splitting SR into poke_sr vs goods_sr (支援者／物品／競技場／能量).
* TCGdex Japanese set list for the JP name on each collector number.

SR/AR gradeA = min(SNKR Grade A, PC ungraded yen) when both exist.
SAR/UR/ACE stay SNKR Grade A. A missing ask is omitted, never filled with PC
or a zero. Does not rewrite cards for sets that already had rows, and does
not touch sealed-box lots.
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
from collections import Counter, defaultdict
from datetime import datetime
from html import unescape
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent))
from refresh_box_ev import (  # noqa: E402
    YEN_PER_USD,
    ev_raw,
    parse_pc_console,
    snapshot_for,
)
from refresh_sealed_boxes import write_data_files  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
NEW_SETS = (
    "SV1V",
    "SV2D",
    "SV2P",
    "SV3a",
    "SV4K",
    "SV4M",
    "SV5K",
    "SV5M",
    "SV5a",
    "SV6",
    "SV6a",
    "SV7",
    "SV9",
)
# Pools that use a SNKR Grade A ask. SR/AR may also take PC.
SNKR_POOLS = {"AR", "poke_sr", "goods_sr", "SAR", "UR", "ACE"}
RARITY_TOKENS = ("SAR", "SSR", "ACE", "UR", "AR", "SR")
TW_RARITY = {"AR": "14", "SR": "8", "SAR": "15", "UR": "10", "ACE": "18"}
SKIP_NAME = ("mirror", "monster ball", "マスターボール", "ミラー")

SNKR_UA = "Pokemon-Box-EV/1.31.11 (+https://github.com/swishish/Pokemon-Box-EV)"
BROWSER_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36"
)

NAME_RE = re.compile(
    r"^(?P<en>.+?)\s+(?P<rarity>SAR|SSR|ACE|UR|AR|SR)\s*\[(?P<code>[A-Za-z0-9]+)\s+(?P<num>\d+)\s*/\s*\d+\]",
)
NUM_RE = re.compile(r"(\d{3})/(\d{3})")
TITLE_RE = re.compile(r"<title>([^|<]+)")
INFO_RE = re.compile(r'class="cardInformationColumn"([\s\S]*?)</section>', re.I)
HEADER_RE = re.compile(r'class="commonHeader"[^>]*>([\s\S]*?)</h3>', re.I)
GOODS_MARKERS = ("支援者", "物品", "競技場", "能量")


class FetchError(Exception):
    pass


def now_shanghai() -> str:
    return datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d %H:%M Asia/Shanghai")


def http_get(url: str, *, ua: str, accept: str, retries: int = 4) -> bytes:
    delay = 1.0
    last: Exception | None = None
    for attempt in range(retries):
        req = urllib.request.Request(
            url,
            headers={"User-Agent": ua, "Accept": accept, "Referer": "https://snkrdunk.com/"},
        )
        try:
            with urllib.request.urlopen(req, timeout=40) as resp:
                return resp.read()
        except urllib.error.HTTPError as err:
            last = err
            if err.code in (429, 500, 502, 503, 504) and attempt + 1 < retries:
                time.sleep(delay)
                delay *= 2
                continue
            raise FetchError(f"HTTP {err.code} {url}") from err
        except urllib.error.URLError as err:
            last = err
            if attempt + 1 < retries:
                time.sleep(delay)
                delay *= 2
                continue
            raise FetchError(f"URL error {url}: {err.reason}") from err
    raise FetchError(f"failed {url}: {last}")


def get_json(url: str) -> dict:
    raw = http_get(url, ua=SNKR_UA, accept="application/json")
    data = json.loads(raw.decode("utf-8", "replace"))
    if not isinstance(data, dict):
        raise FetchError(f"JSON object expected from {url}")
    return data


def get_html(url: str) -> str:
    raw = http_get(url, ua=BROWSER_UA, accept="text/html")
    return raw.decode("utf-8", "replace")


def parse_snkr_name(name: str, set_code: str) -> tuple[str, int] | None:
    """Return (rarity, collector number) for this set's single, else None."""
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
    rarity = match.group("rarity").upper()
    if rarity not in RARITY_TOKENS:
        return None
    return rarity, int(match.group("num"))


def console_url(box_url: str) -> str | None:
    """Box page /game/<slug>/booster-box -> console /console/<slug>."""
    match = re.search(r"pricecharting\.com/game/([^/]+)/", box_url or "")
    if not match:
        return None
    return "https://www.pricecharting.com/console/" + match.group(1)


def grade_a_ask(chips: dict) -> float | None:
    for chip in chips.get("chips") or []:
        code = chip.get("conditionCode")
        text = chip.get("text")
        if code != "trading_card_single_nearly_unused" and text != "A":
            continue
        if not chip.get("hasListing"):
            return None
        try:
            price = float(chip.get("usedMinPrice"))
        except (TypeError, ValueError):
            return None
        if price > 0:
            return price
        return None
    return None


def parse_tw_detail(html: str) -> dict | None:
    title = TITLE_RE.search(html or "")
    num = NUM_RE.search(html or "")
    if not title or not num:
        return None
    name = unescape(title.group(1)).strip()
    column = INFO_RE.search(html or "")
    header = ""
    if column:
        found = HEADER_RE.search(column.group(1))
        if found:
            header = unescape(re.sub(r"<[^>]+>", " ", found.group(1)))
            header = re.sub(r"\s+", " ", header).strip()
    # None when the card column has no kind header — caller must not guess.
    if not header:
        goods = None
    else:
        goods = any(marker in header for marker in GOODS_MARKERS)
    return {"name_zh": name, "num": int(num.group(1)), "goods": goods, "header": header}


def pool_for(rarity: str, goods: bool | None) -> str | None:
    if rarity == "AR":
        return "AR"
    if rarity == "SAR":
        return "SAR"
    if rarity == "UR":
        return "UR"
    if rarity == "ACE":
        return "ACE"
    if rarity == "SR":
        if goods is True:
            return "goods_sr"
        if goods is False:
            return "poke_sr"
        return None
    return None


def search_singles(set_code: str, sleeper) -> dict[tuple[str, int], int]:
    """(rarity, number) -> apparel id. First exact sibling wins."""
    found: dict[tuple[str, int], int] = {}
    page = 1
    total = None
    while page <= 20:
        url = (
            "https://snkrdunk.com/en/v1/search?keyword="
            + urllib.parse.quote(set_code)
            + f"&perPage=50&page={page}"
        )
        payload = get_json(url)
        sleeper()
        if total is None:
            try:
                total = int(payload.get("streetwearCount") or 0)
            except (TypeError, ValueError):
                total = 0
        rows = payload.get("streetwears") or []
        if not rows:
            break
        for row in rows:
            parsed = parse_snkr_name(str(row.get("name") or ""), set_code)
            if not parsed:
                continue
            key = parsed
            try:
                aid = int(row.get("id"))
            except (TypeError, ValueError):
                continue
            found.setdefault(key, aid)
        if page * 50 >= total:
            break
        page += 1
    return found


def fetch_pc(set_code: str, set_obj: dict, sleeper) -> dict[int, dict]:
    url = console_url(set_obj.get("pricecharting_box_url") or set_obj.get("pc_url") or "")
    if not url:
        return {}
    html = get_html(url)
    sleeper()
    out_dir = Path("/tmp/pc-sets")
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{set_code}.html").write_text(html, encoding="utf-8")
    return parse_pc_console(html)


def tcgdex_sr_goods(set_code: str, num: int) -> bool | None:
    """True when the SR is a Trainer or Energy. None if the card page is missing."""
    url = f"https://api.tcgdex.net/v2/ja/cards/{set_code}-{num:03d}"
    try:
        raw = http_get(url, ua=BROWSER_UA, accept="application/json")
    except FetchError:
        return None
    data = json.loads(raw.decode("utf-8", "replace"))
    category = data.get("category")
    if category == "Pokemon":
        return False
    if category in ("Trainer", "Energy"):
        return True
    return None


def fetch_tcgdex_names(set_code: str) -> dict[int, str]:
    url = f"https://api.tcgdex.net/v2/ja/sets/{set_code}"
    raw = http_get(url, ua=BROWSER_UA, accept="application/json")
    data = json.loads(raw.decode("utf-8", "replace"))
    out = {}
    for card in data.get("cards") or []:
        try:
            num = int(card.get("localId"))
        except (TypeError, ValueError):
            continue
        name = (card.get("name") or "").strip()
        if name:
            out[num] = name
    return out


def fetch_tw_names(set_code: str, sleeper) -> dict[int, dict]:
    rarity_q = "&".join(f"rarity[]={v}" for v in TW_RARITY.values())
    base = (
        "https://asia.pokemon-card.com/tw/card-search/list/"
        f"?expansionCodes={urllib.parse.quote(set_code)}&regulation=all&{rarity_q}"
    )
    ids: list[str] = []
    for page in range(1, 8):
        url = base if page == 1 else base + f"&pageNo={page}"
        html = get_html(url)
        sleeper()
        found = re.findall(r"/tw/card-search/detail/(\d+)/", html)
        if not found:
            break
        ids.extend(found)
        if f"pageNo={page + 1}" not in html and page > 1:
            break
        # page 1 always links page 2 when more exist; stop when this page is short
        if len(found) < 20:
            break
    out: dict[int, dict] = {}
    for detail_id in dict.fromkeys(ids):
        html = get_html(f"https://asia.pokemon-card.com/tw/card-search/detail/{detail_id}/")
        sleeper()
        parsed = parse_tw_detail(html)
        if not parsed:
            continue
        out.setdefault(parsed["num"], parsed)
    return out


def fetch_grade_a(apparel_id: int, sleeper) -> float | None:
    apparel = get_json(f"https://snkrdunk.com/v1/apparels/{int(apparel_id)}")
    sleeper()
    catalog = apparel.get("productCatalogId")
    try:
        catalog_id = int(catalog)
    except (TypeError, ValueError):
        return None
    chips = get_json(f"https://snkrdunk.com/v2/products/{catalog_id}/size-chips")
    sleeper()
    return grade_a_ask(chips)


def build_card(
    set_code: str,
    num: int,
    pool: str,
    ja_name: str,
    zh_name: str | None,
    rarity: str,
    snkr: float | None,
    pc: dict | None,
) -> dict | None:
    pc_yen = None
    if pc and pc.get("usd") is not None:
        try:
            usd = float(pc["usd"])
        except (TypeError, ValueError):
            usd = None
        if usd and usd > 0:
            pc_yen = round(usd * YEN_PER_USD, 2)
        else:
            usd = None
    else:
        usd = None
    ev, src = ev_raw(pool, snkr, pc_yen)
    if ev is None or not src:
        return None
    rarity_suffix = f" {rarity}"
    name = ja_name or (pc or {}).get("title") or f"#{num}"
    if rarity_suffix.strip() not in name:
        name = f"{name}{rarity_suffix}"
    if zh_name:
        name_zh = zh_name if rarity_suffix.strip() in zh_name else f"{zh_name}{rarity_suffix}"
    else:
        name_zh = name
    card = {
        "set": set_code,
        "pool": pool,
        "num": num,
        "name": name,
        "gradeA": ev,
        "psa10_ask": None,
        "psa9_ask": None,
        "gem_rate": None,
        "cgc10_usd": None,
        "name_zh": name_zh,
        "gradeA_snkr": snkr,
        "gradeA_src": src,
    }
    if usd is not None and pc is not None:
        card["pc_ungraded_usd"] = usd
        card["pc_yen"] = pc_yen
        card["pc_card_url"] = pc.get("href")
        card["pc_card_title"] = pc.get("title")
    return card


def fill_set(set_code: str, set_obj: dict, sleeper) -> tuple[list[dict], dict]:
    singles = search_singles(set_code, sleeper)
    pc_by_num = fetch_pc(set_code, set_obj, sleeper)
    ja_names = fetch_tcgdex_names(set_code)
    tw = fetch_tw_names(set_code, sleeper)
    cards: list[dict] = []
    missing_ask: list[tuple] = []
    unclassified: list[tuple] = []
    for (rarity, num), apparel_id in sorted(singles.items(), key=lambda kv: (kv[0][1], kv[0][0])):
        tw_row = tw.get(num)
        goods = tw_row["goods"] if tw_row else None
        if rarity == "SR" and goods is None:
            goods = tcgdex_sr_goods(set_code, num)
            sleeper()
        pool = pool_for(rarity, goods if rarity == "SR" else False)
        if pool is None:
            unclassified.append((rarity, num, apparel_id))
            continue
        if pool not in (set_obj.get("rates") or {}):
            continue
        snkr = fetch_grade_a(apparel_id, sleeper)
        card = build_card(
            set_code,
            num,
            pool,
            ja_names.get(num) or "",
            (tw_row or {}).get("name_zh"),
            rarity,
            snkr,
            pc_by_num.get(num),
        )
        if card is None:
            missing_ask.append((pool, num, apparel_id, ja_names.get(num)))
            continue
        cards.append(card)
    stats = {
        "discovered": len(singles),
        "priced": len(cards),
        "pools": dict(Counter(c["pool"] for c in cards)),
        "missing_ask": missing_ask,
        "unclassified_sr": unclassified,
        "pc_rows": len(pc_by_num),
        "tw_rows": len(tw),
    }
    return cards, stats


def apply(root: Path, cards_by_set: dict[str, list[dict]], stats_by_set: dict[str, dict], as_of: str) -> None:
    data = json.loads((root / "data.json").read_text(encoding="utf-8"))
    data["cards"] = [c for c in data["cards"] if c.get("set") not in cards_by_set]
    for code in NEW_SETS:
        rows = list(cards_by_set.get(code) or [])
        rows.sort(key=lambda c: (int(c.get("num") or 0), str(c.get("pool"))))
        data["cards"].extend(rows)
    haircuts = data["defaults"]["haircuts"]
    grade_pools = set(data.get("grade_pools") or [])
    priced_sets = []
    for code, cards in cards_by_set.items():
        set_obj = data["sets"][code]
        set_obj["snapshot"] = snapshot_for(set_obj, cards, haircuts, grade_pools, data["fees"])
        set_obj["pc_sr_ar_as_of"] = as_of
        pools = stats_by_set[code]["pools"]
        set_obj["method_note"] = (
            f"Card EV {as_of}: SR/AR = min(PriceCharting ungraded, SNKRDUNK Grade A ask); "
            f"SAR/UR/ACE = SNKRDUNK Grade A ask only. Priced cards {len(cards)} "
            f"pools {pools}. Missing Grade A ask omitted, not filled from PC. "
            "Pokegto rates unchanged. Sealed lots unchanged."
        )
        if cards:
            priced_sets.append(code)
    if priced_sets:
        data["as_of"] = as_of
    lines = []
    for code in NEW_SETS:
        st = stats_by_set[code]
        lines.append(
            f"{code}: priced {st['priced']}/{st['discovered']} pools {st['pools']} "
            f"missing_ask {len(st['missing_ask'])}"
        )
    data["notes"]["sv13_card_ev"] = (
        f"{as_of} card rows for {', '.join(priced_sets)}. "
        "Grade A ask = SNKRDUNK size-chip A (trading_card_single_nearly_unused) "
        "usedMinPrice when hasListing. PC ungraded from the set console page. "
        "SR/AR min(PC, SNKR); SAR/UR/ACE SNKR only. "
        + " | ".join(lines)
    )
    write_data_files(root, data)


def self_test() -> None:
    assert parse_snkr_name(
        'Iono SAR[SV2D 096/071](Expansion Pack "Clay burst")', "SV2D"
    ) == ("SAR", 96)
    assert parse_snkr_name(
        'Tyranitar AR[SV2D 079/071](Expansion Pack "Clay burst")', "SV2D"
    ) == ("AR", 79)
    assert parse_snkr_name(
        'Sparkling Crystal ACE [SV7 094/102](Expansion Pack "Stellar Miracle")', "SV7"
    ) == ("ACE", 94)
    assert parse_snkr_name(
        'Lacey SAR [SV7 131/102](Expansion Pack "Stellar Miracle")', "SV7"
    ) == ("SAR", 131)
    assert parse_snkr_name('Iono SAR[SV2P 096/071](x)', "SV2D") is None
    assert parse_snkr_name('Gengar R: Monster Ball Mirror[SV2a 094/165](x)', "SV2a") is None
    assert parse_snkr_name(
        'Pokemon Card Game Scarlet & Violet Expansion Pack "Clay burst" Box', "SV2D"
    ) is None
    assert grade_a_ask(
        {
            "chips": [
                {
                    "conditionCode": "trading_card_single_nearly_unused",
                    "text": "A",
                    "hasListing": True,
                    "usedMinPrice": 40000,
                }
            ]
        }
    ) == 40000
    assert grade_a_ask(
        {
            "chips": [
                {
                    "conditionCode": "trading_card_single_nearly_unused",
                    "text": "A",
                    "hasListing": False,
                    "usedMinPrice": 40000,
                }
            ]
        }
    ) is None
    assert pool_for("SR", True) == "goods_sr"
    assert pool_for("SR", False) == "poke_sr"
    assert pool_for("SAR", True) == "SAR"
    assert pool_for("ACE", False) == "ACE"
    html = """
    <title>奇樹 | 訓練家網站</title>
    <h1 class="pageHeader cardDetail">奇樹</h1>
    <div class="cardInformationColumn">
      <h3 class="commonHeader">支援者卡</h3>
    </div></section>
    <p>091/071</p>
    """
    parsed = parse_tw_detail(html)
    assert parsed["name_zh"] == "奇樹" and parsed["num"] == 91 and parsed["goods"] is True
    basic = parse_tw_detail(
        "<title>毽子草 | 訓練家網站</title><h1>毽子草</h1>"
        '<div class="cardInformationColumn"><h3 class="commonHeader">招式</h3></div></section>001/071'
    )
    assert basic["goods"] is False and basic["num"] == 1
    unknown = parse_tw_detail("<title>未知 | 訓練家網站</title><p>002/071</p>")
    assert unknown["goods"] is None
    assert console_url(
        "https://www.pricecharting.com/game/pokemon-japanese-clay-burst/booster-box"
    ) == "https://www.pricecharting.com/console/pokemon-japanese-clay-burst"
    card = build_card(
        "SV2D",
        96,
        "SAR",
        "ナンジャモ",
        "奇樹",
        "SAR",
        40000,
        {"usd": 245.86, "href": "https://example.test/iono", "title": "Iono #96"},
    )
    assert card is not None
    assert card["gradeA"] == 40000 and card["gradeA_src"] == "snkr"
    assert card["name_zh"] == "奇樹 SAR"
    sr = build_card(
        "SV2D",
        79,
        "AR",
        "バンギラス",
        "班基拉斯",
        "AR",
        3000,
        {"usd": 11.91, "href": "https://example.test/t", "title": "Tyranitar #79"},
    )
    assert sr is not None and sr["gradeA_src"] == "min_pc_snkr"
    assert sr["gradeA"] == min(3000, round(11.91 * YEN_PER_USD, 2))
    none_card = build_card("SV2D", 92, "SAR", "イーユイex", None, "SAR", None, {"usd": 35, "href": "x", "title": "y"})
    assert none_card is None
    print("fill_sv_card_ev self-test ok")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", default=str(ROOT))
    ap.add_argument("--sets", default=",".join(NEW_SETS))
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--write", action="store_true", help="Write data.json and data.js")
    ap.add_argument("--cache", default="/tmp/sv-card-ev-cache.json")
    args = ap.parse_args()
    if args.self_test:
        self_test()
        return
    codes = [c.strip() for c in args.sets.split(",") if c.strip()]
    root = Path(args.root)
    data = json.loads((root / "data.json").read_text(encoding="utf-8"))
    cards_by_set: dict[str, list[dict]] = {}
    stats_by_set: dict[str, dict] = {}
    cache_path = Path(args.cache)
    cache = {"cards": {}, "stats": {}}
    if cache_path.exists():
        cache = json.loads(cache_path.read_text(encoding="utf-8"))

    def sleeper() -> None:
        time.sleep(0.2)

    for code in codes:
        if code not in data["sets"]:
            raise SystemExit(f"unknown set {code}")
        if code in (cache.get("cards") or {}) and code not in (cache.get("refresh") or []):
            cards = cache["cards"][code]
            stats = cache["stats"][code]
            print(f"cache {code}: priced {len(cards)} pools {stats.get('pools')}", flush=True)
            cards_by_set[code] = cards
            stats_by_set[code] = stats
            continue
        print(f"fetch {code} ...", flush=True)
        cards, stats = fill_set(code, data["sets"][code], sleeper)
        cards_by_set[code] = cards
        # missing_ask tuples are not needed in the cache beyond a short list
        stats_by_set[code] = stats
        cache.setdefault("cards", {})[code] = cards
        cache.setdefault("stats", {})[code] = {
            **{k: v for k, v in stats.items() if k not in ("missing_ask", "unclassified_sr")},
            "missing_ask": [list(row) for row in stats["missing_ask"]],
            "unclassified_sr": [list(row) for row in stats["unclassified_sr"]],
        }
        cache_path.write_text(json.dumps(cache, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(
            f"  discovered {stats['discovered']} priced {stats['priced']} "
            f"pools {stats['pools']} pc {stats['pc_rows']} tw {stats['tw_rows']} "
            f"missing_ask {len(stats['missing_ask'])} unclassified {len(stats['unclassified_sr'])}",
            flush=True,
        )
        for row in stats["missing_ask"][:8]:
            print("   no Grade A", row, flush=True)
    if not args.write:
        print("dry-run (pass --write to update data.json)")
        return
    as_of = now_shanghai()
    apply(root, cards_by_set, stats_by_set, as_of)
    print("wrote", as_of)


if __name__ == "__main__":
    main()
