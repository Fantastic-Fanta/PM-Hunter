#!/bin/zsh
# Builds the Android app and publishes it on poopihunt.xyz (and
# hunt.stingray-digital.online, which serves the same folder on the stingray
# server, for older builds): Hunt.apk, version.json and a download page.
# Phones with Hunt show "Update" the next time the app opens. A copy also
# lands in ~/Downloads (named $FILE below).
#   ./publish-android.sh            build and publish
#   ./publish-android.sh --no-upload  build only
set -e
cd "$(dirname "$0")/android"
SERVER=root@153.92.4.40
SITE=/srv/poopihunt.xyz/public   # Hunt's own folder; nothing else is touched
FILE=Hunt2.apk                   # the APK's name, on the site and in Downloads
export JAVA_HOME=/Library/Java/JavaVirtualMachines/temurin-21.jdk/Contents/Home
./gradlew -q assembleDebug
apk=app/build/outputs/apk/debug/app-debug.apk
info=$(/opt/homebrew/share/android-commandlinetools/build-tools/35.0.0/aapt2 dump badging $apk | head -1)
code=$(echo $info | sed -E "s/.*versionCode='([0-9]+)'.*/\1/")
name=$(echo $info | sed -E "s/.*versionName='([^']+)'.*/\1/")
cp $apk ../$FILE
cp $apk ~/Downloads/$FILE
echo "built $name"
[[ $1 == --no-upload ]] && exit 0

out=$(mktemp -d)
printf '{"versionCode": %s, "versionName": "%s", "apk": "%s"}\n' $code "$name" $FILE > $out/version.json
ssh -i ~/.ssh/clodsire_vps $SERVER "cat $SITE/windows.json 2>/dev/null" > $out/windows.json || true
python3 ../site/fill_page.py $out/version.json $out/windows.json > $out/index.html
# the APK first, version.json last: no phone sees a version before its APK is there
scp -q -i ~/.ssh/clodsire_vps $apk $SERVER:$SITE/$FILE.new
ssh -i ~/.ssh/clodsire_vps $SERVER "mv $SITE/$FILE.new $SITE/$FILE"
scp -q -i ~/.ssh/clodsire_vps $out/index.html $out/version.json $SERVER:$SITE/
ssh -i ~/.ssh/clodsire_vps $SERVER "chown -R poopihunt:poopihunt $SITE && find $SITE -maxdepth 1 -type f -exec chmod 644 {} +"
echo "published $name on https://poopihunt.xyz"
