#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SCRIPT="$ROOT/scripts/deploy-ngc-stage-candidate.sh"

if NSPIRE_ALLOW_STAGE_CANDIDATE_UPLOAD= "$SCRIPT" >"$ROOT/.build/ngc-stage-upload-gate.out" 2>&1; then
  echo "FAIL: stage candidate upload gate allowed an unconfirmed invocation" >&2
  exit 1
fi
if ! grep -q 'NSPIRE_ALLOW_STAGE_CANDIDATE_UPLOAD=1' "$ROOT/.build/ngc-stage-upload-gate.out"; then
  echo "FAIL: gate did not explain the explicit confirmation variable" >&2
  cat "$ROOT/.build/ngc-stage-upload-gate.out" >&2
  exit 1
fi
rm -f "$ROOT/.build/ngc-stage-upload-gate.out"

if ! grep -q 'EXPECTED_SHA="34345e069a1bbaacd1b4b289e37ada9d04d8f1105c11fa259b5d519937998f26"' "$SCRIPT"; then
  echo "FAIL: exact stage candidate SHA is not pinned" >&2
  exit 1
fi
if ! grep -q 'ARTIFACT="\$ROOT/.build/ngc-lcdinit-trial/nspire_ai.tns"' "$SCRIPT"; then
  echo "FAIL: stage candidate path is not pinned" >&2
  exit 1
fi
if ! grep -q '"\$ROOT/scripts/run-nspire-remote.sh" upload "\$ARTIFACT" "\$REMOTE_DEST"' "$SCRIPT"; then
  echo "FAIL: stage candidate must use the TI Java session upload path" >&2
  exit 1
fi
if ! grep -q 'NSPIRE_ALLOW_STAGE_CANDIDATE_UPLOAD' "$ROOT/bridge/nspire-navnet-helper/NspireRemoteControl.java"; then
  echo "FAIL: Java direct-upload gate is missing" >&2
  exit 1
fi
if ! grep -q '34345e069a1bbaacd1b4b289e37ada9d04d8f1105c11fa259b5d519937998f26' "$ROOT/bridge/nspire-navnet-helper/NspireRemoteControl.java"; then
  echo "FAIL: Java direct-upload path is not pinned to the lcd-init candidate" >&2
  exit 1
fi
if ! grep -q '91820a3ac0ec564a995f0136e64b2c8d384ce9507ea8d410d9f99bb97fb70858' "$ROOT/bridge/nspire-navnet-helper/NspireRemoteControl.java"; then
  echo "FAIL: superseded stage candidate is not blocked in Java" >&2
  exit 1
fi
if ! grep -q '51ca73922afbc0ba2f0b488a98f4ff703eaa8081c64edac951ba1335d833a301' "$ROOT/bridge/nspire-navnet-helper/NspireRemoteControl.java"; then
  echo "FAIL: rejected relocation candidate SHA is not blocked in Java" >&2
  exit 1
fi
if ! grep -q 'unsupported document format' "$ROOT/bridge/nspire-navnet-helper/NspireRemoteControl.java"; then
  echo "FAIL: Java relocation-candidate rejection reason is missing" >&2
  exit 1
fi
if ! grep -q '5f3d5213ccc3ff5ef60054981541df03565f69b943ac734f0ea73dafeea62cc9' "$ROOT/bridge/nspire-navnet-helper/NspireRemoteControl.java"; then
  echo "FAIL: SDK-wrapper NGC candidate is not held behind physical review" >&2
  exit 1
fi
if ! grep -q 'unsupported document format' "$ROOT/scripts/deploy-ngc-wrapper-candidate.sh"; then
  echo "FAIL: SDK-wrapper candidate negative gate is missing the physical rejection" >&2
  exit 1
fi
if ! grep -q 'unsupported document format' "$ROOT/scripts/deploy-ngc-lcd-order-candidate.sh"; then
  echo "FAIL: lcd-order candidate path is not permanently blocked" >&2
  exit 1
fi
SIZE_SCRIPT="$ROOT/scripts/deploy-ngc-size-candidate.sh"
if NSPIRE_ALLOW_NGC_SIZE_CANDIDATE_UPLOAD=1 "$SIZE_SCRIPT" >"$ROOT/.build/ngc-size-upload-gate.out" 2>&1; then
  echo "FAIL: size-reduced candidate upload gate allowed a physically rejected package" >&2
  exit 1
fi
if ! grep -q 'unsupported document format' "$ROOT/.build/ngc-size-upload-gate.out"; then
  echo "FAIL: size-reduced gate did not explain physical rejection" >&2
  cat "$ROOT/.build/ngc-size-upload-gate.out" >&2
  exit 1
fi
rm -f "$ROOT/.build/ngc-size-upload-gate.out"
if ! grep -q 'EXPECTED_SHA="86b883f680a41f3c034167227ae3b0645d26652a8e8a299b2857b0d17a2f1ea1"' "$SIZE_SCRIPT"; then
  echo "FAIL: exact size-reduced candidate SHA is not pinned" >&2
  exit 1
fi
if ! grep -q 'unsupported document format' "$ROOT/bridge/nspire-navnet-helper/NspireRemoteControl.java"; then
  echo "FAIL: Java physical-rejection gate is missing" >&2
  exit 1
fi
if ! grep -q '86b883f680a41f3c034167227ae3b0645d26652a8e8a299b2857b0d17a2f1ea1' "$ROOT/bridge/nspire-navnet-helper/NspireRemoteControl.java"; then
  echo "FAIL: Java direct-upload path is not pinned to the size-reduced candidate" >&2
  exit 1
fi
if NSPIRE_ALLOW_NGC_ENTRY_STAGE8_UPLOAD=1 "$ROOT/scripts/deploy-ngc-entry-stage8.sh" >"$ROOT/.build/ngc-stage8-reject-gate.out" 2>&1; then
  echo "FAIL: physically rejected stage-8 package remained uploadable" >&2
  exit 1
fi
if ! grep -q 'unsupported document format' "$ROOT/.build/ngc-stage8-reject-gate.out"; then
  echo "FAIL: stage-8 gate did not explain physical rejection" >&2
  exit 1
fi
rm -f "$ROOT/.build/ngc-stage8-reject-gate.out"
echo "PASS: NGC stage-candidate upload requires explicit confirmation and exact path/SHA"
