#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON="${PYTHON:-python3}"
(cd "$ROOT" && "$PYTHON" -m unittest -v bridge.test_bridge)
