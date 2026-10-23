#!/bin/zsh
# Packs the Windows test build into ~/Downloads/pokehunt-windows.zip, for a
# friend to unzip and double-click. Their finds don't ping your Discord (no
# "webhook"); everything goes to the Windows debug channel (debuglog.py).
set -e
cd "$(dirname "$0")"
out=$(mktemp -d)/pokehunt
mkdir -p $out/samples
cp pokehunt.py devices.py debuglog.py remote.py requirements.txt pokehunt.bat $out/
cp windows/* $out/
# the debug channel's address, from secrets.json (it isn't in the repository)
hook=$(.venv/bin/python -c "import json; print(json.load(open('secrets.json'))['windows_debug_webhook'])" 2>/dev/null) ||
  { echo "no secrets.json: this zip won't report to the debug channel"; hook=""; }
sed -i '' "s#__WINDOWS_DEBUG_WEBHOOK__#$hook#" $out/debuglog.py $out/pokehunt.bat
cp samples/*.png $out/samples/
.venv/bin/python -c "
import json, sys
c = json.load(open('config.json'))
c['webhook'] = ''
json.dump(c, open(sys.argv[1], 'w'), indent=2)" $out/config.json
rm -f ~/Downloads/pokehunt-windows.zip
(cd $out/.. && zip -qr ~/Downloads/pokehunt-windows.zip pokehunt)
echo "~/Downloads/pokehunt-windows.zip: $(unzip -l ~/Downloads/pokehunt-windows.zip | tail -1 | awk '{print $2}') files, $(du -h ~/Downloads/pokehunt-windows.zip | cut -f1)"
