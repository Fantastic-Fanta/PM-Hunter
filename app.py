"""Hunt's window, for Windows (Hunt.exe): Start / Pause / Stop, what to hunt,
the website code, and updates from poopihunt.xyz. Runs on a Mac too
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
SETTINGS = os.path.join(DATA, "window.json")
SITE = os.environ.get("HUNT_SITE", "https://poopihunt.xyz")
UPDATES = SITE + "/windows.json"  # {"version": 29105678, "name": "23 Oct 18:02", "file": "HuntSetup.exe", "sha256": ...}
try:
    from build_info import VERSION, NAME  # written by the Windows build
except ImportError:
    VERSION, NAME = 0, "dev"
RULE_FIELDS = {"species", "shiny", "tint", "reskin", "gradient", "not_species"}  # as pokehunt.py's FIELDS

# black on white, and greys
INK, PAPER, GREY, LINE, SOFT = "#111111", "#ffffff", "#8a8a8a", "#e4e4e4", "#f4f4f4"
FONT = "Segoe UI" if WINDOWS else "Helvetica Neue"
MONO = "Consolas" if WINDOWS else "Menlo"
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


def check_config(cfg):
    """What's wrong with a config the hunter would refuse, or ""."""
    if not isinstance(cfg, dict):
        return "It has to start with { and end with }."
    if not cfg.get("targets"):
        return 'There are no "targets": nothing to hunt for.'
    for group in ("targets", "notify", "post"):
        rules = cfg.get(group, [])
        if not isinstance(rules, list) or not all(isinstance(r, dict) for r in rules):
            return f'"{group}" has to be a list of {{ ... }} rules.'
        for rule in rules:
            bad = set(rule) - RULE_FIELDS
            if bad:
                return (f'"{group}" has a rule with {", ".join(sorted(bad))}, which Hunt doesn\'t know. '
                        f'Rules can say {", ".join(sorted(RULE_FIELDS))}.')
    return ""


class Button(tk.Label):
    """A flat button that looks the same on Windows and on a Mac: black (the
    one that matters) or outlined."""

    def __init__(self, parent, text, command, filled=False, size=10, padx=16, pady=7):
        super().__init__(parent, text=text, cursor="hand2", padx=padx, pady=pady if filled else pady - 1,
                         font=(FONT, size, "bold" if filled else "normal"), highlightthickness=0 if filled else 1,
                         highlightbackground=LINE, highlightcolor=LINE)
        self.command, self.filled, self.enabled = command, filled, True
        self.bind("<Button-1>", lambda e: self.enabled and self.command())
        self.bind("<Enter>", lambda e: self.enabled and self.config(bg="#3a3a3a" if filled else SOFT))
        self.bind("<Leave>", lambda e: self.paint())
        self.paint()

    def paint(self):
        if self.filled:
            bg, fg = (INK, PAPER) if self.enabled else (LINE, GREY)
        else:
            bg, fg = PAPER, INK if self.enabled else GREY
        self.config(bg=bg, fg=fg, cursor="hand2" if self.enabled else "arrow")

    def set(self, text=None, enabled=None):
        if text is not None:
            self.config(text=text)
        if enabled is not None:
            self.enabled = enabled
        self.paint()


