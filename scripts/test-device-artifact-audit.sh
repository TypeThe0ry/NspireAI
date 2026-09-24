#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SCRIPT="$ROOT/scripts/audit-device-artifact.sh"
bash -n "$SCRIPT"
grep -q 'download /nspire_ai.tns' "$SCRIPT"
grep -q 'REMOTE_CLASS=UNKNOWN_NOT_APPROVED' "$SCRIPT"
if grep -Ev '^[[:space:]]*#' "$SCRIPT" | grep -Eq 'n-link[[:space:]]+upload|sendFileToNode|sendKey|run-nspire-remote\.sh"[[:space:]]+key'; then
  echo "FAIL: device artifact audit contains a write/key operation" >&2
  exit 1
fi
echo "PASS: device artifact audit is read-only and classifies exact package hashes"
