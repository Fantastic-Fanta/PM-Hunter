# Hunt for Windows: hunt-engine.exe, the hunter (pokehunt.py), in dist/Hunt.
# Hunt.exe, the window that runs it, is Rust (window/) and is built next to it.
# Built on GitHub (.github/workflows/windows.yml), since it has to be built on
# Windows:
#   pyinstaller windows/hunt.spec
import os

from PyInstaller.utils.hooks import collect_submodules

ROOT = os.path.dirname(SPECPATH)
ICON = os.path.join(SPECPATH, "hunt.ico")

hunter = Analysis(
    [os.path.join(ROOT, "pokehunt.py")], pathex=[ROOT],
    datas=[(os.path.join(ROOT, "samples", "*.png"), "samples")],   # for "Check this PC"
    hiddenimports=collect_submodules("winrt") + ["pynput.keyboard._win32", "pynput.mouse._win32"],
    excludes=["pyautogui", "tkinter", "scipy"])                     # the Mac's clicks; no window here

hunter_exe = EXE(PYZ(hunter.pure), hunter.scripts, [], exclude_binaries=True, name="hunt-engine",
                 console=True, icon=ICON, upx=False)
COLLECT(hunter_exe, hunter.binaries, hunter.datas, name="Hunt", upx=False)
