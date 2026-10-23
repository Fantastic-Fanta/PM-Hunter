"""The hunt's link to the website (poopihunt.xyz), for Mac and Windows; the
Android app's Remote.kt does the same. Asks for the login code when the
hunt starts, sends what the hunter shows there, and takes the page's
commands. This computer makes every connection: commands come back on a
request the server holds open (/api/device/poll).

Standard library only.
"""
import json
import os
import platform
import queue
import secrets
import sys
import threading
import time
import urllib.request

SITE = "https://poopihunt.xyz"
WEB_BALLS = ["Quick Ball", "Timer Ball", "Repeat Ball"]  # the page's Bag, unless config.json's "web_balls" says


def key(path):
    """This computer's secret for the website, made the first time."""
    try:
        with open(path) as f:
            k = f.read().strip()
        if len(k) >= 40:
            return k
    except OSError:
        pass
    k = secrets.token_urlsafe(32)
    with open(path, "w") as f:
        f.write(k)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return k


def show(text):
    """To this window only: the code mustn't reach the debug webhook."""
    out = sys.stdout
    while hasattr(out, "out"):  # past debuglog's Tee (and Lines below): the console itself
        out = out.out
    out.write(text + "\n")
    out.flush()


class Remote:
    def __init__(self, key, build, on_command, site=SITE):
        self.key = key
        self.build = build
        self.site = site.rstrip("/")
        self.on_command = on_command
        self.label = f"{platform.node() or 'Computer'} ({'Mac' if sys.platform == 'darwin' else platform.system()})"
        self.code = ""
        self.stopped = False
        self.state = None
        self.shot = None
        self.sprite = None
        self.wake = threading.Event()

    def start(self):
        threading.Thread(target=self._connect, daemon=True).start()
        threading.Thread(target=self._send, daemon=True).start()

    def stop(self):
        self.stopped = True
        self.wake.set()
        try:
            self._call("POST", "/api/device/stop", {}, timeout=5)
        except Exception:
            pass

    def send_state(self, s):
        self.state = s
        self.wake.set()

    def send_shot(self, jpg):
        self.shot = jpg
        self.wake.set()

    def send_sprite(self, sprite):
        """The wild Pokemon as the game draws it: {"count", "species", "png"} (base64)."""
        self.sprite = sprite
        self.wake.set()

    def new_code(self):
        """A new code, shown in this window; the old one stops working."""
        self.code = self._call("POST", "/api/device/code", {})["code"]
        show(f"\n  Website code: {self.code}   (type it on {self.site}/app)\n")

    def log_out_browsers(self):
        self._call("POST", "/api/device/logout", {})

    def _connect(self):
        wait = 2
        while not self.stopped and not self.code:
            try:
                r = self._call("POST", "/api/device/start", {"label": self.label, "kind": sys.platform,
                                                             "build": self.build})
                self.code = r["code"]
                show(f"\n  Website code: {self.code}   (type it on {self.site}/app)\n")
            except Exception:
                time.sleep(wait)
                wait = min(wait * 2, 60)
        wait = 2
        while not self.stopped:
            try:
                r = self._call("GET", "/api/device/poll", timeout=40)
                for cmd in r.get("commands", []):
                    if cmd.get("cmd") == "login":
                        self.code = ""  # spent, right or wrong
                    if not self.stopped:
                        self.on_command(cmd)
                wait = 2
            except Exception:
                if self.stopped:
                    break
                time.sleep(wait)
                wait = min(wait * 2, 30)

    def _send(self):
        """The newest state, sprite and picture, as they come; older ones are skipped."""
        last, sent_at = None, 0.0
        while not self.stopped:
            self.wake.wait(5)
            self.wake.clear()
            s, self.state = self.state, None
            jpg, self.shot = self.shot, None
            sprite, self.sprite = self.sprite, None
            if s is None and last is not None and time.time() - sent_at > 20:
                s = last  # the last state again every 20 s: a restarted server knows it again
            try:
                if sprite:
                    self._call("POST", "/api/device/sprite", sprite)
                if jpg:
                    self._call("POST", "/api/device/shot", jpg=jpg)
                if s is not None:
                    self._call("POST", "/api/device/state", s)
                    last, sent_at = s, time.time()
            except Exception:
                time.sleep(2)  # offline: the next state replaces this one

    def _call(self, method, path, data=None, jpg=None, timeout=15):
        body = jpg if jpg is not None else (json.dumps(data).encode() if data is not None else None)
        req = urllib.request.Request(self.site + path, body, method=method, headers={
            "Authorization": f"Bearer {self.key}", "User-Agent": f"Hunt ({sys.platform}, {self.build})",
            "Content-Type": "image/jpeg" if jpg is not None else "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            text = r.read().decode()
        return json.loads(text) if text.strip() else {}


class Lines:
    """Passes everything printed on, keeping the last lines for the website."""

    def __init__(self, out, web):
        self.out, self.web = out, web

    def write(self, text):
        self.web.line(text)
        return self.out.write(text)

    def flush(self):
        self.out.flush()

    def __getattr__(self, name):
        return getattr(self.out, name)


class Web:
    """What the hunter shows on the website, and the page's commands."""

    def __init__(self):
        self.link = None         # a Remote while hunting
        self.remote = False      # taken over: battles wait for the page's buttons
        self.commands = queue.Queue()
        self.want_shot = False
        self.encounters = 0
        self.phase = "waiting"   # or "battle"
        self.enc = None
        self.wanted = False
        self.lead = {}
        self.moves = []          # [{"name", "pp", "max"}]
        self.msg = ""
        self.acting = ""
        self.error = ""
        self.done = 0
        self.known_moves = {}    # by lead
        self.lines = []          # the last lines printed

    def line(self, text):
        self.lines = (self.lines + [l for l in text.splitlines() if l.strip() and not l.startswith(
            ("  wanted:", "  auto-catch:", "  post only:"))])[-8:]

    def publish(self, running, cfg):
        if not self.link:
            return
        s = {"hunting": True, "running": running, "remote": self.remote, "phase": self.phase,
             "count": self.encounters, "wanted": self.wanted, "msg": self.msg, "acting": self.acting,
             "error": self.error, "done": self.done, "balls": cfg.get("web_balls", WEB_BALLS),
             "heal_item": cfg["catch"]["heal_item"], "pp_item": cfg["catch"]["pp_item"],
             "moves": self.moves, "log": self.lines}
        if self.enc:
            s["enc"] = {k: self.enc.get(k, "") for k in ("species", "level", "reskin", "gradient", "status")}
            s["enc"].update(shiny=bool(self.enc.get("shiny")), tint=bool(self.enc.get("tint")))
        if self.lead:
            s["lead"] = self.lead
        self.link.send_state(s)
