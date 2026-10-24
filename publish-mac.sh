#!/bin/zsh
# Builds Hunt.app for any Mac (Apple silicon and Intel) and publishes it on
# poopihunt.xyz: Hunt-mac.zip, mac.json and the download page. Hunt.app looks
# at mac.json when it opens and whenever a hunt stops; a newer build is
# downloaded and put in its place, and it opens again. A copy lands in ~/Downloads.
#   ./publish-mac.sh              build and publish
#   ./publish-mac.sh --no-upload  build only
set -e
cd "$(dirname "$0")"
SERVER=root@153.92.4.40
KEY=~/.ssh/clodsire_vps
SITE=/srv/poopihunt.xyz/public   # Hunt's own folder; nothing else is touched
FILE=Hunt-mac.zip

out=$(mktemp -d)
./make-mac-app.sh --publish $out
code=$(sed -E 's/.*"version": ([0-9]+).*/\1/' $out/Hunt.app/Contents/Resources/build.json)
name=$(sed -E 's/.*"name": "([^"]*)".*/\1/' $out/Hunt.app/Contents/Resources/build.json)
(cd $out && ditto -c -k --keepParent Hunt.app $FILE)
cp $out/$FILE ~/Downloads/$FILE
echo "built $name: ~/Downloads/$FILE ($(du -h $out/$FILE | cut -f1))"
[[ $1 == --no-upload ]] && exit 0

sha=$(shasum -a 256 $out/$FILE | cut -d' ' -f1)
printf '{"version": %s, "name": "%s", "file": "%s", "sha256": "%s"}\n' $code "$name" $FILE $sha > $out/mac.json
ssh -i $KEY $SERVER "cat $SITE/version.json" > $out/version.json
ssh -i $KEY $SERVER "cat $SITE/windows.json 2>/dev/null" > $out/windows.json || true
python3 site/fill_page.py $out/version.json $out/windows.json $out/mac.json > $out/index.html
# the app first, mac.json last: no Mac sees a version before its app is there
scp -q -i $KEY $out/$FILE $SERVER:$SITE/$FILE.new
ssh -i $KEY $SERVER "mv $SITE/$FILE.new $SITE/$FILE"
scp -q -i $KEY $out/index.html $out/mac.json $SERVER:$SITE/
ssh -i $KEY $SERVER "chown -R poopihunt:poopihunt $SITE && find $SITE -maxdepth 1 -type f -exec chmod 644 {} +"
echo "published $name on https://poopihunt.xyz"
