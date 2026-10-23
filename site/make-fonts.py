#!/usr/bin/env python3
"""Gen 3's fonts (pokeemerald's glyph sheets and width tables) as web fonts
for the battle page: each glyph's letter pixels become squares on a 16 px
grid; the shadow comes from CSS.

Rebuilds site/app/fonts/gen3.woff2 and gen3-small.woff2. The inputs come from
the pokeemerald decomp (https://github.com/pret/pokeemerald): charmap.txt,
src/fonts.c, graphics/fonts/latin_normal.png and latin_small.png, put in one
folder. Needs pillow and fonttools (pip install pillow fonttools brotli).

    python site/make-fonts.py <folder with those files> site/app/fonts
"""
import re
import sys

from PIL import Image
from fontTools.fontBuilder import FontBuilder
from fontTools.pens.ttGlyphPen import TTGlyphPen

ref, out = sys.argv[1], sys.argv[2]
PX, CELL = 64, 16                       # units per pixel; a glyph cell is 16 x 16 px
ASC = 12                                # the baseline: under row 11

def widths(name):
    src = open(f"{ref}/fonts.c").read()
    body = src.split(f"gFont{name}LatinGlyphWidths[] = {{")[1].split("}")[0]
    return [int(v) for v in re.findall(r"\d+", body)]

def charmap():
    out = {}
    for line in open(f"{ref}/charmap.txt", encoding="utf-8"):
        m = re.match(r"^'(\\'|[^']+)'\s*=\s*([0-9A-F]{2})\s*$", line.strip())
        if m:
            ch = "'" if m.group(1) == "\\'" else m.group(1)
            if len(ch) == 1 and ch not in out:
                out[ch] = int(m.group(2), 16)
    out.setdefault('"', out["”"])
    out[""] = 0x34  # Lv
    out[""] = 0x53  # PK
    out[""] = 0x54  # MN
    return out

def build(sheet, wname, family, path):
    im = Image.open(f"{ref}/{sheet}.png"); px = im.load()
    W = widths(wname)
    cmap_chars = charmap()
    names, glyphs, metrics, cmap = [".notdef"], {}, {}, {}
    pen = TTGlyphPen(None); glyphs[".notdef"] = pen.glyph(); metrics[".notdef"] = (6 * PX, 0)
    for ch, code in sorted(cmap_chars.items(), key=lambda kv: kv[1]):
        cx, cy = code % 16 * CELL, code // 16 * CELL
        pen = TTGlyphPen(None)
        for y in range(CELL):
            x = 0
            while x < CELL:
                if px[cx + x, cy + y] != 1:  # 1: the letter; 2 its shadow, 3 its background
                    x += 1; continue
                x2 = x
                while x2 < CELL and px[cx + x2, cy + y] == 1: x2 += 1
                top, bot = (ASC - y) * PX, (ASC - y - 1) * PX
                pen.moveTo((x * PX, bot)); pen.lineTo((x * PX, top)); pen.lineTo((x2 * PX, top)); pen.lineTo((x2 * PX, bot)); pen.closePath()
                x = x2
        name = f"g{code:02X}"
        if name not in glyphs:
            names.append(name); glyphs[name] = pen.glyph(); metrics[name] = (W[code] * PX, 0)
        cmap[ord(ch)] = name
    fb = FontBuilder(CELL * PX, isTTF=True)
    fb.setupGlyphOrder(names)
    fb.setupCharacterMap(cmap)
    fb.setupGlyf(glyphs)
    fb.setupHorizontalMetrics(metrics)
    fb.setupHorizontalHeader(ascent=ASC * PX, descent=-(CELL - ASC) * PX)
    fb.setupNameTable({"familyName": family, "styleName": "Regular"})
    fb.setupOS2(version=4, sTypoAscender=ASC * PX, sTypoDescender=-(CELL - ASC) * PX, sTypoLineGap=0,
                usWinAscent=ASC * PX, usWinDescent=(CELL - ASC) * PX, fsSelection=0x40 | 0x80)
    fb.setupPost()
    fb.font.flavor = "woff2"
    fb.save(path)
    print(path, len(names), "glyphs")

build("latin_normal", "Normal", "Gen3", f"{out}/gen3.woff2")
build("latin_small", "Small", "Gen3 Small", f"{out}/gen3-small.woff2")
