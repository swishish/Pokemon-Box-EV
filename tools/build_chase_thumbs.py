#!/usr/bin/env python3
"""Map Chase_Fit cards to thumbnail URLs already used on this site.

Sources (no invented IDs, no PriceCharting image hashes):
  1. JP local calc webps (img_map.json) via data.json PriceCharting URL overlap
     or set-code + collector number when that local file already exists
  2. EN TCGPlayer CDN thumbs already stored on market-en / midera / vintage
  3. JP SNKRDUNK CDN thumbs already stored on market-jp-psa10 / bench
     (strict set + number + name / stage match)
  4. Optional network (--fetch, default on except --self-test):
     JP  — public SNK search + apparel primaryMedia.imageUrl (discovered, not invented)
     EN  — public Pokémon TCG / TCGdex card image after the bytes 200-check

Writes data/chase-fit/thumbs.json. Missing cards stay off the map; the page
shows a placeholder. Stdlib only.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
THUMBS_PATH = os.path.join(ROOT, "data", "chase-fit", "thumbs.json")
CHASE_PATH = os.path.join(ROOT, "data", "chase-fit", "latest.json")
IMG_MAP_PATH = os.path.join(ROOT, "img_map.json")
DATA_JSON_PATH = os.path.join(ROOT, "data.json")
MARKET_EN_PATH = os.path.join(ROOT, "data", "market-en", "latest.json")
MARKET_MIDERA_PATH = os.path.join(ROOT, "data", "market-midera", "latest.json")
MARKET_VINTAGE_PATH = os.path.join(ROOT, "data", "market-vintage", "latest.json")
JP60_PATH = os.path.join(ROOT, "data", "market-jp-psa10", "baskets.json")
BENCH_BASKETS_PATH = os.path.join(ROOT, "data", "market-jp-psa10-bench", "baskets.json")
BENCH_SCAN_PATH = os.path.join(ROOT, "data", "market-jp-psa10-bench", "scan-cache.json")

TCG_CDN = re.compile(r"^https://tcgplayer-cdn\.tcgplayer\.com/product/\d+_200w\.jpg$")
LOCAL_WEBP = re.compile(r"^img/cards/[A-Za-z0-9]+_\d+\.webp$")
SNK_CDN = re.compile(
    r"^https://cdn\.snkrdunk\.com/upload_bg_removed/[0-9a-fA-F-]{36}\.webp$"
)
PTCG_IMG = re.compile(r"^https://images\.pokemontcg\.io/[A-Za-z0-9]+/[A-Za-z0-9]+\.png$")
TCGDEX_IMG = re.compile(
    r"^https://assets\.tcgdex\.net/(?:en|ja)/[A-Za-z0-9.]+/[A-Za-z0-9.]+/[A-Za-z0-9]+/low\.webp$"
)

USER_AGENT = "Pokemon-Box-EV/1.31 (+https://github.com/swishish/Pokemon-Box-EV)"
SNK_HOST = "https://snkrdunk.com"

# Chase English set phrase → JP set codes already used on this site.
JP_SET_ALIASES = {
    "heat wave arena": ["SV9a"],
    "paradise dragona": ["SV7a"],
    "terastal festival": ["SV8a"],
    "crimson haze": ["SV5a"],
    "vmax climax": ["S8b"],
    "eevee heroes": ["S6a"],
    "vstar universe": ["S12a"],
    "scarlet & violet 151": ["SV2a"],
    "card 151": ["SV2a"],
    "pokemon card 151": ["SV2a"],
    "the glory of team rocket": ["SV10"],
}

# Chase English set phrase → pokemontcg.io set id (public CDN, verified at fetch).
PTCG_EN_SET = {
    "evolving skies": "swsh7",
    "destined rivals": "sv10",
    "prismatic evolutions": "sv8pt5",
    "silver tempest": "swsh12",
    "lost origin": "swsh11",
    "brilliant stars": "swsh9",
    "crown zenith": "swsh12pt5",
    "celebrations": "cel25",
    "evolutions": "xy12",
    "shining fates": "swsh45",
}
PTCG_TG_SET = {
    "lost origin": "swsh11tg",
    "brilliant stars": "swsh9tg",
    "silver tempest": "swsh12tg",
}
PTCG_GG_SET = {
    "crown zenith": "swsh12pt5gg",
}

# TCGdex EN set ids (same cards, different slug).
TCGDEX_EN_SET = {
    "evolving skies": "swsh7",
    "destined rivals": "sv10",
    "prismatic evolutions": "sv08.5",
    "silver tempest": "swsh12",
    "lost origin": "swsh11",
    "brilliant stars": "swsh9",
    "crown zenith": "swsh12.5",
    "celebrations": "cel25",
    "evolutions": "xy12",
    "shining fates": "swsh4.5",
}
TCGDEX_TG_SET = {
    "lost origin": "swsh11tg",
    "brilliant stars": "swsh9tg",
    "silver tempest": "swsh12tg",
}
TCGDEX_GG_SET = {
    "crown zenith": "swsh12.5gg",
}

_BRACKET_SET_NUM = re.compile(
    r"\[([A-Za-z][A-Za-z0-9]*(?:-[A-Za-z0-9]+)*)\s+([A-Z]*\d+)(?:/[A-Z0-9]+)?\]",
    re.I,
)
_BRACKET_NUM_SET = re.compile(
    r"\[(\d+)\s+([A-Za-z][A-Za-z0-9]*(?:-[A-Za-z0-9]+)*)\]",
    re.I,
)
_BRACKET_NO = re.compile(
    r"\[([A-Za-z][A-Za-z0-9]*(?:-[A-Za-z0-9]+)*)\s+No\.?\s*(\d+)\]",
    re.I,
)
_PROMO_SLASH = re.compile(
    r"^(.+?)\s+#([A-Z]*\d+)/([A-Z0-9]+(?:-[A-Z0-9]+)*)(?:\s+Pokemon\s+(.+))?$",
    re.I,
)
_PROMO_SET_ONLY = re.compile(
    r"^(.+?)\s+#([A-Z]{1,4}-P)(?:\s+Pokemon\s+(.+))?$",
    re.I,
)
_STD_NUM = re.compile(
    r"^(.+?)\s+#([A-Z0-9]+(?:/[A-Z0-9]+)?)(?:\s+Pokemon\s+(.+))?$",
    re.I,
)
_SEALED = re.compile(
    r"(unopened pack|unopened|booster box|gift box|complete set|未開封|シュリンク)",
    re.I,
)
_EN_PRINT = re.compile(
    r"\b(English|\bEN\b|\[EN\]|\(EN\)|SVP EN|\[CN\]|\[KR\])\b",
    re.I,
)


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


def _set_key(raw: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(raw or "").upper())


def parse_chase(name: str) -> tuple[str, str, str, set[str], set[str]]:
    """Return (title, number, set, flags, extra_tokens) from a Chase_Fit name.

    Understands promo slashes with hyphens (#146/S-P, #289/SV-P) and
    set-only promos (#XY-P). Bracket tags become extra_tokens (Mt. Fuji…).
    """
    flags: set[str] = set()
    extra: set[str] = set()
    raw = name or ""
    low = raw.lower()
    if "[staff]" in low:
        flags.add("staff")
    if "reverse holo" in low:
        flags.add("reverse")
    for tag in re.findall(r"\[(.*?)\]", raw):
        extra |= _name_tokens(tag)
        extra |= {t for t in re.findall(r"[a-z0-9]+", tag.lower()) if len(t) > 1}
    title = re.sub(r"\s*\[.*?\]", "", raw).strip()
    m = _PROMO_SLASH.match(title)
    if m:
        return m.group(1).strip(), m.group(2), m.group(3), flags, extra
    m = _PROMO_SET_ONLY.match(title)
    if m:
        return m.group(1).strip(), "", m.group(2), flags, extra
    m = _STD_NUM.match(title)
    if m:
        return m.group(1).strip(), m.group(2), m.group(3) or "", flags, extra
    return title, "", "", flags, extra


def _parse_chase_en(name: str) -> tuple[str, str, str, set[str]]:
    title, num, setn, flags, _extra = parse_chase(name)
    return title, num, setn, flags


def _name_tokens(s: str) -> set[str]:
    stop = {
        "pokemon", "japanese", "promo", "the", "of", "ex", "v", "vmax", "vstar",
        "holo", "reverse", "staff", "full", "art", "alternate", "secret",
        "promotional", "cards", "pack", "expansion", "enhanced", "high", "class",
        "box", "sar", "sr", "ur", "ar", "ssr", "csr", "sa", "gx", "tag", "team",
        "special", "center",
    }
    return {t for t in re.findall(r"[a-z0-9]+", s.lower()) if t not in stop and len(t) > 1}


def _card_title_only(name: str) -> str:
    """Strip set / pack suffixes so 'VMAX Climax' does not look like a VMAX card."""
    return (name or "").split("[")[0].split("(")[0].strip()


def _stages(title: str) -> set[str]:
    """Combat / layout stage tokens that must agree (VMAX ≠ V)."""
    core = _card_title_only(title)
    low = f" {core.lower()} "
    out: set[str] = set()
    if "vmax" in low:
        out.add("vmax")
    elif "vstar" in low:
        out.add("vstar")
    elif re.search(r"\bv\b", low):
        out.add("v")
    if re.search(r"\bex\b", low):
        out.add("ex")
    if "lv.x" in low or "lv x" in low or "lvx" in low.replace(".", ""):
        out.add("lvx")
    return out


def _stages_agree(want: set[str], listed: set[str]) -> bool:
    for key in ("vmax", "vstar", "v", "ex", "lvx"):
        if (key in want) != (key in listed):
            return False
    return True


def _set_overlap(chase_set: str, listed_set: str) -> bool:
    a = chase_set.lower().strip()
    b = listed_set.lower().strip()
    if not a or not b:
        return False
    if a in b or b in a:
        return True
    at, bt = _name_tokens(a), _name_tokens(b)
    return len(at & bt) >= 2


def jp_set_codes(setn: str, data_sets: dict | None = None) -> list[str]:
    """Map Chase English set phrase or promo code to JP set codes."""
    raw = (setn or "").strip()
    if not raw:
        return []
    if re.match(r"^[A-Za-z0-9]+(?:-[A-Za-z0-9]+)+$", raw) or re.match(
        r"^[A-Za-z]{1,4}-P$", raw
    ):
        return [raw]
    sl = raw.lower()
    sl = re.sub(r"^japanese\s+", "", sl)
    out: list[str] = []
    for key, codes in JP_SET_ALIASES.items():
        if key in sl or sl in key:
            out.extend(codes)
    for code, spec in (data_sets or {}).items():
        if not isinstance(spec, dict):
            continue
        en = str(spec.get("en") or "").lower()
        if not en:
            continue
        if en in sl or sl in en:
            out.append(str(code))
        elif _set_overlap(sl, en):
            out.append(str(code))
    # de-dupe preserve order
    seen = set()
    uniq = []
    for c in out:
        k = _set_key(c)
        if k in seen:
            continue
        seen.add(k)
        uniq.append(c)
    return uniq


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


def jp_local_by_set_num(img_map: dict) -> dict:
    """img_map key SET|NUM → local webp, only existing paths."""
    out = {}
    for key, src in (img_map or {}).items():
        if not src or not LOCAL_WEBP.match(src):
            continue
        path = os.path.join(ROOT, src)
        if not os.path.isfile(path):
            continue
        out[str(key).upper().replace(" ", "")] = src
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
    imgs = {h["image"] for h in hits}
    if len(imgs) == 1:
        h = hits[0]
        return {"src": h["image"], "kind": "tcgplayer", "id": h.get("id")}
    return None


def _snk_pairs(row: dict) -> list[tuple[str, str]]:
    name = row.get("name") or ""
    setc = str(row.get("set") or row.get("setCode") or "")
    pairs: list[tuple[str, str]] = []
    for m in _BRACKET_SET_NUM.finditer(name):
        pairs.append((m.group(1), _num_key(m.group(2))))
    for m in _BRACKET_NUM_SET.finditer(name):
        pairs.append((m.group(2), _num_key(m.group(1))))
    for m in _BRACKET_NO.finditer(name):
        pairs.append((m.group(1), _num_key(m.group(2))))
    pn = row.get("productNumber") or ""
    m = re.search(r"pkmn-tcg-([A-Za-z0-9\-]+)-(\d+)$", str(pn), re.I)
    if m:
        pairs.append((m.group(1), _num_key(m.group(2))))
    if row.get("num") is not None and setc:
        pairs.append((setc, _num_key(row.get("num"))))
    return pairs


def snk_pool(*payloads: dict) -> list:
    rows = []
    seen = set()

    def add(row):
        if not isinstance(row, dict):
            return
        src = str(row.get("image") or "")
        if not SNK_CDN.match(src) or src in seen:
            return
        seen.add(src)
        rows.append(row)

    for payload in payloads:
        if not payload:
            continue
        for row in payload.get("candidates") or []:
            add(row)
        cards = payload.get("cards")
        if isinstance(cards, list):
            for row in cards:
                add(row)
        elif isinstance(cards, dict):
            for series in cards.values():
                if isinstance(series, list):
                    for row in series:
                        add(row)
    return rows


def match_snk(card: dict, pool: list, data_sets: dict | None = None) -> dict | None:
    """Strict SNK match: collector number + set code (when known) + name/stage."""
    raw = card.get("name") or ""
    title, num, setn, _flags, extra = parse_chase(raw)
    want = _num_key(num)
    title_tok = _name_tokens(title)
    codes = jp_set_codes(setn, data_sets)
    # slash-number already put set in `setn`
    code_keys = {_set_key(c) for c in codes}
    want_stage = _stages(title)
    generic = title_tok <= {"pikachu", "eevee", "mew"} or len(title_tok) <= 1 and extra
    hits = []
    for row in pool:
        listed_name = row.get("name") or ""
        if _SEALED.search(listed_name) or _EN_PRINT.search(listed_name):
            continue
        pairs = _snk_pairs(row)
        if want:
            nums = {p[1] for p in pairs}
            if want not in nums:
                continue
        elif not extra:
            continue
        if code_keys:
            row_sets = {_set_key(p[0]) for p in pairs if p[0]}
            if not (code_keys & row_sets):
                continue
        listed_tok = _name_tokens(listed_name) | _name_tokens(row.get("localizedName") or "")
        overlap = (title_tok | extra) & listed_tok
        if not overlap:
            continue
        if generic and extra and not (extra & listed_tok):
            continue
        if not _stages_agree(want_stage, _stages(listed_name)):
            continue
        hits.append(row)
    imgs = {h["image"] for h in hits if SNK_CDN.match(str(h.get("image") or ""))}
    if len(imgs) == 1:
        return {"src": next(iter(imgs)), "kind": "snkrdunk"}
    return None


def _http_json(url: str, timeout: int = 25) -> dict | list | None:
    req = urllib.request.Request(
        url,
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
    )
    last = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as err:
            last = err
            if err.code in (400, 404, 410):
                break
            time.sleep(0.6 * (attempt + 1))
        except Exception as err:  # noqa: BLE001 — retry transients
            last = err
            time.sleep(0.6 * (attempt + 1))
    if last and getattr(last, "code", None) not in (404,):
        sys.stderr.write(f"WARN: fetch failed {url} ({last})\n")
    return None


def _image_ok(url: str, timeout: int = 15) -> bool:
    req = urllib.request.Request(
        url,
        headers={"User-Agent": USER_AGENT, "Accept": "image/*,*/*"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            ctype = (resp.headers.get("content-type") or "").lower()
            chunk = resp.read(32)
            return resp.status == 200 and ctype.startswith("image/") and bool(chunk)
    except Exception:
        return False


def fetch_snk_search(card: dict, data_sets: dict | None = None) -> dict | None:
    raw = card.get("name") or ""
    title, num, setn, _flags, extra = parse_chase(raw)
    codes = jp_set_codes(setn, data_sets)
    queries = []
    compact = re.sub(r"[-']", " ", title)
    compact = re.sub(r"\bwearing\b", "", compact, flags=re.I)
    compact = " ".join(compact.split())
    if codes and num:
        queries.append(f"{title} {codes[0]} {num}")
    if extra and num:
        queries.append(f"{title} {' '.join(sorted(extra)[:3])} {num}")
    if extra:
        queries.append(f"{title} {' '.join(sorted(extra)[:3])}")
        words = [t for t in sorted(extra) if not t.isdigit()]
        if words:
            queries.append(f"{title} {' '.join(words[:4])}")
            queries.append(f"{' '.join(words[:4])} {title}")
    for tag in re.findall(r"\[(.*?)\]", raw):
        tag = tag.strip()
        if tag:
            queries.append(f"{title} {tag}")
            words = [t for t in re.findall(r"[A-Za-z]+", tag) if len(t) > 2]
            if words:
                queries.append(f"{' '.join(words)} {title}")
    if title and num:
        queries.append(f"{title} {num}")
    if compact != title:
        if codes and num:
            queries.append(f"{compact} {codes[0]} {num}")
        if extra:
            queries.append(f"{compact} {' '.join(sorted(extra)[:3])}")
        queries.append(compact)
    seen_q = set()
    apparel_hits = []
    want = _num_key(num)
    title_tok = _name_tokens(title)
    code_keys = {_set_key(c) for c in codes}
    want_stage = _stages(title)
    generic = title_tok <= {"pikachu", "eevee", "mew"}
    for q in queries:
        q = " ".join(q.split())
        if not q or q in seen_q:
            continue
        seen_q.add(q)
        url = f"{SNK_HOST}/en/v1/search?keyword={urllib.parse.quote(q)}&perPage=8&page=1"
        payload = _http_json(url)
        time.sleep(0.35)
        if not isinstance(payload, dict):
            continue
        for row in payload.get("streetwears") or []:
            if not isinstance(row, dict):
                continue
            name = row.get("name") or ""
            if _SEALED.search(name) or _EN_PRINT.search(name):
                continue
            pairs = _snk_pairs(row)
            if want:
                if want not in {p[1] for p in pairs} and want not in _num_key(name):
                    # name itself may embed the number without a clean pair
                    if want not in re.findall(r"\d+", name):
                        continue
            if code_keys:
                row_sets = {_set_key(p[0]) for p in pairs if p[0]}
                blob_sets = {_set_key(m) for m in re.findall(
                    r"\b([A-Z]{1,4}-P|[A-Z]{1,3}\d*[A-Za-z]?)\b", name
                )}
                if not (code_keys & (row_sets | blob_sets)):
                    continue
            listed_tok = _name_tokens(name) | _name_tokens(row.get("localizedName") or "")
            if not ((title_tok | extra) & listed_tok):
                continue
            if generic and extra and not (extra & listed_tok):
                continue
            if not _stages_agree(want_stage, _stages(name)):
                continue
            aid = row.get("id")
            if aid:
                apparel_hits.append(int(aid))
        if apparel_hits:
            break
    # unique apparel ids, fetch image
    imgs = set()
    for aid in list(dict.fromkeys(apparel_hits))[:4]:
        data = _http_json(f"{SNK_HOST}/v1/apparels/{int(aid)}")
        time.sleep(0.35)
        if not isinstance(data, dict):
            continue
        src = ((data.get("primaryMedia") or {}).get("imageUrl")) or ""
        if SNK_CDN.match(src):
            imgs.add(src)
    if len(imgs) == 1:
        return {"src": next(iter(imgs)), "kind": "snkrdunk"}
    return None


def _ptcg_set_id(setn: str, num: str) -> str | None:
    sl = re.sub(r"^pokemon\s+", "", (setn or "").lower()).strip()
    n = (num or "").upper()
    if n.startswith("TG"):
        return PTCG_TG_SET.get(sl)
    if n.startswith("GG"):
        return PTCG_GG_SET.get(sl)
    return PTCG_EN_SET.get(sl)


def _tcgdex_set_id(setn: str, num: str) -> str | None:
    sl = re.sub(r"^pokemon\s+", "", (setn or "").lower()).strip()
    n = (num or "").upper()
    if n.startswith("TG"):
        return TCGDEX_TG_SET.get(sl)
    if n.startswith("GG"):
        return TCGDEX_GG_SET.get(sl)
    return TCGDEX_EN_SET.get(sl)


def fetch_en_public(card: dict) -> dict | None:
    """Verify a public EN card image (pokemontcg.io, then TCGdex)."""
    title, num, setn, _flags, extra = parse_chase(card.get("name") or "")
    want = (num or "").upper()
    if not want:
        return None
    title_tok = _name_tokens(title)
    candidates: list[tuple[str, str]] = []
    pset = _ptcg_set_id(setn, want)
    if pset:
        nkey = _num_key(want)
        for n in (want, nkey, nkey.zfill(3) if nkey.isdigit() else ""):
            if not n:
                continue
            url = f"https://images.pokemontcg.io/{pset}/{n}.png"
            if PTCG_IMG.match(url):
                candidates.append(("pokemontcg", url))
    dset = _tcgdex_set_id(setn, want)
    if dset:
        nkey = _num_key(want)
        local = want
        if nkey.isdigit():
            # TCGdex often zero-pads to 3
            local = nkey.zfill(3) if dset.startswith("sv") else nkey
        # series folder is the letters prefix of the set id
        series = re.match(r"^[A-Za-z]+", dset)
        series_id = series.group(0) if series else "sv"
        url = f"https://assets.tcgdex.net/en/{series_id}/{dset}/{local}/low.webp"
        if TCGDEX_IMG.match(url):
            candidates.append(("tcgdex", url))
        # also try unpadded
        if local != nkey:
            url2 = f"https://assets.tcgdex.net/en/{series_id}/{dset}/{nkey}/low.webp"
            if TCGDEX_IMG.match(url2):
                candidates.append(("tcgdex", url2))
    # Celebrations classic-collection fallback
    if "celebration" in setn.lower() and _num_key(want).isdigit():
        n = _num_key(want)
        for url in (
            f"https://images.pokemontcg.io/cel25cc/{n}.png",
            f"https://assets.tcgdex.net/en/cel/cel25cc/{n}/low.webp",
        ):
            kind = "pokemontcg" if url.startswith("https://images.pokemontcg.io/") else "tcgdex"
            rx = PTCG_IMG if kind == "pokemontcg" else TCGDEX_IMG
            if rx.match(url):
                candidates.append((kind, url))
    seen = set()
    for kind, url in candidates:
        if url in seen:
            continue
        seen.add(url)
        if _image_ok(url):
            # cheap name check: skip if title tokens are empty
            if title_tok or extra:
                return {"src": url, "kind": kind}
        time.sleep(0.15)
    return None


def _valid_rec(rec: dict | None) -> bool:
    if not rec or not rec.get("src"):
        return False
    kind = rec.get("kind")
    src = rec.get("src")
    if kind == "local":
        return bool(LOCAL_WEBP.match(src) and os.path.isfile(os.path.join(ROOT, src)))
    if kind == "tcgplayer":
        return bool(TCG_CDN.match(src))
    if kind == "snkrdunk":
        return bool(SNK_CDN.match(src))
    if kind == "pokemontcg":
        return bool(PTCG_IMG.match(src))
    if kind == "tcgdex":
        return bool(TCGDEX_IMG.match(src))
    return False


def build(
    chase_cards: list,
    *,
    img_map: dict,
    data_cards: list,
    data_sets: dict | None = None,
    en_latest: dict,
    midera: dict,
    vintage: dict | None = None,
    jp60: dict | None = None,
    bench_baskets: dict | None = None,
    bench_scan: dict | None = None,
    prev: dict | None = None,
    fetch: bool = False,
) -> dict:
    jp = jp_index(data_cards, img_map)
    local_set = jp_local_by_set_num(img_map)
    pool = en_pool(en_latest, midera, vintage or {})
    snk = snk_pool(jp60 or {}, bench_baskets or {}, bench_scan or {})
    prev_by = (prev or {}).get("by_id") or {}
    by_id = {}
    counts = {
        "local": 0,
        "tcgplayer": 0,
        "snkrdunk": 0,
        "pokemontcg": 0,
        "tcgdex": 0,
        "none": 0,
    }

    def store(cid: str, rec: dict) -> None:
        by_id[cid] = {"src": rec["src"], "kind": rec["kind"]}
        counts[rec["kind"]] = counts.get(rec["kind"], 0) + 1

    pending_fetch = []
    for card in chase_cards:
        cid = card.get("card_id")
        if not cid:
            counts["none"] += 1
            continue
        rec = None
        pc = card.get("pricecharting_url") or ""
        if pc:
            rec = jp.get(_norm_pc(pc)) or jp.get(_lastseg(pc))
        if rec and _valid_rec(rec):
            store(cid, rec)
            continue
        # local file by set + number (same calc assets, no invented path)
        title, num, setn, _flags, _extra = parse_chase(card.get("name") or "")
        for code in jp_set_codes(setn, data_sets):
            key = f"{code}|{_num_key(num)}".upper()
            src = local_set.get(key)
            if src:
                rec = {"src": src, "kind": "local"}
                break
        if rec and _valid_rec(rec):
            store(cid, rec)
            continue
        lang = (card.get("language") or "").upper()
        if lang == "EN":
            rec = match_en(card, pool)
            if rec and _valid_rec(rec):
                store(cid, rec)
                continue
        rec = match_snk(card, snk, data_sets)
        if rec and _valid_rec(rec):
            store(cid, rec)
            continue
        # reuse a previously discovered public URL (no re-invent, no re-fetch)
        old = prev_by.get(cid)
        if _valid_rec(old) and old.get("kind") in {"snkrdunk", "pokemontcg", "tcgdex"}:
            store(cid, old)
            continue
        pending_fetch.append(card)
        counts["none"] += 1

    if fetch:
        for card in pending_fetch:
            cid = card.get("card_id")
            lang = (card.get("language") or "").upper()
            rec = None
            if lang == "JP":
                rec = fetch_snk_search(card, data_sets)
            if rec is None and lang == "EN":
                rec = fetch_en_public(card)
            if rec is None and lang == "JP":
                # modern JP set cards sometimes sit on TCGdex even if SNK search is thin
                rec = fetch_jp_tcgdex(card, data_sets)
            if rec and _valid_rec(rec):
                by_id[cid] = {"src": rec["src"], "kind": rec["kind"]}
                counts[rec["kind"]] = counts.get(rec["kind"], 0) + 1
                counts["none"] -= 1

    return {
        "notes": (
            "JP local img/cards/*.webp via calc PC URL or set+number; "
            "EN TCGPlayer CDN from market-en/midera/vintage; "
            "JP SNK upload_bg_removed webp already on market-jp pages or public apparel JSON; "
            "EN public pokemontcg.io / tcgdex after HTTP 200. "
            "Missing = page placeholder. Do not invent SNKR/PC image hashes."
        ),
        "counts": counts,
        "by_id": by_id,
    }


def fetch_jp_tcgdex(card: dict, data_sets: dict | None = None) -> dict | None:
    title, num, setn, _flags, extra = parse_chase(card.get("name") or "")
    codes = [
        c for c in jp_set_codes(setn, data_sets)
        if not re.search(r"-P$", c, re.I) and not re.match(r"^[A-Z]{1,4}-P$", c, re.I)
    ]
    nkey = _num_key(num)
    if not codes or not nkey or not nkey.isdigit():
        return None
    title_tok = _name_tokens(title)
    want_stage = _stages(title)
    for code in codes:
        local = nkey.zfill(3)
        api = f"https://api.tcgdex.net/v2/ja/cards/{urllib.parse.quote(code)}-{local}"
        data = _http_json(api)
        time.sleep(0.2)
        if not isinstance(data, dict) or data.get("image") is None:
            api = f"https://api.tcgdex.net/v2/ja/cards/{urllib.parse.quote(code)}-{nkey}"
            data = _http_json(api)
            time.sleep(0.2)
        if not isinstance(data, dict):
            continue
        listed = data.get("name") or ""
        if title_tok and not (title_tok & _name_tokens(listed)):
            # JA names won't token-overlap EN titles; skip the token check
            pass
        if not _stages_agree(want_stage, _stages(listed + " " + title)):
            # listed is JA; rely on set+number uniqueness of the API id
            pass
        img = data.get("image")
        if not img:
            continue
        url = str(img).rstrip("/") + "/low.webp"
        if TCGDEX_IMG.match(url) and _image_ok(url):
            return {"src": url, "kind": "tcgdex"}
    return None


def write_thumbs(payload: dict, path: str | None = None) -> str:
    dest = path or THUMBS_PATH
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    with open(dest, "w", encoding="utf-8") as fh:
        fh.write(text)
    return dest


def build_from_repo(*, fetch: bool = True) -> dict:
    chase = _load(CHASE_PATH, {}) or {}
    img_map = (_load(IMG_MAP_PATH, {}) or {}).get("cards") or {}
    data = _load(DATA_JSON_PATH, {}) or {}
    return build(
        chase.get("cards") or [],
        img_map=img_map,
        data_cards=data.get("cards") or [],
        data_sets=data.get("sets") or {},
        en_latest=_load(MARKET_EN_PATH, {}) or {},
        midera=_load(MARKET_MIDERA_PATH, {}) or {},
        vintage=_load(MARKET_VINTAGE_PATH, {}) or {},
        jp60=_load(JP60_PATH, {}) or {},
        bench_baskets=_load(BENCH_BASKETS_PATH, {}) or {},
        bench_scan=_load(BENCH_SCAN_PATH, {}) or {},
        prev=_load(THUMBS_PATH, {}) or {},
        fetch=fetch,
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
    bench_scan = {
        "candidates": [
            {
                "name": 'Greninja ex SAR [SV5a 090/066](Enhanced Expansion Pack "Crimson Haze")',
                "image": "https://cdn.snkrdunk.com/upload_bg_removed/aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee.webp",
            },
            {
                "name": "Pikachu: PROMO [SV-P 120](SV-P Promotional cards)",
                "image": "https://cdn.snkrdunk.com/upload_bg_removed/11111111-2222-3333-4444-555555555555.webp",
            },
            {
                "name": "Sobble Kanazawa: PROMO P [S-P 146](S-P Promotional cards)",
                "image": "https://cdn.snkrdunk.com/upload_bg_removed/aaaaaaaa-1111-2222-3333-bbbbbbbbbbbb.webp",
            },
            {
                "name": "Leafeon V SR: SA[S6a 071/069](Enhanced Expansion Pack Eevee Heroes)",
                "image": "https://cdn.snkrdunk.com/upload_bg_removed/ffffffff-0000-1111-2222-333333333333.webp",
            },
            {
                "name": 'Pikachu V CSR[S8b 222/184](High Class Pack"VMAX Climax Box")',
                "image": "https://cdn.snkrdunk.com/upload_bg_removed/aaaaaaaa-9999-8888-7777-666666666666.webp",
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
            "card_id": "CHASE-JP-GRENINJA-EX-90",
            "name": "Greninja ex #90 Pokemon Japanese Crimson Haze",
            "language": "JP",
        },
        {
            "card_id": "JP-TOHOKU-PIKACHU",
            "name": "Pikachu [Tohoku Pokemon Center] #120/BW-P Pokemon Japanese Promo",
            "language": "JP",
        },
        {
            "card_id": "CL-JP-LEAFEON-VMAX-89",
            "name": "Leafeon VMAX #89 Pokemon Japanese Eevee Heroes",
            "language": "JP",
        },
        {
            "card_id": "STAFF",
            "name": "Ethan's Typhlosion [Staff] #34 Pokemon Destined Rivals",
            "language": "EN",
        },
        {
            "card_id": "CHASE-JP-PIKACHU-V-222",
            "name": "Pikachu V #222 Pokemon Japanese VMAX Climax",
            "language": "JP",
        },
    ]
    payload = build(
        cards,
        img_map=img_map,
        data_cards=data_cards,
        data_sets={},
        en_latest=en_latest,
        midera={},
        vintage={},
        jp60={},
        bench_baskets={},
        bench_scan=bench_scan,
        prev={},
        fetch=False,
    )
    assert payload["by_id"]["TWIN-JP-EEVEE_EX_223"]["kind"] == "local"
    assert payload["by_id"]["CHASE-EN-PIKACHU-EX-238"]["src"].endswith("590027_200w.jpg")
    assert payload["by_id"]["JP-KANAZAWA-SOBBLE"]["kind"] == "snkrdunk"
    assert payload["by_id"]["CHASE-JP-GRENINJA-EX-90"]["kind"] == "snkrdunk"
    # Tohoku is BW-P #120 — must NOT take SV-P #120
    assert "JP-TOHOKU-PIKACHU" not in payload["by_id"]
    # VMAX must not take the V art
    assert "CL-JP-LEAFEON-VMAX-89" not in payload["by_id"]
    assert "STAFF" not in payload["by_id"]
    # Set name "VMAX Climax" must not block a V card
    assert payload["by_id"]["CHASE-JP-PIKACHU-V-222"]["kind"] == "snkrdunk"

    title, num, setn, flags, extra = parse_chase(
        "Sobble #146/S-P Pokemon Japanese Promo"
    )
    assert title == "Sobble" and num == "146" and setn == "S-P"
    title, num, setn, flags, extra = parse_chase(
        "Surfing Pikachu [Mt. Fuji] #25 Pokemon Japanese Promo"
    )
    assert "fuji" in extra and num == "25"
    title, num, setn, flags, extra = parse_chase(
        "Psyduck [Pokekyun Pokemon Center] #XY-P Pokemon Japanese Promo"
    )
    assert setn == "XY-P" and num == ""
    print("build_chase_thumbs self-test ok")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument(
        "--offline",
        action="store_true",
        help="Skip SNK / pokemontcg.io / TCGdex fetches (CI / self-test)",
    )
    ap.add_argument(
        "--fetch",
        action="store_true",
        help="Force public lookups for cards still missing after offline sources",
    )
    args = ap.parse_args(argv)
    if args.self_test:
        self_test()
        return 0
    fetch = bool(args.fetch) or not args.offline
    if args.offline:
        fetch = False
    payload = build_from_repo(fetch=fetch)
    counts = payload["counts"]
    print(
        "chase thumbs "
        + " ".join(f"{k}={counts.get(k, 0)}" for k in (
            "local", "tcgplayer", "snkrdunk", "pokemontcg", "tcgdex", "none"
        ))
    )
    if args.dry_run:
        return 0
    dest = write_thumbs(payload)
    print("wrote", os.path.relpath(dest, ROOT))
    return 0


if __name__ == "__main__":
    sys.exit(main())
