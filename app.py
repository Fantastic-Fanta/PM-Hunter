"""Hunt's window, for Windows (Hunt.exe): Start / Pause / Stop, the website
code, and updates from poopihunt.xyz. What to hunt is config.json in Hunt's
folder, edited by hand (with no rules every reskin with a gradient is caught). Runs on a Mac too
(python app.py), for trying it out.

The hunter itself (pokehunt.py, hunt-engine.exe in Hunt.exe's folder) runs as
a process of its own: what it prints shows in the window, its "@state" lines
set the status, and the buttons go to it as lines (toggle, code, logout).
Closing its input stops it. What a run keeps (config.json, finds, the
website key) is in %LOCALAPPDATA%\\Hunt.

Standard library only (tkinter).
"""
import hashlib
import json
import os
import queue
import re
import subprocess
import sys
import tempfile
import threading
import time
import tkinter as tk
import traceback
import tkinter.font as tkfont
import urllib.parse
import urllib.request
import webbrowser
from tkinter import messagebox

FROZEN = getattr(sys, "frozen", False)
WINDOWS = sys.platform == "win32"
HERE = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
if WINDOWS:
    DATA = os.environ.get("HUNT_DATA") or os.path.join(os.environ["LOCALAPPDATA"], "Hunt")
else:
    DATA = os.environ.get("HUNT_DATA") or HERE
CONFIG = os.path.join(DATA, "config.json")
LOG = os.path.join(DATA, "hunt.log")  # what the hunter prints; hunt.old.log before it
SETTINGS = os.path.join(DATA, "window.json")
SITE = os.environ.get("HUNT_SITE", "https://poopihunt.xyz")
UPDATES = SITE + "/windows.json"  # {"version": 29105678, "name": "23 Oct 18:02", "file": "HuntSetup.exe", "sha256": ...}
try:
    from build_info import VERSION, NAME  # written by the Windows build
except ImportError:
    VERSION, NAME = 0, "dev"

# black on white, and greys
INK, PAPER, GREY, LINE, SOFT = "#111111", "#ffffff", "#8a8a8a", "#e4e4e4", "#f4f4f4"
FONT = "Segoe UI" if WINDOWS else "Helvetica Neue"
WEBHOOK = r"https://((ptb|canary)\.)?discord(app)?\.com/api/webhooks/\d+/\S+$"
MONO = "Consolas" if WINDOWS else "Menlo"
PANEL, ROW = 230, 44  # what ••• adds under the pill: the panel's height, a row's
SLIDE = 0.45  # s, for the pill to grow into the panel or back
KEY = "#ff00fe"  # on Windows the window is see-through where it's this color: all but the pill


def engine(args):
    if FROZEN:
        return [os.path.join(os.path.dirname(sys.executable), "hunt-engine.exe")] + args
    return [sys.executable, "-u", os.path.join(HERE, "pokehunt.py")] + args


def load_settings():
    try:
        with open(SETTINGS) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_json(path, data):
    tmp = path + ".new"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
        f.write("\n")
    os.replace(tmp, path)


