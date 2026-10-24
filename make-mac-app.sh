#!/bin/zsh
# Builds Hunt.app: Hunt for the Mac as an app with Clodsire's icon, to
# double-click or keep in the Dock (Poopihunt.command still works too).
#   ./make-mac-app.sh              Hunt.app here, keeping what it saves in this folder
#                                  (config.json, calibration, finds, hunt.log), as before
#   ./make-mac-app.sh --publish D  Hunt.app in D for poopihunt.xyz (publish-mac.sh): for
#                                  Apple silicon and Intel, what it saves in
#                                  ~/Library/Application Support/Hunt on each Mac
# It's signed with this Mac's Apple Development (or Developer ID) certificate when
# there is one (HUNT_SIGN="name" picks another), so macOS keeps its permissions
# (Screen Recording, Accessibility, Input Monitoring) through rebuilds and
# updates; without one it's signed ad hoc and they're asked for again after a rebuild.
set -e
cd "$(dirname "$0")"
export PATH=/opt/homebrew/opt/rustup/bin:$HOME/.cargo/bin:$PATH
out=$PWD
publish=
if [[ $1 == --publish ]]; then publish=1; out=${2:?where to}; fi
code=$(( $(date +%s) / 60 - 29000000 ))  # higher is newer, like the other builds
name=$(date "+%d %b %H:%M")

if [[ -n $publish ]]; then
  rustup target add aarch64-apple-darwin x86_64-apple-darwin > /dev/null
  cargo build -q --release -p hunt --target aarch64-apple-darwin
  cargo build -q --release -p hunt --target x86_64-apple-darwin
  bin=$(mktemp -d)/Hunt
  lipo -create -output $bin target/aarch64-apple-darwin/release/Hunt target/x86_64-apple-darwin/release/Hunt
else
  cargo build -q --release -p hunt
  bin=target/release/Hunt
fi

app=$out/Hunt.app
rm -rf $app
mkdir -p $app/Contents/MacOS $app/Contents/Resources/samples
cp $bin $app/Contents/MacOS/Hunt
cp samples/*.png $app/Contents/Resources/samples/  # for Hunt --diagnose
printf '{"version": %s, "name": "%s"}\n' $code "$name" > $app/Contents/Resources/build.json

# the icon: Clodsire, every size macOS asks for
icons=$(mktemp -d)/Hunt.iconset
mkdir -p $icons
for s in 16 32 128 256 512; do
  sips -z $s $s windows/hunt-icon.png --out $icons/icon_${s}x${s}.png > /dev/null
  sips -z $((s * 2)) $((s * 2)) windows/hunt-icon.png --out $icons/icon_${s}x${s}@2x.png > /dev/null
done
iconutil -c icns $icons -o $app/Contents/Resources/Hunt.icns

cat > $app/Contents/Info.plist <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key><string>Hunt</string>
  <key>CFBundleDisplayName</key><string>Hunt</string>
  <key>CFBundleIdentifier</key><string>xyz.poopihunt.hunt</string>
  <key>CFBundleExecutable</key><string>Hunt</string>
  <key>CFBundleIconFile</key><string>Hunt</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>$name</string>
  <key>CFBundleVersion</key><string>$code</string>
  <key>LSMinimumSystemVersion</key><string>11.0</string>
  <key>NSHighResolutionCapable</key><true/>
</dict>
</plist>
PLIST

sign=${HUNT_SIGN:-$(security find-identity -p codesigning -v | grep -E '"(Developer ID Application|Apple Development):' | head -1 | sed -E 's/.*"(.*)"/\1/')}
codesign --force --sign "${sign:--}" $app
echo "built $app ($name, signed ${sign:-ad hoc})"

if [[ -z $publish ]]; then
  # what it saves stays in this folder, the way Poopihunt.command has it
  support=~/Library/"Application Support"/Hunt
  mkdir -p $support
  print -r -- $PWD > $support/data-dir
  echo "it keeps what it saves in $PWD"
fi
