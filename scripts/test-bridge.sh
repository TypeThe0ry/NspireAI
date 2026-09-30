#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
# The rendering tests need the bridge environment (Pillow, matplotlib).
if [[ -z "${PYTHON:-}" && -x "$ROOT/bridge/.venv/bin/python" ]]; then
  PYTHON="$ROOT/bridge/.venv/bin/python"
fi
PYTHON="${PYTHON:-python3}"
# Every bridge/test_*.py, so that a new test file is never left out.
(cd "$ROOT" && NSPIREAI_HELLO_SETTLE=0 "$PYTHON" -m unittest discover -v -s bridge -t . -p 'test_*.py')
