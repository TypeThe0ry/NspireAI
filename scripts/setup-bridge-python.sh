#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-}"
if [ -z "$PYTHON_BIN" ] && command -v python3.12 >/dev/null 2>&1; then
  PYTHON_BIN="$(command -v python3.12)"
fi
if [ -z "$PYTHON_BIN" ] && command -v python3 >/dev/null 2>&1; then
  PYTHON_BIN="$(command -v python3)"
fi
if [ -z "$PYTHON_BIN" ]; then
  echo "Python 3.9 or newer is required" >&2
  exit 2
fi

"$PYTHON_BIN" -c 'import sys; assert sys.version_info >= (3, 9), sys.version'
if [ ! -x "$ROOT/bridge/.venv/bin/python" ]; then
  "$PYTHON_BIN" -m venv "$ROOT/bridge/.venv"
fi
"$ROOT/bridge/.venv/bin/python" -m pip install --upgrade pip
"$ROOT/bridge/.venv/bin/python" -m pip install -r "$ROOT/bridge/requirements.txt"
printf 'bridge Python: %s\n' "$ROOT/bridge/.venv/bin/python"
