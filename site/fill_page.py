"""The download page (site/index.html) with the builds that are up: the
Android one from version.json, the Windows one from windows.json. The
Windows button is left out while there's no Windows build.
  python3 site/fill_page.py version.json [windows.json] > index.html
Used by publish-android.sh, publish-windows.sh and publish-server.sh.
"""
import json
import os
import re
import sys

with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "index.html"), encoding="utf-8") as f:
    page = f.read()
with open(sys.argv[1]) as f:
    android = json.load(f)
windows = None
if len(sys.argv) > 2:
    try:
        with open(sys.argv[2]) as f:
            windows = json.load(f)
    except (OSError, ValueError):
        pass
if windows:
    page = page.replace("WINDOWS_FILE", windows["file"]).replace("WINDOWS_BUILD", windows["name"])
else:
    page = re.sub(r'\s*<a href="WINDOWS_FILE".*?</a>', "", page, flags=re.S)
    page = page.replace('href="/app/" style="top:68px"', 'href="/app/" style="top:50px"')  # into its place
page = page.replace("APKFILE", android["apk"]).replace("BUILD", android["versionName"])
sys.stdout.write(page)
