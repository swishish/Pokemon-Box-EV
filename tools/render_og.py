#!/usr/bin/env python3
"""Render page-specific Open Graph share cards (1200×630 PNG).

Home Box EV keeps the committed ``og.png`` (「日盒開盒值不值？」).
``index.html`` / ``calc.html`` / ``chase-fit.html`` stay on that EV card.
JP PSA10 pages get their own files so WhatsApp / Facebook do not reuse
the Box EV artwork or promote ``og:site_name`` as the EV home title.

WhatsApp caches previews by image *path* and often ignores ``?v=``.
When a live share card is stuck on old pixels, bump the PNG filename
(not only the query string) so a re-share / debugger scrape cannot
reuse the cached EV file.

  python3 tools/render_og.py --self-test   # stdlib: PNG IHDR + HTML meta
  python3 tools/render_og.py               # needs Pillow; rewrite PNGs
"""
from __future__ import annotations

import argparse
import html
import os
import struct
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OG_W, OG_H = 1200, 630
PAGES_PREFIX = "https://swishish.github.io/Pokemon-Box-EV/"

# Page-specific cards. Home / calc / chase-fit keep og.png and are not listed here.
CARDS = (
    {
        "key": "psa10-bench",
        # v2 path: WhatsApp still served cached EV art for og-psa10-bench.png
        # even after ?v=1.30.5. Filename change is the real cache bust.
        "png": "og-psa10-bench-v2.png",
        "pages": ("market-jp-psa10-bench.html",),
        "site_name": "日版 PSA10 基準",
        "title": "日版 PSA10 基準",
        "sub": "復古 · 中世代 · 現行",
        "fine": "靜態快照 · 非正式投資建議",
        "badge": "swishish.github.io",
        "forbidden": ("日盒開盒值不值", "開盒 EV", "Box EV", "Chase 公允值"),
    },
    {
        "key": "psa10",
        "png": "og-psa10.png",
        "pages": ("market-jp-psa10.html",),
        "site_name": "日版 PSA10 指數",
        "title": "日版 PSA10 指數",
        "sub": "等權 · 流動性 · 市值",
        "fine": "靜態快照 · 非正式投資建議",
        "badge": "swishish.github.io",
        "forbidden": ("日盒開盒值不值", "開盒 EV", "Box EV"),
    },
)

HOME_PAGES = ("index.html", "calc.html", "chase-fit.html")
HOME_SITE_NAME = "日盒開盒值不值？"
HOME_OG = "og.png"

# Written-TC methodology: ban HK colloquial particles on PSA10 pages.
CANTONESE_PARTICLES = (
    "唔",
    "呢頁",
    "嘅",
    "咗",
    "嚟",
    "啲",
    "冇",
    "咪",
    "啦",
    "喺",
    "佢",
    "只睇",
    "唔好",
    "做唔到",
    "唔係",
    "唔會",
    "唔再",
    "唔發明",
)

# Same pokéball mark as favicon.svg (scaled in the card).
POKE_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32" aria-hidden="true">
  <circle cx="16" cy="16" r="14" fill="#fff"/>
  <clipPath id="ball"><circle cx="16" cy="16" r="14"/></clipPath>
  <g clip-path="url(#ball)">
    <rect width="32" height="16" fill="#e4434f"/>
    <rect y="14" width="32" height="4" fill="#141414"/>
  </g>
  <circle cx="16" cy="16" r="14" fill="none" stroke="#141414" stroke-width="2"/>
  <circle cx="16" cy="16" r="5" fill="#fff" stroke="#141414" stroke-width="2.5"/>
  <circle cx="16" cy="16" r="2.2" fill="#fff" stroke="#141414" stroke-width="1.4"/>
