#!/bin/zsh
# Builds Hunt for Windows on GitHub (it has to be built on Windows:
# .github/workflows/windows.yml) and publishes it on poopihunt.xyz:
# PoopihuntSetup.exe, windows.json and the download page. Hunt on a PC offers the
# update when it next starts, or within 6 hours. A copy lands in ~/Downloads.
# GitHub builds what's pushed, so commit and push first.
#   ./publish-windows.sh              build and publish
#   ./publish-windows.sh --no-upload  build only
set -e
cd "$(dirname "$0")"
SERVER=root@153.92.4.40
KEY=~/.ssh/clodsire_vps
SITE=/srv/poopihunt.xyz/public   # Hunt's own folder; nothing else is touched
FILE=PoopihuntSetup.exe

git fetch -q origin main
if [[ -n $(git status --porcelain -- '*.py' requirements.txt config.example.json samples windows window .github) ||
      $(git rev-parse HEAD) != $(git rev-parse origin/main) ]]; then
  echo "GitHub builds what's pushed to main: commit and push the Windows changes first"
  exit 1
fi

code=$(( $(date +%s) / 60 - 29000000 ))  # higher is newer, like the Android build's versionCode
name=$(date "+%d %b %H:%M")
started=$(date -u +%Y-%m-%dT%H:%M:%SZ)
gh workflow run windows.yml --ref main -f version=$code -f name="$name"
id=
for i in {1..30}; do  # the run that just started
  id=$(gh run list --workflow windows.yml --event workflow_dispatch --limit 5 --json databaseId,createdAt \
       --jq "[.[] | select(.createdAt >= \"$started\")] | last | .databaseId // empty")
  [[ -n $id ]] && break
  sleep 2
done
[[ -n $id ]] || { echo "the build didn't start on GitHub"; exit 1; }
echo "building $name: $(gh run view $id --json url --jq .url)"
if ! gh run watch $id --exit-status --interval 5 > /dev/null; then
  gh run view $id --log-failed | tail -40
  exit 1
fi
# what Windows read of the samples, and the sizes
gh run view $id --log | sed -E 's/^.*[0-9]Z //' | grep -E "read like on the Mac|^[0-9.]+ MB (installed|Hunt.exe)|^ok: the pill" || true

out=$(mktemp -d)
gh run download $id -n PoopihuntSetup -D $out
cp $out/$FILE ~/Downloads/$FILE
echo "built $name: ~/Downloads/$FILE ($(du -h $out/$FILE | cut -f1))"
[[ $1 == --no-upload ]] && exit 0

sha=$(shasum -a 256 $out/$FILE | cut -d' ' -f1)
printf '{"version": %s, "name": "%s", "file": "%s", "sha256": "%s"}\n' $code "$name" $FILE $sha > $out/windows.json
ssh -i $KEY $SERVER "cat $SITE/version.json" > $out/version.json
python3 site/fill_page.py $out/version.json $out/windows.json > $out/index.html
# the installer first, windows.json last: no PC sees a version before its installer is there
scp -q -i $KEY $out/$FILE $SERVER:$SITE/$FILE.new
ssh -i $KEY $SERVER "mv $SITE/$FILE.new $SITE/$FILE"
scp -q -i $KEY $out/index.html $out/windows.json $SERVER:$SITE/
ssh -i $KEY $SERVER "chown -R poopihunt:poopihunt $SITE && find $SITE -maxdepth 1 -type f -exec chmod 644 {} +"
echo "published $name on https://poopihunt.xyz"
