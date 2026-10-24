"""The download page (site/index.html) with the builds that are up: the
Android one from version.json, the Windows one from windows.json, the Mac one
from mac.json. A button is left out while there's no build for it.
  python3 site/fill_page.py version.json [windows.json [mac.json]] > index.html
Used by publish-android.sh, publish-windows.sh, publish-mac.sh and publish-server.sh.
"""
import json
import os
import re
import sys

with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "index.html"), encoding="utf-8") as f:
    page = f.read()
with open(sys.argv[1]) as f:
    android = json.load(f)


def build(i):
    try:
        with open(sys.argv[i]) as f:
            return json.load(f)
    except (IndexError, OSError, ValueError):
        return None


for key, info in (("WINDOWS", build(2)), ("MAC", build(3))):
    if info:
        page = page.replace(f"{key}_FILE", info["file"]).replace(f"{key}_BUILD", info["name"])
    else:
        page = re.sub(rf'\s*<a href="{key}_FILE".*?</a>', "", page, flags=re.S)
page = page.replace("APKFILE", android["apk"]).replace("BUILD", android["versionName"])
sys.stdout.write(page)
