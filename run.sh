#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$SCRIPT_DIR/venv"

if [ ! -d "$VENV_DIR" ]; then
    echo "[First run] Creating virtual environment..."
    python3 -m venv "$VENV_DIR" --without-pip
    curl -sSL https://bootstrap.pypa.io/get-pip.py | "$VENV_DIR/bin/python3"
fi

if ! "$VENV_DIR/bin/python3" -m pip show requests mutagen Pillow rich >/dev/null 2>&1; then
    echo "[Installing dependencies...]"
    "$VENV_DIR/bin/python3" -m pip install -r "$SCRIPT_DIR/requirements.txt"
fi

"$VENV_DIR/bin/python3" "$SCRIPT_DIR/run.py" "$@"
