#!/bin/zsh
# Double-click to open Poopihunt: the pill window with the Pokemon hunter in it
# (pokehunt.command is the same hunter in Terminal, without the window). The
# first time it builds Hunt (Rust's cargo: `brew install rust`), which takes a
# few minutes; after that, a few seconds.
cd "$(dirname "$0")"
app=target/release/Hunt
# built again when its code is newer than it (after a git pull)
if [[ ! -x $app || -n $(find core window Cargo.toml Cargo.lock -newer $app 2>/dev/null | grep -v '/target/') ]]; then
  if ! command -v cargo > /dev/null; then
    echo "Hunt needs Rust to build it: brew install rust (or https://rustup.rs), then open this again."
    read -k1 "?Press any key to close."; exit 1
  fi
  echo "Building Hunt (a few minutes the first time)..."
  cargo build -q --release -p hunt || {
    echo "The build failed."; read -k1 "?Press any key to close."; exit 1; }
fi
# Terminal out of the way (it stays, minimized: it's what macOS lets see the screen and click)
osascript -e 'tell application "Terminal" to set miniaturized of front window to true' 2>/dev/null
HUNT_DATA="$PWD" $app
exit 0
