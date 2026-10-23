"""Everything a run prints, its errors and some screenshots, to a Discord
webhook: for following a run on someone else's PC (the Windows version is
tested on a friend's). Standard library only, so it can report a crash even
when the other packages failed to install.

Lines are gathered and sent every few seconds, as code blocks; Discord takes
about 30 messages a minute per webhook.
"""
import atexit
import io
import json
import platform
import sys
import threading
import time
import traceback
import urllib.error
import urllib.request
import uuid

# Windows runs report here unless config.json says "debug_webhook" (or "" for off). The address
# isn't in the repository: make-windows-zip.sh puts it in from secrets.json.
HOOK = "__WINDOWS_DEBUG_WEBHOOK__"
URL = {"win32": HOOK}.get(sys.platform, "") if HOOK.startswith("https://") else ""
SHOTS = 25          # screenshots per run at most (besides crashes)
CLICK_SHOTS = 10    # the first clicks get a close-up each


class Debug:
    def __init__(self):
        self.url = ""
        self.run = uuid.uuid4().hex[:4]  # tells runs apart in the channel
        self.who = f"{platform.system()} {platform.release()} · run {self.run}"
        self.lines = []
        self.lock = threading.Lock()
        self.shots = 0
        self.clicks = 0
        self.wake = threading.Event()

    def start(self, url):
        self.url = url
        if not url:
            return
        sys.stdout = Tee(sys.stdout, self)
        sys.stderr = Tee(sys.stderr, self)
        old_hook = sys.excepthook

        def crashed(kind, value, tb):  # anything that ends the run
            self.flush()
            self.post("crashed:\n```\n" + "".join(traceback.format_exception(kind, value, tb))[-1800:] + "\n```")
            if isinstance(sys.stderr, Tee):  # it's posted already
                sys.stderr = sys.stderr.out
            old_hook(kind, value, tb)

        sys.excepthook = crashed
        threading.Thread(target=self._sender, daemon=True).start()
        atexit.register(self.flush)

    def note(self, text):
        """A line for the webhook only, not the console."""
        if self.url:
            with self.lock:
                self.lines.append(text)

    def flush(self):
        with self.lock:
            lines, self.lines = self.lines, []
        chunk = ""
        for line in lines:
            line = line[:1800]
            if len(chunk) + len(line) > 1800:
                self.post("```\n" + chunk + "```")
                chunk = ""
            chunk += line + "\n"
        if chunk.strip():
            self.post("```\n" + chunk + "```")

    def _sender(self):
        while True:
            self.wake.wait(4)
            self.wake.clear()
            self.flush()

    def shot(self, caption, img, max_width=1280):
        """A screenshot (PIL image) with a caption, up to SHOTS a run."""
        if not self.url or self.shots >= SHOTS:
            return
        self.shots += 1
        img = img.copy()
        if img.width > max_width:
            img = img.resize((max_width, int(img.height * max_width / img.width)))
        buf = io.BytesIO()
        img.save(buf, "PNG")
        self.flush()  # keep the order
        threading.Thread(target=self.post, args=(caption, buf.getvalue()), daemon=True).start()

    def click_shot(self):
        """Whether this click gets a close-up (the first CLICK_SHOTS)."""
        self.clicks += 1
        return bool(self.url) and self.clicks <= CLICK_SHOTS

    def post(self, text, png=None):
        if not self.url:
            return
        boundary = uuid.uuid4().hex
        payload = json.dumps({"content": f"`{self.who}` {text}"[:2000],
                              "allowed_mentions": {"parse": []}}).encode()
        parts = [(b'name="payload_json"', b"application/json", payload)]
        if png:
            parts.append((b'name="files[0]"; filename="shot.png"', b"image/png", png))
        body = b"".join(b"--%s\r\nContent-Disposition: form-data; %s\r\nContent-Type: %s\r\n\r\n%s\r\n"
                        % (boundary.encode(), d, t, data) for d, t, data in parts)
        body += b"--%s--\r\n" % boundary.encode()
        req = urllib.request.Request(self.url, body, {
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "User-Agent": "pokehunt-debug (catbot, 1.0)"})
        for attempt in range(4):
            try:
                urllib.request.urlopen(req, timeout=20).read()
                return
            except urllib.error.HTTPError as e:
                if e.code != 429:  # anything but "slow down" won't get better
                    return
                try:
                    time.sleep(float(json.loads(e.read()).get("retry_after", 2)) + 0.2)
                except ValueError:
                    time.sleep(2)
            except Exception:
                time.sleep(3 * (attempt + 1))


class Tee(io.TextIOBase):
    """Writes to the console and, line by line, to the webhook."""

    def __init__(self, out, debug):
        self.out, self.debug, self.part = out, debug, ""

    def write(self, s):
        self.out.write(s)
        self.part += s
        *done, self.part = self.part.split("\n")
        for line in done:
            if line.strip():
                self.debug.note(line.rstrip())
        return len(s)

    def flush(self):
        self.out.flush()


debug = Debug()
