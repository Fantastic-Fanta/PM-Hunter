# Hunt's icon, from windows/hunt-icon.png (Clodsire, drawn white on a black
# circle like the pill). Writes windows/hunt.ico (Hunt.exe, the installer)
# and window/icons/clodsire-192.png (the window's own):
#   python windows/make_icon.py
import os

from PIL import Image

here = os.path.dirname(os.path.abspath(__file__))
icon = Image.open(os.path.join(here, "hunt-icon.png")).convert("RGBA")
icon.resize((192, 192), Image.LANCZOS).save(os.path.join(here, "..", "window", "icons", "clodsire-192.png"))
icon.resize((256, 256), Image.LANCZOS).save(os.path.join(here, "hunt.ico"),
                                           sizes=[(s, s) for s in (16, 24, 32, 48, 64, 128, 256)])
