"""The screen pokehunt looks at and clicks on: this Mac or Windows PC. Screenshots
are PIL images and clicks are in that image's pixels, so pokehunt.py never deals
with Retina points or Windows display scaling.

Text recognition is the system's own: Vision on macOS, Windows.Media.Ocr on
Windows. (The phone version is the app in android/.)
"""
import io
import subprocess
import sys
import time

from PIL import Image

MAC, WINDOWS = sys.platform == "darwin", sys.platform == "win32"
H_KEYCODE = 4 if MAC else 0x48  # physical H key, layout independent
N_KEYCODE = 45 if MAC else 0x4E  # N and L: the website's new code / log browsers out
L_KEYCODE = 37 if MAC else 0x4C


class FailSafeException(Exception):
    """The mouse was flung into a screen corner: stop."""

# ---------------------------------------------------------------- text recognition

def ocr(img):
    """[(text, center x, center y)] with x, y as fractions of the image, top-left
    origin."""
    if MAC:
        return _ocr_mac(img)
    if WINDOWS:
        return _ocr_windows(img)
    sys.exit("pokehunt reads text with macOS's or Windows' own OCR; run it on one of those.")


def _ocr_mac(img):
    import Vision
    from Foundation import NSData
    buf = io.BytesIO()
    img.save(buf, "PNG")
    data = NSData.dataWithBytes_length_(buf.getvalue(), len(buf.getvalue()))
    req = Vision.VNRecognizeTextRequest.alloc().init()
    req.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelAccurate)
    req.setUsesLanguageCorrection_(False)
    handler = Vision.VNImageRequestHandler.alloc().initWithData_options_(data, None)
    handler.performRequests_error_([req], None)
    out = []
    for r in req.results() or []:
        bb = r.boundingBox()
        out.append((str(r.topCandidates_(1)[0].string()),
                    bb.origin.x + bb.size.width / 2,
                    1 - bb.origin.y - bb.size.height / 2))
    return out


_win_engine = []


def _ocr_windows(img):
    """Windows puts a whole row of text in one line ("Quick Ball      x12");
    it's split where the words are far apart, the way Vision reports them."""
    from winrt.windows.graphics.imaging import BitmapPixelFormat, SoftwareBitmap
    from winrt.windows.media.ocr import OcrEngine
    from winrt.windows.storage.streams import DataWriter
    if not _win_engine:
        from winrt.windows.globalization import Language
        engine = (OcrEngine.try_create_from_language(Language("en-US"))
                  or OcrEngine.try_create_from_user_profile_languages())
        if engine is None:
            sys.exit("Windows has no OCR language: Settings > Time & language > Language, add English.")
        _win_engine.append(engine)
    most = OcrEngine.max_image_dimension
    if max(img.size) > most:
        k = most / max(img.size)
        img = img.resize((int(img.width * k), int(img.height * k)), Image.LANCZOS)
    w, h = img.size
    r, g, b = img.convert("RGB").split()
    writer = DataWriter()
    writer.write_bytes(Image.merge("RGBA", (b, g, r, Image.new("L", img.size, 255))).tobytes())
    bmp = SoftwareBitmap.create_copy_from_buffer(writer.detach_buffer(), BitmapPixelFormat.BGRA8, w, h)
    out = []
    for line in _win_engine[0].recognize_async(bmp).get().lines:
        group = []
        for word in line.words:
            rect = word.bounding_rect
            if group:
                prev = group[-1][1]
                if rect.x - (prev.x + prev.width) > 1.2 * max(rect.height, prev.height):
                    out.append(_text_box(group, w, h))
                    group = []
            group.append((word.text, rect))
        if group:
            out.append(_text_box(group, w, h))
    return out


def _text_box(words, w, h):
    x0 = min(r.x for _, r in words)
    x1 = max(r.x + r.width for _, r in words)
    y0 = min(r.y for _, r in words)
    y1 = max(r.y + r.height for _, r in words)
    return " ".join(t for t, _ in words), (x0 + x1) / 2 / w, (y0 + y1) / 2 / h

# ---------------------------------------------------------------- sounds

def sound(name):
    """A macOS system sound (Glass, Pop, Bottle); Windows plays its nearest."""
    if MAC:
        subprocess.Popen(["afplay", f"/System/Library/Sounds/{name}.aiff"])
    elif WINDOWS:
        import winsound
        alias = {"Glass": "SystemExclamation", "Pop": "SystemAsterisk"}.get(name, "SystemDefault")
        winsound.PlaySound(alias, winsound.SND_ALIAS | winsound.SND_ASYNC)
    else:
        print("\a", end="", flush=True)

# ---------------------------------------------------------------- the screen

