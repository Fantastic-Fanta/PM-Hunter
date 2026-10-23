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

# colors: the website's (site/index.html)
TEAL, RED, INK, PAPER, GREY, LINE = "#6aa4a4", "#e05040", "#30303a", "#ffffff", "#8a8a96", "#e4e4ea"
GREEN, AMBER = "#3a9a5a", "#d08a20"
FONT = "Segoe UI" if WINDOWS else "Helvetica Neue"


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
    """A flat button that looks the same on Windows and on a Mac."""

    def __init__(self, parent, text, command, bg=PAPER, fg=INK, bold=False, size=10, padx=14, pady=7):
        super().__init__(parent, text=text, bg=bg, fg=fg, cursor="hand2", padx=padx, pady=pady,
                         font=(FONT, size, "bold" if bold else "normal"))
        self.command, self.colors, self.enabled = command, (bg, fg), True
        self.bind("<Button-1>", lambda e: self.enabled and self.command())
        self.bind("<Enter>", lambda e: self.enabled and self.config(bg=self._shade(bg)))
        self.bind("<Leave>", lambda e: self.config(bg=self.colors[0] if self.enabled else LINE))

    @staticmethod
    def _shade(color):
        r, g, b = (int(color[i:i + 2], 16) for i in (1, 3, 5))
        k = 0.88 if r + g + b > 600 else 1.15
        return "#%02x%02x%02x" % tuple(min(255, int(c * k)) for c in (r, g, b))

    def set(self, text=None, enabled=None, bg=None, fg=None):
        if text is not None:
            self.config(text=text)
        if bg or fg:
            self.colors = (bg or self.colors[0], fg or self.colors[1])
        if enabled is not None:
            self.enabled = enabled
        self.config(bg=self.colors[0] if self.enabled else LINE, fg=self.colors[1] if self.enabled else GREY,
                    cursor="hand2" if self.enabled else "arrow")


