#!/usr/bin/env python3
"""Refresh JP box EV snapshot: set-specific pity rates + SR/AR min(PC, SNKR).

Reads data.json + PriceCharting console HTML in /tmp/pc-sets (or --pc-dir).
Writes data.json, data.js, and replaces window.EV_DATA in index.html.
Does not invent prices or rates; missing PC keeps SNKR (and vice versa).
"""
from __future__ import annotations

import argparse
import json
import re
from datetime import datetime
from html import unescape
from pathlib import Path
from zoneinfo import ZoneInfo

SRAR_POOLS = {"AR", "poke_sr", "goods_sr"}
SNKR_SRAR_GARBAGE = 100000  # reject typo asks like 111111
USD_HKD = 7.80
JPY_PER_HKD = 20.3
YEN_PER_USD = USD_HKD * JPY_PER_HKD  # site HKD convention

# Community floors / exclusive 超配. Expected counts = floors + exclusive chase
# (+ documented over-alloc already inside chase means). Sources in each note.
PITY = {
    "M1L": {
        "family": "mega_regular",
        "floors": {"AR": 3.0, "goods_sr": 1.0},
        "chase_exclusive": {"poke_sr": 0.80, "SAR": 0.28, "MUR": 0.015},
        "over_alloc": 0.10,
        "over_alloc_note": "Pokegto 3枚箱 ~10%: extra chase is poke_sr OR SAR/MUR, not an independent second pack-rate.",
        "rates": {"AR": 3.0, "goods_sr": 1.0, "poke_sr": 0.80, "SAR": 0.28, "MUR": 0.015},
        "source": "Pokegto ~1,000 BOX (2026-01-11) https://pokemon-infomation.com/https-pokemon-infomation-com-pull-rates-megabrave/",
    },
    "M1S": {
        "family": "mega_regular",
        "floors": {"AR": 3.0, "goods_sr": 1.0},
        "chase_exclusive": {"poke_sr": 0.80, "SAR": 0.28, "MUR": 0.015},
        "over_alloc": 0.10,
        "over_alloc_note": "Same MEGA regular pattern as M1L (Pokegto twin-set 1,000 BOX).",
        "rates": {"AR": 3.0, "goods_sr": 1.0, "poke_sr": 0.80, "SAR": 0.28, "MUR": 0.015},
        "source": "Pokegto https://pokemon-infomation.com/pull-rates-megasymphonia/",
    },
    "M3": {
        "family": "mega_regular",
        "floors": {"AR": 3.0, "goods_sr": 1.0},
        "chase_exclusive": {"poke_sr": 0.80, "SAR": 0.282, "MUR": 0.013},
        "over_alloc": 0.10,
        "rates": {"AR": 3.0, "goods_sr": 1.0, "poke_sr": 0.80, "SAR": 0.282, "MUR": 0.013},
        "source": "Pokegto https://pokemon-infomation.com/pull-rates-munikisuzero/ (poke_sr~80%, SAR~28%, MUR~1–2%, goods SR×1, AR×3)",
    },
    "M4": {
        "family": "mega_regular",
        "floors": {"AR": 3.0, "goods_sr": 1.0},
        "chase_exclusive": {"poke_sr": 0.80, "SAR": 0.282, "MUR": 0.013},
        "over_alloc": 0.10,
        "rates": {"AR": 3.0, "goods_sr": 1.0, "poke_sr": 0.80, "SAR": 0.282, "MUR": 0.013},
        "source": "Pokegto https://pokemon-infomation.com/pull-rates-ninjaspiner/",
    },
    "M5": {
        "family": "mega_regular",
        "floors": {"AR": 3.0, "goods_sr": 1.0},
        "chase_exclusive": {"poke_sr": 0.80, "SAR": 0.282, "MUR": 0.01},
        "over_alloc": 0.10,
        "rates": {"AR": 3.0, "goods_sr": 1.0, "poke_sr": 0.80, "SAR": 0.282, "MUR": 0.01},
        "source": "Pokegto https://pokemon-infomation.com/pull-rates-abysseye/ (MUR メガダークライex ~1.0%)",
    },
    "M6": {
        "family": "mega_regular",
        "floors": {"AR": 3.0, "goods_sr": 1.0},
        "chase_exclusive": {"poke_sr": 0.86, "SAR": 0.13, "MUR": 0.01},
        "over_alloc": 0.0,
        "over_alloc_note": "pokeka-atari Storm Emeralda: 2枚確定 = goods SR floor + chase (SR~86% / SAR~13% / MUR~1%). No Pokegto page yet; do not keep prior poke_sr 1.12.",
        "rates": {"AR": 3.0, "goods_sr": 1.0, "poke_sr": 0.86, "SAR": 0.13, "MUR": 0.01},
        "source": "pokeka-atari https://pokeka-atari.jp/expansion/storm-emeralda + MEGA regular goods-SR floor (Pokegto family)",
    },
    "M2a": {
        "family": "mega_highclass",
        "floors": {"AR": 3.0, "goods_sr": 1.0, "MA": 1.0},
        "chase_exclusive": {"poke_sr": 0.10, "SAR": 0.37, "MUR": 0.0175},
        "over_alloc": 0.0,
        "over_alloc_note": "Extra slot 0–1 (~50% 3-hit boxes): supporter SR OR SAR OR MUR. poke_sr stays 0.10 — do not force a 2-SR floor. SAR 0.37 is carton ~7/20 (0.35) vs Pokegto table 40%; keep mid, do not inflate.",
        "rates": {"AR": 3.0, "goods_sr": 1.0, "MA": 1.0, "poke_sr": 0.10, "SAR": 0.37, "MUR": 0.0175},
        "source": "Pokegto 2,000 BOX (2026-01-02) https://pokemon-infomation.com/pull-rates-megadreamex/ ; PokeGuardian box pattern; note.com/ZEN",
    },
    "M6a": {
        "family": "special",
        "floors": {"AR": 4.0, "S": 2.0, "SAR": 1.0},
        "chase_exclusive": {"FUR": 0.166667},
        "rates": {"AR": 4.0, "S": 2.0, "SAR": 1.0, "FUR": 0.166667},
        "source": "Pokegto ~720 BOX https://pokemon-infomation.com/pull-rates-30th/ (FUR 6BOXに1枚 = 1/6)",
    },
    "SV8a": {
        "family": "sv_highclass",
        "floors": {"SAR": 1.0},
        "chase_exclusive": {"poke_sr": 0.20, "sup_SAR": 0.10, "UR": 0.06},
        "rates": {"SAR": 1.0, "sup_SAR": 0.10, "poke_sr": 0.20, "UR": 0.06},
        "source": "Pokegto https://pokemon-infomation.com/pull-rates-terafesex/ ; Master Ball + ACE omitted (no SNKR Grade A in snapshot)",
    },
    "SV4a": {
        "family": "sv_highclass",
        "floors": {"SSR": 1.0, "S": 2.9},
        "chase_exclusive": {"goods_sr": 0.30, "SAR": 0.10, "UR": 0.10, "AR": 0.10},
        "rates": {"S": 2.9, "AR": 0.10, "SSR": 1.0, "goods_sr": 0.30, "SAR": 0.10, "UR": 0.10},
        "source": "Pokegto https://pokemon-infomation.com/pull-rates-shinytreasure/",
    },
    "SV11B": {
        "family": "sv11",
        "floors": {"AR": 4.0},
        "chase_exclusive": {"poke_sr": 0.56, "goods_sr": 0.19, "SAR": 0.20, "BWR": 0.05},
        "over_alloc": 0.0,
        "over_alloc_note": "One exclusive SR/SAR/BWR slot (~1.00). Prior BWR 0.10 + SR 1.00 + SAR 0.20 double-counted. BWR ~1/20 BOX (axstex); SR split 6:2 poke:goods.",
        "rates": {"AR": 4.0, "poke_sr": 0.56, "goods_sr": 0.19, "SAR": 0.20, "BWR": 0.05},
        "source": "axstex https://axstex01.com/blackbolt-ranking/ ; pokeka-atari White Flare sibling (BWR~1%, SAR~21%, SR~76%); card-compass ~24 BOX/BWR",
    },
    "SV11W": {
        "family": "sv11",
        "floors": {"AR": 4.0},
        "chase_exclusive": {"poke_sr": 0.56, "goods_sr": 0.19, "SAR": 0.20, "BWR": 0.05},
        "over_alloc": 0.0,
        "rates": {"AR": 4.0, "poke_sr": 0.56, "goods_sr": 0.19, "SAR": 0.20, "BWR": 0.05},
        "source": "pokeka-atari https://pokeka-atari.jp/expansion/white-flare ; same exclusive-slot model as SV11B",
    },
    "SV3": {
        "family": "sv_regular",
        "floors": {"AR": 3.0},
        "chase_exclusive": {"poke_sr": 0.50, "goods_sr": 0.25, "SAR": 0.20, "UR": 0.10},
        "over_alloc": 0.05,
        "over_alloc_note": "Pokegto: 1 SR-or-above floor + ~10% 2枚箱. Means already include the extra ~0.05.",
        "rates": {"AR": 3.0, "poke_sr": 0.50, "goods_sr": 0.25, "SAR": 0.20, "UR": 0.10},
        "source": "Pokegto https://pokemon-infomation.com/pull-rates-kokuennoshihaisha/",
    },
    "SV7a": {
        "family": "sv_regular",
        "floors": {"AR": 3.0, "ACE": 1.0},
        "chase_exclusive": {"poke_sr": 0.50, "goods_sr": 0.30, "SAR": 0.20, "UR": 0.10},
        "over_alloc": 0.10,
        "rates": {"AR": 3.0, "ACE": 1.0, "poke_sr": 0.50, "goods_sr": 0.30, "SAR": 0.20, "UR": 0.10},
        "source": "Pokegto https://pokemon-infomation.com/pull-rates-rakuendoragona/",
    },
    "SV1a": {
        "family": "sv_regular",
        "floors": {"AR": 3.0},
        "chase_exclusive": {"poke_sr": 0.45, "goods_sr": 0.30, "SAR": 0.20, "UR": 0.10},
        "over_alloc": 0.05,
        "rates": {"AR": 3.0, "poke_sr": 0.45, "goods_sr": 0.30, "SAR": 0.20, "UR": 0.10},
        "source": "Pokegto https://pokemon-infomation.com/pull-rates-tripletbeat/",
    },
    "SV1S": {
        "family": "sv_regular",
        "floors": {"AR": 3.0},
        "chase_exclusive": {"poke_sr": 0.45, "goods_sr": 0.30, "SAR": 0.15, "UR": 0.10},
        "over_alloc": 0.00,
        "rates": {"AR": 3.0, "poke_sr": 0.45, "goods_sr": 0.30, "SAR": 0.15, "UR": 0.10},
        "source": "Pokegto 500 BOX https://pokemon-infomation.com/pull-rates-scarletex/ (SAR ~15% this set)",
    },
    "SV2a": {
        "family": "sv_regular",
        "floors": {"AR": 3.0},
        "chase_exclusive": {"poke_sr": 0.60, "goods_sr": 0.20, "SAR": 0.20, "UR": 0.10},
        "over_alloc": 0.10,
        "rates": {"AR": 3.0, "poke_sr": 0.60, "goods_sr": 0.20, "SAR": 0.20, "UR": 0.10},
        "source": "Pokegto https://pokemon-infomation.com/pull-rates-pokemoncard151/ ; Master Ball omitted (no SNKR Grade A)",
    },
    "SV8": {
        "family": "sv_regular",
        "floors": {"AR": 3.0},
        "chase_exclusive": {"poke_sr": 0.55, "goods_sr": 0.25, "SAR": 0.20, "UR": 0.10},
        "over_alloc": 0.10,
        "omitted": "ACE SPEC ×1/box (Pokegto) omitted — no ACE cards/SNKR Grade A in snapshot; do not invent.",
        "rates": {"AR": 3.0, "poke_sr": 0.55, "goods_sr": 0.25, "SAR": 0.20, "UR": 0.10},
        "source": "Pokegto 1,000 BOX https://pokemon-infomation.com/pull-rates-tyodenbraker/",
    },
    "SV10": {
        "family": "sv_regular",
        "floors": {"AR": 3.0},
        "chase_exclusive": {"poke_sr": 0.50, "goods_sr": 0.30, "SAR": 0.20, "UR": 0.10},
        "over_alloc": 0.10,
        "rates": {"AR": 3.0, "poke_sr": 0.50, "goods_sr": 0.30, "SAR": 0.20, "UR": 0.10},
        "source": "Pokegto https://pokemon-infomation.com/pull-rates-rocketdaneikou/",
    },
    "SV9a": {
        "family": "sv_regular",
        "floors": {"AR": 3.0},
        "chase_exclusive": {"poke_sr": 0.55, "goods_sr": 0.25, "SAR": 0.20, "UR": 0.10},
        "over_alloc": 0.10,
        "rates": {"AR": 3.0, "poke_sr": 0.55, "goods_sr": 0.25, "SAR": 0.20, "UR": 0.10},
        "source": "SV enhanced-regular pattern (Pokegto family + existing snapshot); Heat Wave Arena Pokegto linked from family index",
    },
}


