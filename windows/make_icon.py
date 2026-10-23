# Hunt's icon: an original Clodsire silhouette, side on (the long low body,
# the spikes along its back, a wide mouth), white on a black circle like the
# pill. Writes windows/hunt.ico (Hunt.exe, the installer) and
# window/icons/clodsire-192.png (the window's own):
#   python windows/make_icon.py
import os
from PIL import Image, ImageDraw

N = 1024 * 4  # drawn big, then shrunk smooth
def P(x, y): return (x * 4, y * 4)

def bez(p0, p1, p2, p3, n=60):
    out = []
    for i in range(n + 1):
        t = i / n
        a, b, c, d = (1 - t) ** 3, 3 * t * (1 - t) ** 2, 3 * t * t * (1 - t), t ** 3
        out.append((a * p0[0] + b * p1[0] + c * p2[0] + d * p3[0], a * p0[1] + b * p1[1] + c * p2[1] + d * p3[1]))
    return out

def shape(*curves):
    pts = []
    for c in curves:
        pts += bez(*c)
    return [P(*p) for p in pts]

def mirror(pts): return [(N - x, y) for x, y in pts]

img = Image.new("RGBA", (N, N), (0, 0, 0, 0))
d = ImageDraw.Draw(img)
d.ellipse([P(12, 12), P(1012, 1012)], fill=(17, 17, 17, 255))
white = (255, 255, 255, 255)

# side on, facing left. Spikes along the back first, the body over their bases
dark = (17, 17, 17, 255)
for base0, apex, base1 in (((470, 430), (590, 150), (640, 450)),   # the big one, swept back
                           ((610, 450), (735, 285), (735, 520)),
                           ((715, 505), (842, 420), (800, 600))):
    pts = shape((base0, (base0[0] + 40, base0[1] - 150), (apex[0] - 40, apex[1] + 40), apex),
                (apex, (apex[0] + 10, apex[1] + 120), (base1[0] + 10, base1[1] - 80), base1))
    d.polygon(pts, fill=white)

# the body: a long, low dome, high at the front, down to the tail
body = shape(((150, 700), (130, 520), (260, 410), (440, 405)),
             ((440, 405), (640, 400), (800, 520), (880, 660)),
             ((880, 660), (920, 700), (935, 745), (905, 760)),   # the tail's tip
             ((905, 760), (760, 790), (400, 800), (220, 790)),
             ((220, 790), (170, 785), (152, 750), (150, 700)))
d.polygon(body, fill=white)

# stubby legs
for x in (240, 600):
    d.rounded_rectangle([P(x, 760), P(x + 120, 845)], radius=4 * 36, fill=white)

# a small eye near the top of the face, a wide mouth
d.ellipse([P(258, 488), P(304, 534)], fill=dark)
mouth = shape(((170, 610), (230, 640), (320, 645), (390, 625)))
d.line(mouth, fill=dark, width=4 * 13, joint="curve")

# keep it inside the circle
mask = Image.new("L", (N, N), 0)
ImageDraw.Draw(mask).ellipse([P(12, 12), P(1012, 1012)], fill=255)
img.putalpha(Image.composite(img.getchannel("A"), mask, mask))

here = os.path.dirname(os.path.abspath(__file__))
big = img.resize((1024, 1024), Image.LANCZOS)
big.resize((192, 192), Image.LANCZOS).save(os.path.join(here, "..", "window", "icons", "clodsire-192.png"))
big.resize((256, 256), Image.LANCZOS).save(os.path.join(here, "hunt.ico"),
                                          sizes=[(s, s) for s in (16, 24, 32, 48, 64, 128, 256)])