class Link(tk.Label):
    def __init__(self, parent, text, command, bg=PAPER, size=9):
        super().__init__(parent, text=text, bg=bg, fg=TEAL, cursor="hand2", font=(FONT, size, "underline"))
        self.bind("<Button-1>", lambda e: command())


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
        root.configure(bg=PAPER)
        root.resizable(False, False)
        try:
            icon = os.path.join(HERE, "icon-192.png")  # Hunt.exe's; the website's when run from here
            self.icon = tk.PhotoImage(file=icon if os.path.exists(icon) else
                                      os.path.join(HERE, "site", "app", "icon-192.png"))
            root.iconphoto(True, self.icon)
        except tk.TclError:
            self.icon = None

        head = tk.Frame(root, bg=TEAL, padx=16, pady=10)
        head.pack(fill="x")
        if self.icon:
            self.small_icon = self.icon.subsample(6)
            tk.Label(head, image=self.small_icon, bg=TEAL).pack(side="left")
        tk.Label(head, text="Hunt", bg=TEAL, fg=PAPER, font=(FONT, 16, "bold")).pack(side="left", padx=(8, 0))
        tk.Label(head, text=f"build {NAME}", bg=TEAL, fg="#e8f4f4", font=(FONT, 9)).pack(side="right")

        self.update_bar = tk.Frame(root, bg="#fff4d6", padx=16, pady=8)
        self.update_text = tk.Label(self.update_bar, bg="#fff4d6", fg=INK, font=(FONT, 10), anchor="w")
        self.update_text.pack(side="left")
        self.update_button = Button(self.update_bar, "Update now", self.do_update, bg=AMBER, fg=PAPER,
                                    bold=True, pady=4)
        self.update_button.pack(side="right")

        body = tk.Frame(root, bg=PAPER, padx=20, pady=16)
        body.pack(fill="both")
        self.body = body

        row = tk.Frame(body, bg=PAPER)
        row.pack(fill="x")
        self.dot = tk.Canvas(row, width=14, height=14, bg=PAPER, highlightthickness=0)
        self.dot.pack(side="left", padx=(0, 8))
        self.status = tk.Label(row, bg=PAPER, fg=INK, font=(FONT, 18, "bold"), anchor="w")
        self.status.pack(side="left")
        self.detail = tk.Label(body, bg=PAPER, fg=GREY, font=(FONT, 10), anchor="w", justify="left",
                               wraplength=420)
        self.detail.pack(fill="x", pady=(2, 12))

        self.found = tk.Label(body, bg=RED, fg=PAPER, font=(FONT, 11, "bold"), justify="left",
                              wraplength=400, padx=12, pady=8, anchor="w")

        buttons = tk.Frame(body, bg=PAPER)
        buttons.pack(fill="x")
        self.main_button = Button(buttons, "Start hunting", self.main, bg=RED, fg=PAPER, bold=True, size=12,
                                  padx=22, pady=10)
        self.main_button.pack(side="left")
        self.stop_button = Button(buttons, "Stop", self.stop, bg=LINE, size=12, padx=18, pady=10)
        self.stop_button.pack(side="left", padx=(10, 0))
        self.watch = tk.BooleanVar(value=False)
        self.watch_box = tk.Checkbutton(buttons, text="Watch only (never clicks)", variable=self.watch,
                                        bg=PAPER, fg=INK, activebackground=PAPER, selectcolor=PAPER,
                                        font=(FONT, 9), highlightthickness=0, bd=0)
        self.watch_box.pack(side="left", padx=(14, 0))

        tk.Frame(body, bg=LINE, height=1).pack(fill="x", pady=14)
        web = tk.Frame(body, bg=PAPER)
        web.pack(fill="x")
        tk.Label(web, text="Website", bg=PAPER, fg=GREY, font=(FONT, 9)).grid(row=0, column=0, sticky="w")
        self.code_label = tk.Label(web, bg=PAPER, fg=INK, font=("Consolas" if WINDOWS else "Menlo", 15, "bold"))
        self.code_label.grid(row=1, column=0, sticky="w")
        self.web_hint = tk.Label(web, bg=PAPER, fg=GREY, font=(FONT, 9), justify="left", anchor="w")
        self.web_hint.grid(row=1, column=1, sticky="w", padx=(12, 0))
        links = tk.Frame(web, bg=PAPER)
        links.grid(row=2, column=0, columnspan=2, sticky="w", pady=(4, 0))
        Link(links, "Open poopihunt.xyz/app", lambda: webbrowser.open(SITE + "/app")).pack(side="left")
        self.new_code = Link(links, "New code", lambda: self.send("code"))
        self.new_code.pack(side="left", padx=(12, 0))
        self.log_out = Link(links, "Log out every browser", self.log_out_browsers)
        self.log_out.pack(side="left", padx=(12, 0))

        tk.Frame(body, bg=LINE, height=1).pack(fill="x", pady=14)
        tools = tk.Frame(body, bg=PAPER)
        tools.pack(fill="x")
        Button(tools, "What to hunt", self.edit_config, bg=LINE, size=10, padx=12, pady=6).pack(side="left")
        Button(tools, "Finds", lambda: self.open_folder("finds"), bg=LINE, size=10, padx=12,
               pady=6).pack(side="left", padx=(8, 0))
        self.more_button = Button(tools, "More  ▾", self.more, bg=LINE, size=10, padx=12, pady=6)
        self.more_button.pack(side="left", padx=(8, 0))
        self.details_link = Link(tools, "Show details", self.toggle_details)
        self.details_link.pack(side="right")

        self.reports = tk.BooleanVar(value=self.settings.get("reports", True))
        menu = tk.Menu(root, tearoff=0)
        menu.add_command(label="Check this PC (clicks nothing)", command=lambda: self.start("diagnose"))
        menu.add_command(label="Test catching (catches every encounter)", command=lambda: self.start("debug"))
        menu.add_command(label="Fit the layout again at the next battle", command=lambda: self.start("calibrate"))
        menu.add_separator()
        menu.add_command(label="Open Hunt's folder", command=lambda: self.open_folder(""))
        menu.add_checkbutton(label="Send problem reports to the developer", variable=self.reports,
                             command=self.save_settings)
        self.menu = menu

        self.log_frame = tk.Frame(body, bg=PAPER)
        self.log = tk.Text(self.log_frame, height=12, width=60, bg="#f6f6f8", fg=INK, relief="flat",
                           font=("Consolas" if WINDOWS else "Menlo", 9), wrap="none", padx=8, pady=6,
                           highlightthickness=0, state="disabled")
        self.log.pack(fill="both", pady=(12, 0))

        tk.Label(root, text="In Roblox, Ctrl+Shift+H pauses and resumes. Fling the mouse into a screen "
                            "corner to stop.", bg="#f6f6f8", fg=GREY, font=(FONT, 9), pady=8,
                 wraplength=440).pack(fill="x", side="bottom")

    def toggle_details(self):
        if self.log_frame.winfo_ismapped():
            self.log_frame.pack_forget()
            self.details_link.config(text="Show details")
        else:
            self.log_frame.pack(fill="both")
            self.details_link.config(text="Hide details")

    def more(self):
        b = self.more_button
        self.menu.tk_popup(b.winfo_rootx(), b.winfo_rooty() + b.winfo_height())

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
        running = self.proc is not None
        s = self.state
        mode = self.mode
        if not running:
            color, title = GREY, "Ready"
            detail = "Open Roblox and the Pokemon game, then Start hunting."
            if self.last_exit:
                title, detail = self.last_exit
        elif mode == "diagnose":
            color, title, detail = AMBER, "Checking this PC", "Reading the sample pictures, then the screen."
        elif not s:
            color, title, detail = AMBER, "Starting", "Getting ready..."
        elif s.get("remote"):
            color, title, detail = TEAL, "Taken over", "The website is playing this battle."
        elif not s.get("running"):
            color, title = AMBER, "Paused"
            detail = "Resume when you want it to carry on."
        elif s.get("phase") == "battle" and s.get("wanted"):
            color, title, detail = RED, "Found one!", s.get("enc", "")
        else:
            color = GREEN
            title = "Watching" if mode == "watch" else "Test catching" if mode == "debug" else "Hunting"
            detail = "Waiting for an encounter" if s.get("phase") != "battle" else f"In a battle: {s.get('enc')}"
        if running and s and mode != "diagnose":
            n = s.get("count", 0)
            detail = f"{n} encounter{'' if n == 1 else 's'}  ·  {detail}"
        self.status.config(text=title)
        self.detail.config(text=detail)
        self.dot.delete("all")
        self.dot.create_oval(2, 2, 12, 12, fill=color, outline=color)

        if not running:
            self.main_button.set("Start hunting", True)
        elif mode == "diagnose":
            self.main_button.set("Checking...", False)
        elif s.get("running") or s.get("remote") or not s:
            self.main_button.set("Pause", bool(s) and not s.get("remote"))
        else:
            self.main_button.set("Resume", True)
        self.stop_button.set(enabled=running)
        self.watch_box.config(state="disabled" if running else "normal")

        web_on = running and mode in ("hunt", "debug", "calibrate")
        self.code_label.config(text=self.code if web_on and self.code else "------", fg=INK if web_on else LINE)
        self.web_hint.config(text="Type this code on the website\nto watch and battle from there."
                             if web_on and self.code else "Connecting..." if web_on else
                             "Start hunting to get a code." if mode != "watch" else "Not while watching.")
        for link in (self.new_code, self.log_out):
            link.config(fg=TEAL if web_on else LINE)

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
        self.found.pack_forget()
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
            self.found.config(text=f"Found: {s.get('enc')}!\nHunt is keeping the battle open for you. Catch it "
                                   "in Roblox, then press Resume.")
            self.found.pack(fill="x", pady=(0, 12), before=self.main_button.master)
            self.root.deiconify()
            self.root.lift()
            self.root.bell()

    def ended(self, proc):
        code = proc.returncode
        if self.mode == "diagnose":
            self.last_exit = ("Check done", "The results are under Show details.")
            if not self.log_frame.winfo_ismapped():
                self.toggle_details()
        elif code not in (0, None) and self.last_line and "stopped" not in self.last_line:
            self.last_exit = ("Stopped", self.last_line)
            if not self.log_frame.winfo_ismapped():
                self.toggle_details()
        else:
            self.last_exit = ("Stopped", "Start hunting to carry on.")
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
        win = tk.Toplevel(self.root, bg=PAPER, padx=16, pady=14)
        win.title("What to hunt")
        win.transient(self.root)
        tk.Label(win, text="Discord webhook (optional): finds are posted there", bg=PAPER, fg=INK,
                 font=(FONT, 10, "bold"), anchor="w").pack(fill="x")
        hook_var = tk.StringVar(value=hook)
        tk.Entry(win, textvariable=hook_var, font=(FONT, 10), relief="solid", bd=1,
                 highlightthickness=0).pack(fill="x", pady=(4, 12), ipady=4)
        tk.Label(win, text='"targets": held for you when one shows up.  "notify": caught by Hunt itself.  '
                           '"catch": how it catches.', bg=PAPER, fg=GREY, font=(FONT, 9), anchor="w",
                 justify="left", wraplength=560).pack(fill="x")
        frame = tk.Frame(win, bg=LINE, bd=1)
        frame.pack(fill="both", expand=True, pady=(6, 0))
        edit = tk.Text(frame, width=72, height=26, font=("Consolas" if WINDOWS else "Menlo", 10), relief="flat",
                       undo=True, wrap="none", padx=8, pady=6, highlightthickness=0)
        scroll = tk.Scrollbar(frame, command=edit.yview)
        edit.config(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        edit.pack(side="left", fill="both", expand=True)
        edit.insert("1.0", text)
        edit.tag_config("bad", background="#ffd6d0")
        error = tk.Label(win, bg=PAPER, fg=RED, font=(FONT, 10), anchor="w", justify="left", wraplength=560)
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
        row.pack(fill="x", pady=(10, 0))
        Button(row, "Save", save, bg=RED, fg=PAPER, bold=True).pack(side="right")
        Button(row, "Cancel", win.destroy, bg=LINE).pack(side="right", padx=(0, 8))
        Link(row, "Start over from the example", example).pack(side="left")
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
        self.update = info
        self.update_text.config(text=f"A new Hunt is out (build {info.get('name', '')}).")
        self.update_bar.pack(fill="x", after=self.root.winfo_children()[0])
        self.update_button.set("Update now", True)

    def do_update(self):
        if self.proc is not None:
            if not messagebox.askyesno("Hunt", "Updating stops the hunt for a minute. Update now?"):
                return
        self.update_button.set("Downloading...", False)
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
                        self.root.after(0, self.update_button.set, f"Downloading {100 * got // total}%")
            if info.get("sha256") and digest.hexdigest() != info["sha256"]:
                raise ValueError("the download came out different from the website's (try again)")
            os.replace(path + ".part", path)
            self.setup = path
            self.root.after(0, self.downloaded)
        except Exception as e:
            self.root.after(0, self.update_failed, e)

    def update_failed(self, e):
        self.update_button.set("Try again", True)
        self.update_text.config(text=f"The update didn't download: {e}")

    def downloaded(self):
        self.update_pending = True
        self.update_button.set("Installing...", False)
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
    Hunt(root)
    root.mainloop()


if __name__ == "__main__":
    main()