class Hunt:
    def __init__(self, root):
        self.root = root
        self.proc = None          # the hunter, while it runs
        self.mode = None          # what it was started for: hunt, watch, debug, diagnose
        self.lines = queue.Queue()
        self.state = {}
        self.code = ""
        self.last_line = ""
        self.settings = load_settings()
        self.last_exit = None     # the word the pill shows after the hunter ended: Done, Stopped
        self.update = None        # windows.json, when there's a newer build
        self.update_pending = False
        self.setup = None         # the downloaded installer
        self.note = ""            # what the pill says instead, while an update downloads
        os.makedirs(DATA, exist_ok=True)
        try:
            if os.path.getsize(LOG) > 5 << 20:
                os.replace(LOG, os.path.join(DATA, "hunt.old.log"))
        except OSError:
            pass
        self.log = open(LOG, "a", encoding="utf-8")
        if not os.path.exists(CONFIG):  # no rules: every reskin with a gradient is caught all the same
            save_json(CONFIG, {"webhook": "", "ping": "@here", "targets": [], "notify": []})
        self.build()
        self.show_state()
        root.protocol("WM_DELETE_WINDOW", self.close)
        root.after(100, self.poll)
        if FROZEN:
            threading.Thread(target=self.check_update, daemon=True).start()

    # ------------------------------------------------------------ the window

    def build(self):
        root = self.root
        root.title("Hunt")
        try:
            icon = os.path.join(HERE, "icon-192.png")  # Hunt.exe's; the website's when run from here
            self.icon = tk.PhotoImage(file=icon if os.path.exists(icon) else
                                      os.path.join(HERE, "site", "app", "icon-192.png"))
            root.iconphoto(True, self.icon)
        except tk.TclError:
            self.icon = None

        # a pill with no frame: the window's own color is see-through around it.
        # ••• grows it down into a panel: the canvas holds both, and the window
        # shows as much of it as is open.
        root.overrideredirect(True)
        u = self.u = max(1.0, root.winfo_fpixels("1i") / 96)  # Windows' display scaling
        self.W, self.H = round(360 * u), round(52 * u)
        self.full = self.H + round(PANEL * u)
        self.open, self.extra, self.found_now = False, 0.0, False
        if WINDOWS:
            clear = KEY
            root.attributes("-transparentcolor", KEY)
        else:
            clear = "systemTransparent"
            root.attributes("-transparent", True)
        root.configure(bg=clear)
        c = self.canvas = tk.Canvas(root, width=self.W, height=self.full, bg=clear, highlightthickness=0, bd=0)
        self.font = tkfont.Font(family=MONO, size=11, weight="bold")
        self.label_font = tkfont.Font(family=FONT, size=10)
        self.small_font = tkfont.Font(family=FONT, size=9)

        def box(var, on_in, on_out):
            e = tk.Entry(c, textvariable=var, font=(FONT, 10), relief="flat", bd=0, highlightthickness=0)
            e.bind("<FocusIn>", on_in)
            e.bind("<Button-1>", lambda ev: e.focus_force())
            e.bind("<FocusOut>", on_out)
            e.bind("<Return>", lambda ev: c.focus_set())
            return e

        self.hook, self.hook_open = self.hook_saved(), False
        self.hook_var = tk.StringVar(value="Webhook on" if self.hook else "Discord webhook")
        self.hook_box = box(self.hook_var, self.hook_in, self.hook_out)
        self.hook_box.bind("<Escape>", lambda e: (self.hook_var.set(self.hook), c.focus_set()))
        self.hook_box.bind("<Button-3>", self.hook_paste)  # right click pastes
        self.hook_box.bind("<Button-2>", self.hook_paste)  # (a Mac's right click)
        self.ball = self.ball_saved()
        self.ball_var = tk.StringVar(value=self.ball)
        self.ball_box = box(self.ball_var, lambda e: self.ball_box.select_range(0, "end"), self.ball_out)

        sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
        x, y = self.settings.get("pos") or (0, -1)
        if not (0 <= x <= sw - self.W // 2 and 0 <= y <= sh - self.H):
            x, y = (sw - self.W) // 2, round(60 * u)  # top middle: room under it for the panel
        self.pos = [x, y]
        self.place()

        self.hover = None
        actions = {"main": self.main, "more": self.more, "code": self.open_site, "debug": self.flip_debug,
                   "test": self.flip_test, "update": self.do_update, "folder": lambda: self.open_folder(""),
                   "quit": self.close}
        for tag, action in actions.items():
            c.tag_bind(tag, "<Enter>", lambda e, t=tag: self.hovering(t))
            c.tag_bind(tag, "<Leave>", lambda e: self.hovering(None))
            c.tag_bind(tag, "<Button-1>", lambda e, a=action: a())
        c.bind("<Button-1>", lambda e: c.focus_set(), add="+")  # a click off a box leaves it
        c.tag_bind("drag", "<ButtonPress-1>", self.drag_start)
        c.tag_bind("drag", "<B1-Motion>", self.drag)
        c.tag_bind("drag", "<ButtonRelease-1>", self.drag_end)

        self.debug = self.settings.get("debug", False)  # Debug mode: every encounter caught, screenshots
        self.test = False                               # Catch test: the next encounter caught, then off
        self.reports = self.settings.get("reports", True)
        root.after(10, self.frameless)

    def frameless(self):
        """Shown again, without the frame (a Mac keeps it until then). On Windows a
        window without one isn't in the taskbar or Alt+Tab by itself: it's put there."""
        if WINDOWS:
            import ctypes
            user32 = ctypes.windll.user32
            hwnd = user32.GetParent(self.root.winfo_id())
            style = user32.GetWindowLongW(hwnd, -20)  # GWL_EXSTYLE
            user32.SetWindowLongW(hwnd, -20, (style & ~0x80) | 0x40000)  # not WS_EX_TOOLWINDOW, WS_EX_APPWINDOW
        self.root.withdraw()
        self.root.after(10, self.root.deiconify)

    def place(self):
        """The window over the pill and as much of the panel as is out, and the
        pill's outline around both. Lifted only if the panel would leave the screen."""
        x, y = self.pos
        h = round(self.H + self.extra)
        over = max(0, y + h - (self.root.winfo_screenheight() - round(48 * self.u)))
        self.root.geometry(f"{self.W}x{h}+{round(x)}+{round(y - over)}")
        self.canvas.place(x=0, y=0)
        self.outline(h)

    def outline(self, h):
        c, W = self.canvas, self.W
        c.delete("shape")
        fill, edge = (PAPER, "#cfcfcf") if self.found_now else (INK, "#3a3a3a")
        self.rounded(0, 0, W, h, edge, ("shape", "drag"), self.H / 2)  # a hairline around it, for dark games
        self.rounded(1, 1, W - 1, h - 1, fill, ("shape", "drag"), self.H / 2 - 1)
        c.tag_lower("shape")

    def rounded(self, x0, y0, x1, y1, color, tags=(), r=None):
        c = self.canvas
        r = min(r or (y1 - y0) / 2, (y1 - y0) / 2)
        c.create_oval(x0, y0, x0 + 2 * r, y0 + 2 * r, fill=color, outline="", tags=tags)
        c.create_oval(x1 - 2 * r, y0, x1, y0 + 2 * r, fill=color, outline="", tags=tags)
        c.create_oval(x0, y1 - 2 * r, x0 + 2 * r, y1, fill=color, outline="", tags=tags)
        c.create_oval(x1 - 2 * r, y1 - 2 * r, x1, y1, fill=color, outline="", tags=tags)
        c.create_rectangle(x0 + r, y0, x1 - r, y1, fill=color, outline="", tags=tags)
        c.create_rectangle(x0, y0 + r, x1, y1 - r, fill=color, outline="", tags=tags)

    def more(self):
        """••• grows the pill down into the panel, or back."""
        self.open = not self.open
        self.canvas.itemconfig("panel", state="hidden")  # shown once it's all out
        if not self.open:
            self.canvas.focus_set()
        start, goal, t0 = self.extra, (self.full - self.H) if self.open else 0, time.time()

        def step():
            t = min(1.0, (time.time() - t0) / SLIDE)
            k = 4 * t ** 3 if t < 0.5 else 1 - (-2 * t + 2) ** 3 / 2  # eased in and out
            self.extra = start + (goal - start) * k
            self.place()
            if self.open and t >= 0.8:
                self.canvas.itemconfig("panel", state="normal")  # its settings, as it comes to rest
            if t < 1:
                self.root.after(8, step)
        self.drawn = None
        self.draw()
        step()

    def drag_start(self, e):
        self.grabbed = (e.x_root - self.pos[0], e.y_root - self.pos[1])

    def drag(self, e):
        dx, dy = self.grabbed
        self.pos = [e.x_root - dx, e.y_root - dy]
        self.place()

    def drag_end(self, e):
        self.settings["pos"] = [round(v) for v in self.pos]
        save_json(SETTINGS, self.settings)

    def hovering(self, tag):
        """Under the mouse: lighter. Recolored, not drawn again, or the click
        after it would miss what it was on."""
        self.hover = tag
        c = self.canvas
        c.config(cursor="hand2" if tag and tag in self.lit else "")
        for t, (item, normal, lit) in self.lit.items():
            c.itemconfig(item, fill=lit if t == tag else normal)

    def open_folder(self, name):
        path = os.path.join(DATA, name)
        os.makedirs(path, exist_ok=True)
        if WINDOWS:
            os.startfile(path)
        else:
            subprocess.Popen(["open", path])

    def show_state(self):
        """The pill: start / stop, the website code (or a word: Done, Stopped),
        the webhook box. A find turns it white."""
        running = self.proc is not None
        s, mode = self.state, self.mode
        self.found_now = bool(running and s.get("phase") == "battle" and s.get("wanted") and not s.get("remote"))
        web_on = running and mode in ("hunt", "debug", "calibrate")
        self.code_text = self.code if web_on and self.code else "" if running else self.last_exit or ""
        if not running:
            self.main_icon = "play"
        elif s and not s.get("running") and not s.get("remote") and mode != "diagnose":
            self.main_icon = "play"  # paused: by a find, or Ctrl+Shift+H
        else:
            self.main_icon = "stop"
        self.draw()

    def draw(self):
        drawn = (self.code_text, self.main_icon, self.found_now, self.open, self.debug, self.test,
                 bool(self.update), self.note)
        if drawn == getattr(self, "drawn", None):
            return  # the same again: left alone, so a click on it isn't lost
        self.drawn = drawn
        c, u, W, H = self.canvas, self.u, self.W, self.H
        c.delete("all")
        self.lit = {}  # what the mouse lights up: tag -> (item, color, lit color)
        pill = self.rounded

        def circle(cx, cy, r, color, tag):
            return c.create_oval(cx - r, cy - r, cx + r, cy + r, fill=color, outline="", tags=tag)

        light = self.found_now  # a find turns it white
        fill, ink, dim, edge = (PAPER, INK, "#6a6a6a", "#cfcfcf") if light else (INK, PAPER, "#8a8a8a", "#3a3a3a")
        hover_bg, box = ("#e8e8e8", "#efefef") if light else ("#2e2e2e", "#222222")
        self.outline(round(H + self.extra))

        # ---- the pill: start / stop, the website code, the webhook box, •••
        cy = H / 2
        x = 25 * u
        self.lit["main"] = (circle(x, cy, 17 * u, ink, "main"), ink, dim)
        if self.main_icon == "play":
            c.create_polygon(x - 4 * u, cy - 6.5 * u, x - 4 * u, cy + 6.5 * u, x + 7 * u, cy, fill=fill,
                             outline="", tags="main")
        else:
            c.create_rectangle(x - 5.5 * u, cy - 5.5 * u, x + 5.5 * u, cy + 5.5 * u, fill=fill, outline="",
                               tags="main")
        left = x + 31 * u
        code = self.code_text or "------"
        color = ink if self.code and code == self.code else dim if self.code_text else edge
        self.lit["code"] = (c.create_text(left, cy, text=code, anchor="w", font=self.font, fill=color, tags="code"),
                            color, ink if code == self.code else color)
        x = W - round(28 * u)
        self.lit["more"] = (circle(x, cy, 15 * u, hover_bg if self.open else fill, "more"),
                            hover_bg if self.open else fill, hover_bg)
        for dx in (-6, 0, 6):
            circle(x + dx * u, cy, 1.8 * u, ink, "more")
        if self.update:
            circle(x + 10 * u, cy - 9 * u, 2.4 * u, ink, "more")  # an update is waiting in the panel
        x0, x1 = left + 92 * u, x - 20 * u
        pill(x0, cy - 14 * u, x1, cy + 14 * u, box, "hook")
        self.hook_box.config(bg=box, fg=ink if self.hook_open else dim, insertbackground=ink,
                             selectbackground=dim, selectforeground=fill)
        c.create_window(x0 + 12 * u, cy, window=self.hook_box, anchor="w", width=x1 - x0 - 24 * u, height=22 * u)

        # ---- the panel, under it inside the same outline
        pad = 22 * u
        shown = "normal" if self.open and self.extra >= self.full - H - 0.5 else "hidden"
        c.create_line(pad, H, W - pad, H, fill=edge, tags=("panel", "drag"))

        def row(i, label):
            y = H + 6 * u + ROW * u * (i + 0.5)
            c.create_text(pad, y, text=label, anchor="w", font=self.label_font, fill=ink, tags=("panel", "drag"))
            return y

        def switch(y, on, tag):
            x1 = W - pad
            x0 = x1 - 40 * u
            track, knob = (ink, fill) if on else (edge, dim)
            pill(x0, y - 11 * u, x1, y + 11 * u, track, ("panel", tag))
            circle(x1 - 11 * u if on else x0 + 11 * u, y, 8 * u, knob, ("panel", tag))
            self.lit[tag] = (c.create_rectangle(x0, y - 11 * u, x1, y + 11 * u, fill="", outline="",
                                                tags=("panel", tag)), "", "")

        switch(row(0, "Debug mode"), self.debug, "debug")
        switch(row(1, "Catch test"), self.test, "test")
        y = row(2, "Preferred ball")
        bx0, bx1 = W - pad - 150 * u, W - pad
        pill(bx0, y - 14 * u, bx1, y + 14 * u, box, ("panel", "ball"))
        self.ball_box.config(bg=box, fg=ink, insertbackground=ink, selectbackground=dim, selectforeground=fill)
        c.create_window(bx0 + 12 * u, y, window=self.ball_box, anchor="w", width=bx1 - bx0 - 24 * u, height=22 * u,
                        tags="panel")
        y = row(3, "Update")
        if self.update:
            words = self.note or "Install"
            bx0 = W - pad - max(90 * u, self.label_font.measure(words) + 32 * u)
            pill(bx0, y - 15 * u, W - pad, y + 15 * u, ink, ("panel", "update"))
            c.create_text((bx0 + W - pad) / 2, y, text=words, font=self.label_font, fill=fill,
                          tags=("panel", "update"))
            self.lit["update"] = (c.create_rectangle(bx0, y - 15 * u, W - pad, y + 15 * u, fill="", outline="",
                                                     tags=("panel", "update")), "", "")
        else:
            c.create_text(W - pad, y, text="Up to date", anchor="e", font=self.label_font, fill=dim,
                          tags=("panel", "drag"))
        y = self.full - 24 * u
        c.create_text(pad + 4 * u, y, text=f"Build {NAME}", anchor="w", font=self.small_font, fill=edge,
                      tags=("panel", "drag"))
        quit_ = c.create_text(W - pad - 4 * u, y, text="Quit", anchor="e", font=self.small_font, fill=dim,
                              tags=("panel", "quit"))
        self.lit["quit"] = (quit_, dim, ink)
        x = c.bbox(quit_)[0] - 18 * u
        self.lit["folder"] = (c.create_text(x, y, text="Folder", anchor="e", font=self.small_font, fill=dim,
                                            tags=("panel", "folder")), dim, ink)
        c.itemconfig("panel", state=shown)
        self.hovering(self.hover)

    # ------------------------------------------------------------ the panel's settings

    def flip_debug(self):
        """Debug mode: every encounter is caught, with screenshots for the
        developer. A hunt that's on starts again the new way."""
        self.debug = not self.debug
        self.settings["debug"] = self.debug
        save_json(SETTINGS, self.settings)
        self.draw()
        if self.proc is not None and self.mode in ("hunt", "debug"):
            self.restart(self.hunt_mode())

    def flip_test(self):
        """Catch test: the next encounter is caught (then the game saved), and
        it turns itself off. Before a hunt, it waits for one."""
        self.test = not self.test
        self.send("test on" if self.test else "test off")
        self.draw()

    def hunt_mode(self):
        return "debug" if self.debug else "hunt"

    def restart(self, mode):
        self.stop(wait=True)
        self.proc = None
        self.start(mode)

    def ball_saved(self):
        try:
            with open(CONFIG, encoding="utf-8") as f:
                catch = json.load(f).get("catch", {})
            return catch.get("first_ball") if catch.get("first_ball") == "Park Ball" else \
                catch.get("awake_ball") or "Repeat Ball"
        except (OSError, ValueError, AttributeError):
            return "Repeat Ball"

    def ball_out(self, e=None):
        """The preferred ball, saved in config.json's catch plan: a Quick Ball
        first, then this one. A Park Ball is the only ball (it never misses)."""
        words = self.ball_var.get().title().split()
        if words and not words[-1].endswith("Ball"):
            words.append("Ball")
        ball = " ".join(words) or "Repeat Ball"
        self.ball_var.set(ball)
        if ball == self.ball:
            return
        try:
            with open(CONFIG, encoding="utf-8") as f:
                cfg = json.load(f)
            catch = dict(cfg.get("catch", {}))
        except (OSError, ValueError, AttributeError):
            self.ball_var.set(self.ball)
            return
        for k in ("first_ball", "awake_ball", "asleep_ball", "new_ball"):
            catch.pop(k, None)  # the defaults: a Quick Ball, then Repeat Balls (Dive Balls for new ones)
        if ball == "Park Ball":
            catch.update(first_ball=ball, awake_ball=ball, asleep_ball=ball, new_ball=ball)
        elif ball != "Repeat Ball":
            catch.update(awake_ball=ball, asleep_ball=ball, new_ball=ball)
        save_json(CONFIG, {**cfg, "catch": catch})
        self.ball = ball
        if self.proc is not None and self.mode in ("hunt", "debug"):
            self.restart(self.mode)

    # ------------------------------------------------------------ the webhook

    def hook_saved(self):
        try:
            with open(CONFIG, encoding="utf-8") as f:
                hook = json.load(f).get("webhook", "")
            return hook if isinstance(hook, str) else ""
        except (OSError, ValueError, AttributeError):
            return ""

    def hook_say(self, words=None):
        """The box when it isn't being typed in: whether there's a webhook."""
        self.hook_open = False
        self.hook_var.set(words or ("Webhook on" if self.hook else "Discord webhook"))
        self.drawn = None
        self.draw()

    def hook_in(self, e=None):
        if not self.hook_open:
            self.hook_open = True
            self.hook_var.set(self.hook)
            self.hook_box.select_range(0, "end")
            self.hook_box.icursor("end")
            self.drawn = None
            self.draw()

    def hook_out(self, e=None):
        """Typed in, then left (or Enter): saved in config.json, and a hunt that's
        on starts again with it."""
        if not self.hook_open:
            return
        url = self.hook_var.get().strip()
        if url == self.hook:
            return self.hook_say()
        if url and not re.match(WEBHOOK, url):
            self.hook_say("Not a Discord webhook")
            self.root.after(2500, lambda: self.hook_open or self.hook_say())
            return
        try:
            with open(CONFIG, encoding="utf-8") as f:
                cfg = json.load(f)
            if not isinstance(cfg, dict):
                raise ValueError
        except (OSError, ValueError):
            self.hook_say("config.json has a mistake")
            return
        save_json(CONFIG, {**cfg, "webhook": url})
        self.hook = url
        self.hook_say()
        if self.proc is not None and self.mode in ("hunt", "debug"):
            self.restart(self.mode)

    def hook_paste(self, e=None):
        self.hook_in()
        try:
            self.hook_var.set(self.root.clipboard_get().strip())
        except tk.TclError:
            pass
        self.hook_box.icursor("end")
        return "break"

    @staticmethod
    def fit(text, font, width):
        if font.measure(text) <= width:
            return text
        while text and font.measure(text + "…") > width:
            text = text[:-1]
        return text.rstrip() + "…"

    # ------------------------------------------------------------ the hunter

    def main(self):
        if self.proc is None:
            self.start(self.hunt_mode())
        elif self.main_icon == "play":
            self.send("toggle")  # paused (a find, or Ctrl+Shift+H): carry on
        else:
            self.stop()

    def open_site(self):
        if self.code and self.code_text == self.code:
            webbrowser.open(SITE + "/app")

    def start(self, mode):
        if self.proc is not None:
            if mode in ("hunt", "debug"):
                return
            if not messagebox.askyesno("Hunt", "Stop the hunt that's running and do this instead?"):
                return
            self.stop(wait=True)
        args = {"hunt": [], "watch": ["--watch"], "debug": ["--debug"], "diagnose": ["--diagnose"],
                "calibrate": ["--calibrate"]}[mode] + (["--practice", "1"] if self.test else [])
        env = dict(os.environ, HUNT_DATA=DATA, PYTHONUNBUFFERED="1", PYTHONIOENCODING="utf-8")
        if not self.reports:
            env["HUNT_DEBUG"] = "0"
        try:
            self.proc = subprocess.Popen(
                engine(args + ["--window", "--config", CONFIG]), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, env=env, cwd=DATA, text=True, encoding="utf-8", errors="replace",
                bufsize=1, creationflags=subprocess.CREATE_NO_WINDOW if WINDOWS else 0)
        except OSError as e:
            messagebox.showerror("Hunt", f"Couldn't start the hunter: {e}")
            return
        self.mode, self.state, self.code, self.last_exit = mode, {}, "", None
        self.add_line(f"\n--- {time.strftime('%Y-%m-%d %H:%M:%S')} {mode} (build {NAME}) ---")
        threading.Thread(target=self.read, args=(self.proc,), daemon=True).start()
        self.show_state()

    def read(self, proc):
        for line in proc.stdout:
            self.lines.put((proc, line.rstrip("\n")))
        proc.wait()
        self.lines.put((proc, None))

    def send(self, cmd):
        if self.proc is None:
            return
        try:
            self.proc.stdin.write(cmd + "\n")
            self.proc.stdin.flush()
        except OSError:
            pass

    def stop(self, wait=False):
        proc = self.proc
        if proc is None:
            return
        try:
            proc.stdin.close()  # the hunter stops when its input ends
        except OSError:
            pass

        def kill_later():
            try:
                proc.wait(6)
            except subprocess.TimeoutExpired:
                proc.kill()

        if wait:
            kill_later()
        else:
            threading.Thread(target=kill_later, daemon=True).start()

    def poll(self):
        changed = False
        try:
            while True:
                proc, line = self.lines.get_nowait()
                if proc is not self.proc:
                    continue
                changed = True
                if line is None:
                    self.ended(proc)
                elif line.startswith("@state "):
                    try:
                        self.got_state(json.loads(line[7:]))
                    except ValueError:
                        pass
                else:
                    m = re.search(r"Website code: (\w+)", line)
                    if m:
                        self.code = m.group(1)
                    else:
                        if "a browser logged in with the code" in line:
                            self.code = ""
                        self.add_line(line)
        except queue.Empty:
            pass
        if changed:
            self.show_state()
        self.root.after(100, self.poll)

    def got_state(self, s):
        was = self.state
        self.state = s
        if "test" in s:
            self.test = bool(s["test"])  # off once its encounter is caught
        if s.get("phase") == "battle" and s.get("wanted") and not (was.get("wanted") and was.get("enc") == s.get("enc")):
            # it's holding the battle: catch it in Roblox, then Resume
            self.add_line(f"Found: {s.get('enc')}. Catch it in Roblox, then press Resume.")
            self.root.deiconify()
            self.root.lift()
            self.root.bell()

    def ended(self, proc):
        code = proc.returncode
        if self.mode == "diagnose":
            self.last_exit = "Done"  # what it found is in hunt.log
        elif code not in (0, None) and self.last_line and "stopped" not in self.last_line:
            self.last_exit = "Stopped"  # why is in hunt.log
        else:
            self.last_exit = None
        self.proc, self.state, self.code, self.test = None, {}, "", False
        if self.update_pending:
            self.install_update()

    def add_line(self, line):
        if not line.strip():
            return
        self.last_line = line.strip()
        try:
            self.log.write(line + "\n")
            self.log.flush()
        except OSError:
            pass

    def close(self):
        if self.proc is not None:
            if not messagebox.askyesno("Hunt", "Stop hunting and close Hunt?"):
                return
            self.stop(wait=True)
        self.root.destroy()

    # ------------------------------------------------------------ updates

    def check_update(self):
        """Once at the start and then every 6 hours: a newer build on the website?"""
        while True:
            try:
                req = urllib.request.Request(UPDATES, headers={"User-Agent": f"Hunt (windows, {NAME})"})
                with urllib.request.urlopen(req, timeout=15) as r:
                    info = json.loads(r.read())
                if int(info.get("version", 0)) > VERSION:
                    self.root.after(0, self.offer_update, info)
            except Exception:
                pass
            time.sleep(6 * 3600)

    def offer_update(self, info):
        self.update = info
        self.draw()

    def do_update(self):
        if self.proc is not None:
            if not messagebox.askyesno("Hunt", "Updating stops the hunt for a minute. Update now?"):
                return
        self.set_note("Downloading")
        threading.Thread(target=self.download_update, daemon=True).start()

    def download_update(self):
        info = self.update
        try:
            url = urllib.parse.urljoin(UPDATES, info.get("file", "HuntSetup.exe"))
            path = os.path.join(tempfile.gettempdir(), f"HuntSetup-{info['version']}.exe")
            req = urllib.request.Request(url, headers={"User-Agent": f"Hunt (windows, {NAME})"})
            digest = hashlib.sha256()
            with urllib.request.urlopen(req, timeout=60) as r, open(path + ".part", "wb") as f:
                total, got = int(r.headers.get("Content-Length") or 0), 0
                while True:
                    chunk = r.read(1 << 16)
                    if not chunk:
                        break
                    f.write(chunk)
                    digest.update(chunk)
                    got += len(chunk)
                    if total:
                        self.root.after(0, self.set_note, f"{100 * got // total}%")
            if info.get("sha256") and digest.hexdigest() != info["sha256"]:
                raise ValueError("the download came out different from the website's (try again)")
            os.replace(path + ".part", path)
            self.setup = path
            self.root.after(0, self.downloaded)
        except Exception as e:
            self.root.after(0, self.update_failed, e)

    def set_note(self, note):
        self.note = note
        self.show_state()

    def update_failed(self, e):
        self.set_note("")
        messagebox.showerror("Hunt", f"The update didn't download: {e}")

    def downloaded(self):
        self.update_pending = True
        self.set_note("Installing")
        if self.proc is not None:
            self.stop()  # ended() installs, once the hunter is gone
        else:
            self.install_update()

    def install_update(self):
        """The installer replaces Hunt's folder and starts the new Hunt; this one gets out of its way."""
        self.update_pending = False
        if WINDOWS:
            subprocess.Popen([self.setup, "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART"],
                             creationflags=subprocess.DETACHED_PROCESS)
        self.root.destroy()


def single_instance():
    """One Hunt at a time: two would fight over the mouse."""
    if not WINDOWS:
        return True
    import ctypes
    ctypes.windll.kernel32.CreateMutexW(None, False, "Local\\HuntWindow")
    return ctypes.windll.kernel32.GetLastError() != 183  # ERROR_ALREADY_EXISTS


def main():
    if WINDOWS:
        import ctypes
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)  # sharp text on scaled screens
        except (AttributeError, OSError):
            pass
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("poopihunt.Hunt")  # its own taskbar icon
    root = tk.Tk()
    if not single_instance():
        root.withdraw()
        messagebox.showinfo("Hunt", "Hunt is already open: look for the Poke Ball in the taskbar.")
        return
    hunt = Hunt(root)
    if "--smoke" in sys.argv:
        root.after(1500, smoke, hunt)
    root.mainloop()


