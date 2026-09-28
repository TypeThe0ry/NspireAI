#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SCRIPT="$ROOT/scripts/audit-ndless-runtime-device.sh"
bash -n "$SCRIPT"
grep -q 'download /ndless/ndless_resources.tns' "$SCRIPT"
grep -q 'STOCK_NDLESS_R2022' "$SCRIPT"
grep -q 'LOADER_IRQ_BOUNDARY_VARIANT' "$SCRIPT"
if grep -Ev '^[[:space:]]*#' "$SCRIPT" | grep -Eq 'n-link[[:space:]]+upload|sendFileToNode|sendKey|run-nspire-remote\.sh"[[:space:]]+key'; then
  echo "FAIL: Ndless runtime audit contains a write/key operation" >&2
  exit 1
fi
echo "PASS: Ndless runtime audit is read-only and classifies stock/boundary hashes"
