#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CARGO_MANIFEST="$ROOT/.deps/n-link/desktop/src-tauri/Cargo.toml"

if [ ! -f "$CARGO_MANIFEST" ]; then
  "$ROOT/scripts/bootstrap-upstreams.sh"
fi

# The historical Tauri manifest checks that desktop/dist exists even when we
# only need its CLI subcommand.
mkdir -p "$ROOT/.deps/n-link/desktop/dist"
cargo build --release --manifest-path "$CARGO_MANIFEST" --bin n-link
printf 'N-Link CLI: %s\n' "$ROOT/.deps/n-link/desktop/src-tauri/target/release/n-link"
