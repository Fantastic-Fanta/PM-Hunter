# Every icon, from windows/hunt-icon.png (Clodsire, drawn white on a black circle
# like the pill):
#   windows/hunt.ico                   Hunt.exe, the installer
#   window/icons/clodsire-192.png      the window's own (its opening)
#   windows/hunt-icon-app.png          the Mac app's: a rounded square, as macOS draws apps
#   android/.../mipmap-*/ic_launcher_* the phone's: Clodsire on black, room round it in any
#                                      launcher's shape (and its one-color themed version)
#   android/.../clodsire_mark.png      the phone app's title, the pill's opening
#   python windows/make_icon.py        (needs Pillow)
import os

from PIL import Image, ImageDraw

here = os.path.dirname(os.path.abspath(__file__))
root = os.path.join(here, "..")
res = os.path.join(root, "android", "app", "src", "main", "res")
icon = Image.open(os.path.join(here, "hunt-icon.png")).convert("RGBA")

icon.resize((192, 192), Image.LANCZOS).save(os.path.join(root, "window", "icons", "clodsire-192.png"))
icon.resize((256, 256), Image.LANCZOS).save(os.path.join(here, "hunt.ico"),
                                           sizes=[(s, s) for s in (16, 24, 32, 48, 64, 128, 256)])

# Clodsire alone, white (its own soft edge kept), none of the circle
w, h = icon.size
circle = Image.new("L", icon.size, 0)
ImageDraw.Draw(circle).ellipse((6, 6, w - 7, h - 7), fill=255)
alpha = icon.convert("L").point(lambda v: 0 if v < 40 else min(255, int((v - 40) * 255 / 160)))
white = Image.new("RGBA", icon.size, (255, 255, 255, 0))
white.putalpha(Image.composite(alpha, Image.new("L", icon.size, 0), circle))
body = white.crop(white.getbbox())

mark = body.copy()
mark.thumbnail((480, 480), Image.LANCZOS)
mark.save(os.path.join(res, "drawable-nodpi", "clodsire_mark.png"))

# the Mac's: an 824 px rounded square in 1024 (macOS's grid), Clodsire 72% of it across
S, tile = 1024, 824
off = (S - tile) // 2
app = Image.new("RGBA", (S, S), (0, 0, 0, 0))
square = Image.new("L", (S, S), 0)
ImageDraw.Draw(square).rounded_rectangle((off, off, off + tile, off + tile), radius=185, fill=255)
app.paste(Image.new("RGBA", (S, S), (0, 0, 0, 255)), (0, 0), square)
k = tile * 0.72 / body.width
b = body.resize((int(body.width * k), int(body.height * k)), Image.LANCZOS)
app.alpha_composite(b, ((S - b.width) // 2, (S - b.height) // 2 + 14))
app.save(os.path.join(here, "hunt-icon-app.png"))

# Android's adaptive icon: a 108 dp foreground (black background in colors.xml), Clodsire
# 44% across: launchers show the middle 72 dp, in their own shape
for density, n in {"mdpi": 108, "hdpi": 162, "xhdpi": 216, "xxhdpi": 324, "xxxhdpi": 432}.items():
    k = n * 0.44 / body.width
    b = body.resize((max(1, int(body.width * k)), max(1, int(body.height * k))), Image.LANCZOS)
    canvas = Image.new("RGBA", (n, n), (0, 0, 0, 0))
    canvas.alpha_composite(b, ((n - b.width) // 2, (n - b.height) // 2 + int(n * 0.015)))
    for name in ("ic_launcher_foreground", "ic_launcher_monochrome"):
        canvas.save(os.path.join(res, f"mipmap-{density}", f"{name}.png"))
print("icons written")
