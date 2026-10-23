# Hunt for Windows: Hunt.exe (the window, app.py) and hunt-engine.exe (the
# hunter it runs, pokehunt.py), sharing one folder, dist/Hunt. Built on
# GitHub (.github/workflows/windows.yml), since it has to be built on Windows:
#   pyinstaller windows/hunt.spec
import os

from PyInstaller.utils.hooks import collect_submodules

ROOT = os.path.dirname(SPECPATH)
ICON = os.path.join(SPECPATH, "hunt.ico")

window = Analysis(
    [os.path.join(ROOT, "app.py")], pathex=[ROOT],
    datas=[(os.path.join(ROOT, "config.example.json"), "."),       # a new PC's "What to hunt"
           (os.path.join(ROOT, "site", "app", "icon-192.png"), ".")],
    excludes=["numpy", "PIL"])
hunter = Analysis(
    [os.path.join(ROOT, "pokehunt.py")], pathex=[ROOT],
    datas=[(os.path.join(ROOT, "samples", "*.png"), "samples")],   # for "Check this PC"
    hiddenimports=collect_submodules("winrt") + ["pynput.keyboard._win32", "pynput.mouse._win32"],
    excludes=["pyautogui", "tkinter", "scipy"])                     # the Mac's clicks; the window's

window_exe = EXE(PYZ(window.pure), window.scripts, [], exclude_binaries=True, name="Hunt",
                 console=False, icon=ICON, upx=False)
hunter_exe = EXE(PYZ(hunter.pure), hunter.scripts, [], exclude_binaries=True, name="hunt-engine",
                 console=True, icon=ICON, upx=False)
COLLECT(window_exe, window.binaries, window.datas,
        hunter_exe, hunter.binaries, hunter.datas, name="Hunt", upx=False)
