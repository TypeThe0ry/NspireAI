#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SCRIPT="$ROOT/scripts/deploy-ngc-stage2-probe.sh"
bash -n "$SCRIPT"
grep -q 'REMOTE="/stage2_probe.tns"' "$SCRIPT"
grep -q 'NSPIRE_ALLOW_NGC_STAGE2_PROBE_UPLOAD' "$SCRIPT"
grep -q 'readback_sha' "$SCRIPT"
if NSPIRE_ALLOW_NGC_STAGE2_PROBE_UPLOAD= "$SCRIPT" >"$ROOT/.build/stage2-probe-gate.out" 2>&1; then
  echo "FAIL: stage-2 probe gate accepted an upload without explicit opt-in" >&2
  exit 1
fi
grep -q 'NSPIRE_ALLOW_NGC_STAGE2_PROBE_UPLOAD=1' "$ROOT/.build/stage2-probe-gate.out"
rm -f "$ROOT/.build/stage2-probe-gate.out"
echo "PASS: stage-2 probe is isolated, opt-in, and readback-verified"
