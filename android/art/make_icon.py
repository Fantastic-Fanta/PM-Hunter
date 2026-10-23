"""Builds the launcher icon from icon-source.jpg (a photo on white): cuts the
plush out, centers it on black, and writes the adaptive icon's foreground and
its one-color version (Android 13+ themed icons) for every screen density.

  python make_icon.py    (from this folder; needs pillow, numpy, scipy)
"""
import os
import sys

import numpy as np
from PIL import Image, ImageFilter
from scipy import ndimage

HERE = os.path.dirname(os.path.abspath(__file__))
RES = os.path.join(HERE, "..", "app", "src", "main", "res")
DENSITIES = {"mdpi": 108, "hdpi": 162, "xhdpi": 216, "xxhdpi": 324, "xxxhdpi": 432}
WIDTH = 0.60  # of the 108dp canvas; Android masks it to the middle 72dp

img = Image.open(os.path.join(HERE, "icon-source.jpg")).convert("RGB")
rgb = np.asarray(img).astype(int)
# the white backdrop: near-white pixels connected to the edge (the plush's
# white eye spots are inside it, so they stay)
light = (rgb.min(axis=2) > 225) & (rgb.max(axis=2) - rgb.min(axis=2) < 18)
labels, _ = ndimage.label(light)
edge = set(np.unique(np.r_[labels[0], labels[-1], labels[:, 0], labels[:, -1]])) - {0}
backdrop = np.isin(labels, list(edge))
plush = ndimage.binary_fill_holes(ndimage.binary_opening(~backdrop, iterations=2))
# keep the biggest piece (drops specks), soften the edge a little
labels, n = ndimage.label(plush)
sizes = ndimage.sum(plush, labels, range(1, n + 1))
plush = labels == (int(np.argmax(sizes)) + 1)
plush = ndimage.binary_erosion(plush, iterations=3)  # no white fringe from the backdrop
alpha = Image.fromarray((plush * 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(1.2))
cut = img.convert("RGBA")
cut.putalpha(alpha)
cut = cut.crop(cut.getbbox())

for name, size in DENSITIES.items():
    w = int(size * WIDTH)
    h = int(cut.height * w / cut.width)
    fg = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    fg.paste(cut.resize((w, h), Image.LANCZOS), ((size - w) // 2, (size - h) // 2 + size // 40))
    out = os.path.join(RES, f"mipmap-{name}")
    os.makedirs(out, exist_ok=True)
    fg.save(os.path.join(out, "ic_launcher_foreground.png"))
    mono = Image.new("RGBA", fg.size, (255, 255, 255, 0))
    mono.putalpha(fg.getchannel("A"))
    mono.save(os.path.join(out, "ic_launcher_monochrome.png"))
print("written to", os.path.normpath(RES), file=sys.stderr)
