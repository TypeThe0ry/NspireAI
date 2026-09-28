#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SCRIPT="$ROOT/scripts/deploy-ngc-stage1-probe.sh"
bash -n "$SCRIPT"
grep -q 'REMOTE="/stage1_probe.tns"' "$SCRIPT"
grep -q 'NSPIRE_ALLOW_NGC_STAGE1_PROBE_UPLOAD' "$SCRIPT"
grep -q 'readback_sha' "$SCRIPT"
if NSPIRE_ALLOW_NGC_STAGE1_PROBE_UPLOAD= "$SCRIPT" >"$ROOT/.build/stage1-probe-gate.out" 2>&1; then
  echo "FAIL: stage-1 probe gate accepted an upload without explicit opt-in" >&2
  exit 1
fi
grep -q 'NSPIRE_ALLOW_NGC_STAGE1_PROBE_UPLOAD=1' "$ROOT/.build/stage1-probe-gate.out"
rm -f "$ROOT/.build/stage1-probe-gate.out"
echo "PASS: stage-1 probe is isolated, opt-in, and readback-verified"
