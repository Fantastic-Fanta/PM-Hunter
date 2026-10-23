#!/usr/bin/env python3
"""pokehunt: hunts wild encounters in the Roblox Pokemon game for wanted ones.

Each encounter (the red Mute button is up) is read from the enemy's box:
  species   the name                          Quaxly
  shiny     dark blue star in the box
  tint      yellow sparkle in the box
  reskin    text under the HP bar             INFERNO
  gradient  tab hanging under the box         Mirage
Not wanted: it clicks Run. Wanted: a sound and a Discord ping, then it clicks
an empty spot every few seconds (so Roblox doesn't kick you for idling) until
you move the mouse or press a key. Then it pauses so you can catch it.

What's wanted and the webhook are in config.json. Every encounter is logged
to encounters.csv, wanted ones are saved to finds/.

Runs on macOS and Windows (devices.py). The layout is fitted to each screen
size at its first encounter and again whenever the battle can't be read, so
a differently shaped Roblox window works too (calibration.json).

  python pokehunt.py                 # hunt (switches to Roblox first)
  python pokehunt.py --watch         # read and log encounters, never click
  python pokehunt.py --image x.png   # read a screenshot and print what it sees
  python pokehunt.py --calibrate     # fit the layout at the next encounter and
                                     # draw it on debug/calibration.png to check
  python pokehunt.py --test-discord  # send a test message to the webhook
  python pokehunt.py --debug         # catch every encounter, then save: for testing
  python pokehunt.py --diagnose      # check reading, screen and mouse on a new PC,
                                     # without clicking anything

  Ctrl+Shift+H   pause / resume (also: resume after you caught one)
Stop: Ctrl-C, or fling the mouse into a screen corner.
"""
import argparse
import base64
import csv
import difflib
import io
import json
import os
import queue
import re
import sys
import threading
import time
import urllib.request
import uuid

import debuglog
if __name__ == "__main__":
    # first thing: from here on everything printed, and any crash (a missing
    # package below, say), also goes to the debug webhook (Windows runs)
    debuglog.debug.start(debuglog.URL)

import numpy as np
from PIL import Image

import devices
import remote
from devices import ocr, sound

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.environ.get("HUNT_DATA") or HERE  # what a run keeps: Hunt.exe's are in %LOCALAPPDATA%\Hunt
DEBUG_DIR = os.path.join(DATA, "debug")
FINDS_DIR = os.path.join(DATA, "finds")
LOG = os.path.join(DATA, "encounters.csv")
CAL_FILE = os.path.join(DATA, "calibration.json")
KEY_FILE = os.path.join(DATA, "website.key")  # this computer's secret for poopihunt.xyz
try:
    from build_info import NAME as BUILD  # Hunt.exe's build, written by the Windows build
except ImportError:
    BUILD = time.strftime("%d %b %H:%M", time.localtime(os.path.getmtime(__file__)))
WINDOW = False  # run by Hunt's window (app.py), --window
dev = devices.Desktop()
FIELDS = ("species", "shiny", "tint", "reskin", "gradient")

# ---------------------------------------------------------------- layout
# Measured on samples/*.png (a 2000 px wide Roblox window). Everything is
# placed relative to the Mute button, so other window sizes scale along;
# windows that stretch the game differently are calibrated, below.

REF_MUTE = (1533, 153, 1749, 218)   # left, top, right, bottom
BOX = (370, 317, 890, 535)          # enemy box plus the gradient tab under it
HP_BAR_END = 435                    # below this, inside the box: the reskin
BOX_END = 487                       # below this: the gradient tab
ICONS = (395, 425, 525, 490)        # shiny star / tint sparkle
OWNED_ICON = (495, 395, 565, 445)   # Poke Ball left of the HP bar: species caught before
PANEL = (300, 140, 1750, 1220)      # the whole game, for screenshots
FOE_SPRITE = (1170, 285)            # top left of the wild Pokemon's 96 x 96 sprite (Gen 5),
SPRITE_PIXEL = 4.165                # drawn this many reference pixels per sprite pixel
BUTTONS = {                         # area, usual color
    "fight": ((305, 1025, 655, 1115), (210, 219, 189)),
    "switch": ((668, 1025, 1020, 1115), (145, 170, 157)),
    "bag": ((1030, 1025, 1383, 1115), (61, 92, 111)),
    "run": ((1393, 1025, 1745, 1115), (26, 51, 66)),
}
RUN = (1568, 1070)
BAG = (1206, 1070)
FIGHT = (480, 1070)
MSG = (300, 885, 1750, 1020)          # battle text ("What should Clodsire do?")
PLAYER_BOX = (1168, 685, 1680, 870)   # your Pokemon's box, HP "350 / 376"
BAG_SPLIT = 1100         # bag rows' names are left of this, the search box right
STATUSES = ("SLP", "PSN", "PAR", "BRN", "FRZ", "TOX")
TURN_TIMEOUT = 60        # s for one turn: a throw with its shakes, or a move
AFK_SPOT = (150, 800)    # black margin left of the game, below the chat window
CHAT_MARGIN = (80, 140, 270, 560)  # black margin where chat lines start (the
                                   # game's blue side tabs end left of it)
CHAT_BUTTON = (185, 82)  # Roblox's chat bubble, top left, if find_chat_button can't see it
IDLE_SPOT = (1550, 960)  # empty right end of the "What should ... do?" box


class Layout:
    """Maps reference coordinates onto the current screenshot."""

    def __init__(self, mute, fit=None):
        self.mute = mute
        self.s = (mute[2] - mute[0]) / (REF_MUTE[2] - REF_MUTE[0])
        self.fit = fit or cal["fit"]

    def pt(self, x, y):
        kx, ky, dx, dy = self.fit
        return (self.mute[0] + ((x - REF_MUTE[0]) * kx + dx) * self.s,
                self.mute[1] + ((y - REF_MUTE[1]) * ky + dy) * self.s)

    def box(self, b):
        x0, y0 = self.pt(b[0], b[1])
        x1, y1 = self.pt(b[2], b[3])
        return max(int(x0), 0), max(int(y0), 0), int(x1), int(y1)

    def patch(self, rgb, b):
        x0, y0, x1, y1 = self.box(b)
        return rgb[y0:y1, x0:x1]

# ---------------------------------------------------------------- vision

def load_rgb(img):
    return np.asarray(img.convert("RGB")).astype(np.int16)


def blobs(mask):
    """The blobs of a True/False mask (pixels touching sideways or up and
    down, like scipy's label): [(row slice, column slice, pixels)], in the
    order a row by row scan meets them. Rows are cut into runs of True, and
    runs that overlap in neighboring rows are joined."""
    h, w = mask.shape
    padded = np.zeros((h, w + 2), np.int8)
    padded[:, 1:-1] = mask
    step = np.diff(padded, axis=1)
    row, start = np.nonzero(step == 1)
    end = np.nonzero(step == -1)[1]
    n = len(row)
    if not n:
        return []
    # the runs of the row above that overlap each run: a stretch [lo, hi)
    above = (row - 1) * (w + 1)
    lo = np.searchsorted(row * (w + 1) + end, above + start, "right")
    hi = np.searchsorted(row * (w + 1) + start, above + end, "left")
    count = np.clip(hi - lo, 0, None)
    a = np.repeat(np.arange(n), count)
    b = np.repeat(lo, count) + np.arange(count.sum()) - np.repeat(np.cumsum(count) - count, count)
    # each run points at its blob's first run: joined until every pair agrees
    first = np.arange(n)
    while True:
        while True:
            up = first[first]
            if (up == first).all():
                break
            first = up
        if (first[a] == first[b]).all():
            break
        lower = np.minimum(first[a], first[b])
        np.minimum.at(first, first[a], lower)
        np.minimum.at(first, first[b], lower)
    _, blob = np.unique(first, return_inverse=True)
    k = blob.max() + 1
    y0, x0 = np.full(k, h), np.full(k, w)
    y1, x1 = np.zeros(k, int), np.zeros(k, int)
    np.minimum.at(y0, blob, row)
    np.maximum.at(y1, blob, row + 1)
    np.minimum.at(x0, blob, start)
    np.maximum.at(x1, blob, end)
    size = np.bincount(blob, end - start)
    return [(slice(int(y0[i]), int(y1[i])), slice(int(x0[i]), int(x1[i])), int(size[i])) for i in range(k)]


def red_mask(rgb):
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    return (r > 150) & (g < 50) & (b < 50)


def is_mute(rgb, box):
    """Red button with white text, 3.3x as wide as tall."""
    x0, y0, x1, y1 = box
    w, h = x1 - x0, y1 - y0
    if h < 12 or not 2.6 < w / h < 4.2:
        return False
    p = rgb[y0:y1, x0:x1]
    if p.size == 0:
        return False
    white = (p.min(axis=2) > 200).mean()
    return red_mask(p).mean() > 0.6 and 0.05 < white < 0.4


def find_mute(rgb, hint=None):
    """(left, top, right, bottom) of the Mute button, or None. Checks the last
    known spot first, which is much cheaper than searching the whole screen."""
    if hint and is_mute(rgb, hint):
        return hint
    for sl in blobs(red_mask(rgb)):
        box = (sl[1].start, sl[0].start, sl[1].stop, sl[0].stop)
        if is_mute(rgb, box):
            return box
    return None


def menu_ready(rgb, lay, seen=None):
    """Fight / Switch / Bag / Run are showing (one may be lit up by the mouse),
    in their usual colors or the ones calibration saw."""
    seen = seen or cal
    ok = 0
    for name, (area, color) in BUTTONS.items():
        p = lay.patch(rgb, area).reshape(-1, 3)
        if len(p) and any(np.abs(np.median(p, axis=0) - c).max() < 30
                          for c in (color, seen.get(name, color))):
            ok += 1
    return ok >= 3


def box_visible(rgb, lay):
    """The enemy's gray box isn't covered (by the chat window, say): every
    quarter of it is mostly its gray."""
    p = lay.patch(rgb, (BOX[0], BOX[1], 882, BOX_END))
    gray = (np.abs(p - cal.get("box", BOX_GRAY)) < 18).all(axis=2)
    return all(q.mean() > 0.5 for q in np.array_split(gray, 4, axis=1))


