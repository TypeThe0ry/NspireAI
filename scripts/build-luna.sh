#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LUNA_DIR="$ROOT/.deps/luna"
if [ ! -d "$LUNA_DIR" ]; then
  "$ROOT/scripts/bootstrap-upstreams.sh"
fi
make -C "$LUNA_DIR"
printf 'Luna built at %s/luna\n' "$LUNA_DIR"