def parse_pc_console(html: str) -> dict[int, dict]:
    out: dict[int, dict] = {}
    for part in html.split('<td class="title"')[1:]:
        am = re.search(r'<a href="([^"]+)">([^<]*)</a>', part)
        if not am:
            continue
        href, title = am.group(1), unescape(am.group(2)).strip()
        if "booster" in title.lower() or "booster" in href.lower():
            continue
        nm = re.search(r"#(\d+)", title)
        if not nm:
            continue
        num = int(nm.group(1))
        up = re.search(r'used_price.*?<span class="js-price">([^<]*)</span>', part, re.S)
        raw = (up.group(1) if up else "").strip().replace("$", "").replace(",", "")
        usd = None
        try:
            if raw:
                usd = float(raw)
        except ValueError:
            usd = None
        rec = {
            "title": title,
            "href": href if href.startswith("http") else "https://www.pricecharting.com" + href,
            "usd": usd,
        }
        # first hit wins (table is unique by number)
        out.setdefault(num, rec)
    return out


def snkr_ok(pool: str, yen) -> float | None:
    if yen is None:
        return None
    try:
        v = float(yen)
    except (TypeError, ValueError):
        return None
    if v <= 0:
        return None
    if pool in SRAR_POOLS and v >= SNKR_SRAR_GARBAGE:
        return None
    return v


