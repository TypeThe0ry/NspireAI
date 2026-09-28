#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SCRIPT="$ROOT/scripts/deploy-ngc-stage0-probe.sh"
bash -n "$SCRIPT"
grep -q 'REMOTE="/stage0_probe.tns"' "$SCRIPT"
grep -q 'NSPIRE_ALLOW_NGC_STAGE0_PROBE_UPLOAD' "$SCRIPT"
grep -q 'readback_sha' "$SCRIPT"
if NSPIRE_ALLOW_NGC_STAGE0_PROBE_UPLOAD= "$SCRIPT" >"$ROOT/.build/stage0-probe-gate.out" 2>&1; then
  echo "FAIL: stage-0 probe gate accepted an upload without explicit opt-in" >&2
  exit 1
fi
grep -q 'NSPIRE_ALLOW_NGC_STAGE0_PROBE_UPLOAD=1' "$ROOT/.build/stage0-probe-gate.out"
rm -f "$ROOT/.build/stage0-probe-gate.out"
echo "PASS: stage-0 probe is isolated, opt-in, and readback-verified"
