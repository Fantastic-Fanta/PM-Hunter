#!/bin/zsh
# Double-click to start the Pokemon hunter in Terminal. Ctrl+Shift+H pauses / resumes.
cd "$(dirname "$0")"
if [[ ! -x .venv/bin/python ]]; then
  echo "First run: setting up Python packages (takes a minute)..."
  python3 -m venv .venv && .venv/bin/pip install -q -r requirements.txt || {
    echo "Setup failed."; read -k1 "?Press any key to close."; exit 1; }
fi
.venv/bin/python -W ignore pokehunt.py
read -k1 "?pokehunt stopped. Press any key to close."
