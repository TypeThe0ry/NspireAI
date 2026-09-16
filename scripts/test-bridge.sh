#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON="${PYTHON:-python3}"
(cd "$ROOT" && "$PYTHON" -m unittest -v \
  bridge.test_protocol bridge.test_bridge bridge.test_navnet_bridge)
