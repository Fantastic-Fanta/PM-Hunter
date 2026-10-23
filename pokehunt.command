#!/bin/zsh
# Double-click to start the Pokemon hunter in Terminal. Ctrl+Shift+H pauses / resumes.
cd "$(dirname "$0")"
app=target/release/Hunt
if [[ ! -x $app || -n $(find core window Cargo.toml Cargo.lock -newer $app 2>/dev/null | grep -v '/target/') ]]; then
  echo "Building Hunt (a few minutes the first time)..."
  cargo build -q --release -p hunt || { echo "The build failed."; read -k1 "?Press any key to close."; exit 1; }
fi
HUNT_DATA="$PWD" $app --terminal "$@"
read -k1 "?pokehunt stopped. Press any key to close."
