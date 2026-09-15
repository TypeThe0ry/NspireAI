#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
mkdir -p "$ROOT/dist"

LUNA_BIN="${LUNA_BIN:-}"
if [ -z "$LUNA_BIN" ] && command -v luna >/dev/null 2>&1; then LUNA_BIN="$(command -v luna)"; fi
if [ -z "$LUNA_BIN" ] && [ -x "$ROOT/.deps/luna/luna" ]; then LUNA_BIN="$ROOT/.deps/luna/luna"; fi
if [ -z "$LUNA_BIN" ] && command -v nspire-tools >/dev/null 2>&1; then
  LUNA_BIN="$(nspire-tools path)/tools/luna/luna"
fi

if [ -z "$LUNA_BIN" ] || [ ! -x "$LUNA_BIN" ]; then
  cat >&2 <<'EOF'
Luna is not available. Run scripts/bootstrap-upstreams.sh, build Luna with
  make -C .deps/luna
then rerun this command, or set LUNA_BIN=/absolute/path/to/luna.
EOF
  exit 2
fi

"$LUNA_BIN" "$ROOT/src/ai-ui-demo.lua" "$ROOT/dist/AI-ui-demo.tns"
"$LUNA_BIN" "$ROOT/src/ai.lua" "$ROOT/dist/AI.tns"
printf 'built %s and %s with %s\n' "$ROOT/dist/AI-ui-demo.tns" "$ROOT/dist/AI.tns" "$LUNA_BIN"