def chat_showing(rgb, lay):
    """Chat messages are on screen. They float over the enemy's box and get
    read as its name, so nothing is read while they show. Every chat line
    starts in the black margin left of the game."""
    p = lay.patch(rgb, CHAT_MARGIN)
    if p.size == 0:
        return False
    # On a wide screen (a phone) Roblox's own menu and chat buttons reach into
    # the top of this strip, dark gray pills: start below them.
    mx, mn = p.max(axis=2), p.min(axis=2)
    pill = np.nonzero(((mx - mn < 12) & (mx >= 15) & (mx <= 60)).mean(axis=1)[:len(p) // 3] > 0.15)[0]
    if len(pill):
        p = p[pill[-1] + 3:]
    return p.size > 0 and (p.max(axis=2) > 90).mean() > 0.003


def find_chat_button(rgb):
    """Roblox's chat button: the right end of the dark gray pill (menu, chat)
    in the top-left corner, or None. Roblox draws it by its window, not by the
    game, so it isn't where the layout would put it on every screen shape (on
    Windows the click went right of it). As Vision.chatButton on Android."""
    h, w = rgb.shape[:2]
    p = rgb[:int(h * 0.15), :int(w * 0.3)]
    mx, mn = p.max(axis=2), p.min(axis=2)
    pills = []
    for rows, cols, _ in blobs((mx - mn < 12) & (mx >= 15) & (mx <= 60)):
        bh, bw = rows.stop - rows.start, cols.stop - cols.start
        if (h * 0.03 < bh < h * 0.12 and 1.5 <= bw / bh <= 4.0 and
                cols.start < w * 0.18 and cols.stop < w * 0.29):
            pills.append((rows.start, rows, cols))
    if not pills:
        return None
    _, rows, cols = min(pills, key=lambda b: b[0])  # the topmost: Roblox's own bar, not the chat window
    return cols.stop - 0.6 * (rows.stop - rows.start), (rows.start + rows.stop) / 2


def clean_name(text):
    text = re.split(r"\s+l[vy]\b", text, flags=re.I)[0]   # "Marill Lv. 52"
    words = re.sub(r"[^A-Za-z0-9 .'\-:]", " ", text).split()
    while len(words) > 1 and len(words[-1]) == 1:  # misread gender sign
        words.pop()
    return " ".join(words).strip(" .")


def read_enemy(img, rgb, lay):
    """The enemy's species, level, shiny, tint, reskin and gradient."""
    x0, y0, x1, y1 = lay.box(BOX)
    crop = img.crop((x0, y0, x1, y1))
    if lay.s < 1:  # small window: blow the text up for the OCR
        crop = crop.resize((int(crop.width / lay.s), int(crop.height / lay.s)), Image.LANCZOS)
    hp = (HP_BAR_END - BOX[1]) / (BOX[3] - BOX[1])
    end = (BOX_END - BOX[1]) / (BOX[3] - BOX[1])
    enc = {"species": "", "level": "", "reskin": "", "gradient": "", "status": ""}
    for text, cx, cy in sorted(ocr(crop), key=lambda t: t[1]):
        words = text.split()
        badge = [w.upper() for w in words if w.upper() in STATUSES]
        if badge:  # the SLP / PSN ... badge left of the HP bar
            enc["status"] = badge[0]
            text = " ".join(w for w in words if w.upper() not in STATUSES)
            if not text:
                continue
        level = re.match(r"\s*l[vy][.,:]?\s*(\d+)", text, re.I)
        if level:
            enc["level"] = level.group(1)
        elif cy < hp and cx < 0.6:
            enc["species"] = (enc["species"] + " " + clean_name(text)).strip()
        elif not re.search("[A-Za-z]{2}", text):
            continue  # an icon read as text ("*" for the shiny star)
        elif hp <= cy < end:
            enc["reskin"] = (enc["reskin"] + " " + text.strip()).strip()
        elif cy >= end:
            enc["gradient"] = (enc["gradient"] + " " + text.strip()).strip()

    if devices.WINDOWS:  # its OCR misses these two in the whole box: each is read on its own
        for field, row, ways in (("reskin", RESKIN_ROW, ("x2", "far")), ("gradient", GRADIENT_TAB, ("sat", "far"))):
            enc[field] = row_text(img, lay, row, ways) or enc[field]
    # the gender sign can come as a text of its own ("Sentret" "O"): cleaned once more, joined
    enc["species"] = clean_name(enc["species"])
    p = lay.patch(rgb, ICONS)
    r, g, b = p[..., 0], p[..., 1], p[..., 2]
    enc["shiny"] = bool(((b > 100) & (r < 60) & (g < 60)).mean() > 0.008)
    enc["tint"] = bool(((r > 220) & (g > 200) & (b < 160)).mean() > 0.008)
    p = lay.patch(rgb, OWNED_ICON)  # little Poke Ball: you've caught this species
    r, g, b = p[..., 0], p[..., 1], p[..., 2]
    enc["owned"] = bool(((r > 170) & (g < 90) & (b < 90)).mean() > 0.01)
    return enc


RESKIN_ROW = (BOX[0] + 40, HP_BAR_END, BOX[2] - 20, BOX_END)    # INFERNO, under the HP bar
GRADIENT_TAB = (BOX[0] + 60, BOX_END, BOX[2] - 60, BOX[3])      # Mirage, in many colors


def row_text(img, lay, ref, ways):
    """Windows' OCR on one row of the enemy's box, cut out and prepared each
    way in turn (tried on the samples on Windows): the longest reading, since
    the colored letters come back partly ("Mira" for Mirage).
      x2   twice the size
      far  what isn't the row's background, black on white, 3x
      sat  what's strongly colored, black on white, 3x"""
    crop = img.crop(lay.box(ref)).convert("RGB")
    if lay.s < 1:
        crop = crop.resize((int(crop.width / lay.s), int(crop.height / lay.s)), Image.LANCZOS)
    a = np.asarray(crop).astype(np.int16)
    bg = np.median(a.reshape(-1, 3), axis=0)
    best = ""
    for way in ways:
        if way == "x2":
            prep, k, fill = crop, 2, tuple(int(c) for c in bg)
        else:
            ink = (np.abs(a - bg).max(axis=2) > 40 if way == "far" else a.max(axis=2) - a.min(axis=2) > 50)
            prep, k, fill = Image.fromarray(np.where(ink, 0, 255).astype(np.uint8)).convert("RGB"), 3, (255, 255, 255)
        prep = prep.resize((prep.width * k, prep.height * k), Image.LANCZOS)
        framed = Image.new("RGB", (prep.width + 40, prep.height + 40), fill)
        framed.paste(prep, (20, 20))
        text = " ".join(t.strip() for t, _, _ in ocr(framed)).strip(" .")
        if re.search("[A-Za-z]{2}", text) and len(text) > len(best):
            best = text
    return best


def describe(enc):
    words = [w for w in ("shiny" if enc["shiny"] else "", enc["species"] or "?",
                         f"Lv{enc['level']}" if enc["level"] else "") if w]
    extras = [f"reskin {enc['reskin']}" if enc["reskin"] else "",
              f"gradient {enc['gradient']}" if enc["gradient"] else "",
              "tint" if enc["tint"] else ""]
    extras = [e for e in extras if e]
    return " ".join(words) + (f"  ({', '.join(extras)})" if extras else "")

# ---------------------------------------------------------------- calibration
# Another window shape can stretch the game differently across than down, and
# Windows shows colors a little differently than a Mac. So the Fight/Switch/
# Bag/Run buttons and the enemy's box are found on screen and the layout is
# fitted to them, once per screen size, and again when the battle can't be read.

ANCHORS = {  # where the samples have them, found the way find_block does
    "fight": (300, 1020, 661, 1120), "switch": (663, 1020, 1024, 1119),
    "bag": (1025, 1020, 1388, 1119), "run": (1389, 1020, 1750, 1120),
    "box": (373, 319, 880, 485),
}
BOX_GRAY = (143, 143, 143)
FLAT = (1.0, 1.0, 0.0, 0.0)  # kx, ky, dx, dy: the reference layout, just scaled
cal = {"fit": FLAT}          # in use: "fit", the colors seen, "key", "saved"


def find_block(rgb, lay, ref, color):
    """Screen box of the patch of color around where lay puts ref, if it's
    about the expected size. The search reaches far: the game may be
    stretched a lot more down than across."""
    cx, cy = (ref[0] + ref[2]) / 2, (ref[1] + ref[3]) / 2
    pad_x = (ref[2] - ref[0]) / 2 + 0.3 * abs(cx - REF_MUTE[0])
    pad_y = (ref[3] - ref[1]) / 2 + 0.3 * abs(cy - REF_MUTE[1])
    x0, y0, x1, y1 = lay.box((ref[0] - pad_x, ref[1] - pad_y, ref[2] + pad_x, ref[3] + pad_y))
    p = rgb[y0:y1, x0:x1]
    if p.size == 0:
        return None
    for tol in (20, 35):
        mask = (np.abs(p - color) < tol).all(axis=2)
        found = blobs(mask)
        if not found:
            continue
        sizes = np.array([size for _, _, size in found])
        sl = found[int(np.argmax(sizes))]
        h, w = sl[0].stop - sl[0].start, sl[1].stop - sl[1].start
        if (0.6 < w / ((ref[2] - ref[0]) * lay.s) < 1.6 and 0.6 < h / ((ref[3] - ref[1]) * lay.s) < 1.6
                and sizes.max() > 0.5 * w * h):
            return x0 + sl[1].start, y0 + sl[0].start, x0 + sl[1].stop, y0 + sl[0].stop
    return None


def calibrate(rgb, mute):
    """Fits the layout to the buttons and the enemy's box on this screen.
    Returns (calibration, worst miss in px), or None if too little of the
    battle screen was found (the menu isn't up, say)."""
    flat = Layout(mute, FLAT)
    refs, got, new = [], [], {}
    for name, ref in ANCHORS.items():
        color = BOX_GRAY if name == "box" else BUTTONS[name][1]
        b = find_block(rgb, flat, ref, color)
        if b:
            refs.append(ref)
            got.append(b)
            p = rgb[b[1]:b[3], b[0]:b[2]].reshape(-1, 3)
            p = p[(np.abs(p - color) < 35).all(axis=1)]
            new[name] = [int(v) for v in np.median(p, axis=0)] if len(p) else list(color)
    if len(refs) - ("box" in new) < 2:
        return None
    refs.append(REF_MUTE)
    got.append(mute)
    ref, got, s = np.array(refs, float), np.array(got, float), flat.s
    rx = np.r_[ref[:, 0], ref[:, 2]] - REF_MUTE[0]
    gx = (np.r_[got[:, 0], got[:, 2]] - mute[0]) / s
    ry = np.r_[ref[:, 1], ref[:, 3]] - REF_MUTE[1]
    gy = (np.r_[got[:, 1], got[:, 3]] - mute[1]) / s
    kx, dx = np.polyfit(rx, gx, 1)
    ky, dy = np.polyfit(ry, gy, 1)
    miss = max(np.abs(kx * rx + dx - gx).max(), np.abs(ky * ry + dy - gy).max())
    if not (0.6 < kx < 1.6 and 0.6 < ky < 1.6) or miss > 12:  # the anchors don't agree on a layout
        return None
    new["fit"] = [float(kx), float(ky), float(dx), float(dy)]
    return (new, miss * s) if menu_ready(rgb, Layout(mute, new["fit"]), new) else None


def use_calibration(size):
    """The saved calibration for this screen size, or the reference layout."""
    key = "%dx%d" % size
    if cal.get("key") == key:
        return
    try:
        with open(CAL_FILE) as f:
            saved = json.load(f).get(key)
    except (OSError, ValueError):
        saved = None
    cal.clear()
    cal.update(saved or {"fit": FLAT})
    cal.update(key=key, saved=bool(saved))


def auto_calibrate(img, rgb, mute, draw=False):
    """Fits and saves the layout for this screen. True if it worked."""
    got = calibrate(rgb, mute)
    if not got:
        return False
    new, miss = got
    changed = not cal.get("saved") or np.abs(np.subtract(new["fit"], cal["fit"])).max() > 0.003
    try:
        with open(CAL_FILE) as f:
            all_cals = json.load(f)
    except (OSError, ValueError):
        all_cals = {}
    all_cals[cal["key"]] = new
    with open(CAL_FILE, "w") as f:  # a screen size per line
        f.write("{\n" + ",\n".join(f" {json.dumps(k)}: {json.dumps(v)}" for k, v in all_cals.items()) + "\n}\n")
    key = cal["key"]
    cal.clear()
    cal.update(new, key=key, saved=True)
    if changed or draw:
        say("  calibrated for this %s screen: %.3f across, %.3f down, within %.0f px"
            % (key, new["fit"][0], new["fit"][1], miss))
        debuglog.debug.shot(f"calibrated for {key}: the boxes should sit on the game's", draw_layout(img, Layout(mute)))
    if draw:
        os.makedirs(DEBUG_DIR, exist_ok=True)
        path = os.path.join(DEBUG_DIR, "calibration.png")
        draw_layout(img, Layout(mute)).save(path)
        say(f"  the layout is drawn on {path}: the boxes should sit on the game's")
    return True


def draw_layout(img, lay):
    """The screenshot with every area pokehunt reads or clicks drawn on it."""
    from PIL import ImageDraw
    out = img.copy()
    d = ImageDraw.Draw(out)
    for b in (BOX, ICONS, MSG, PLAYER_BOX, CHAT_MARGIN, PANEL, *(a for a, _ in BUTTONS.values())):
        d.rectangle(lay.box(b), outline=(255, 0, 255), width=3)
    for y in (HP_BAR_END, BOX_END):
        d.line((*lay.pt(BOX[0], y), *lay.pt(BOX[2], y)), fill=(255, 0, 255), width=3)
    chat = find_chat_button(load_rgb(img)) or lay.pt(*CHAT_BUTTON)
    for x, y in [lay.pt(*p) for p in (RUN, BAG, FIGHT, IDLE_SPOT, AFK_SPOT)] + [chat]:
        d.ellipse((x - 10, y - 10, x + 10, y + 10), outline=(0, 255, 0), width=3)
    return out

# ---------------------------------------------------------------- targets

def norm(s):
    return re.sub(r"[^a-z0-9]", "", str(s or "").lower())


def same_word(want, got):
    """Equal, forgiving one misread letter in longer words."""
    a, b = norm(want).replace("0", "o"), norm(got).replace("0", "o")
    return a == b or (len(a) >= 5 and difflib.SequenceMatcher(None, a, b).ratio() >= 0.8)


LEGENDARIES = """
Articuno Zapdos Moltres Mewtwo Mew Raikou Entei Suicune Lugia Ho-Oh Celebi
Regirock Regice Registeel Latias Latios Kyogre Groudon Rayquaza Jirachi Deoxys
Uxie Mesprit Azelf Dialga Palkia Heatran Regigigas Giratina Cresselia Phione
Manaphy Darkrai Shaymin Arceus Victini Cobalion Terrakion Virizion Tornadus
Thundurus Reshiram Zekrom Landorus Kyurem Keldeo Meloetta Genesect Xerneas
Yveltal Zygarde Diancie Hoopa Volcanion Type:Null Silvally Tapu-Koko Tapu-Lele
Tapu-Bulu Tapu-Fini Cosmog Cosmoem Solgaleo Lunala Necrozma Magearna Marshadow
Zeraora Meltan Melmetal Zacian Zamazenta Eternatus Kubfu Urshifu Zarude
Regieleki Regidrago Glastrier Spectrier Calyrex Enamorus Wo-Chien Chien-Pao
Ting-Lu Chi-Yu Koraidon Miraidon Okidogi Munkidori Fezandipiti Ogerpon
Terapagos Pecharunt
""".split()
REGIONS = r"^(alolan|galarian|hisuian|paldean)\W*"


def field_ok(want, got):
    if isinstance(want, bool):
        return want == bool(got)
    if want == "any":
        return bool(got)
    if want == "none":
        return not got
    wants = []
    for w in (want if isinstance(want, list) else [want]):
        wants += LEGENDARIES if w == "legendary" else [w]
    plain = re.sub(REGIONS, "", str(got or ""), flags=re.I)  # Galarian-Articuno
    return any(same_word(w, got) or same_word(w, plain) for w in wants)


def rule_ok(rule, enc):
    for k, v in rule.items():
        if k == "not_species":
            if field_ok(v, enc.get("species")):
                return False
        elif not field_ok(v, enc.get(k)):
            return False
    return True


def wanted(enc, targets):
    """The first target rule this encounter matches, or None."""
    for rule in targets:
        if rule_ok(rule, enc):
            return rule
    return None


def load_config(path):
    with open(path) as f:
        cfg = json.load(f)
    for rule in cfg.get("targets", []) + cfg.get("notify", []) + cfg.get("post", []):
        bad = set(rule) - set(FIELDS) - {"not_species"}
        if bad:
            sys.exit(f"config.json: unknown field(s) {', '.join(sorted(bad))} in {rule}; "
                     f"use {', '.join(FIELDS)}")
    if not cfg.get("targets"):
        sys.exit("config.json: no targets, nothing to hunt for")
    cfg["catch"] = {**CATCH_PLAN, **cfg.get("catch", {})}
    return cfg


CATCH_PLAN = {  # config.json "catch" overrides these
    "park_ball": True,  # a favorited Park Ball (it never misses) goes first, before first_ball
    "first_ball": "Quick Ball", "sleep_move": "Yawn", "guard_move": "Protect",
    "asleep_ball": "Repeat Ball", "awake_ball": "Repeat Ball", "new_ball": "Dive Ball",
    "max_balls": 8,
    "max_turns": 30, "legendary_max_balls": 30, "legendary_max_turns": 90,
    "heal_below": 0.4, "heal_item": "Full Restore",
    "pp_item": "Max Elixir", "on_fail": "hold",
    "sleep_with": "Clodsire",  # the lead that knows Yawn and Protect; others just throw balls
}

# ---------------------------------------------------------------- discord

def discord(cfg, text, img=None):
    """Posts to the webhook in the background; failures are printed, not fatal."""
    url = cfg.get("webhook")
    if not url:
        return

    def post():
        boundary = uuid.uuid4().hex
        payload = {"content": text,
                   "allowed_mentions": {"parse": ["everyone", "users", "roles"]}}
        parts = [(b'name="payload_json"', b"application/json", json.dumps(payload).encode())]
        if img is not None:
            buf = io.BytesIO()
            img.save(buf, "PNG")
            parts.append((b'name="files[0]"; filename="encounter.png"', b"image/png", buf.getvalue()))
        body = b"".join(b"--%s\r\nContent-Disposition: form-data; %s\r\nContent-Type: %s\r\n\r\n%s\r\n"
                        % (boundary.encode(), disp, ctype, data) for disp, ctype, data in parts)
        body += b"--%s--\r\n" % boundary.encode()
        req = urllib.request.Request(url, body, {
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "User-Agent": "pokehunt (catbot, 1.0)"})
        for attempt in range(4):  # the network drops a request now and then
            try:
                urllib.request.urlopen(req, timeout=20).read()
                return
            except Exception as e:
                if attempt == 3:
                    print(f"  ! Discord message failed: {e}", flush=True)
                else:
                    time.sleep(3 * (attempt + 1))

    t = threading.Thread(target=post, daemon=True)
    t.start()
    return t

# ---------------------------------------------------------------- screen I/O

def grab():
    return dev.grab()


def focus_roblox():
    dev.focus_roblox()


def keep_roblox_in_front():
    """Another app popped up: bring Roblox back. Cheap when it's already in front."""
    if not dev.roblox_in_front():
        say("  (bringing Roblox back to the front)")
        focus_roblox()


last_click = [time.time()]
AFK_EVERY = 15 * 60  # Roblox kicks after 20 min without input


def click_at(x, y):
    keep_roblox_in_front()
    last_click[0] = time.time()
    debuglog.debug.note(f"    click ({x:.0f}, {y:.0f})")
    if debuglog.debug.click_shot():  # see where the first clicks land
        debuglog.debug.shot(f"click #{debuglog.debug.clicks} at ({x:.0f}, {y:.0f})", click_closeup(grab(), x, y))
    dev.click(x, y)


def click_closeup(img, x, y):
    """The screen around a click, the spot marked."""
    from PIL import ImageDraw
    w, h = img.width // 3, img.height // 3
    x0 = int(min(max(x - w / 2, 0), img.width - w))
    y0 = int(min(max(y - h / 2, 0), img.height - h))
    out = img.crop((x0, y0, x0 + w, y0 + h))
    d = ImageDraw.Draw(out)
    cx, cy = x - x0, y - y0
    d.ellipse((cx - 14, cy - 14, cx + 14, cy + 14), outline=(255, 0, 255), width=4)
    d.line((cx - 26, cy, cx + 26, cy), fill=(255, 0, 255), width=2)
    d.line((cx, cy - 26, cx, cy + 26), fill=(255, 0, 255), width=2)
    return out


def move_mouse(x, y):
    dev.move(x, y)


def snapshot(img, reason):
    """Save a screenshot of something unexpected to debug/ (keeps the last 30)."""
    import glob
    os.makedirs(DEBUG_DIR, exist_ok=True)
    path = os.path.join(DEBUG_DIR, time.strftime("%Y%m%d-%H%M%S-") + reason + ".png")
    img.save(path)
    for old in sorted(glob.glob(os.path.join(DEBUG_DIR, "*.png")))[:-30]:
        os.remove(old)
    debuglog.debug.shot(reason, img)
    return path


_last_said = [None]


def say(msg):
    """Print, but don't repeat the same waiting message every loop."""
    if msg != _last_said[0]:
        print(msg, flush=True)
        _last_said[0] = msg

# ---------------------------------------------------------------- pause / resume

running = threading.Event()
running.set()
last_key = [0.0]  # time of the last key press: you're back at the keyboard


def start_hotkey():
    from pynput import keyboard
    held = set()

    def on_press(key):
        last_key[0] = time.time()
        if key in (keyboard.Key.ctrl, keyboard.Key.ctrl_l, keyboard.Key.ctrl_r):
            held.add("ctrl")
        elif key in (keyboard.Key.shift, keyboard.Key.shift_l, keyboard.Key.shift_r):
            held.add("shift")
        elif getattr(key, "vk", None) == devices.H_KEYCODE and {"ctrl", "shift"} <= held:
            toggle()
        elif getattr(key, "vk", None) in (devices.N_KEYCODE, devices.L_KEYCODE) and {"ctrl", "shift"} <= held:
            website_key(key.vk)

    def on_release(key):
        name = getattr(key, "name", "") or ""
        held.discard("ctrl" if name.startswith("ctrl") else
                     "shift" if name.startswith("shift") else None)

    keyboard.Listener(on_press=on_press, on_release=on_release, daemon=True).start()


def toggle():
    if running.is_set():
        running.clear()
        print("\n|| paused (Ctrl+Shift+H to resume)", flush=True)
        sound("Bottle")
    else:
        print("\n>> hunting (Ctrl+Shift+H to pause)", flush=True)
        sound("Pop")
        running.set()
    publish()


def website_key(vk):
    """Ctrl+Shift+N: a new website code. Ctrl+Shift+L: log every browser out."""
    link = web.link
    if not link:
        return

    def go():
        try:
            if vk == devices.N_KEYCODE:
                link.new_code()
            else:
                link.log_out_browsers()
                print("  (website: every browser is logged out)", flush=True)
        except Exception as e:
            print(f"  (website: no connection: {e})", flush=True)

    threading.Thread(target=go, daemon=True).start()


def wait_running():
    """Paused: waits for Ctrl+Shift+H, or the website taking over."""
    while not running.is_set() and not web.remote:
        running.wait(0.2)

# ---------------------------------------------------------------- the website

web = remote.Web()


def publish():
    web.publish(running.is_set(), _cfg)
    if WINDOW:
        enc = web.enc
        remote.show("@state " + json.dumps({
            "running": running.is_set(), "remote": web.remote, "count": web.encounters,
            "phase": web.phase, "wanted": web.wanted, "enc": title(enc) if enc else ""}))


def window_buttons():
    """Hunt's window sends its buttons as lines: toggle, code, logout. When it
    closes the line ends (or it just closes it to stop), and so does the hunt."""
    def read():
        for line in sys.stdin:
            cmd = line.strip()
            if cmd == "toggle":
                toggle()
            elif cmd in ("code", "logout"):
                website_key(devices.N_KEYCODE if cmd == "code" else devices.L_KEYCODE)
        import _thread
        _thread.interrupt_main()

    threading.Thread(target=read, daemon=True).start()


def from_website(cmd):
    """A command from the website (the link's thread)."""
    what = cmd.get("cmd")
    if what == "takeover" and not web.remote:
        while not web.commands.empty():
            web.commands.get_nowait()
        web.remote = True
        print("\n<< taken over from the website", flush=True)
        sound("Bottle")
        publish()
    elif what == "resume" and web.remote:
        web.remote = False
        running.set()
        print("\n>> handed back from the website: hunting", flush=True)
        sound("Pop")
        publish()
    elif what == "shot":
        web.want_shot = True
    elif what == "login":
        print("  (website: a browser logged in with the code, so it's spent: Ctrl+Shift+N for a new one. "
              "Wasn't you? Ctrl+Shift+L logs every browser out)", flush=True)
        sound("Glass")
    elif what in ("ball", "move", "heal", "pp", "run"):
        web.commands.put(cmd)


def send_sprite(img, lay, enc):
    """The wild Pokemon's sprite as the game draws it (shiny, gradient, reskin
    and all), for the website: the sprite's 96 x 96 pixels, background and all;
    the page cuts it out with the species' shape."""
    if not web.link:
        return
    try:
        _send_sprite(img, lay, enc)
    except Exception as e:  # the page has the plain sprite: never worth stopping the hunt for
        trace(f"sprite: {e}")


def _send_sprite(img, lay, enc):
    # the sprite keeps its shape: scaled like the screen's height (phones squeeze the battle)
    x0, y0 = lay.pt(*FOE_SPRITE)
    k = SPRITE_PIXEL * lay.s * lay.fit[1]
    xs = [int(x0 + (i + .5) * k) for i in range(96)]
    ys = [int(y0 + (i + .5) * k) for i in range(96)]
    a = np.asarray(img.convert("RGB"))
    xs, ys = np.clip(xs, 0, a.shape[1] - 1), np.clip(ys, 0, a.shape[0] - 1)
    buf = io.BytesIO()
    Image.fromarray(a[np.ix_(ys, xs)]).save(buf, "PNG")
    web.link.send_sprite({"count": web.encounters, "species": enc["species"],
                          "png": base64.b64encode(buf.getvalue()).decode()})


def send_shot(img, lay):
    shot = img.crop(lay.box(PANEL)).convert("RGB")
    shot.thumbnail((960, 960))
    buf = io.BytesIO()
    shot.save(buf, "JPEG", quality=70)
    if web.link:
        web.link.send_shot(buf.getvalue())


# "PP / max" under a move. The slash is read as | or I now and then, or lost
# ("40 40"); nothing else there has numbers.
PP = re.compile(r"(\d+)\s*(?:[/|Il\\]|\s)\s*(\d+)")


def lead_from(texts):
    """Your Pokemon from the text read in its box (PLAYER_BOX): [(text, x, y)]."""
    texts = sorted(texts, key=lambda t: (t[2], t[1]))
    joined = " ".join(t for t, _, _ in texts)  # "376 / 376" can come in pieces
    hp = re.search(r"(\d+)\s*[/|]\s*(\d+)", joined)  # the slash is read as | now and then
    level = re.search(r"\bl[vy][.,:]?\s*(\d+)", joined, re.I)
    name = next((n for n in (clean_name(t) for t, _, _ in texts)
                 if re.search("[A-Za-z]{2}", n) and n.lower() != "exp" and not re.match(r"l[vy]\b", n, re.I)), "")
    return {"name": name, "level": int(level.group(1)) if level else None,
            "hp": int(hp.group(1)) if hp else None, "max": int(hp.group(2)) if hp else None}


def moves_from(texts):
    """The moves in the Fight menu from the text read in MSG ([(text, x, y)],
    fractions): up to four buttons side by side, the name on top, "PP / max" under it."""
    pp_re = PP
    pps = [(int(x * 4), m) for t, x, y in texts if y >= 0.5 for m in [pp_re.search(t)] if m]
    # left to right; one hit can hold two buttons' PP ("30 / 35 Normal Physical 40 / 40")
    in_order = [m for t, x, y in sorted(texts, key=lambda t: t[1]) if y >= 0.5 for m in pp_re.finditer(t)]
    names = [(t.strip(), x) for t, x, y in sorted(texts, key=lambda t: t[1])
             if y < 0.5 and re.search("[A-Za-z]{2}", t) and not re.search(r"[\d?!]", t) and len(t.strip()) <= 22
             and t.strip().lower() != "back"]
    moves = []
    for i, (t, x) in enumerate(names):
        # one PP per button: in order; else by the button the text sits in
        pp = in_order[i] if len(in_order) == len(names) else next((m for col, m in pps if col == int(x * 4)), None)
        moves.append({"name": t, "pp": int(pp.group(1)) if pp else None, "max": int(pp.group(2)) if pp else None})
    return moves


def read_lead(img, lay):
    """Reads your Pokemon's box for the page; a box half covered (the level-up
    stats panel) leaves the last whole reading."""
    x0, y0, x1, y1 = lay.box(PLAYER_BOX)
    lead = lead_from(ocr(img.crop((x0, y0, x1, y1))))
    if lead["name"] and None not in (lead["level"], lead["hp"], lead["max"]) and lead["hp"] <= lead["max"]:
        web.lead = lead


def read_moves(lay):
    """Fight -> reads the moves and their PP -> Back to Fight / Bag / Run."""
    ensure_battle(lay)
    click_at(*lay.pt(*FIGHT))
    move_mouse(*lay.pt(*IDLE_SPOT))
    moves, end = [], time.time() + 5
    while time.time() < end and not moves:
        time.sleep(0.4)
        img = grab()
        if menu_ready(load_rgb(img), lay):
            continue  # the Fight menu isn't up yet
        x0, y0, x1, y1 = lay.box(MSG)
        moves = moves_from(ocr(img.crop((x0, y0, x1, y1))))
    trace("fight: moves " + ", ".join(f"{m['name']} {m['pp']}/{m['max']}" for m in moves))
    end = time.time() + 6
    while time.time() < end:
        img = grab()
        if menu_ready(load_rgb(img), lay):
            break
        back = next(((x, y) for t, x, y in screen_texts(img) if t.lower() == "back"), None)
        if back:
            click_at(*back)
            move_mouse(*lay.pt(*IDLE_SPOT))
        time.sleep(0.6)
    return moves


def set_moves(moves):
    web.moves = moves
    if web.lead.get("name"):
        web.known_moves[web.lead["name"]] = moves


def battle_over(msg):
    print(f"  {msg}", flush=True)
    web.phase, web.acting = "waiting", ""
    if msg:
        web.msg = msg
    publish()


def remote_battle(cfg, enc, img, lay):
    """Taken over from the website: the battle waits for the page's buttons,
    and the page sees each turn (a picture, the battle's text, your lead).
    Returns True if it caught it, once the battle is over or handed back."""
    start_img, start_lay = img, lay
    read_lead(img, lay)
    web.moves = web.known_moves.get(web.lead.get("name"), [])
    send_shot(img, lay)
    publish()
    plan = cfg["catch"]
    try:
        if not web.moves:
            web.acting = "Reading your moves"
            publish()
            set_moves(read_moves(lay))
            web.acting = ""
            web.error = "" if web.moves else "Couldn't read your moves"
            publish()
        checked, gone = time.time(), None
        while web.remote:
            try:
                cmd = web.commands.get(timeout=0.2)
            except queue.Empty:
                if web.want_shot:
                    web.want_shot = False
                    send_shot(grab(), lay)
                if time.time() - checked < 1:
                    continue
                checked = time.time()
                img, rgb, now = look(lay.mute)
                if not now:
                    gone = gone or time.time()
                    if time.time() - gone > GONE_AFTER:
                        battle_over("the battle is over")
                        return False
                else:
                    gone, lay = None, now
                continue
            what, name = cmd.get("cmd"), cmd.get("name", "")
            web.error = ""
            web.acting = {"ball": f"Throwing a {name}", "move": f"Using {name}",
                          "heal": f"Using a {plan['heal_item']}", "pp": f"Using a {plan['pp_item']}",
                          "run": "Running"}.get(what, "")
            if not web.acting:
                continue
            print(f"  website: {web.acting[0].lower() + web.acting[1:]}", flush=True)
            publish()
            if what == "ball":
                done = name in cfg.get("web_balls", remote.WEB_BALLS) and use_item(lay, "Pokeballs", name)
            elif what == "move":
                used = use_move(lay, name)
                done = used == "used"
                if used == "used":
                    set_moves([dict(m, pp=m["pp"] - 1 if m["name"] == name and m["pp"] else m["pp"]) for m in web.moves])
                elif used == "no pp":
                    set_moves([dict(m, pp=0) if m["name"] == name else m for m in web.moves])
                    web.error = f"{name} has no PP left"
                else:
                    web.error = f"Couldn't find {name} in the Fight menu"
            elif what == "heal":
                done = use_item(lay, "Recovers", plan["heal_item"], web.lead.get("name"))
            elif what == "pp":
                done = use_item(lay, "Recovers", plan["pp_item"], web.lead.get("name"))
                if done:
                    set_moves([dict(m, pp=m["max"] if m["max"] is not None else m["pp"]) for m in web.moves])
            else:  # run: clicks Run until the battle is over
                web.done = cmd.get("seq", 0)
                if run_away(lay, for_website=True):
                    battle_over("got away")
                    return False
                web.error, done = "Couldn't get away", False
            web.done = cmd.get("seq", 0)
            if not done:
                web.error = web.error or f"Couldn't: {web.acting[0].lower() + web.acting[1:]} didn't work"
                web.acting = ""
                img, rgb, now = look(lay.mute)
                if now:
                    send_shot(img, now)
                publish()
                continue
            outcome, img, rgb, lay, msgs = wait_turn(lay)
            web.acting = ""
            if msgs:
                web.msg = " ".join(msgs[-3:])
            print(f"  turn: {outcome}" + (f". The battle said: {' / '.join(msgs)}" if msgs else ""), flush=True)
            if img is not None:
                if outcome == "turn":
                    read_lead(img, lay)
                    if not chat_showing(rgb, lay) and box_visible(rgb, lay):
                        again = read_enemy(img, rgb, lay)
                        if again["species"] and same_mon(again, enc):
                            web.enc = again
                if find_mute(rgb, lay.mute):
                    send_shot(img, lay)
            if outcome == "caught":
                log_encounter(enc, "caught")
                discord(cfg, f"Caught from the website: {title(enc)}", game_shot(start_img, start_lay))
                battle_over("caught it!")
                return True
            if outcome == "over":
                battle_over("the battle is over")
                return False
            if outcome == "timeout":
                web.error = "That turn took over a minute"
            publish()
    except BattleOver:
        battle_over("the battle is over")
    return False

# ---------------------------------------------------------------- hunting

SAVE_EVERY = 125    # s; the game allows one save every 2 minutes
GONE_AFTER = 1.0    # s without the Mute button before an encounter counts as over
SEEN_AFTER = 0.4    # s with the Mute button before it counts as started


def look(hint):
    img = grab()
    use_calibration(img.size)
    rgb = load_rgb(img)
    mute = find_mute(rgb, hint)
    return img, rgb, (Layout(mute) if mute else None)


def read_encounter(hint, clicks=False, draw=False):
    """Waits for the Fight/Run menu and reads the enemy until two reads in a
    row agree. Returns (enc, img, layout), or None once the encounter is over.
    With clicks it may click, to close the chat when that's in the way. The
    layout is fitted on the first encounter on this screen, when the battle
    can't be read for a few seconds, or (draw) to draw it for you to check."""
    prev, gone, start, saved = None, None, time.time(), False
    chat_click, chat_clicks, cal_at = 0.0, 0, 0.0
    while True:
        img, rgb, lay = look(hint)
        if not lay:
            gone = gone or time.time()
            if time.time() - gone > GONE_AFTER:
                return None
            time.sleep(0.1)
            continue
        gone, hint = None, lay.mute
        if clicks:
            keep_roblox_in_front()  # a popup may be covering the enemy's box
        menu = menu_ready(rgb, lay)
        chat = chat_showing(rgb, lay)
        # The chat button toggles, so give up after two clicks rather than
        # flip it open and shut for good if something else looks like chat.
        if chat and clicks and time.time() - chat_click > 5 and chat_clicks < 2:
            say("  closing the chat, it covers the enemy's box")
            click_at(*(find_chat_button(rgb) or lay.pt(*CHAT_BUTTON)))
            move_mouse(*lay.pt(*IDLE_SPOT))
            chat_click, chat_clicks = time.time(), chat_clicks + 1
        readable = menu and not chat and box_visible(rgb, lay)
        if time.time() - cal_at > 2 and (draw or not cal.get("saved") or
                                         (not readable and time.time() - start > 3)):
            cal_at = time.time()
            if auto_calibrate(img, rgb, lay.mute, draw):
                draw = False
                continue  # read again with the new layout
        if readable:
            enc = read_enemy(img, rgb, lay)
            if enc["species"] and enc == prev:
                return enc, img, lay
            prev = enc
        if time.time() - start > 15 and not saved:
            # never Run from something unread: it might be the one
            why = ("chat is covering the enemy's box" if chat else
                   "can't read the enemy's box" if menu else "no Fight/Run menu")
            trouble(f"{why} for 15 s, waiting", img)
            saved = True
        time.sleep(0.2)


def run_away(lay, for_website=False):
    """Clicks Run (again if the escape failed) until the encounter is over.
    Stops (False) when the website takes over, unless it's the website's Run."""
    clicked, gone, start = 0.0, None, time.time()
    while time.time() - start < 20:
        wait_running()
        if web.remote and not for_website:
            return False
        img, rgb, now = look(lay.mute)
        if not now:
            gone = gone or time.time()
            if time.time() - gone > GONE_AFTER:
                return True
        else:
            gone, lay = None, now
            if menu_ready(rgb, lay) and time.time() - clicked > 2.5:
                if clicked:
                    say("  run failed, trying again")
                click_at(*lay.pt(*RUN))
                move_mouse(*lay.pt(*IDLE_SPOT))  # don't light up Run
                clicked = time.time()
        time.sleep(0.15)
    trouble("still in the encounter 20 s after clicking Run", img)
    return False


_cfg = {}


def trouble(msg, img):
    """Something needs a look: print, save a screenshot, tell Discord."""
    say(f"  ({msg}, screenshot: {snapshot(img, 'trouble')})")
    shot = img.copy()
    shot.thumbnail((1200, 1200))
    discord(_cfg, f"pokehunt: {msg}.", shot)


def screen_texts(img):
    """[(text, x, y)] in screen pixels."""
    return [(t.strip(), x * img.width, y * img.height) for t, x, y in ocr(img)]


def find_use_button(rgb, below):
    """Center of the bright green Use button under the backpack."""
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    best = None
    for sl in blobs((g > 200) & (r < 170) & (b < 170)):
        h, w = sl[0].stop - sl[0].start, sl[1].stop - sl[1].start
        if h > 8 and 3 < w / h < 12 and sl[0].start > below and (best is None or w * h > best[0]):
            best = (w * h, (sl[1].start + sl[1].stop) / 2, (sl[0].start + sl[0].stop) / 2)
    return best[1:] if best else None


def favorited(rgb, texts, y, v):
    """The bag's row at y is a favorite: its count tag ("x985") is yellow;
    the others' are light blue."""
    count = next(((x, ty) for t, x, ty in texts if re.fullmatch(r"x[\d,.]+", t) and abs(ty - y) < 30 * v), None)
    if not count:
        return False
    x, cy = count
    p = rgb[max(int(cy - 10 * v), 0):int(cy + 10 * v), max(int(x - 110 * v), 0):int(x - 70 * v)]
    if p.size == 0:
        return False
    r, g, b = p[..., 0], p[..., 1], p[..., 2]
    return bool(((r > 200) & (g > 150) & (b < 150)).mean() > 0.5)


def region_text(img, lay, box):
    x0, y0, x1, y1 = lay.box(box)
    return " ".join(t for t, _, _ in ocr(img.crop((x0, y0, x1, y1))))


def own_hp(img, lay):
    """Your Pokemon's HP as a fraction, or None if it can't be read."""
    m = re.search(r"(\d+)\s*/\s*(\d+)", region_text(img, lay, PLAYER_BOX))
    return int(m.group(1)) / int(m.group(2)) if m and int(m.group(2)) else None


def close_bag(texts):
    for t, x, y in texts:
        if t.lower() == "close":
            click_at(x, y)
            return


def trace(msg):
    """A step of a catch, for following it in the log."""
    print(f"    {msg}", flush=True)


class BattleOver(Exception):
    """The battle ended (it fled, it was caught, ...) while a catch was going."""


def ensure_battle(lay):
    """Waits for the Fight / Bag / Run menu; raises BattleOver if the battle
    is over (out of battle the Bag button's spot is somewhere else)."""
    end = time.time() + 4
    while time.time() < end:
        img, rgb, now = look(lay.mute)
        if now and menu_ready(rgb, now):
            return
        time.sleep(0.4)
    raise BattleOver()


TABS = ("Recovers", "Pokeballs", "Key Items", "Evolution", "Competitive", "TMs", "Held Items", "Z-Crystals")


def find_tab(texts, want, split, width):
    """Where the backpack's tab is, (x, y). On a small window the labels are
    tiny and some aren't read, or are misread, but the tabs sit in a fixed
    order, evenly spaced: one that isn't read is worked out from the ones
    that are. As Reader.findTab on Android."""
    i = next((n for n, t in enumerate(TABS) if t.lower() == want.lower()), -1)
    if i < 0:
        return next(((x, y) for t, x, y in texts if t.lower() == want.lower()), None)
    hits = [(n, x, y) for t, x, y in texts if x < split
            for n in [next((n for n, tab in enumerate(TABS) if norm(tab) == norm(t) or same_word(tab, t)), -1)]
            if n >= 0]
    if not hits:
        return None
    col = min(x for _, x, _ in hits)  # the tab column; the list's heading is further right
    in_col = [h for h in hits if abs(h[1] - col) < 0.04 * width]
    exact = next(((x, y) for n, x, y in in_col if n == i), None)
    if exact or len({n for n, _, _ in in_col}) < 2:
        return exact
    ns, ys = np.array([n for n, _, _ in in_col], float), np.array([y for _, _, y in in_col], float)
    step = ((ns - ns.mean()) * (ys - ys.mean())).sum() / ((ns - ns.mean()) ** 2).sum()
    return float(np.median([x for _, x, _ in in_col])), float(ys.mean() + step * (i - ns.mean()))


def use_item(lay, tab, item, target=None, prefer=None):
    """Bag -> tab -> item -> Use, and -> party member when the item asks for
    one. Favorited items sit at the top of their tab; anything else is
    searched for. prefer: used instead, if it's a favorite there. The name
    of what was used; False on failure (the backpack is closed again)."""
    ensure_battle(lay)
    trace(f"bag: opening it for {item}")
    click_at(*lay.pt(*BAG))
    move_mouse(*lay.pt(*AFK_SPOT))
    split = lay.pt(BAG_SPLIT, 0)[0]
    step, row_y, tab_at, searched = "tab", 0, 0.0, False
    picked_at, picks = 0.0, 0
    v = lay.s * lay.fit[1]  # screen px per reference px, down
    texts, img = [], None
    end = time.time() + 12
    while time.time() < end:
        time.sleep(0.4)
        img = grab()
        texts = screen_texts(img)
        if step == "tab":
            # the bag opens on the tab it was left on: the item in sight there, no click
            names = {norm(item)} | ({norm(prefer)} if prefer else set())
            if any(norm(t) in names and x < split for t, x, y in texts):
                trace(f"bag: {item} is in sight already")
                step, tab_at = "item", time.time()
                continue
            hit = find_tab(texts, tab, split, img.width)
            if hit:
                trace(f"bag: {tab} tab at ({hit[0]:.0f}, {hit[1]:.0f})")
                click_at(*hit)
                step, tab_at = "item", time.time()
        elif step == "item":
            if prefer and not searched and norm(prefer) != norm(item):
                fav = sorted(y for t, x, y in texts if norm(t) == norm(prefer) and x < split)
                if fav and favorited(load_rgb(img), texts, fav[0], v):
                    print(f"  {prefer} is a favorite: throwing that instead of the {item}", flush=True)
                    item = prefer
            # exact names: "Dive Ball" and "Dream Ball" are one misread apart
            rows = sorted((y, x) for t, x, y in texts if norm(t) == norm(item) and x < split)
            if rows:
                row_y, x = rows[0]  # the list row, not the description under it
                left = next((t for t, tx, ty in texts
                             if re.fullmatch(r"x[\d,.]+", t) and abs(ty - row_y) < 30), "?")
                trace(f"bag: clicking {item} ({left} in the bag) at ({x:.0f}, {row_y:.0f})")
                click_at(x, row_y)
                move_mouse(*lay.pt(*AFK_SPOT))
                step, picked_at = "use", time.time()
            elif not searched and time.time() - tab_at > 1.5:
                # the box sits just under Close: whatever it shows ("Search", "QUICK BALL",
                # a lowercase "full" left in it), never the menu bar's "A ABC" up top
                close = next((y for t, x, y in texts if t.lower() == "close" and x > split), None)
                box = next(((x, y) for t, x, y in sorted(texts, key=lambda t: t[2])
                            if close is not None and x > split and 40 * v < y - close < 170 * v
                            and not re.fullmatch(r"x[\d,.]+", t)), None)
                if box:  # not a favorite: type it into the tab's search box
                    click_at(*box)
                    dev.type(item, erase=25)
                    searched = True
        elif step == "use":
            use = find_use_button(load_rgb(img), row_y) or next(
                ((x, y) for t, x, y in texts if t.lower() == "use" and y > row_y), None)
            # the panel over Use names what's selected: never use anything else
            named = [(t, y) for t, x, y in texts if use and 0 <= use[1] - y <= 140 * v and x < use[0]]
            if use and any(norm(t) == norm(item) for t, _ in named):
                trace(f"bag: {item} is selected: Use")
                click_at(*use)
                move_mouse(*lay.pt(*AFK_SPOT))
                if not target:
                    return item
                step = "target"
                end = max(end, time.time() + 6)  # the party screen takes a moment to come up
            elif named or time.time() - picked_at > 2.5:
                shown = min(named, key=lambda n: n[1])[0] if named else "nothing"
                picks += 1
                if picks > 3:
                    trace(f"bag: {shown} is selected, not {item}: giving up")
                    break
                trace(f"bag: {shown} is selected, not {item}: picking it again")
                step = "item"
        elif step == "target":
            if any(t.lower().startswith("choose a pokemon") for t, _, _ in texts):
                hit = next(((x, y) for t, x, y in texts if same_word(target, t)), None)
                if hit:
                    click_at(*hit)
                    return item
    say(f"  (couldn't use {item}: {snapshot(img, 'bag')}, text read: "
        f"{', '.join(t for t, _, _ in texts)[:300]})")
    close_bag(texts)
    return False


def use_move(lay, move):
    """Fight -> move. "used", or "no pp" (it goes Back to the menu then)."""
    ensure_battle(lay)
    click_at(*lay.pt(*FIGHT))
    move_mouse(*lay.pt(*IDLE_SPOT))
    end = time.time() + 6
    while time.time() < end:
        time.sleep(0.4)
        texts = screen_texts(grab())
        hit = next(((x, y) for t, x, y in texts if t.lower() == move.lower()), None)
        if not hit:
            continue
        # the moves on this row, left to right, and their "PP / max" under them
        row = sorted(x for t, x, y in texts if abs(y - hit[1]) < 15 and not re.search(r"\d", t))
        pps = [m for t, x, y in sorted(texts, key=lambda t: t[1]) if 20 < y - hit[1] < 120
               for m in PP.findall(t)]
        # one PP per move, or they can't be told apart (a slash misread, two read as one)
        i = row.index(hit[0]) if hit[0] in row and len(pps) == len(row) else -1
        if 0 <= i < len(pps) and int(pps[i][0]) == 0:
            back = next(((x, y) for t, x, y in texts if t.lower() == "back"), None)
            trace(f"fight: {move} has no PP left, back")
            if back:
                click_at(*back)
            return "no pp"
        trace(f"fight: {move}" + (f" ({pps[i][0]} PP)" if 0 <= i < len(pps) else ""))
        click_at(*hit)
        move_mouse(*lay.pt(*IDLE_SPOT))
        return "used"
    trace(f"fight: didn't find {move} in the move menu")
    return "no menu"


def caught_said(msg):
    """The battle's text after a catch: "Gotcha! ... was caught!", or only
    "Rattata was added to the Party." when the first message went by too fast."""
    m = msg.lower()
    return "was caught" in m or "gotcha" in m or "added to the party" in m or bool(re.search(r"sent to (the )?(pc|box)", m))


def wait_turn(lay):
    """After an action, waits until the turn is over. Returns (outcome,
    img, rgb, lay, messages): "caught", "turn" (the menu is back), "over"
    (the battle ended without a catch) or "timeout"."""
    start, seen, gone, msgs = time.time(), False, None, []
    img = rgb = None
    while time.time() - start < TURN_TIMEOUT:
        time.sleep(0.25)
        img, rgb, now = look(lay.mute)
        if not now:  # backpack or party screen still up, or the battle is over
            if seen:
                gone = gone or time.time()
                if time.time() - gone > 4:
                    caught = any(caught_said(m) for m in msgs)
                    return ("caught" if caught else "over"), img, rgb, lay, msgs
            continue
        seen, gone, lay = True, None, now
        msg = region_text(img, now, MSG)
        if msg and (not msgs or msgs[-1] != msg):
            msgs.append(msg)
        if caught_said(msg):
            return "caught", img, rgb, now, msgs
        if time.time() - start > 1.5 and menu_ready(rgb, now) and "should" in msg.lower():
            return "turn", img, rgb, now, msgs
    return "timeout", img, rgb, lay, msgs


def same_mon(a, b):
    """Still the same Pokemon, forgiving a misread letter ("Type: Nulll")."""
    if a["level"] and b["level"] and a["level"] != b["level"]:
        return False
    return same_word(a["species"], b["species"]) and all(
        same_word(a[k], b[k]) for k in ("reskin", "gradient") if a[k] and b[k])


def catch(enc, img, lay, plan, first_ball=True, debug=False):
    """catching(), stopping when the battle ends under it."""
    progress = [0, 0, ""]  # balls, turns, the last ball
    try:
        return catching(enc, img, lay, plan, first_ball, debug, progress)
    except BattleOver:
        if progress[2] == "Park Ball":  # it never misses: the battle ending is the catch
            return "caught", "a Park Ball"
        print("  the battle is over", flush=True)
        return "over", f"{progress[0]} ball(s), {progress[1]} turns"


def catching(enc, img, lay, plan, first_ball=True, debug=False, progress=None):
    """Catches with the plan from config.json "catch": a first-turn ball,
    then Yawn, Protect while it gets drowsy, and the sleep ball once it's
    asleep. Statused targets (Yawn can't work) get the other ball. Heals and
    restores PP along the way. Returns (result, details): "caught", "gave up",
    "no bag" (couldn't use the bag), "over" or "timeout". debug: a screenshot
    of every turn to the debug webhook."""
    rgb = load_rgb(img)
    m = re.search(r"should (.+?) do", region_text(img, lay, MSG))
    lead = m.group(1) if m else plan.get("lead", "")
    legendary = field_ok("legendary", enc["species"])  # much harder to catch
    max_balls = plan["legendary_max_balls" if legendary else "max_balls"]
    max_turns = plan["legendary_max_turns" if legendary else "max_turns"]
    balls, turns, drowsy, heals, elixirs = [], 0, False, 0, 0
    now = enc
    # the first ball is a Park Ball if one is favorited in the bag (not for practice or tests)
    park = "Park Ball" if plan.get("park_ball", True) and first_ball and not debug else None
    # A Repeat Ball only helps on species you've caught before (the little
    # Poke Ball icon); anything new gets new_ball instead.
    new = None if enc.get("owned", True) else plan.get("new_ball")
    print(f"  catching {title(enc)} Lv{enc['level'] or '?'} with {lead or '?'}: up to {max_balls} balls, "
          f"{max_turns} turns" + (" (legendary)" if legendary else "") +
          ("" if enc.get("owned", True) else f", not caught before: {new or 'same balls'}"), flush=True)
    while True:
        if web.remote:
            print("  taken over mid-battle", flush=True)
            return "taken over", f"{len(balls)} ball(s), {turns} turns"
        hp = own_hp(img, lay)
        print(f"  turn {turns + 1}: {lead or 'ours'} at {f'{hp:.0%}' if hp is not None else '? HP'}, "
              f"{now['species']} {now['status'] or 'no status'}" + (", drowsy" if drowsy else ""), flush=True)
        if turns == 0 and first_ball and (park or plan.get("first_ball")):
            ball = plan.get("first_ball") or new or plan["awake_ball"]  # unless a Park Ball is favorited
        elif hp is not None and hp < plan.get("heal_below", 0.4) and heals < 5:
            trace(f"low HP: {plan['heal_item']}")
            if not use_item(lay, "Recovers", plan["heal_item"], lead):
                return "no bag", f"{plan['heal_item']} failed"
            heals, ball = heals + 1, None
        elif now["status"] == "SLP":
            ball = new or plan["asleep_ball"]
        elif now["status"]:
            ball = new or plan["awake_ball"]  # poisoned etc.: Yawn can't put it to sleep
        elif not same_word(plan.get("sleep_with", ""), lead):
            ball = new or plan["awake_ball"]  # only the Yawn user puts it to sleep; others just throw
        else:
            move = plan["guard_move"] if drowsy else plan["sleep_move"]
            used = use_move(lay, move)
            if used == "used":
                drowsy, ball = not drowsy, None
            elif used == "no pp" and elixirs < 3:
                trace(f"{move} is out of PP: {plan['pp_item']}")
                if not use_item(lay, "Recovers", plan["pp_item"], lead):
                    return "no bag", f"{plan['pp_item']} failed"
                elixirs, ball = elixirs + 1, None
            else:
                ball = new or plan["awake_ball"]
        if ball:
            if len(balls) >= max_balls:
                return "gave up", f"{len(balls)} balls"
            trace(f"throwing a {ball} (#{len(balls) + 1})")
            used = use_item(lay, "Pokeballs", ball, prefer=park)
            if not used:
                return "no bag", f"{ball} failed"
            balls.append(used)
            park = None
            if progress is not None:
                progress[:] = [len(balls), turns, used]
        outcome, img, rgb, lay, msgs = wait_turn(lay)
        turns += 1
        if progress is not None:
            progress[:] = [len(balls), turns, balls[-1] if balls else ""]
        print(f"  turn {turns}: {outcome}" + (f". The battle said: {' / '.join(msgs)}" if msgs else ""), flush=True)
        if debug and img is not None:
            debuglog.debug.shot(f"catch turn {turns}: {outcome}", game_shot(img, lay))
        details = f"{len(balls)} ball(s), last {balls[-1] if balls else '-'}, {turns} turns"
        if balls and balls[-1] == "Park Ball" and outcome in ("caught", "over"):
            return "caught", "a Park Ball"  # it never misses: the battle ending is the catch
        if outcome != "turn":
            return outcome, details
        if any("fell asleep" in m.lower() for m in msgs):
            drowsy = False
        if turns >= max_turns:
            return "gave up", details
        if chat_showing(rgb, lay) or not box_visible(rgb, lay):
            trace("can't see its box this turn")
            time.sleep(1)  # read again next turn; keep going with what we know
            continue
        again = read_enemy(img, rgb, lay)
        if again["species"] and not same_mon(again, enc):
            return "caught", details  # already the next encounter
        now = again


SAVE_WAIT = re.compile(r"wait\s+(\d+)\s+more\s+second", re.I)
save_wait = [0]  # s the game said to wait before saving again


def save_message(t):
    """The box after Save: "Save successful", "Try again", or "Unable to save.
    Please wait 95 more seconds." (one save every 2 minutes)."""
    t = t.lower()
    return ("save" in t and ("successful" in t or "again" in t or "unable" in t)) or bool(SAVE_WAIT.search(t))


MENU_ITEMS = ("Pokedex", "Party", "Bag", "PokeBoosters", "Save", "Trainer Card", "Codes")


def menu_open(texts):
    """The MENU panel is open: two of its labels are read (forgiving a misread)."""
    return sum(any(norm(m) == norm(t) or same_word(m, t) for m in MENU_ITEMS) for t, _, _ in texts) >= 2


def menu_button(hit, texts, retry):
    """Where to click a MENU item: its tile, half a row above its label (on a
    phone only the tile takes taps); the label itself on the retry, or if no
    other label shows how far apart the rows are."""
    x, y = hit
    rows = [abs(ty - y) for t, tx, ty in texts if (tx, ty) != hit and any(norm(m) == norm(t) for m in MENU_ITEMS)]
    rows = [r for r in rows if r > 0]
    return hit if retry or not rows else (x, y - min(rows) / 2)


def save_game():
    """MENU -> Save -> dismiss the message -> close MENU, between
    encounters. True if the game saved; if it said to wait, save_wait has
    how long."""
    img = grab()
    texts = screen_texts(img)
    tab = next(((x, y) for t, x, y in texts if norm(t) == "menu"), None)
    if not tab or find_mute(load_rgb(img)):
        return False
    trace(f"save: MENU at ({tab[0]:.0f}, {tab[1]:.0f})")
    click_at(*tab)
    step, saved, shown, saved_at, tries = "save", False, False, 0.0, 0
    end = time.time() + 20
    while time.time() < end:
        time.sleep(0.4)
        img = grab()
        texts = screen_texts(img)
        if step == "save":
            if find_mute(load_rgb(img)):
                return False  # an encounter came first
            hit = next(((x, y) for t, x, y in texts if norm(t) == "save"), None)
            if hit and menu_open(texts):
                at = menu_button(hit, texts, retry=tries > 0)
                trace(f"save: clicking Save at ({at[0]:.0f}, {at[1]:.0f})")
                click_at(*at)
                step, saved_at = "message", time.time()
        elif step == "message":
            msg = next(((t, x, y) for t, x, y in texts if save_message(t)), None)
            if msg:
                if not shown:
                    trace(f'save: the game says "{msg[0]}"')
                wait = SAVE_WAIT.search(msg[0])
                if wait:
                    save_wait[0] = int(wait.group(1))
                # The text types itself out: a click while it's typing only
                # finishes it, so keep clicking until the box is gone.
                saved = saved or "successful" in msg[0].lower()
                shown = True
                click_at(msg[1], msg[2])
                time.sleep(0.6)
            elif shown:  # the message was there and is gone now
                step = "close"
            elif time.time() - saved_at > 4 and tries < 2:  # the click on Save didn't take
                tries += 1
                trace("save: no message yet, clicking Save again")
                step = "save"
        if step == "close":  # the menu panel comes back; its tab closes it
            if menu_open(texts):
                hit = next(((x, y) for t, x, y in texts if norm(t) == "menu"), None)
                if hit:
                    trace("save: closing the menu")
                    click_at(*hit)
                    step = "closed"
        elif step == "closed":
            if not menu_open(texts):
                return saved
    say(f"  (saving got stuck at \"{step}\": {snapshot(img, 'save-stuck-at-' + step)}, "
        f"read: {', '.join(t for t, _, _ in texts)[:300]})")
    return saved


def clear_leftovers():
    """Closes a save message or menu panel left open; no encounters come
    while either is up. True if it found something to close."""
    texts = screen_texts(grab())
    msg = next(((x, y) for t, x, y in texts if save_message(t)), None)
    if msg:
        click_at(*msg)
        return True
    if menu_open(texts):
        tab = next(((x, y) for t, x, y in texts if norm(t) == "menu"), None)
        if tab:
            click_at(*tab)
            return True
    return False


def keep_alive(cfg, lay):
    """Clicks an empty spot every few seconds until you're back (mouse moved or
    a key pressed) or the encounter is over. Then pauses the hunt."""
    every = cfg.get("keepalive_seconds", 5)
    since = time.time()
    while running.is_set() and not web.remote:
        click_at(*lay.pt(*IDLE_SPOT))
        parked = dev.pointer()
        until = time.time() + every
        while time.time() < until:
            time.sleep(0.1)
            if web.remote:
                break
            x, y = dev.pointer()
            if abs(x - parked[0]) + abs(y - parked[1]) > 4 or last_key[0] > since:
                print("  you're back: stopped clicking.", flush=True)
                break
        else:
            gone = 0
            while gone < 3 and not find_mute(load_rgb(grab()), lay.mute):
                gone += 1
                time.sleep(0.4)
            if gone < 3:
                continue
            print("  the encounter ended.", flush=True)
        break
    if running.is_set() and not web.remote:
        running.clear()
        sound("Bottle")
        print("|| paused. Ctrl+Shift+H to hunt again.", flush=True)


def log_encounter(enc, action):
    new = not os.path.exists(LOG)
    with open(LOG, "a", newline="") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["time", "species", "level", "shiny", "tint", "reskin", "gradient", "action"])
        w.writerow([time.strftime("%Y-%m-%d %H:%M:%S"), enc["species"], enc["level"],
                    int(enc["shiny"]), int(enc["tint"]), enc["reskin"], enc["gradient"], action])


def title(enc):
    """Doodle reskin, Mirage gradient, Shiny, Tinted Lapras"""
    traits = [f"{enc['reskin'].title()} reskin" if enc["reskin"] else "",
              f"{enc['gradient'].title()} gradient" if enc["gradient"] else "",
              "Shiny" if enc["shiny"] else "", "Tinted" if enc["tint"] else ""]
    traits = ", ".join(t for t in traits if t)
    return f"{traits} {enc['species']}".strip()


def game_shot(img, lay):
    shot = img.crop(lay.box(PANEL))
    if shot.width > 1200:
        shot = shot.resize((1200, int(shot.height * 1200 / shot.width)), Image.LANCZOS)
    return shot


def found(cfg, enc, img, lay):
    shot = game_shot(img, lay)
    os.makedirs(FINDS_DIR, exist_ok=True)
    shot.save(os.path.join(FINDS_DIR, time.strftime("%Y%m%d-%H%M%S-") + norm(enc["species"]) + ".png"))
    discord(cfg, f"{cfg.get('ping', '')} {title(enc)}".strip(), shot)
    debuglog.debug.shot(f"found: {title(enc)}", shot)
    for name in ("Glass", "Glass", "Glass"):
        sound(name)
        time.sleep(0.4)


def hunt(cfg, args):
    hint, since = None, None
    last_battle = None  # the battle on screen, once read
    _cfg.update(cfg)
    if not args.watch:  # watching never clicks, so nothing to take over
        link_website()
    last_enc = last_status = time.time()
    quiet_said = False
    practice_left = args.practice
    save_due, last_save, last_check = False, 0.0, 0.0
    if args.paused:
        running.clear()
        print("pokehunt paused: Ctrl+Shift+H to start.")
    else:
        focus_roblox()
        print("pokehunt " + ("watching (no clicks)." if args.watch else "hunting."))
    for rule in cfg["targets"]:
        print("  wanted:", ", ".join(f"{k}={v}" for k, v in rule.items()))
    notify = cfg.get("notify", []) + ([{"species": "any"}] if args.debug else [])
    for rule in notify:
        print("  auto-catch:", ", ".join(f"{k}={v}" for k, v in rule.items()))
    if args.debug:
        print("  debug mode: catching every encounter, then saving; a screenshot every catch turn and click")
        debuglog.CLICK_SHOTS = debuglog.SHOTS = 10 ** 9  # a close-up of every click, not just the first 10
    for rule in cfg.get("post", []):
        print("  post only:", ", ".join(f"{k}={v}" for k, v in rule.items()))
    print("  Ctrl+Shift+H  pause / resume")
    if web.link:
        print("  Ctrl+Shift+N  new website code    Ctrl+Shift+L  log every browser out")
    print("Quit: Ctrl-C here, close this window, or fling the mouse into a corner.")
    # started in the middle of a battle: that one's yours (the hunter would act on it at once)
    if running.is_set() and not args.watch and look(None)[2] is not None:
        running.clear()
        print("  a battle is on screen, so pokehunt is paused: Ctrl+Shift+H when it should carry on "
              "(or take over on the website)", flush=True)
        sound("Bottle")
    publish()  # the website sees it at once, not at the first encounter
    while True:
        if not running.is_set() and not web.remote:
            wait_running()
            focus_roblox()
            since = None
            last_enc = last_status = time.time()
        img, rgb, lay = look(hint)
        if time.time() - last_status > 60 * cfg.get("status_every_minutes", 60):
            discord(cfg, f"pokehunt: still hunting, {web.encounters} encounters so far.")
            last_status = time.time()
        if not lay:
            since, last_battle = None, None
            if web.phase != "waiting":
                web.phase = "waiting"
                publish()
            if save_due and not args.watch and time.time() - last_save > SAVE_EVERY:
                save_wait[0] = 0
                if save_game():
                    say("  (game saved)")
                    save_due, last_save = False, time.time()
                else:  # try again in 20 s, or when the game said it can
                    if save_wait[0]:
                        say(f"  (the game says wait {save_wait[0]} s to save)")
                    last_save = time.time() - SAVE_EVERY + max(20, save_wait[0] + 5)
                continue
            say("waiting for an encounter...")
            if not args.watch:
                keep_roblox_in_front()  # a popup may be hiding the encounter
            quiet = time.time() - last_enc
            if not args.watch and quiet > 20 and time.time() - last_check > 20:
                last_check = time.time()
                if clear_leftovers():
                    say("  (closed a save message / menu left open)")
            if hint and not args.watch and time.time() - last_click[0] > AFK_EVERY:
                click_at(*Layout(hint).pt(*AFK_SPOT))
                say("  (anti-AFK click)")
            quiet = time.time() - last_enc
            if quiet > 60 * cfg.get("quiet_alert_minutes", 3) and not quiet_said:
                trouble(f"no encounter for {quiet / 60:.0f} min", img)
                quiet_said = True
            time.sleep(0.2)
            continue
        hint = lay.mute
        since = since or time.time()
        if time.time() - since < SEEN_AFTER:
            time.sleep(0.1)
            continue

        got = read_encounter(hint, clicks=not args.watch, draw=args.calibrate)
        args.calibrate = False
        since = None
        if not got:
            last_battle = None
            continue
        enc, img, lay = got
        # the same battle read again: handed back from the website, or resumed mid-battle
        again = bool(last_battle and same_mon(last_battle, enc))
        last_battle = enc
        if not again:
            web.encounters += 1
            last_enc, quiet_said = time.time(), False
        rule = wanted(enc, cfg["targets"])
        _last_said[0] = None
        web.phase, web.enc, web.wanted, web.msg, web.error = "battle", enc, bool(rule), "", ""
        if not again:
            send_sprite(img, lay, enc)
        action = ("website" if web.remote else "WANTED!" if rule else "-" if args.watch else
                  "practice" if practice_left else "catch" if wanted(enc, notify) else "run")
        if not again:
            print(f"{time.strftime('%H:%M:%S')}  #{web.encounters:<4} {describe(enc):<55} {action}", flush=True)
        publish()
        if web.remote:
            if not again:
                log_encounter(enc, "found" if rule else "website")
                if rule:
                    found(cfg, enc, img, lay)  # you may not be looking at the page
            if remote_battle(cfg, enc, img, lay):
                save_due = cfg.get("save_after_catch", True)
        elif rule:
            if not again:
                log_encounter(enc, "found")
                found(cfg, enc, img, lay)
            if not args.watch:
                keep_alive(cfg, lay)
            else:
                wait_gone(lay)
        elif args.watch:
            if not again:
                log_encounter(enc, "seen")
            wait_gone(lay)
        else:
            practice = practice_left > 0
            if practice or wanted(enc, notify):  # worth catching, not stopping
                practice_left -= practice
                result, details = catch(enc, img, lay, cfg["catch"], first_ball=not practice, debug=args.debug)
                if result == "taken over":
                    continue  # read again: the website battles it
                print(f"  catch: {result} ({details})", flush=True)
                log_encounter(enc, result)
                if result == "caught":
                    save_due = cfg.get("save_after_catch", True)
                    if not practice:
                        discord(cfg, f"Caught: {title(enc)} ({details})", game_shot(img, lay))
                    continue
                if result in ("gave up", "no bag") and not practice and \
                        (result == "no bag" or cfg["catch"].get("on_fail") == "hold"):
                    found(cfg, enc, img, lay)  # couldn't do it alone: ping, hold the battle for you
                    keep_alive(cfg, lay)
                    continue
                if not practice:
                    discord(cfg, f"Couldn't catch ({result}, {details}), ran from: {title(enc)}",
                            game_shot(img, lay))
                if find_mute(load_rgb(grab()), lay.mute):
                    run_away(lay)
                continue
            if not again and wanted(enc, cfg.get("post", [])):  # worth a look, not a ball
                say("  (posted to Discord)")
                discord(cfg, f"Seen: {title(enc)} (ran)", game_shot(img, lay))
            if not again:
                log_encounter(enc, "ran")
            run_away(lay)


def link_website():
    """Connects this hunt to poopihunt.xyz; the code to log in there is printed
    (to this window only) once it's connected."""
    if web.link:
        return
    if not isinstance(sys.stdout, remote.Lines):
        sys.stdout = remote.Lines(sys.stdout, web)
    web.link = remote.Remote(remote.key(KEY_FILE), BUILD, from_website, os.environ.get("HUNT_SITE", remote.SITE))
    web.link.start()
    import atexit
    atexit.register(web.link.stop)


def wait_gone(lay):
    gone = None
    while True:
        if find_mute(load_rgb(grab()), lay.mute):
            gone = None
        else:
            gone = gone or time.time()
            if time.time() - gone > GONE_AFTER:
                return
        time.sleep(0.2)


def system_info():
    """What this computer is, for the debug log."""
    import platform
    from importlib import metadata
    versions = []
    for p in ("numpy", "pillow", "mss", "pyautogui", "pynput", "winrt-runtime", "pyobjc-framework-Vision"):
        try:
            versions.append(f"{p} {metadata.version(p)}")
        except metadata.PackageNotFoundError:
            pass
    return (f"pokehunt on {platform.platform()}, Python {platform.python_version()}, "
            f"{platform.machine()}\n  packages: {', '.join(versions)}")


# What the Mac reads in samples/ (species, shiny, tint, reskin, gradient); a new
# PC should read the same. Levels are left out: the Mac misses a few.
EXPECTED = {
    "dewpider-mirage.png": ("Dewpider", False, False, "", "Mirage"),
    "marill-inferno-tint.png": ("Marill", False, True, "INFERNO", ""),
    "quaxly-shiny.png": ("Quaxly", True, False, "", ""),
    "remoraid-shiny.png": ("Remoraid", True, False, "", ""),
    "shellos-tint.png": ("Shellos", False, True, "", ""),
    "phone-pikachu-shiny.png": ("Pikachu", True, False, "", ""),
    "phone16-pichu-shiny.png": ("Pichu-Spiky-eared", True, False, "", ""),
}


def diagnose(cfg):
    """For a first run on a new PC: is the text read like on the Mac, how big
    is the screen, do mouse positions line up, is Roblox there? Moves the
    mouse a little, never clicks."""
    global CAL_FILE
    print(system_info())
    print("== reading the samples")
    real, CAL_FILE = CAL_FILE, os.path.join(DEBUG_DIR, "calibration-samples.json")
    os.makedirs(DEBUG_DIR, exist_ok=True)
    ok = 0
    for name, want in EXPECTED.items():
        path = os.path.join(HERE, "samples", name)
        if not os.path.exists(path):
            print(f"  {name}: missing")
            continue
        img = Image.open(path).convert("RGB")
        cal.clear()
        use_calibration(img.size)
        rgb = load_rgb(img)
        mute = find_mute(rgb)
        if mute:
            auto_calibrate(img, rgb, mute)
        lay = Layout(mute) if mute else None
        if not lay or not menu_ready(rgb, lay):
            print(f"  {name}: FAILED, no battle found")
            continue
        start = time.time()
        enc = read_enemy(img, rgb, lay)
        got = (enc["species"], enc["shiny"], enc["tint"], enc["reskin"], enc["gradient"])
        same = same_word(want[0], got[0]) and got[1:3] == want[1:3] and \
            all(same_word(w, g) if w else not g for w, g in zip(want[3:], got[3:]))
        ok += same
        print(f"  {name}: {'ok' if same else 'DIFFERENT'}  read {describe(enc)}  ({time.time() - start:.1f} s)"
              + ("" if same else f"  expected {want}"))
        if not same:  # what the OCR saw in the enemy's box, to tell why
            print("    the box's text: " + ", ".join(f"{t!r} at ({x:.2f}, {y:.2f})"
                                                    for t, x, y in ocr(img.crop(lay.box(BOX)))))
    print(f"  {ok} of {len(EXPECTED)} read like on the Mac")
    CAL_FILE = real
    cal.clear()

    print("== the screen")
    img = grab()
    print(f"  screenshot {img.width}x{img.height}, {dev.scale:g} screenshot px per mouse px")
    print("  " + devices.window_info().replace("\n", "\n  "))
    debuglog.debug.shot("what the screen looks like", img)
    rgb = load_rgb(img)
    mute = find_mute(rgb)
    if mute:
        use_calibration(img.size)
        print("  a battle is on screen: calibrating")
        if not auto_calibrate(img, rgb, mute, draw=True):
            print("  couldn't calibrate: the Fight / Switch / Bag / Run menu isn't up")
        lay = Layout(mute)
        if menu_ready(rgb, lay) and box_visible(rgb, lay):
            print(f"  reads: {describe(read_enemy(img, rgb, lay))}")
    else:
        print("  no battle on screen (that's fine; start one and run this again to check calibration)")

    print("== the mouse (moves, no clicks)")
    home = dev.pointer()
    for fx, fy in ((0.3, 0.3), (0.7, 0.3), (0.7, 0.7), (0.3, 0.7)):
        x, y = img.width * fx, img.height * fy
        dev.move(x, y)
        time.sleep(0.15)
        px, py = dev.pointer()
        want = (x / dev.scale, y / dev.scale)
        off = abs(px - want[0]) + abs(py - want[1])
        print(f"  to ({x:.0f}, {y:.0f}): mouse at ({px}, {py}), {'ok' if off <= 3 else f'OFF BY {off:.0f}'}")
    dev.move(home[0] * dev.scale, home[1] * dev.scale)
    print("== done")


def read_image(path, cfg, draw=False):
    img = Image.open(path).convert("RGB")
    use_calibration(img.size)
    rgb = load_rgb(img)
    mute = find_mute(rgb)
    if not mute:
        print(f"{path}: no encounter (no Mute button)")
        return
    if draw and not auto_calibrate(img, rgb, mute, draw=True):
        print(f"{path}: couldn't calibrate: no Fight/Switch/Bag/Run menu found")
    if not cal.get("saved"):  # a screen of another shape: fit it, without saving
        got = calibrate(rgb, mute)
        if got:
            cal.update(got[0])
    lay = Layout(mute)
    if chat_showing(rgb, lay):
        print(f"{path}: encounter, but chat messages cover the enemy's box")
        return
    if not box_visible(rgb, lay):
        print(f"{path}: encounter, but the enemy's box is covered")
        return
    enc = read_enemy(img, rgb, lay)
    rule = wanted(enc, cfg["targets"])
    print(f"{path}: {describe(enc)}  menu {'ready' if menu_ready(rgb, lay) else 'not ready'}"
          f"  -> {'WANTED ' + json.dumps(rule) if rule else 'run'}")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--image", nargs="+", help="read screenshot file(s) instead of the screen")
    ap.add_argument("--watch", action="store_true", help="read and log, never click")
    ap.add_argument("--paused", action="store_true", help="start paused, Ctrl+Shift+H starts")
    ap.add_argument("--debug", action="store_true",
                    help="catch every encounter (then save), a screenshot every catch turn")
    ap.add_argument("--practice", type=int, default=0, metavar="N",
                    help="catch the next N encounters too, without the first-turn ball")
    ap.add_argument("--calibrate", action="store_true",
                    help="fit the layout at the next encounter (or in --image) and draw it")
    ap.add_argument("--test-discord", action="store_true", help="send a test message")
    ap.add_argument("--diagnose", action="store_true",
                    help="check reading, the screen and the mouse on this PC, no clicks")
    ap.add_argument("--config", default=os.path.join(DATA, "config.json"))
    ap.add_argument("--window", action="store_true", help=argparse.SUPPRESS)
    args = ap.parse_args()
    global WINDOW
    WINDOW = args.window
    os.makedirs(DATA, exist_ok=True)
    cfg = load_config(args.config)
    url = cfg.get("debug_webhook", debuglog.URL)
    if url and not debuglog.debug.url:
        debuglog.debug.start(url)
    debuglog.debug.url = url
    debuglog.debug.note(system_info())

    if args.diagnose:
        diagnose(cfg)
        return

    if args.test_discord:
        discord(cfg, "pokehunt is connected. Wanted encounters will show up here.").join()
        return
    if args.image:
        for path in args.image:
            read_image(path, cfg, args.calibrate)
        return
    start_hotkey()
    if WINDOW:
        window_buttons()
    while True:  # unattended: an unexpected error restarts the hunt, never ends it
        try:
            hunt(cfg, args)
        except KeyboardInterrupt:
            print("\nstopped.")
            return
        except Exception as e:
            if type(e).__name__ == "FailSafeException":
                print("\nstopped (mouse flung into a corner).")
                return
            import traceback
            os.makedirs(DEBUG_DIR, exist_ok=True)
            with open(os.path.join(DEBUG_DIR, "errors.log"), "a") as f:
                f.write(time.strftime("\n%Y-%m-%d %H:%M:%S\n") + traceback.format_exc())
            print(f"\n! {type(e).__name__}: {e} (restarting, details in debug/errors.log)", flush=True)
            debuglog.debug.note(traceback.format_exc())
            discord(cfg, f"pokehunt hit an error and restarted: {type(e).__name__}: {e}")
            time.sleep(5)


if __name__ == "__main__":
    main()