class Link(tk.Label):
    """Grey text that's a button: black under the mouse."""

    def __init__(self, parent, text, command, bg=PAPER, size=10, fg=GREY):
        super().__init__(parent, text=text, bg=bg, fg=fg, cursor="hand2", font=(FONT, size))
        self.bind("<Button-1>", lambda e: command())
        self.bind("<Enter>", lambda e: self.config(fg=INK))
        self.bind("<Leave>", lambda e: self.config(fg=fg))


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
        self.last_exit = None     # (title, detail) the status shows after the hunter ended
        self.update = None        # windows.json, when there's a newer build
        self.update_pending = False
        self.setup = None         # the downloaded installer
        self.note = ""            # what the pill says instead, while an update downloads
        os.makedirs(DATA, exist_ok=True)
        if not os.path.exists(CONFIG):
            with open(os.path.join(HERE, "config.example.json"), encoding="utf-8") as f:
                cfg = json.load(f)
            cfg["webhook"] = ""
            save_json(CONFIG, cfg)
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

        # a pill with no frame: the window's own color is see-through around it
        root.overrideredirect(True)
        u = self.u = max(1.0, root.winfo_fpixels("1i") / 96)  # Windows' display scaling
        self.W, self.H = round(380 * u), round(52 * u)
        if WINDOWS:
            clear = KEY
            root.attributes("-transparentcolor", KEY)
        else:
            clear = "systemTransparent"
            root.attributes("-transparent", True)
        root.configure(bg=clear)
        c = self.canvas = tk.Canvas(root, width=self.W, height=self.H, bg=clear, highlightthickness=0, bd=0)
        c.pack()
        self.title_font = tkfont.Font(family=FONT, size=11, weight="bold")
        self.sub_font = tkfont.Font(family=FONT, size=10)
        self.code_font = tkfont.Font(family=MONO, size=10, weight="bold")
        sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
        x, y = self.settings.get("pos") or (0, -1)
        if not (0 <= x <= sw - self.W // 2 and 0 <= y <= sh - self.H):
            x, y = (sw - self.W) // 2, sh - self.H - round(90 * u)  # bottom middle, above the taskbar
        root.geometry(f"{self.W}x{self.H}+{x}+{y}")

        self.hover = None
        actions = {"main": self.main, "stop": self.stop, "more": self.more,
                   "code": lambda: webbrowser.open(SITE + "/app")}
        for tag, action in actions.items():
            c.tag_bind(tag, "<Enter>", lambda e, t=tag: self.hovering(t))
            c.tag_bind(tag, "<Leave>", lambda e: self.hovering(None))
            c.tag_bind(tag, "<Button-1>", lambda e, a=action: a())
        c.tag_bind("drag", "<ButtonPress-1>", self.drag_start)
        c.tag_bind("drag", "<B1-Motion>", self.drag)
        c.tag_bind("drag", "<ButtonRelease-1>", self.drag_end)

        self.log_win = tk.Toplevel(root, bg=PAPER)
        self.log_win.title("Hunt log")
        self.log_win.withdraw()
        self.log_win.protocol("WM_DELETE_WINDOW", lambda: (self.show_log.set(False), self.toggle_log()))
        self.log = tk.Text(self.log_win, height=24, width=96, bg=PAPER, fg=INK, relief="flat", font=(MONO, 9),
                           wrap="none", padx=12, pady=10, highlightthickness=0, state="disabled",
                           selectbackground=LINE, selectforeground=INK)
        self.log.pack(fill="both", expand=True)

        self.watch = tk.BooleanVar(value=False)
        self.show_log = tk.BooleanVar(value=False)
        self.reports = tk.BooleanVar(value=self.settings.get("reports", True))
        menu = tk.Menu(root, tearoff=0)
        menu.add_command(label="What to hunt...", command=self.edit_config)
        menu.add_command(label="Finds", command=lambda: self.open_folder("finds"))
        menu.add_checkbutton(label="Watch only (never clicks)", variable=self.watch)
        menu.add_checkbutton(label="Log", variable=self.show_log, command=self.toggle_log)
        menu.add_separator()
        menu.add_command(label="New website code", command=lambda: self.send("code"))
        menu.add_command(label="Log out every browser", command=self.log_out_browsers)
        menu.add_separator()
        menu.add_command(label="Check this PC (clicks nothing)", command=lambda: self.start("diagnose"))
        menu.add_command(label="Test catching (catches every encounter)", command=lambda: self.start("debug"))
        menu.add_command(label="Fit the layout again at the next battle", command=lambda: self.start("calibrate"))
        menu.add_command(label="Open Hunt's folder", command=lambda: self.open_folder(""))
        menu.add_checkbutton(label="Send problem reports to the developer", variable=self.reports,
                             command=self.save_settings)
        menu.add_separator()
        menu.add_command(label="In Roblox, Ctrl+Shift+H pauses", state="disabled")
        menu.add_command(label=f"Build {NAME}", state="disabled")
        menu.add_command(label="Close Hunt", command=self.close)
        self.menu = menu
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

    def drag_start(self, e):
        self.grabbed = (e.x_root - self.root.winfo_x(), e.y_root - self.root.winfo_y())

    def drag(self, e):
        dx, dy = self.grabbed
        self.root.geometry(f"+{e.x_root - dx}+{e.y_root - dy}")

    def drag_end(self, e):
        dx, dy = self.grabbed
        self.settings["pos"] = [e.x_root - dx, e.y_root - dy]
        save_json(SETTINGS, self.settings)

    def hovering(self, tag):
        """Under the mouse: lighter, and the words say what it does. Recolored, not
        drawn again, or the click after it would miss what it was on."""
        self.hover = tag
        c = self.canvas
        c.config(cursor="hand2" if tag and (tag != "main" or self.main_enabled) else "")
        for t, (item, normal, lit) in self.lit.items():
            c.itemconfig(item, fill=lit if t == tag else normal)
        hints = {"main": self.main_label, "stop": "Stop", "more": "More",
                 "code": "Open the website" if self.code else "Connecting to the website"}
        item, room = self.sub_item
        c.itemconfig(item, text=self.fit(hints.get(tag) or self.sub, self.sub_font, room) if room > 0 else "")

    def toggle_log(self):
        if self.show_log.get():
            self.log_win.deiconify()
            self.log_win.lift()
        else:
            self.log_win.withdraw()

    def more(self):
        x, y = self.root.winfo_rootx() + self.W - round(40 * self.u), self.root.winfo_rooty() + self.H
        self.menu.tk_popup(x, y)

    def save_settings(self):
        self.settings["reports"] = self.reports.get()
        save_json(SETTINGS, self.settings)

    def open_folder(self, name):
        path = os.path.join(DATA, name)
        os.makedirs(path, exist_ok=True)
        if WINDOWS:
            os.startfile(path)
        else:
            subprocess.Popen(["open", path])

    def show_state(self):
        """What the pill says (title, then greyer) and which buttons it has."""
        running = self.proc is not None
        s = self.state
        mode = self.mode
        n = s.get("count", 0)
        count = f"{n} encounter{'' if n == 1 else 's'}"
        self.found_now = False
        if not running:
            title, sub = self.last_exit or ("Ready", "")
        elif mode == "diagnose":
            title, sub = "Checking this PC", ""
        elif not s:
            title, sub = "Starting", ""
        elif s.get("remote"):
            title, sub = "Taken over", "by the website"
        elif not s.get("running"):
            title, sub = "Paused", count
        elif s.get("phase") == "battle" and s.get("wanted"):
            title, sub, self.found_now = "Found", s.get("enc", ""), True
        else:
            title = "Watching" if mode == "watch" else "Test catching" if mode == "debug" else "Hunting"
            sub = f"{count} · in a battle" if s.get("phase") == "battle" else count
        self.title, self.sub = title, self.note or sub

        if not running:
            self.main_label, self.main_icon, self.main_enabled = "Start", "play", True
        elif mode == "diagnose":
            self.main_label, self.main_icon, self.main_enabled = "Checking", "play", False
        elif s.get("running") or s.get("remote") or not s:
            self.main_label, self.main_icon, self.main_enabled = "Pause", "pause", bool(s) and not s.get("remote")
        else:
            self.main_label, self.main_icon, self.main_enabled = "Resume", "play", True
        self.running = running
        self.web_on = running and mode in ("hunt", "debug", "calibrate")
        self.menu.entryconfig("Watch only*", state="disabled" if running else "normal")
        for item in ("New website code", "Log out every browser"):
            self.menu.entryconfig(item, state="normal" if self.web_on and self.code else "disabled")
        self.draw()

    def draw(self):
        drawn = (self.title, self.sub, self.main_icon, self.main_enabled, self.running, self.web_on, self.code,
                 self.found_now, bool(self.update))
        if drawn == getattr(self, "drawn", None):
            return  # the same again: left alone, so a click on it isn't lost
        self.drawn = drawn
        c, u, W, H = self.canvas, self.u, self.W, self.H
        c.delete("all")
        self.lit = {}  # what the mouse lights up: tag -> (item, color, lit color)
        light = self.found_now  # a find turns the pill white
        fill, ink, dim, edge = (PAPER, INK, "#6a6a6a", "#cfcfcf") if light else (INK, PAPER, "#8a8a8a", "#3a3a3a")
        hover_bg = "#e8e8e8" if light else "#2e2e2e"

        def pill(x0, y0, x1, y1, color, tags=()):
            r = (y1 - y0) / 2
            c.create_oval(x0, y0, x0 + 2 * r, y1, fill=color, outline="", tags=tags)
            c.create_oval(x1 - 2 * r, y0, x1, y1, fill=color, outline="", tags=tags)
            c.create_rectangle(x0 + r, y0, x1 - r, y1, fill=color, outline="", tags=tags)

        pill(0, 0, W, H, edge, "drag")  # a hairline around it, for dark games
        pill(1, 1, W - 1, H - 1, fill, "drag")
        cy = H / 2

        def circle(cx, r, color, tag, y=cy):
            return c.create_oval(cx - r, y - r, cx + r, y + r, fill=color, outline="", tags=tag)

        # the buttons, from the right: more, stop (while it runs), start / pause
        x = W - round(30 * u)
        self.lit["more"] = (circle(x, 15 * u, fill, "more"), fill, hover_bg)
        for dx in (-6, 0, 6):
            circle(x + dx * u, 1.8 * u, ink, "more")
        if self.update:
            circle(x + 10 * u, 2.4 * u, ink, "more", cy - 9 * u)  # an update is waiting in the menu
        x -= round(36 * u)
        if self.running:
            self.lit["stop"] = (circle(x, 15 * u, fill, "stop"), fill, hover_bg)
            c.create_rectangle(x - 5 * u, cy - 5 * u, x + 5 * u, cy + 5 * u, fill=ink, outline="", tags="stop")
            x -= round(38 * u)
        on = self.main_enabled
        self.lit["main"] = (circle(x, 17 * u, ink if on else edge, "main"),) + ((ink, dim) if on else (edge, edge))
        face = fill
        if self.main_icon == "play":
            c.create_polygon(x - 4 * u, cy - 6.5 * u, x - 4 * u, cy + 6.5 * u, x + 7 * u, cy, fill=face,
                             outline="", tags="main")
        else:
            for x0 in (x - 5 * u, x + 1.5 * u):
                c.create_rectangle(x0, cy - 6 * u, x0 + 3.5 * u, cy + 6 * u, fill=face, outline="", tags="main")
        right = x - 17 * u - 14 * u  # where the words have to end

        if self.web_on and not light:  # the website code, which opens the website
            code = self.code or "·····"
            color = dim if self.code else edge
            self.lit["code"] = (c.create_text(right, cy, text=code, anchor="e", font=self.code_font, fill=color,
                                              tags="code"), color, ink if self.code else edge)
            right -= self.code_font.measure(code) + 16 * u

        left = 24 * u
        title = self.fit(self.title, self.title_font, right - left)
        c.create_text(left, cy, text=title, anchor="w", font=self.title_font, fill=ink, tags="drag")
        left += self.title_font.measure(title) + 8 * u
        room = right - left if right - left > 30 * u else 0
        self.sub_item = (c.create_text(left, cy, anchor="w", font=self.sub_font, fill=dim, tags="drag"), room)
        self.hovering(self.hover)

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
            self.start("watch" if self.watch.get() else "hunt")
        else:
            self.send("toggle")

    def start(self, mode):
        if self.proc is not None:
            if mode in ("hunt", "watch"):
                return
            if not messagebox.askyesno("Hunt", "Stop the hunt that's running and do this instead?"):
                return
            self.stop(wait=True)
        args = {"hunt": [], "watch": ["--watch"], "debug": ["--debug"], "diagnose": ["--diagnose"],
                "calibrate": ["--calibrate"]}[mode]
        env = dict(os.environ, HUNT_DATA=DATA, PYTHONUNBUFFERED="1", PYTHONIOENCODING="utf-8")
        if not self.reports.get():
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
        self.add_line(f"--- {time.strftime('%H:%M:%S')} {mode} ---")
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

    def log_out_browsers(self):
        if self.proc is not None and messagebox.askyesno(
                "Hunt", "Log out every browser that's logged in to this PC's hunt?"):
            self.send("logout")

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
        if s.get("phase") == "battle" and s.get("wanted") and not (was.get("wanted") and was.get("enc") == s.get("enc")):
            # it's holding the battle: catch it in Roblox, then Resume
            self.add_line(f"Found: {s.get('enc')}. Catch it in Roblox, then press Resume.")
            self.root.deiconify()
            self.root.lift()
            self.root.bell()

    def ended(self, proc):
        code = proc.returncode
        if self.mode == "diagnose":
            self.last_exit = ("Check done", "see the log")
            self.show_log.set(True)
            self.toggle_log()
        elif code not in (0, None) and self.last_line and "stopped" not in self.last_line:
            self.last_exit = ("Stopped", self.last_line)
            self.show_log.set(True)
            self.toggle_log()
        else:
            self.last_exit = ("Stopped", "")
        self.proc, self.state, self.code = None, {}, ""
        if self.update_pending:
            self.install_update()

    def add_line(self, line):
        if not line.strip():
            return
        self.last_line = line.strip()
        log = self.log
        log.config(state="normal")
        log.insert("end", line + "\n")
        if int(log.index("end-1c").split(".")[0]) > 500:
            log.delete("1.0", "100.0")
        log.see("end")
        log.config(state="disabled")

    def close(self):
        if self.proc is not None:
            if not messagebox.askyesno("Hunt", "Stop hunting and close Hunt?"):
                return
            self.stop(wait=True)
        self.root.destroy()

    # ------------------------------------------------------------ what to hunt

    def edit_config(self):
        with open(CONFIG, encoding="utf-8") as f:
            text = f.read()
        try:
            cfg = json.loads(text)
            hook = cfg.pop("webhook", "")
            text = json.dumps(cfg, indent=2)
        except ValueError:
            hook = ""
        win = tk.Toplevel(self.root, bg=PAPER, padx=24, pady=20)
        win.title("What to hunt")
        win.transient(self.root)
        tk.Label(win, text="Discord webhook", bg=PAPER, fg=INK, font=(FONT, 10), anchor="w").pack(fill="x")
        tk.Label(win, text="Optional: finds are posted there.", bg=PAPER, fg=GREY, font=(FONT, 9),
                 anchor="w").pack(fill="x")
        hook_var = tk.StringVar(value=hook)
        tk.Entry(win, textvariable=hook_var, font=(FONT, 10), relief="flat", bd=0, bg=PAPER, fg=INK,
                 insertbackground=INK, highlightthickness=1, highlightbackground=LINE,
                 highlightcolor=INK).pack(fill="x", pady=(6, 18), ipady=5)
        tk.Label(win, text="Rules", bg=PAPER, fg=INK, font=(FONT, 10), anchor="w").pack(fill="x")
        tk.Label(win, text='"targets" are held for you, "notify" ones Hunt catches itself, "catch" is how.',
                 bg=PAPER, fg=GREY, font=(FONT, 9), anchor="w", justify="left", wraplength=560).pack(fill="x")
        frame = tk.Frame(win, bg=PAPER, highlightthickness=1, highlightbackground=LINE)
        frame.pack(fill="both", expand=True, pady=(6, 0))
        edit = tk.Text(frame, width=72, height=20, font=(MONO, 10), relief="flat", bg=PAPER, fg=INK,
                       insertbackground=INK, selectbackground=LINE, selectforeground=INK, undo=True,
                       wrap="none", padx=10, pady=8, highlightthickness=0)
        scroll = tk.Scrollbar(frame, command=edit.yview)
        edit.config(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        edit.pack(side="left", fill="both", expand=True)
        edit.insert("1.0", text)
        edit.tag_config("bad", background=LINE)
        error = tk.Label(win, bg=PAPER, fg=INK, font=(FONT, 10, "bold"), anchor="w", justify="left",
                         wraplength=560)
        error.pack(fill="x", pady=(8, 0))

        def save():
            edit.tag_remove("bad", "1.0", "end")
            try:
                cfg = json.loads(edit.get("1.0", "end"))
            except ValueError as e:
                line, col = getattr(e, "lineno", 1), getattr(e, "colno", 1)
                error.config(text=f"Line {line}: {getattr(e, 'msg', e)}. Often a missing or extra comma, "
                                  "or a missing \" around a name.")
                edit.tag_add("bad", f"{line}.0", f"{line}.end")
                edit.mark_set("insert", f"{line}.{col - 1}")
                edit.see(f"{line}.0")
                edit.focus_set()
                return
            problem = check_config(cfg)
            if problem:
                error.config(text=problem)
                return
            pasted = cfg.pop("webhook", "")  # a whole config pasted in: the box wins, unless it's empty
            hook = hook_var.get().strip() or (pasted.strip() if isinstance(pasted, str) else "")
            if hook and not re.match(r"https://((ptb|canary)\.)?discord(app)?\.com/api/webhooks/", hook):
                error.config(text="The webhook should start with https://discord.com/api/webhooks/ "
                                  "(Discord: channel settings > Integrations > Webhooks > Copy URL).")
                return
            save_json(CONFIG, {"webhook": hook, **cfg})
            win.destroy()
            if self.proc is not None and self.mode in ("hunt", "watch", "debug"):
                mode = self.mode
                self.stop(wait=True)
                self.proc = None
                self.start(mode)
                self.add_line("  (restarted with the new settings)")

        def example():
            if messagebox.askyesno("What to hunt", "Replace this with Hunt's example?", parent=win):
                with open(os.path.join(HERE, "config.example.json"), encoding="utf-8") as f:
                    cfg = json.load(f)
                cfg.pop("webhook", None)
                edit.delete("1.0", "end")
                edit.insert("1.0", json.dumps(cfg, indent=2))

        row = tk.Frame(win, bg=PAPER)
        row.pack(fill="x", pady=(12, 0))
        Button(row, "Save", save, filled=True, padx=22).pack(side="right")
        Button(row, "Cancel", win.destroy).pack(side="right", padx=(0, 8))
        Link(row, "Start over from the example", example, size=9).pack(side="left")
        win.grab_set()

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
        if not self.update:
            self.menu.insert_command(0, label="Update Hunt", command=self.do_update)
            self.menu.insert_separator(1)
        self.update = info
        self.menu.entryconfig(0, label=f"Update Hunt (build {info.get('name', '')})")
        self.draw()

    def do_update(self):
        if self.proc is not None:
            if not messagebox.askyesno("Hunt", "Updating stops the hunt for a minute. Update now?"):
                return
        self.set_note("Downloading the update")
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
                        self.root.after(0, self.set_note, f"Updating {100 * got // total}%")
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
        self.set_note("Installing the update")
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
    every state, the menu, the log and What to hunt, then Hunt closes. Anything
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
        hunt.offer_update({"version": VERSION + 1, "name": "smoke"})
        hunt.show_log.set(True)
        hunt.toggle_log()
        hunt.add_line("smoke")
        hunt.edit_config()
        root.update()
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
