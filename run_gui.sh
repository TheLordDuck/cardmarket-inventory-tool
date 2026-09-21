#!/usr/bin/env bash
# Double-click/terminal launcher for the Cardmarket Inventory Tool GUI
# (Linux/macOS). Uses the project's own .venv if one exists there (created
# with `python3 -m venv .venv`, standard Linux/macOS layout: .venv/bin/...),
# falling back to python3 on PATH otherwise.
#
# Whether double-clicking this actually launches it (vs. opening a text
# editor or asking "Run in terminal?") depends on your file manager's
# settings for executable .sh files -- if it doesn't, right-click it and
# choose "Run" / "Run in terminal", or run `./run_gui.sh` from a terminal.
set -e
cd "$(dirname "$(readlink -f "$0")")"

if [ -x ".venv/bin/python" ]; then
    PYTHON=".venv/bin/python"
elif command -v python3 >/dev/null 2>&1; then
    PYTHON="python3"
else
    echo "No Python interpreter found. Run setup first:" >&2
    echo "  python3 -m venv .venv" >&2
    echo "  .venv/bin/pip install -r requirements.txt" >&2
    echo "  .venv/bin/python -m playwright install chrome" >&2
    exit 1
fi

exec "$PYTHON" -m src.gui