class Desktop:
    """The main screen of this Mac or PC, clicked with the mouse."""

    def __init__(self):
        self.scale = None  # screenshot pixels per mouse point: 2.0 on Retina, 1 on Windows
        if WINDOWS:
            _dpi_aware()

    def grab(self):
        import mss
        with getattr(mss, "MSS", mss.mss)() as s:
            shot = s.grab(s.monitors[1])
            img = Image.frombytes("RGB", shot.size, shot.rgb)
        if self.scale is None:
            self.scale = 1.0 if WINDOWS else img.width / _pyautogui().size().width
        return img

    def _s(self):
        if self.scale is None:
            self.grab()
        return self.scale

    def click(self, x, y, right=False):
        # Roblox reads input once a frame and only clicks a button it already
        # sees the mouse over, so: arrive, nudge, let it notice, press, hold,
        # and stay a moment after letting go. right: the right button (the
        # bag's favorite).
        if WINDOWS:
            _win_click(x, y, right)
            return
        s = self._s()
        pg = _pyautogui()
        pg.moveTo(x / s - 2, y / s)
        time.sleep(0.03)
        pg.moveTo(x / s, y / s)
        time.sleep(0.05)
        button = "right" if right else "left"
        pg.mouseDown(button=button); time.sleep(0.04); pg.mouseUp(button=button)
        time.sleep(0.03)

    def move(self, x, y):
        if WINDOWS:
            _win_check_corner()
            _win_move_to(x, y)
        else:
            s = self._s()
            _pyautogui().moveTo(x / s, y / s)

    def pointer(self):
        """Where the mouse is, to notice you moving it."""
        return _win_cursor() if WINDOWS else tuple(_pyautogui().position())

    def type(self, text, clear=False):
        """Into the text box that has focus (give it a moment after the click that
        focused it). clear: whatever's in it goes first (select all, delete), wherever
        the click left the caret."""
        if WINDOWS:
            if clear:
                _win_select_all()
                time.sleep(0.05)
                _win_key(0x08)  # backspace
                time.sleep(0.05)
            for ch in text:
                _win_char(ch)
                time.sleep(0.04)
        else:
            pg = _pyautogui()
            if clear:
                pg.hotkey("command", "a")
                time.sleep(0.05)
                pg.press("backspace")
                time.sleep(0.05)
            pg.typewrite(text, interval=0.04)

    def focus_roblox(self):
        if MAC:
            subprocess.run(["osascript", "-e", 'tell application "Roblox" to activate'],
                           capture_output=True)
        elif WINDOWS:
            _win_focus_roblox()
        time.sleep(0.8)

    def roblox_in_front(self):
        """Roblox's window is the topmost app window."""
        if MAC:
            # NSWorkspace's frontmost app goes stale in a long-running script,
            # the window list doesn't.
            import Quartz
            windows = Quartz.CGWindowListCopyWindowInfo(
                Quartz.kCGWindowListOptionOnScreenOnly | Quartz.kCGWindowListExcludeDesktopElements,
                Quartz.kCGNullWindowID)
            for w in windows:
                b = w.get("kCGWindowBounds", {})
                if w.get("kCGWindowLayer") == 0 and b.get("Width", 0) > 200 and b.get("Height", 0) > 200:
                    return "roblox" in (w.get("kCGWindowOwnerName") or "").lower()
            return False
        if WINDOWS:
            import ctypes
            return _win_title(ctypes.windll.user32.GetForegroundWindow()) == "Roblox"
        return True


def window_info():
    """The screens and Roblox's window, for the debug log."""
    import mss
    with getattr(mss, "MSS", mss.mss)() as m:
        screens = ", ".join(f"{d['width']}x{d['height']} at ({d['left']}, {d['top']})" for d in m.monitors[1:])
    lines = [f"screens: {screens} (pokehunt watches the first)"]
    if WINDOWS:
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.windll.user32
        try:
            lines.append(f"display scaling: {user32.GetDpiForSystem() * 100 // 96}%")
        except AttributeError:
            pass
        robloxes = []

        @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        def each(hwnd, _):
            if user32.IsWindowVisible(hwnd) and "roblox" in _win_title(hwnd).lower():
                r = wintypes.RECT()
                user32.GetWindowRect(hwnd, ctypes.byref(r))
                robloxes.append(f'"{_win_title(hwnd)}" {r.right - r.left}x{r.bottom - r.top} at ({r.left}, {r.top})'
                                + (" minimized" if user32.IsIconic(hwnd) else ""))
            return True

        user32.EnumWindows(each, 0)
        lines.append("Roblox windows: " + ("; ".join(robloxes) or "none open"))
        lines.append(f'in front: "{_win_title(user32.GetForegroundWindow())}"')
    lines.append(f"Roblox in front: {Desktop().roblox_in_front()}")
    return "\n".join(lines)


def _pyautogui():
    import pyautogui
    pyautogui.PAUSE = 0
    return pyautogui

# ---------------------------------------------------------------- Windows input
# SendInput, the way a real mouse and keyboard arrive. pyautogui's jumps of the
# cursor (SetCursorPos) don't reach Roblox as mouse movement, so it never sees
# the mouse over a button; small relative moves do.

