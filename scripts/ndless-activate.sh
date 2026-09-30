#!/usr/bin/env bash
# Reinstall Ndless after the handheld was reset, without touching it:
#   (on the home screen, where a reset leaves it) 2 (Browse) -> Enter (expand ndless/, the browser's selection
#   after a reset) -> Enter (ndless_installer_4.5.5-6.2.0-6.4.0) -> any key.
# Saves a screen capture; it should say "Ndless successfully installed!".
# Run only while no chat page is open (keys sent to an open page are queued
# by the OS and replayed later).
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT="${1:-${TMPDIR:-/tmp}/ndless-activate.png}"
export NSPIRE_REMOTE_TIMEOUT_SECONDS="${NSPIRE_REMOTE_TIMEOUT_SECONDS:-30}"
key() { "$ROOT/scripts/run-nspire-remote.sh" key "$@" 2>&1 | grep -E '^KEY|Exception|timed out' ; }
key '2'; sleep 2
key '~enter~'; sleep 2
key '~enter~'; sleep 5
key 'a'; sleep 4
"$ROOT/scripts/run-nspire-remote.sh" screen "$OUT" 2>&1 | grep -E '^SCREEN|Exception' || true
echo "check $OUT for \"Ndless successfully installed!\""
