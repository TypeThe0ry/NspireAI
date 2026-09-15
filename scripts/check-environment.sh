#!/usr/bin/env bash
set -u
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
printf 'host: '; uname -m
printf 'macOS: '; sw_vers -productVersion 2>/dev/null || true
for command_name in git clang cmake make rustc cargo python3 lua nspire-tools nspire-gcc n-link docker; do
  if command -v "$command_name" >/dev/null 2>&1; then
    printf '%-16s %s\n' "$command_name" "$(command -v "$command_name")"
  else
    printf '%-16s MISSING\n' "$command_name"
  fi
done
if [ -x "$ROOT/.deps/n-link/desktop/src-tauri/target/release/n-link" ]; then
  printf '%-16s %s\n' n-link-built "$ROOT/.deps/n-link/desktop/src-tauri/target/release/n-link"
fi
if [ -x "$ROOT/.deps/luna/luna" ]; then
  printf '%-16s %s\n' luna-built "$ROOT/.deps/luna/luna"
fi
