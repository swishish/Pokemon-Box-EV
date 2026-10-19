#!/usr/bin/env python3
"""Map Chase_Fit cards to thumbnail URLs already used on this site.

Sources (no invented IDs, no PriceCharting image hashes):
  JP  — local calc chase webps (img_map.json) via data.json PriceCharting URL overlap
  EN  — TCGPlayer CDN thumbs already stored on market-en / market-midera constituents

Writes data/chase-fit/thumbs.json. Missing cards stay off the map; the page
shows a placeholder. Stdlib only.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.parse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
THUMBS_PATH = os.path.join(ROOT, "data", "chase-fit", "thumbs.json")
CHASE_PATH = os.path.join(ROOT, "data", "chase-fit", "latest.json")
IMG_MAP_PATH = os.path.join(ROOT, "img_map.json")
DATA_JSON_PATH = os.path.join(ROOT, "data.json")
MARKET_EN_PATH = os.path.join(ROOT, "data", "market-en", "latest.json")
MARKET_MIDERA_PATH = os.path.join(ROOT, "data", "market-midera", "latest.json")

TCG_CDN = re.compile(r"^https://tcgplayer-cdn\.tcgplayer\.com/product/\d+_200w\.jpg$")
LOCAL_WEBP = re.compile(r"^img/cards/[A-Za-z0-9]+_\d+\.webp$")


def _load(path: str, default=None):
    if not os.path.isfile(path):
        return default
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _norm_pc(url: str) -> str:
    u = urllib.parse.unquote(str(url or "")).lower().rstrip("/")
    u = re.sub(r"^https?://(www\.)?", "", u)
    return u.replace("&amp;", "&")


def _lastseg(url: str) -> str:
    u = _norm_pc(url)
    return u.rsplit("/", 1)[-1] if u else ""


def _num_key(raw: str) -> str:
    """Collector number only (strip set-size suffix). '238/191' and '238' → '238'."""
    s = str(raw or "").upper().replace(" ", "")
    s = s.replace("—", "/")
    m = re.match(r"^0*([A-Z]*\d+|[A-Z]+\d+)(?:/[A-Z0-9]+)?$", s)
    if m:
        left = m.group(1)
        return left.lstrip("0") or "0" if left[0].isdigit() else left
    return re.sub(r"[^A-Z0-9]", "", s)


def _parse_chase_en(name: str) -> tuple[str, str, str, set[str]]:
    """Return (title, number, set, flags) from 'Pikachu ex #179 Pokemon Prismatic Evolutions'."""
    flags: set[str] = set()
    low = name.lower()
    if "[staff]" in low:
        flags.add("staff")
    if "reverse holo" in low:
        flags.add("reverse")
    title = name
    m = re.match(
        r"^(.+?)\s+#([A-Z0-9]+(?:/[A-Z0-9]+)?)(?:\s+Pokemon\s+(.+))?$",
        name,
        re.I,
    )
    if m:
        title, num, setn = m.group(1), m.group(2), m.group(3) or ""
    else:
        title, num, setn = name, "", ""
    title = re.sub(r"\s*\[.*?\]", "", title).strip()
    return title, num, setn, flags


def _name_tokens(s: str) -> set[str]:
    stop = {
        "pokemon", "japanese", "promo", "the", "of", "ex", "v", "vmax", "vstar",
        "holo", "reverse", "staff", "full", "art", "alternate", "secret",
    }
    return {t for t in re.findall(r"[a-z0-9]+", s.lower()) if t not in stop and len(t) > 1}


def _set_overlap(chase_set: str, listed_set: str) -> bool:
    a = chase_set.lower().strip()
    b = listed_set.lower().strip()
    if not a or not b:
        return False
    if a in b or b in a:
        return True
    at, bt = _name_tokens(a), _name_tokens(b)
    return len(at & bt) >= 2


def jp_index(data_cards: list, img_map: dict) -> dict:
    out = {}
    for row in data_cards or []:
        key = f"{row.get('set')}|{row.get('num')}"
        src = img_map.get(key) or img_map.get(f"{row.get('set')}|{str(row.get('num'))}")
        if not src or not LOCAL_WEBP.match(src):
            continue
        rec = {"src": src, "kind": "local", "key": key}
        pc = row.get("pc_card_url") or ""
        if pc:
            out[_norm_pc(pc)] = rec
            out[_lastseg(pc)] = rec
    return out


def en_pool(*payloads: dict) -> list:
    rows = []
    for payload in payloads:
        if not payload:
            continue
        for c in payload.get("constituents") or []:
            if isinstance(c, dict) and TCG_CDN.match(str(c.get("image") or "")):
                rows.append(c)
        indexes = payload.get("indexes") or {}
        if isinstance(indexes, dict):
            for spec in indexes.values():
                for c in (spec or {}).get("constituents") or []:
                    if isinstance(c, dict) and TCG_CDN.match(str(c.get("image") or "")):
                        rows.append(c)
    return rows


def match_en(card: dict, pool: list) -> dict | None:
    title, num, setn, flags = _parse_chase_en(card.get("name") or "")
    want = _num_key(num)
    if not want:
        return None
    title_tok = _name_tokens(title)
    if not title_tok:
        return None
    hits = []
    for row in pool:
        listed_num = _num_key(row.get("number") or "")
        if listed_num != want:
            continue
        listed_name = row.get("name") or ""
        low = listed_name.lower()
        if "staff" in flags and "staff" not in low:
            continue
        if "staff" not in flags and "staff" in low:
            continue
        if "reverse" in flags and "reverse" not in low:
            continue
        if not _set_overlap(setn, row.get("setName") or ""):
            continue
        listed_tok = _name_tokens(listed_name)
        if not (title_tok & listed_tok):
            continue
        hits.append(row)
    if len(hits) == 1:
        return {"src": hits[0]["image"], "kind": "tcgplayer", "id": hits[0].get("id")}
    # unique image among hits
    imgs = {h["image"] for h in hits}
    if len(imgs) == 1:
        h = hits[0]
        return {"src": h["image"], "kind": "tcgplayer", "id": h.get("id")}
    return None


def build(chase_cards: list, *, img_map: dict, data_cards: list, en_latest: dict, midera: dict) -> dict:
    jp = jp_index(data_cards, img_map)
    pool = en_pool(en_latest, midera)
    by_id = {}
    counts = {"local": 0, "tcgplayer": 0, "none": 0}
    for card in chase_cards:
        cid = card.get("card_id")
        if not cid:
            counts["none"] += 1
            continue
        rec = None
        pc = card.get("pricecharting_url") or ""
        if pc:
            rec = jp.get(_norm_pc(pc)) or jp.get(_lastseg(pc))
        if rec:
            by_id[cid] = {"src": rec["src"], "kind": "local"}
            counts["local"] += 1
            continue
        if (card.get("language") or "").upper() == "EN":
            rec = match_en(card, pool)
            if rec:
                by_id[cid] = {"src": rec["src"], "kind": "tcgplayer"}
                counts["tcgplayer"] += 1
                continue
        counts["none"] += 1
    return {
        "notes": "JP local img/cards/*.webp via calc data.json PC URL overlap; EN TCGPlayer CDN from market-en/midera. Missing = page placeholder. Do not invent SNKR/PC image URLs.",
        "counts": counts,
        "by_id": by_id,
    }


def write_thumbs(payload: dict, path: str | None = None) -> str:
    dest = path or THUMBS_PATH
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    with open(dest, "w", encoding="utf-8") as fh:
        fh.write(text)
    return dest


def build_from_repo() -> dict:
    chase = _load(CHASE_PATH, {}) or {}
    img_map = (_load(IMG_MAP_PATH, {}) or {}).get("cards") or {}
    data = _load(DATA_JSON_PATH, {}) or {}
    return build(
        chase.get("cards") or [],
        img_map=img_map,
        data_cards=data.get("cards") or [],
        en_latest=_load(MARKET_EN_PATH, {}) or {},
        midera=_load(MARKET_MIDERA_PATH, {}) or {},
    )


def self_test() -> None:
    img_map = {"SV8a|223": "img/cards/SV8a_223.webp"}
    data_cards = [
        {
            "set": "SV8a",
            "num": 223,
            "pc_card_url": "https://www.pricecharting.com/game/pokemon-japanese-terastal-festival-ex/eevee-ex-223",
        }
    ]
    en_latest = {
        "constituents": [
            {
                "id": "590027",
                "name": "Pikachu ex - 238/191",
                "number": "238/191",
                "setName": "Surging Sparks",
                "image": "https://tcgplayer-cdn.tcgplayer.com/product/590027_200w.jpg",
            },
            {
                "id": "999",
                "name": "Wrong Pikachu",
                "number": "238/191",
                "setName": "Base Set",
                "image": "https://tcgplayer-cdn.tcgplayer.com/product/999_200w.jpg",
            },
        ]
    }
    cards = [
        {
            "card_id": "TWIN-JP-EEVEE_EX_223",
            "name": "Eevee Ex 223",
            "language": "JP",
            "pricecharting_url": "https://www.pricecharting.com/game/pokemon-japanese-terastal-festival-ex/eevee-ex-223",
        },
        {
            "card_id": "CHASE-EN-PIKACHU-EX-238",
            "name": "Pikachu ex #238 Pokemon Surging Sparks",
            "language": "EN",
        },
        {
            "card_id": "JP-KANAZAWA-SOBBLE",
            "name": "Sobble #146/S-P Pokemon Japanese Promo",
            "language": "JP",
            "pricecharting_url": "https://www.pricecharting.com/game/pokemon-japanese-promo/sobble-146s-p",
        },
        {
            "card_id": "STAFF",
            "name": "Ethan's Typhlosion [Staff] #34 Pokemon Destined Rivals",
            "language": "EN",
        },
    ]
    payload = build(cards, img_map=img_map, data_cards=data_cards, en_latest=en_latest, midera={})
    assert payload["by_id"]["TWIN-JP-EEVEE_EX_223"]["kind"] == "local"
    assert payload["by_id"]["CHASE-EN-PIKACHU-EX-238"]["src"].endswith("590027_200w.jpg")
    assert "JP-KANAZAWA-SOBBLE" not in payload["by_id"]
    assert "STAFF" not in payload["by_id"]
    print("build_chase_thumbs self-test ok")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    if args.self_test:
        self_test()
        return 0
    payload = build_from_repo()
    counts = payload["counts"]
    print(
        f"chase thumbs local={counts['local']} tcgplayer={counts['tcgplayer']} "
        f"none={counts['none']}"
    )
    if args.dry_run:
        return 0
    dest = write_thumbs(payload)
    print("wrote", os.path.relpath(dest, ROOT))
    return 0


if __name__ == "__main__":
    sys.exit(main())
