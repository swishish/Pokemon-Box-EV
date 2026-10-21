#!/usr/bin/env python3
"""Build data/base-psa-pop/catalog.json from PriceCharting console listings.

Uses public console JSON only. Writes slugs that already exist as products.
Does not invent names, numbers, or pop counts. Variants (error / Red Cheeks /
Ghost Stamp / sealed) are excluded. Base Set 2 is matched by card name.
"""
from __future__ import annotations

import json
import re
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36"
)
NUM_END = re.compile(r"#(\d+)\s*$")
VARIANT_RE = re.compile(
    r"error|black star|promo|jumbo|signed|ghost stamp|red cheeks|"
    r"gray stamp|booster|theme deck|prerelease|w stamp|1999-2000",
    re.I,
)
EXISTING_REQUIRED = {
    "charizard",
    "charmeleon",
    "charmander",
    "alakazam",
    "blastoise",
    "chansey",
    "gyarados",
    "hitmonchan",
    "magneton",
    "mewtwo",
    "nidoking",
    "ninetales",
    "poliwrath",
    "raichu",
    "venusaur",
    "zapdos",
}


def fetch_console(console: str) -> list[dict]:
    products: list[dict] = []
    cursor = None
    for _ in range(20):
        url = f"https://www.pricecharting.com/console/{console}"
        if cursor:
            url += f"?cursor={cursor}"
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode())
        batch = data.get("products") or []
        products.extend(batch)
        nxt = data.get("cursor")
        if not batch or not nxt or nxt == cursor:
            break
        cursor = nxt
        time.sleep(0.15)
    return products


def card_name(product_name: str) -> str:
    text = NUM_END.sub("", product_name or "").strip()
    return re.sub(r"\s*\[[^\]]+\]\s*", " ", text).strip()


def numbered(product: dict) -> tuple[int, str, str] | None:
    name = product.get("productName") or ""
    uri = product.get("productUri") or ""
    m = NUM_END.search(name)
    if not m or not uri:
        return None
    return int(m.group(1)), name, uri


def is_variant(name: str, uri: str) -> bool:
    return bool(VARIANT_RE.search(f"{name} {uri}"))


def rarity_for(number: int) -> str:
    if 1 <= number <= 16:
        return "holo"
    if number in {17, 18, 19, 20, 21, 22} or 70 <= number <= 79 or number == 96:
        return "rare"
    if 23 <= number <= 42 or 80 <= number <= 90:
        return "uncommon"
    return "common"


def card_id(uri: str, number: int) -> str:
    tail = f"-{number}"
    base = uri[:-len(tail)] if uri.endswith(tail) else uri
    return base.replace("'", "").replace(".", "").lower()


def collect_print(products: list[dict], *, edition: str) -> dict[int, tuple[str, str]]:
    out: dict[int, tuple[str, str]] = {}
    for product in products:
        parsed = numbered(product)
        if not parsed:
            continue
        number, name, uri = parsed
        blob = f"{name} {uri}".lower()
        if is_variant(name, uri):
            continue
        if edition == "1st":
            if "1st edition" not in blob:
                continue
            if "[" in name and "1st edition]" not in name.lower():
                continue
        elif edition == "unlimited":
            if "[" in name or "1st" in blob or "shadowless" in blob:
                continue
        elif edition == "base2":
            if "[" in name or "1st" in blob or "shadowless" in blob:
                continue
        if number in out:
            continue
        out[number] = (card_name(name), uri)
    return out


def main() -> int:
    base = fetch_console("pokemon-base-set")
    base2 = fetch_console("pokemon-base-set-2")
    first = collect_print(base, edition="1st")
    unlimited = collect_print(base, edition="unlimited")
    base2_by_name = {
        name.lower(): (number, uri) for number, (name, uri) in collect_print(base2, edition="base2").items()
    }

    cards = []
    for number in range(1, 103):
        if number not in first:
            print(f"skip #{number}: no canonical 1st Edition product", file=sys.stderr)
            continue
        name, first_uri = first[number]
        cid = card_id(first_uri.replace("-1st-edition", ""), number)
        prints = {
            "1st": f"pokemon-base-set/{first_uri}",
        }
        if number in unlimited:
            unl_name, unl_uri = unlimited[number]
            if unl_name.lower() != name.lower():
                print(f"skip unlimited #{number}: 1st {name!r} vs {unl_name!r}", file=sys.stderr)
            else:
                prints["unlimited"] = f"pokemon-base-set/{unl_uri}"
        b2 = base2_by_name.get(name.lower())
        number_base2 = None
        if b2:
            number_base2, b2_uri = b2
            prints["base2"] = f"pokemon-base-set-2/{b2_uri}"
        line = "charizard" if cid in {"charizard", "charmeleon", "charmander"} else None
        cards.append(
            {
                "id": cid,
                "name": name,
                "number_base": number,
                "number_base2": number_base2,
                "rarity": rarity_for(number),
                "line": line,
                "required": cid in EXISTING_REQUIRED,
                "prints": prints,
            }
        )

    cards.sort(key=lambda c: (c["number_base"], c["id"]))
    payload = {
        "notes": (
            "English Base Set #1–102. Slugs are PriceCharting console productUri values "
            "(HTTP-listed products, not invented). 1st Edition is required. Unlimited and "
            "Base Set 2 are included only when a same-name product exists. Variants "
            "(error / Red Cheeks / Ghost Stamp / sealed) are omitted. Machamp #8 has 1st "
            "only. Do not invent slugs or pops."
        ),
        "source": "https://www.pricecharting.com/pop/item/{slug}",
        "default_card_id": "charizard",
        "prints": [
            {"id": "1st", "label": "1st Edition", "set": "Base Set"},
            {"id": "unlimited", "label": "Unlimited", "set": "Base Set"},
            {"id": "base2", "label": "Base Set 2", "set": "Base Set 2"},
        ],
        "cards": cards,
    }
    out = ROOT / "data" / "base-psa-pop" / "catalog.json"
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {out} cards={len(cards)}")
    print(
        "with unlimited",
        sum(1 for c in cards if "unlimited" in c["prints"]),
        "with base2",
        sum(1 for c in cards if "base2" in c["prints"]),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