def smoke(hunt):
    """The Windows build's check (--smoke): the pill comes up and goes through
    every state, the webhook box and the panel, then Hunt closes. Anything
    that went wrong is in smoke.txt, and Hunt.exe ends with 1."""
    root, errors = hunt.root, []
    root.report_callback_exception = lambda *exc: errors.append("".join(traceback.format_exception(*exc)))

    class Running:  # stands in for the hunter
        stdin = None

    states = [("hunt", {}, ""), ("hunt", {"running": True, "count": 12, "phase": "wild"}, "AB12C"),
              ("hunt", {"running": True, "count": 13, "phase": "battle", "wanted": True, "enc": "Shiny Tympole"},
               "AB12C"), ("hunt", {"running": False, "count": 13}, "AB12C"), ("watch", {"running": True}, ""),
              ("hunt", {"remote": True, "running": True}, "AB12C"), ("diagnose", {}, "")]
    try:
        for mode, state, code in states:
            hunt.proc, hunt.mode, hunt.code = Running(), mode, code
            hunt.got_state(state)
            hunt.show_state()
            for tag in ("main", "stop", "more", "code", None):
                hunt.hovering(tag)
            root.update()
        hunt.proc = None
        hunt.show_state()
        for url in ("not a webhook", "https://discord.com/api/webhooks/1/smoke", ""):
            hunt.hook_in()
            hunt.hook_var.set(url)
            hunt.hook_out()
            root.update()
        hunt.offer_update({"version": VERSION + 1, "name": "smoke"})

        def settle(seconds=SLIDE + 0.2):  # the pill grows into the panel
            end = time.time() + seconds
            while time.time() < end:
                root.update()
                time.sleep(0.01)
        hunt.more()
        settle()
        for flip in (hunt.flip_debug, hunt.flip_debug, hunt.flip_test, hunt.flip_test):
            flip()
            root.update()
        balls = []
        for ball in ("park", "ultra ball", ""):
            hunt.ball_var.set(ball)
            hunt.ball_out()
            with open(CONFIG, encoding="utf-8") as f:
                balls.append(json.load(f).get("catch", {}).get("awake_ball", "-"))
        if balls != ["Park Ball", "Ultra Ball", "-"]:
            errors.append(f"the preferred ball saved as {balls}")
        for line in ("11:38:34  #60   Marill Lv51  (reskin KYOBLOXER, gradient IMPULSIVE BEAST) catch",
                     "  catch: caught (1 ball(s), last Quick Ball, 1 turns)", "|| paused (Ctrl+Shift+H to resume)",
                     "  (no encounter for 31 min, screenshot: C:\\Hunt\\debug\\x.png)", "waiting for an encounter..."):
            hunt.add_line(line)
        root.update()
        try:  # a picture of it, with what's behind it: only the Windows build asks (HUNT_PICTURE)
            if not os.environ.get("HUNT_PICTURE"):
                raise ImportError
            from PIL import ImageGrab
            x, y = root.winfo_rootx(), root.winfo_rooty()
            ImageGrab.grab(bbox=(x - 20, y - 20, x + root.winfo_width() + 20, y + root.winfo_height() + 20)
                           ).save(os.path.join(DATA, "pill.png"))
        except Exception:
            pass
        size = f"ok: the pill is {root.winfo_width()} x {root.winfo_height()} at {root.winfo_x()}, {root.winfo_y()}\n"
    except Exception:
        errors.append(traceback.format_exc())
    with open(os.path.join(DATA, "smoke.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(errors) or size)
    root.destroy()
    if errors:
        sys.exit(1)


if __name__ == "__main__":
    main()
