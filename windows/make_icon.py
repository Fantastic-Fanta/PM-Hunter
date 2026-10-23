# Hunt's icon: Clodsire in three tones (white, grey, its own lines cut out) on
# a black circle like the pill. From PokeAPI's copy of the official artwork (980.png, not
# kept here):
#   curl -sSfLO https://raw.githubusercontent.com/PokeAPI/sprites/master/sprites/pokemon/other/official-artwork/980.png
#   python windows/make_icon.py 980.png
# Writes windows/hunt.ico (Hunt.exe, the installer) and
# window/icons/clodsire-192.png (the window's own).
import os
import sys

import numpy as np
from PIL import Image, ImageDraw

N = 1024
art = Image.open(sys.argv[1]).convert("RGBA")
a = np.asarray(art).astype(int)
shape = a[..., 3] > 128
lum = a[..., :3].sum(axis=2)
# three tones: its light face and belly white, its darker back grey, its own lines (the eye,
# the mouth, the contours) cut out
tone = np.zeros(shape.shape + (4,), np.uint8)
tone[shape & (lum >= 120)] = (150, 150, 150, 255)
tone[shape & (lum >= 340)] = (255, 255, 255, 255)
ys, xs = np.nonzero(shape)
box = (xs.min(), ys.min(), xs.max() + 1, ys.max() + 1)
figure = Image.fromarray(tone).crop(box)
scale = N * 0.8 / figure.width
figure = figure.resize((round(figure.width * scale), round(figure.height * scale)), Image.LANCZOS)

icon = Image.new("RGBA", (N, N), (0, 0, 0, 0))
ImageDraw.Draw(icon).ellipse([4, 4, N - 4, N - 4], fill=(17, 17, 17, 255))
icon.alpha_composite(figure, ((N - figure.width) // 2, (N - figure.height) // 2 + N // 40))

here = os.path.dirname(os.path.abspath(__file__))
icon.resize((192, 192), Image.LANCZOS).save(os.path.join(here, "..", "window", "icons", "clodsire-192.png"))
icon.resize((256, 256), Image.LANCZOS).save(os.path.join(here, "hunt.ico"),
                                           sizes=[(s, s) for s in (16, 24, 32, 48, 64, 128, 256)])
if len(sys.argv) > 2:  # a look at it, big and small
    sheet = Image.new("RGBA", (16 + sum(s + 16 for s in (256, 64, 32, 16)), 288), (120, 138, 150, 255))
    x = 16
    for s in (256, 64, 32, 16):
        sheet.alpha_composite(icon.resize((s, s), Image.LANCZOS), (x, 16))
        x += s + 16
    sheet.save(sys.argv[2])
