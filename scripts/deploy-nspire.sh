#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
N_LINK_BIN="${N_LINK_BIN:-$ROOT/.deps/n-link/desktop/src-tauri/target/release/n-link}"
REMOTE_DEST="${REMOTE_DEST:-/}"

if [ ! -x "$N_LINK_BIN" ]; then
  echo "N-Link CLI not found: $N_LINK_BIN (run scripts/build-n-link.sh)" >&2
  exit 2
fi
for artifact in "$ROOT/dist/AI-ui-demo.tns" "$ROOT/dist/AI.tns" "$ROOT/dist/nspire_ai_nav.luax.tns"; do
  if [ ! -f "$artifact" ]; then
    echo "Missing $artifact (build UI/extension first)" >&2
    exit 2
  fi
done

"$N_LINK_BIN" upload \
  "$ROOT/dist/AI-ui-demo.tns" \
  "$ROOT/dist/AI.tns" \
  "$ROOT/dist/nspire_ai_nav.luax.tns" \
  "$REMOTE_DEST"