</svg>"""

CARD_HTML = """<!DOCTYPE html>
<html lang="zh-Hant">
<head>
<meta charset="utf-8" />
<title>{title}</title>
<style>
  html, body {{
    margin: 0;
    width: {w}px;
    height: {h}px;
    overflow: hidden;
    background: #faf6ef;
    color: #141414;
    font-family: "WenQuanYi Micro Hei", "Noto Sans CJK TC", "Noto Sans TC",
      "PingFang TC", "Microsoft JhengHei", sans-serif;
  }}
  .stripe {{
    height: 12px;
    background: linear-gradient(90deg, #e4434f 0 34%, #ffd23f 34% 66%, #4f86f7 66%);
  }}
  .row {{
    height: {inner}px;
    display: flex;
    align-items: center;
    padding: 0 96px 0 88px;
    gap: 48px;
    box-sizing: border-box;
  }}
  .ball {{
    width: 196px;
    height: 196px;
    flex: 0 0 196px;
  }}
  .ball svg {{ width: 100%; height: 100%; display: block; }}
  .copy {{ min-width: 0; }}
  h1 {{
    margin: 0 0 14px;
    font-size: 64px;
    font-weight: 700;
    letter-spacing: -0.02em;
    line-height: 1.15;
  }}
  .sub {{
    margin: 0 0 10px;
    font-size: 30px;
    color: #5f5a51;
    font-weight: 500;
  }}
  .fine {{
    margin: 0 0 28px;
    font-size: 22px;
    color: #8a8478;
  }}
  .badge {{
    display: inline-block;
    background: #ffd23f;
    color: #141414;
    border-radius: 999px;
    padding: 10px 28px;
    font-size: 22px;
    font-weight: 700;
    line-height: 1.2;
  }}
</style>
</head>
<body>
  <div class="stripe"></div>
  <div class="row">
    <div class="ball">{poke}</div>
    <div class="copy">
      <h1>{title}</h1>
      <p class="sub">{sub}</p>
      <p class="fine">{fine}</p>
      <span class="badge">{badge}</span>
    </div>
  </div>
</body>
</html>
"""


def _assert(cond: bool, msg: str) -> None:
    if not cond:
        raise AssertionError(msg)


def png_size(path: str) -> tuple[int, int]:
    with open(path, "rb") as fh:
        sig = fh.read(8)
        _assert(sig == b"\x89PNG\r\n\x1a\n", f"{path}: not a PNG")
        length, ctype = struct.unpack(">I4s", fh.read(8))
        _assert(ctype == b"IHDR" and length == 13, f"{path}: missing IHDR")
        w, h = struct.unpack(">II", fh.read(8))
    return w, h


def card_html(card: dict) -> str:
    return CARD_HTML.format(
        w=OG_W,
        h=OG_H,
        inner=OG_H - 12,
        poke=POKE_SVG,
        title=html.escape(card["title"]),
        sub=html.escape(card["sub"]),
        fine=html.escape(card["fine"]),
        badge=html.escape(card["badge"]),
    )


FONT_CANDIDATES = (
    "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
    "/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/System/Library/Fonts/PingFang.ttc",
)

INK = (20, 20, 20)
BG = (250, 246, 239)
MUTED = (95, 90, 81)
FINE = (138, 132, 120)
YELLOW = (255, 210, 63)
RED = (228, 67, 79)
BLUE = (79, 134, 247)


def find_font() -> str:
    override = os.environ.get("OG_FONT")
    if override and os.path.isfile(override):
        return override
    for path in FONT_CANDIDATES:
        if os.path.isfile(path):
            return path
    raise FileNotFoundError("no CJK font (set OG_FONT)")


def _font(path: str, size: int):
    from PIL import ImageFont

    try:
        return ImageFont.truetype(path, size=size, index=0)
    except OSError:
        return ImageFont.truetype(path, size=size)


def _draw_pokeball(draw, cx: int, cy: int, r: int) -> None:
    """Same mark as favicon.svg: red top, black band, white button."""
    draw.ellipse((cx - r, cy - r, cx + r, cy + r), fill=(255, 255, 255), outline=INK, width=max(4, r // 16))
    # Red top hemisphere, clipped by overdrawing the lower half in white.
    draw.pieslice((cx - r + 3, cy - r + 3, cx + r - 3, cy + r - 3), 180, 360, fill=RED)
    band_h = max(8, r // 7)
    draw.rectangle((cx - r + 2, cy - band_h // 2, cx + r - 2, cy + band_h // 2), fill=INK)
    br = max(18, r // 4)
    draw.ellipse((cx - br, cy - br, cx + br, cy + br), fill=(255, 255, 255), outline=INK, width=max(4, r // 18))
    ir = max(8, r // 9)
    draw.ellipse((cx - ir, cy - ir, cx + ir, cy + ir), fill=(255, 255, 255), outline=INK, width=max(3, r // 28))


def render_card(card: dict) -> str:
    from PIL import Image, ImageDraw

    dest = os.path.join(ROOT, card["png"])
    markup = card_html(card)
    for word in card["forbidden"]:
        _assert(word not in markup, f"{card['key']}: card HTML still has {word!r}")

    font_path = find_font()
    title_f = _font(font_path, 62)
    sub_f = _font(font_path, 30)
    fine_f = _font(font_path, 22)
    badge_f = _font(font_path, 22)

    im = Image.new("RGB", (OG_W, OG_H), BG)
    draw = ImageDraw.Draw(im)
    draw.rectangle((0, 0, int(OG_W * 0.34), 12), fill=RED)
    draw.rectangle((int(OG_W * 0.34), 0, int(OG_W * 0.66), 12), fill=YELLOW)
    draw.rectangle((int(OG_W * 0.66), 0, OG_W, 12), fill=BLUE)

    ball_r = 98
    ball_cx, ball_cy = 196, OG_H // 2 + 6
    _draw_pokeball(draw, ball_cx, ball_cy, ball_r)

    text_x = 340
    title = card["title"]
    sub = card["sub"]
    fine = card["fine"]
    badge = card["badge"]
    # Vertically group copy around the ball centre.
    title_bbox = draw.textbbox((0, 0), title, font=title_f)
    sub_bbox = draw.textbbox((0, 0), sub, font=sub_f)
    fine_bbox = draw.textbbox((0, 0), fine, font=fine_f)
    badge_bbox = draw.textbbox((0, 0), badge, font=badge_f)
    title_h = title_bbox[3] - title_bbox[1]
    sub_h = sub_bbox[3] - sub_bbox[1]
    fine_h = fine_bbox[3] - fine_bbox[1]
    badge_h = badge_bbox[3] - badge_bbox[1] + 20
    gap1, gap2, gap3 = 16, 12, 28
    block_h = title_h + gap1 + sub_h + gap2 + fine_h + gap3 + badge_h
    y = ball_cy - block_h // 2

    draw.text((text_x, y), title, font=title_f, fill=INK)
    y += title_h + gap1
    draw.text((text_x, y), sub, font=sub_f, fill=MUTED)
    y += sub_h + gap2
    draw.text((text_x, y), fine, font=fine_f, fill=FINE)
    y += fine_h + gap3
    pad_x, pad_y = 26, 10
    bw = badge_bbox[2] - badge_bbox[0] + pad_x * 2
    bh = badge_h
    bx1, by1 = text_x, y
    draw.rounded_rectangle((bx1, by1, bx1 + bw, by1 + bh), radius=bh // 2, fill=YELLOW)
    draw.text((bx1 + pad_x, by1 + pad_y - 2), badge, font=badge_f, fill=INK)

    im.save(dest, format="PNG", optimize=True)
    w, h = png_size(dest)
    _assert((w, h) == (OG_W, OG_H), f"{card['key']}: wrote {w}x{h}")
    return dest


def _meta_content(page: str, prop: str) -> str:
    path = os.path.join(ROOT, page)
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    # property= then name= (og / twitter).
    needle_prop = f'property="{prop}" content="'
    needle_name = f'name="{prop}" content="'
    for needle in (needle_prop, needle_name):
        i = text.find(needle)
        if i >= 0:
            start = i + len(needle)
            end = text.find('"', start)
            _assert(end > start, f"{page}: empty {prop}")
            return text[start:end]
    raise AssertionError(f"{page}: missing {prop}")


def self_test() -> None:
    home_og = os.path.join(ROOT, HOME_OG)
    _assert(os.path.isfile(home_og), "home og.png missing")
    _assert(png_size(home_og) == (OG_W, OG_H), "home og.png size")

    for page in HOME_PAGES:
        _assert(_meta_content(page, "og:site_name") == HOME_SITE_NAME, page)
        image = _meta_content(page, "og:image")
        _assert(image.startswith(PAGES_PREFIX + HOME_OG), f"{page}: home og:image {image}")

    for card in CARDS:
        png_path = os.path.join(ROOT, card["png"])
        _assert(os.path.isfile(png_path), f"missing {card['png']}")
        _assert(png_size(png_path) == (OG_W, OG_H), f"{card['png']} size")
        markup = card_html(card)
        for word in card["forbidden"]:
            _assert(word not in markup, f"{card['key']} template has {word!r}")
            _assert(word not in card["title"], f"{card['key']} title has {word!r}")
            _assert(word not in card["sub"], f"{card['key']} sub has {word!r}")
        _assert(card["title"] in markup, card["key"])
        _assert(card["sub"] in markup, card["key"])
        for page in card["pages"]:
            _assert(_meta_content(page, "og:site_name") == card["site_name"], page)
            _assert(_meta_content(page, "og:title"), page)
            image = _meta_content(page, "og:image")
            tw = _meta_content(page, "twitter:image")
            expect = PAGES_PREFIX + card["png"]
            _assert(image.startswith(expect), f"{page}: og:image {image}")
            _assert(tw.startswith(expect), f"{page}: twitter:image {tw}")
            _assert(image.startswith("https://"), f"{page}: og:image must be absolute https")
            _assert(HOME_OG + "?" not in image and not image.endswith(HOME_OG), page)
            # WhatsApp ignores ?v= and keys cache on the path. A recache
            # must not point at the previously shared EV / bench filenames.
            image_path = image.split("?", 1)[0]
            _assert(not image_path.endswith("/og.png"), f"{page}: still home og.png")
            if card["key"] == "psa10-bench":
                _assert(image_path.endswith("/og-psa10-bench-v2.png"), f"{page}: need new bench path, got {image}")
                _assert("/og-psa10-bench.png" not in image_path, f"{page}: old bench path still cached by WhatsApp")
            site = _meta_content(page, "og:site_name")
            _assert(site != HOME_SITE_NAME, f"{page}: still home site_name")
            for word in card["forbidden"]:
                _assert(word not in site, f"{page} site_name has {word!r}")
                _assert(word not in _meta_content(page, "og:title"), f"{page} og:title has {word!r}")
            with open(os.path.join(ROOT, page), encoding="utf-8") as fh:
                page_text = fh.read()
            for particle in CANTONESE_PARTICLES:
                _assert(particle not in page_text, f"{page}: colloquial {particle!r}")

    print("render_og self-test ok")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--only", choices=[c["key"] for c in CARDS], action="append")
    args = ap.parse_args(argv)
    if args.self_test:
        self_test()
        return 0
    wanted = args.only or [c["key"] for c in CARDS]
    try:
        import PIL  # noqa: F401
    except ImportError:
        print("render_og: Pillow required to write PNGs (pip install pillow)", file=sys.stderr)
        return 2
    for card in CARDS:
        if card["key"] not in wanted:
            continue
        dest = render_card(card)
        print(f"wrote {os.path.relpath(dest, ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
