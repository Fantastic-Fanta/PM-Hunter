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

        # a pill with no frame: the window's own color is see-through around it
        root.overrideredirect(True)
        u = self.u = max(1.0, root.winfo_fpixels("1i") / 96)  # Windows' display scaling
        self.W, self.H = round(360 * u), round(52 * u)
        if WINDOWS:
            clear = KEY
            root.attributes("-transparentcolor", KEY)
        else:
            clear = "systemTransparent"
            root.attributes("-transparent", True)
        root.configure(bg=clear)
        c = self.canvas = tk.Canvas(root, width=self.W, height=self.H, bg=clear, highlightthickness=0, bd=0)
        c.pack()
        self.font = tkfont.Font(family=MONO, size=11, weight="bold")
        self.hook, self.hook_open = self.hook_saved(), False
        self.hook_var = tk.StringVar()
        self.hook_box = tk.Entry(c, textvariable=self.hook_var, font=(FONT, 10), relief="flat", bd=0,
                                 highlightthickness=0)
        self.hook_box.bind("<FocusIn>", self.hook_in)
        self.hook_box.bind("<Button-1>", lambda e: self.hook_box.focus_force())
        self.hook_box.bind("<FocusOut>", self.hook_out)
        self.hook_box.bind("<Return>", lambda e: self.canvas.focus_set())
        self.hook_box.bind("<Escape>", lambda e: (self.hook_var.set(self.hook), self.canvas.focus_set()))
        self.hook_box.bind("<Button-3>", self.hook_paste)  # right click pastes
        self.hook_box.bind("<Button-2>", self.hook_paste)  # (a Mac's right click)
        self.hook_var.set("Webhook on" if self.hook else "Discord webhook")
        sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
        x, y = self.settings.get("pos") or (0, -1)
        if not (0 <= x <= sw - self.W // 2 and 0 <= y <= sh - self.H):
            x, y = (sw - self.W) // 2, sh - self.H - round(90 * u)  # bottom middle, above the taskbar
        root.geometry(f"{self.W}x{self.H}+{x}+{y}")

        self.hover = None
        actions = {"main": self.main, "more": self.more, "code": self.open_site}
        for tag, action in actions.items():
            c.tag_bind(tag, "<Enter>", lambda e, t=tag: self.hovering(t))
            c.tag_bind(tag, "<Leave>", lambda e: self.hovering(None))
            c.tag_bind(tag, "<Button-1>", lambda e, a=action: a())
        c.bind("<Button-1>", lambda e: c.focus_set(), add="+")  # a click off the webhook box leaves it
        c.tag_bind("drag", "<ButtonPress-1>", self.drag_start)
        c.tag_bind("drag", "<B1-Motion>", self.drag)
        c.tag_bind("drag", "<ButtonRelease-1>", self.drag_end)

        self.watch = tk.BooleanVar(value=False)
        self.reports = tk.BooleanVar(value=self.settings.get("reports", True))
        menu = tk.Menu(root, tearoff=0)
        menu.add_checkbutton(label="Watch only (never clicks)", variable=self.watch)
        menu.add_command(label="Finds", command=lambda: self.open_folder("finds"))
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
        """Under the mouse: lighter. Recolored, not drawn again, or the click
        after it would miss what it was on."""
        self.hover = tag
        c = self.canvas
        c.config(cursor="hand2" if tag and (tag != "main" or self.main_enabled) else "")
        for t, (item, normal, lit) in self.lit.items():
            c.itemconfig(item, fill=lit if t == tag else normal)

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
        """The pill: start / stop, the website code (or a word: Done, Stopped,
        an update's progress), the webhook box. A find turns it white."""
        running = self.proc is not None
        s, mode = self.state, self.mode
        self.found_now = bool(running and s.get("phase") == "battle" and s.get("wanted") and not s.get("remote"))
        web_on = running and mode in ("hunt", "debug", "calibrate")
        self.code_text = self.note or (self.code if web_on and self.code else
                                       "" if running else self.last_exit or "")
        if not running:
            self.main_icon, self.main_enabled = "play", True
        elif s and not s.get("running") and not s.get("remote") and mode != "diagnose":
            self.main_icon, self.main_enabled = "play", True  # paused: by a find, or Ctrl+Shift+H
        else:
            self.main_icon, self.main_enabled = "stop", True
        m = self.menu
        m.entryconfig("Watch only*", state="disabled" if running else "normal")
        for item in ("New website code", "Log out every browser"):
            m.entryconfig(item, state="normal" if web_on and self.code else "disabled")
        self.draw()

    def draw(self):
        drawn = (self.code_text, self.main_icon, self.main_enabled, self.found_now, bool(self.update))
        if drawn == getattr(self, "drawn", None):
            return  # the same again: left alone, so a click on it isn't lost
        self.drawn = drawn
        c, u, W, H = self.canvas, self.u, self.W, self.H
        c.delete("all")
        self.lit = {}  # what the mouse lights up: tag -> (item, color, lit color)
        light = self.found_now  # a find turns the pill white
        fill, ink, dim, edge = (PAPER, INK, "#6a6a6a", "#cfcfcf") if light else (INK, PAPER, "#8a8a8a", "#3a3a3a")
        hover_bg, box = ("#e8e8e8", "#efefef") if light else ("#2e2e2e", "#222222")

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

        # start / stop
        x = 8 * u + 17 * u
        self.lit["main"] = (circle(x, 17 * u, ink, "main"), ink, dim)
        if self.main_icon == "play":
            c.create_polygon(x - 4 * u, cy - 6.5 * u, x - 4 * u, cy + 6.5 * u, x + 7 * u, cy, fill=fill,
                             outline="", tags="main")
        else:
            c.create_rectangle(x - 5.5 * u, cy - 5.5 * u, x + 5.5 * u, cy + 5.5 * u, fill=fill, outline="",
                               tags="main")
        # the website code, which opens the website
        left = x + 17 * u + 14 * u
        code = self.code_text if light or self.code_text else "------"
        color = ink if self.code and code == self.code else dim if self.code_text else edge
        self.lit["code"] = (c.create_text(left, cy, text=code, anchor="w", font=self.font, fill=color,
                                          tags="code"), color, ink if code == self.code else color)
        # more, at the right end
        x = W - round(28 * u)
        self.lit["more"] = (circle(x, 15 * u, fill, "more"), fill, hover_bg)
        for dx in (-6, 0, 6):
            circle(x + dx * u, 1.8 * u, ink, "more")
        if self.update:
            circle(x + 10 * u, 2.4 * u, ink, "more", cy - 9 * u)  # an update is waiting in the menu
        # the webhook box, between
        x0, x1 = left + 92 * u, x - 20 * u
        pill(x0, cy - 14 * u, x1, cy + 14 * u, box, "hook")
        self.hook_box.config(bg=box, fg=ink if self.hook_open else dim, insertbackground=ink,
                             selectbackground=dim, selectforeground=fill)
        c.create_window(x0 + 12 * u, cy, window=self.hook_box, anchor="w", width=x1 - x0 - 24 * u,
                        height=22 * u)
        self.hovering(self.hover)

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
            self.hook_say("Not a webhook")
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
        if self.proc is not None and self.mode in ("hunt", "watch", "debug"):
            mode = self.mode
            self.stop(wait=True)
            self.proc = None
            self.start(mode)

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
            self.start("watch" if self.watch.get() else "hunt")
        elif self.main_icon == "play":
            self.send("toggle")  # paused (a find, or Ctrl+Shift+H): carry on
        else:
            self.stop()

    def open_site(self):
        if self.code and self.code_text == self.code:
            webbrowser.open(SITE + "/app")

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
            self.last_exit = "Done"  # what it found is in hunt.log
        elif code not in (0, None) and self.last_line and "stopped" not in self.last_line:
            self.last_exit = "Stopped"  # why is in hunt.log
        else:
            self.last_exit = None
        self.proc, self.state, self.code = None, {}, ""
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
    every state, the menu and the webhook box, then Hunt closes. Anything
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
