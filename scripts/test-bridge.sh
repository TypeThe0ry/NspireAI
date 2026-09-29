#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
# The rendering tests need the bridge environment (Pillow, matplotlib).
if [[ -z "${PYTHON:-}" && -x "$ROOT/bridge/.venv/bin/python" ]]; then
  PYTHON="$ROOT/bridge/.venv/bin/python"
fi
PYTHON="${PYTHON:-python3}"
(cd "$ROOT" && "$PYTHON" -m unittest -v \
  bridge.test_protocol bridge.test_bridge bridge.test_navnet_bridge \
  bridge.test_imagecodec bridge.test_render bridge.test_sessions \
  bridge.test_commands bridge.test_pagehost)