if WINDOWS:
    import ctypes
    from ctypes import wintypes

    class _MOUSEINPUT(ctypes.Structure):
        _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD),
                    ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD),
                    ("dwExtraInfo", ctypes.c_size_t)]

    class _KEYBDINPUT(ctypes.Structure):
        _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                    ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]

    class _HARDWAREINPUT(ctypes.Structure):
        _fields_ = [("uMsg", wintypes.DWORD), ("wParamL", wintypes.WORD), ("wParamH", wintypes.WORD)]

    class _INPUTUNION(ctypes.Union):
        _fields_ = [("mi", _MOUSEINPUT), ("ki", _KEYBDINPUT), ("hi", _HARDWAREINPUT)]

    class _INPUT(ctypes.Structure):
        _fields_ = [("type", wintypes.DWORD), ("u", _INPUTUNION)]

    _MOVE, _LEFTDOWN, _LEFTUP, _RIGHTDOWN, _RIGHTUP, _ABSOLUTE = 0x0001, 0x0002, 0x0004, 0x0008, 0x0010, 0x8000
    _KEYUP, _UNICODE = 0x0002, 0x0004


def _dpi_aware():
    """Screen coordinates in real pixels, the same as mss's screenshots."""
    import ctypes
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # per monitor
    except (AttributeError, OSError):
        ctypes.windll.user32.SetProcessDPIAware()


def _send(*inputs):
    arr = (_INPUT * len(inputs))(*inputs)
    ctypes.windll.user32.SendInput(len(inputs), arr, ctypes.sizeof(_INPUT))


def _mouse(flags, dx=0, dy=0):
    return _INPUT(type=0, u=_INPUTUNION(mi=_MOUSEINPUT(dx=dx, dy=dy, dwFlags=flags)))


def _win_move_to(x, y):
    user32 = ctypes.windll.user32
    w, h = user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)  # the main screen
    _send(_mouse(_MOVE | _ABSOLUTE, round(x * 65535 / (w - 1)), round(y * 65535 / (h - 1))))


def _win_nudge(dx, dy):
    _send(_mouse(_MOVE, dx, dy))


def _win_cursor():
    p = wintypes.POINT()
    ctypes.windll.user32.GetCursorPos(ctypes.byref(p))
    return p.x, p.y


def _win_check_corner():
    """Like pyautogui's fail-safe: the mouse in a screen corner stops pokehunt."""
    user32 = ctypes.windll.user32
    w, h = user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)
    x, y = _win_cursor()
    if (x <= 0 or x >= w - 1) and (y <= 0 or y >= h - 1):
        raise FailSafeException("mouse in a screen corner")


def _win_click(x, y, right=False):
    _win_check_corner()
    _win_move_to(x - 3, y)
    time.sleep(0.03)
    _win_nudge(2, 0)   # relative moves: Roblox sees these as the mouse moving
    time.sleep(0.02)
    _win_move_to(x, y)
    _win_nudge(1, 0)
    _win_nudge(-1, 0)
    time.sleep(0.05)
    _send(_mouse(_RIGHTDOWN if right else _LEFTDOWN))
    time.sleep(0.04)
    _send(_mouse(_RIGHTUP if right else _LEFTUP))
    time.sleep(0.03)


def _key(vk=0, scan=0, flags=0):
    return _INPUT(type=1, u=_INPUTUNION(ki=_KEYBDINPUT(wVk=vk, wScan=scan, dwFlags=flags)))


def _win_key(vk):
    scan = ctypes.windll.user32.MapVirtualKeyW(vk, 0)
    _send(_key(vk, scan), _key(vk, scan, _KEYUP))


def _win_select_all():
    """Ctrl+A."""
    ctrl, a = 0x11, 0x41
    _send(_key(ctrl), _key(a), _key(a, flags=_KEYUP), _key(ctrl, flags=_KEYUP))


def _win_char(ch):
    _send(_key(scan=ord(ch), flags=_UNICODE), _key(scan=ord(ch), flags=_UNICODE | _KEYUP))


def _win_title(hwnd):
    user32 = ctypes.windll.user32
    n = user32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(n + 1)
    user32.GetWindowTextW(hwnd, buf, n + 1)
    return buf.value


def _win_focus_roblox():
    """Brings the Roblox player's window (titled just "Roblox"; a browser on
    roblox.com has more in its title) to the front."""
    user32 = ctypes.windll.user32
    found = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def each(hwnd, _):
        if user32.IsWindowVisible(hwnd) and _win_title(hwnd) == "Roblox":
            found.append(hwnd)
        return True

    user32.EnumWindows(each, 0)
    if not found:
        return
    if user32.IsIconic(found[0]):
        user32.ShowWindow(found[0], 9)  # SW_RESTORE
    # Windows only lets the app in front hand over the focus; a tap of Alt
    # counts as that.
    _send(_key(0x12), _key(0x12, flags=_KEYUP))
    user32.SetForegroundWindow(found[0])
