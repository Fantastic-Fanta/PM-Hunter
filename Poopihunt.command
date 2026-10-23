#!/bin/zsh
# Double-click to open Poopihunt: the pill window, with the Pokemon hunter behind it
# (pokehunt.command is the same hunter in Terminal, without the window). The
# first time it sets up Python's packages and builds the window (Rust's cargo:
# `brew install rust`), which takes a few minutes; after that, a few seconds.
cd "$(dirname "$0")"
if [[ ! -x .venv/bin/python ]]; then
  echo "First run: setting up Python packages (takes a minute)..."
  python3 -m venv .venv && .venv/bin/pip install -q -r requirements.txt || {
    echo "Setup failed."; read -k1 "?Press any key to close."; exit 1; }
fi
app=window/target/release/Hunt
# built again when the window's code is newer than it (after a git pull)
if [[ ! -x $app || -n $(find window/src window/Cargo.toml window/icons window/fonts -newer $app 2>/dev/null) ]]; then
  if ! command -v cargo > /dev/null; then
    echo "Hunt's window needs Rust to build it: brew install rust (or https://rustup.rs), then open this again."
    read -k1 "?Press any key to close."; exit 1
  fi
  echo "Building Hunt's window (a few minutes the first time)..."
  cargo build -q --release --manifest-path window/Cargo.toml || {
    echo "The build failed."; read -k1 "?Press any key to close."; exit 1; }
fi
# Terminal out of the way (it stays, minimized: it's what macOS lets see the screen and click)
osascript -e 'tell application "Terminal" to set miniaturized of front window to true' 2>/dev/null
HUNT_ENGINE=".venv/bin/python -W ignore -u pokehunt.py" HUNT_DATA="$PWD" $app
exit 0