def ev_raw(pool: str, snkr, pc_yen) -> tuple[float | None, str | None]:
    s = snkr_ok(pool, snkr)
    p = None
    if pc_yen is not None:
        try:
            pv = float(pc_yen)
            if pv > 0:
                p = pv
        except (TypeError, ValueError):
            p = None
    if pool in SRAR_POOLS:
        if s is not None and p is not None:
            return (min(s, p), "min_pc_snkr")
        if p is not None:
            return (p, "pc")
        if s is not None:
            return (s, "snkr")
        return (None, None)
    if s is not None:
        return (s, "snkr")
    return (None, None)


def avg(xs):
    ys = [x for x in xs if x is not None]
    if not ys:
        return None
    return sum(ys) / len(ys)


def snapshot_for(set_obj, cards, haircuts, grade_pools, fees):
    rates = set_obj["rates"]
    priced = [c for c in cards if c.get("gradeA") is not None]
    fx = float(fees.get("fx_usdjpy") or 158)
    psa_fee = float(fees.get("psa_usd") or 60) * fx
    cgc_fee = float(fees.get("cgc_usd") or 20) * fx

    def hc(c):
        disc = haircuts.get(c["pool"], 0)
        if c["pool"] == "sup_SAR":
            disc = haircuts.get("sup_SAR", haircuts.get("SAR", 0))
        if c["pool"] == "UR":
            disc = haircuts.get("UR", haircuts.get("AR", 0.5))
        return c["gradeA"] * (1 - disc)

    def psa(c):
        raw = hc(c)
        if c["pool"] not in grade_pools:
            return raw
        if c.get("psa10_ask") is None or c.get("gem_rate") is None or not (c.get("psa10_ask") or 0) > 0:
            return raw
        gem = max(0.0, min(1.0, c["gem_rate"] * (1 - 0.2)))
        ask_keep = 0.9
        win = c["psa10_ask"] * ask_keep - psa_fee
        if c.get("psa9_ask") is not None:
            lose = max(0.0, c["psa9_ask"] * ask_keep - psa_fee)
        else:
            lose = raw
        return max(raw, gem * win + (1 - gem) * lose)

    def cgc(c):
        raw = hc(c)
        if c["pool"] not in grade_pools:
            return raw
        if c.get("cgc10_usd") is None or c.get("gem_rate") is None:
            return raw
        win = c["cgc10_usd"] * fx * 0.9 - cgc_fee
        return max(raw, c["gem_rate"] * win + (1 - c["gem_rate"]) * raw)

    psa_n = sum(
        1
        for c in priced
        if c["pool"] in grade_pools
        and c.get("psa10_ask")
        and c["psa10_ask"] > 0
        and c.get("gem_rate")
        and c["gem_rate"] > 0
    )
    cgc_n = sum(1 for c in priced if c["pool"] in grade_pools and c.get("cgc10_usd") is not None and c.get("gem_rate") is not None)
    has_psa = psa_n >= 3
    has_cgc = cgc_n >= 3

    ga = hc_v = psa_v = cgc_v = 0.0
    for pool, rate in rates.items():
        pcards = [c for c in priced if c["pool"] == pool]
        if not pcards:
            continue
        ga += avg([c["gradeA"] for c in pcards]) * rate
        hc_v += avg([hc(c) for c in pcards]) * rate
        if has_psa:
            psa_v += avg([psa(c) for c in pcards]) * rate
        if has_cgc:
            cgc_v += avg([cgc(c) for c in pcards]) * rate
    return {
        "GA": int(round(ga)),
        "HC": int(round(hc_v)),
        "PSA": int(round(psa_v)) if has_psa else None,
        "CGC": int(round(cgc_v)) if has_cgc else None,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default="/workspace")
    ap.add_argument("--pc-dir", default="/tmp/pc-sets")
    args = ap.parse_args()
    root = Path(args.root)
    pc_dir = Path(args.pc_dir)

    data = json.loads((root / "data.json").read_text())
    as_of = datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d %H:%M Asia/Shanghai")
    data["as_of"] = as_of
    data.setdefault("fx", {})
    data["fx"]["jpy_per_hkd"] = JPY_PER_HKD
    data["fx"]["usd_hkd"] = USD_HKD
    data["fx"]["yen_per_usd_via_hkd"] = YEN_PER_USD

    notes = data.setdefault("notes", {})
    notes["method_sr_ar_price"] = (
        "SR (poke_sr/goods_sr) and AR EV inputs use min(PriceCharting ungraded USD×7.80×20.3, "
        "SNKRDUNK Grade A ¥) when both exist. PC is completed-sale ungraded (loose), not an ask. "
        "Missing source → keep the other; never invent. Archetype: SV8 スコヴィランex SR #120 "
        "SNKR Grade A ¥1000 vs PC ungraded $2.27 (~¥360). Higher rarities (SAR/MUR/MA/FUR/UR/BWR/…) "
        "stay SNKR Grade A; large PC vs SNKR gaps are flagged in notes.sar_pc_gap, not auto-minned."
    )
    notes["method_pity"] = (
        "Set-specific community box floors + exclusive 超配 (not official TPC). MEGA regular: AR×3, "
        "goods/item SR×1, plus one exclusive chase slot (poke_sr OR SAR OR MUR) with ~10% 3枚箱 extra. "
        "SV regular: AR×3 + one exclusive SR/SAR/UR slot (~10% 2枚箱). M2a: AR×3 + goods SR×1 + MA×1 "
        "+ 0–1 extra (poke_sr~10% / SAR~37% / MUR~1.75%) — poke_sr is NOT a 2-SR floor. "
        "SV11: AR×4 + one exclusive SR/SAR/BWR slot (BWR ~1/20). Equal-within-pool averages unchanged "
        "(chase concentration e.g. Umbreon can bias). Sources: Pokegto, pokeka-atari, axstex, PokeGuardian."
    )
    notes["method_denom"] = (
        "Unchanged: HK-landed sealed box = SNKR 3–4 box unit ×1.035 + ship (HK$173/4 × ¥20.3). "
        "Not SNKR single-pack floor. Duties/taxes excluded. UI HKD override still beats denom."
    )
    notes["method_hc"] = data["notes"].get("method_hc", "")
    notes["pc_sr_ar_as_of"] = as_of
    notes["pc_sr_ar_fx"] = f"PC USD → ¥ via HKD: ×{USD_HKD}×{JPY_PER_HKD} = ×{YEN_PER_USD:.2f}"

    pc_by_set = {}
    for code in data["sets"]:
        htmlp = pc_dir / f"{code}.html"
        if htmlp.exists():
            pc_by_set[code] = parse_pc_console(htmlp.read_text("utf-8", "replace"))
        else:
            pc_by_set[code] = {}

    # apply pity rates
    for code, set_obj in data["sets"].items():
        pity = PITY.get(code)
        if not pity:
            continue
        set_obj["rates"] = dict(pity["rates"])
        set_obj["pity"] = {
            "family": pity["family"],
            "floors": pity.get("floors"),
            "chase_exclusive": pity.get("chase_exclusive"),
            "over_alloc": pity.get("over_alloc"),
            "source": pity["source"],
        }
        if pity.get("over_alloc_note"):
            set_obj["pity"]["over_alloc_note"] = pity["over_alloc_note"]
        if pity.get("omitted"):
            set_obj["pity"]["omitted"] = pity["omitted"]
        set_obj["rate_notes"] = pity["source"]

    # apply prices
    stats = {"minned": 0, "pc_only": 0, "snkr_only": 0, "gap_srar": [], "sar_gaps": [], "garbage": []}
    for c in data["cards"]:
        # preserve original SNKR if not already stored
        if "gradeA_snkr" not in c:
            c["gradeA_snkr"] = c.get("gradeA")
        snkr = c.get("gradeA_snkr")
        pool = c["pool"]
        rec = pc_by_set.get(c["set"], {}).get(c["num"])
        if rec and rec.get("usd") is not None:
            c["pc_ungraded_usd"] = rec["usd"]
            c["pc_yen"] = round(rec["usd"] * YEN_PER_USD, 2)
            c["pc_card_url"] = rec["href"]
            c["pc_card_title"] = rec["title"]
        else:
            # do not invent; leave absent
            c.pop("pc_ungraded_usd", None)
            c.pop("pc_yen", None)
            c.pop("pc_card_url", None)
            c.pop("pc_card_title", None)

        if pool in SRAR_POOLS and snkr_ok(pool, snkr) is None and snkr not in (None,):
            try:
                if float(snkr) >= SNKR_SRAR_GARBAGE:
                    stats["garbage"].append((c["set"], c["num"], c.get("name"), snkr))
                    c["gradeA_snkr"] = None
                    snkr = None
            except (TypeError, ValueError):
                pass

        ev, src = ev_raw(pool, snkr, c.get("pc_yen"))
        c["gradeA"] = ev
        if src:
            c["gradeA_src"] = src
        elif "gradeA_src" in c:
            del c["gradeA_src"]

        if src == "min_pc_snkr":
            stats["minned"] += 1
            if snkr and c.get("pc_yen") and snkr > c["pc_yen"] * 1.5:
                stats["gap_srar"].append((c["set"], c["num"], c.get("name"), snkr, c["pc_yen"], ev))
        elif src == "pc":
            stats["pc_only"] += 1
        elif src == "snkr" and pool in SRAR_POOLS:
            stats["snkr_only"] += 1

        if pool in {"SAR", "MUR", "MA", "FUR", "sup_SAR"} and c.get("pc_yen") and snkr_ok(pool, snkr):
            ratio = snkr / c["pc_yen"] if c["pc_yen"] else None
            if ratio and (ratio > 2 or ratio < 0.5):
                stats["sar_gaps"].append((c["set"], pool, c["num"], c.get("name"), snkr, c["pc_yen"], ratio))

    # snapshots
    haircuts = data["defaults"]["haircuts"]
    grade_pools = set(data.get("grade_pools") or [])
    for code, set_obj in data["sets"].items():
        cards = [c for c in data["cards"] if c["set"] == code]
        set_obj["snapshot"] = snapshot_for(set_obj, cards, haircuts, grade_pools, data["fees"])
        set_obj["pc_sr_ar_as_of"] = as_of

    notes["sar_pc_gap"] = (
        "SAR/MUR/MA/FUR not auto-minned. Large PC vs SNKR Grade A (ratio >2 or <0.5) "
        f"count={len(stats['sar_gaps'])}; see tools output / PR. Policy remains SNKR Grade A."
    )
    notes["pc_sr_ar_coverage"] = (
        f"SR/AR min applied on {stats['minned']} cards; PC-only {stats['pc_only']}; "
        f"SNKR-only (no PC) {stats['snkr_only']}; rejected SNKR typos {stats['garbage']}."
    )

    (root / "data.json").write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    # External data.js only — never inline into index.html <script>
    # (a missing </script> or literal </script> in JSON breaks EV_DATA assign).
    js_json = json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    (root / "data.js").write_text("window.EV_DATA = " + js_json + ";\n")

    print("as_of", as_of)
    print("minned", stats["minned"], "pc_only", stats["pc_only"], "snkr_only", stats["snkr_only"])
    print("garbage SNKR", stats["garbage"])
    print("top SR/AR SNKR>>PC:")
    for row in sorted(stats["gap_srar"], key=lambda r: (r[3] or 0) / (r[4] or 1), reverse=True)[:15]:
        print(" ", row)
    print("SAR/higher gaps (flag only):", len(stats["sar_gaps"]))
    for row in sorted(stats["sar_gaps"], key=lambda r: r[6], reverse=True)[:12]:
        print(" ", row)


if __name__ == "__main__":
    main()
