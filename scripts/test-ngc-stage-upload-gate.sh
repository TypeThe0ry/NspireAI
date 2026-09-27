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
if ! grep -q 'c8c7b9977ae933ef4efe2f7fc6f8dad717abc0fe21b7f45fa95a884df991dc06' "$ROOT/scripts/deploy-program-nspire.sh" || \
   ! grep -q 'c8c7b9977ae933ef4efe2f7fc6f8dad717abc0fe21b7f45fa95a884df991dc06' "$ROOT/bridge/nspire-navnet-helper/NspireRemoteControl.java"; then
  echo "FAIL: physically failed two-service bootstrap candidate is not blocked in both upload paths" >&2
  exit 1
fi
if ! grep -q 'fc7f3e32dec85b5e860cbd03e080e52d149dbabba63b552137ccf897bd063471' "$ROOT/scripts/deploy-program-nspire.sh" || \
   ! grep -q 'fc7f3e32dec85b5e860cbd03e080e52d149dbabba63b552137ccf897bd063471' "$ROOT/bridge/nspire-navnet-helper/NspireRemoteControl.java"; then
  echo "FAIL: launch-freezing invalid-handle candidate is not blocked in both upload paths" >&2
  exit 1
fi
if ! grep -q '2a20676747b78f87fab2f2d11d1e56ad70d6390097c3c9319affc60bcf4aefa8' "$ROOT/scripts/deploy-program-nspire.sh" || \
   ! grep -q '2a20676747b78f87fab2f2d11d1e56ad70d6390097c3c9319affc60bcf4aefa8' "$ROOT/bridge/nspire-navnet-helper/NspireRemoteControl.java"; then
  echo "FAIL: physically frozen guarded NGC candidate is not blocked in both upload paths" >&2
  exit 1
fi
if ! grep -q '6430301d3edf5f214854c3f8be6b7c182ec520e832a11d095e7ac9d0e673ffc8' "$ROOT/scripts/deploy-program-nspire.sh" || \
   ! grep -q '6430301d3edf5f214854c3f8be6b7c182ec520e832a11d095e7ac9d0e673ffc8' "$ROOT/bridge/nspire-navnet-helper/NspireRemoteControl.java"; then
  echo "FAIL: physically timed-out Home-guard candidate is not blocked in both upload paths" >&2
  exit 1
fi
if ! grep -q '64a29956816428a03b978e24fcfb627bf80a86b738d9ff29045d4d0059c8c602' "$ROOT/scripts/deploy-program-nspire.sh" || \
   ! grep -q '64a29956816428a03b978e24fcfb627bf80a86b738d9ff29045d4d0059c8c602' "$ROOT/bridge/nspire-navnet-helper/NspireRemoteControl.java"; then
  echo "FAIL: physically frozen first-PING candidate is not blocked in both upload paths" >&2
  exit 1
fi
if ! grep -q 'd30a62b4f96640f0f49acf2813f138910de2e0917d28e9a98c92e946150fb146' "$ROOT/scripts/deploy-program-nspire.sh" || \
   ! grep -q 'd30a62b4f96640f0f49acf2813f138910de2e0917d28e9a98c92e946150fb146' "$ROOT/bridge/nspire-navnet-helper/NspireRemoteControl.java"; then
  echo "FAIL: physically crashed Menu candidate is not blocked in both upload paths" >&2
  exit 1
fi
if ! grep -q 'e34b356d9e82e8fe3799016f59c9873a1ea77e6a609ff48684e96a7974f7c2eb' "$ROOT/scripts/deploy-program-nspire.sh" || \
   ! grep -q 'e34b356d9e82e8fe3799016f59c9873a1ea77e6a609ff48684e96a7974f7c2eb' "$ROOT/bridge/nspire-navnet-helper/NspireRemoteControl.java"; then
  echo "FAIL: physically invalidated Enter-arm candidate is not blocked in both upload paths" >&2
  exit 1
fi
if ! grep -q '45eba3c67e0d52e133d00a3132eea955d0a4ba2ca4dab1571082ac5f4b895091' "$ROOT/scripts/deploy-program-nspire.sh" || \
   ! grep -q '45eba3c67e0d52e133d00a3132eea955d0a4ba2ca4dab1571082ac5f4b895091' "$ROOT/bridge/nspire-navnet-helper/NspireRemoteControl.java"; then
  echo "FAIL: standalone USB-wedging candidate is not blocked in both normal upload paths" >&2
  exit 1
fi
if ! grep -q 'c45c42b7284f865c19a2dcce618f63410bb250b5e4788d30eb25ab0b7fec0876' "$ROOT/scripts/deploy-program-nspire.sh" || \
   ! grep -q 'c45c42b7284f865c19a2dcce618f63410bb250b5e4788d30eb25ab0b7fec0876' "$ROOT/bridge/nspire-navnet-helper/NspireRemoteControl.java"; then
  echo "FAIL: rejected get_event scheduler-poll candidate is not blocked in both upload paths" >&2
  exit 1
fi
if NSPIRE_ALLOW_NGC_ENTRY_STAGE8_UPLOAD=1 "$ROOT/scripts/deploy-ngc-entry-stage8.sh" >"$ROOT/.build/ngc-stage8-reject-gate.out" 2>&1; then
  echo "FAIL: physically rejected stage-8 package remained uploadable" >&2
  exit 1
fi
if [[ ! -x "$ROOT/scripts/deploy-ngc-entry-stage12.sh" ]]; then
  echo "FAIL: stage-12 probe upload gate is missing or not executable" >&2
  exit 1
fi
if NSPIRE_ALLOW_NGC_ENTRY_STAGE12_UPLOAD=1 "$ROOT/scripts/deploy-ngc-entry-stage12.sh" >"$ROOT/.build/ngc-stage12-reject-gate.out" 2>&1; then
  echo "FAIL: stage-12 probe upload gate did not reject the physically frozen probe" >&2
  exit 1
fi
if ! grep -q 'ea71bdf2f15c2fdc37c8a91cc9259d5463c4be27cb654c87d50a83c7ce8e2b0f' "$ROOT/scripts/deploy-program-nspire.sh" || \
   ! grep -q 'ea71bdf2f15c2fdc37c8a91cc9259d5463c4be27cb654c87d50a83c7ce8e2b0f' "$ROOT/bridge/nspire-navnet-helper/NspireRemoteControl.java"; then
  echo "FAIL: physically frozen stage-12 probe is not blocked in both normal upload paths" >&2
  exit 1
fi
if [[ ! -x "$ROOT/scripts/deploy-ngc-entry-stage13.sh" ]]; then
  echo "FAIL: stage-13 probe upload gate is missing or not executable" >&2
  exit 1
fi
if NSPIRE_ALLOW_NGC_ENTRY_STAGE13_UPLOAD=1 "$ROOT/scripts/deploy-ngc-entry-stage13.sh" >"$ROOT/.build/ngc-stage13-reject-gate.out" 2>&1; then
  echo "FAIL: physically frozen stage-13 probe remained uploadable" >&2
  exit 1
fi
if ! grep -q 'froze after launch before CONNECTED' "$ROOT/.build/ngc-stage13-reject-gate.out"; then
  echo "FAIL: stage-13 gate did not explain physical rejection" >&2
  cat "$ROOT/.build/ngc-stage13-reject-gate.out" >&2
  exit 1
fi
rm -f "$ROOT/.build/ngc-stage13-reject-gate.out"
if ! grep -q '7afc998f9014236dbf45dd1cb33b74b5437329460c066516a66e25114163ba12' "$ROOT/scripts/deploy-program-nspire.sh" || \
   ! grep -q '7afc998f9014236dbf45dd1cb33b74b5437329460c066516a66e25114163ba12' "$ROOT/bridge/nspire-navnet-helper/NspireRemoteControl.java"; then
  echo "FAIL: physically frozen stage-13 probe is not blocked in both normal upload paths" >&2
  exit 1
fi
if ! grep -q 'e46e13c8ba6a00efea305321378a7014f596792d2b1c5cd1fa20ba5fd3b375f6' "$ROOT/scripts/deploy-program-nspire.sh" || \
   ! grep -q 'e46e13c8ba6a00efea305321378a7014f596792d2b1c5cd1fa20ba5fd3b375f6' "$ROOT/bridge/nspire-navnet-helper/NspireRemoteControl.java" || \
   ! grep -q 'returned immediately on the CX II' "$ROOT/scripts/deploy-ngc-entry-stage5.sh"; then
  echo "FAIL: stage-5 probe that returned immediately is not permanently blocked" >&2
  exit 1
fi
if ! grep -q 'manifest missing valid ngc_probe' "$ROOT/bridge/nspire-navnet-helper/NspireRemoteControl.java" || \
   ! grep -q 'NSPIRE_ALLOW_NGC_PROBE_UPLOAD' "$ROOT/bridge/nspire-navnet-helper/NspireRemoteControl.java" || \
   ! grep -q 'non-probe artifact must use ngc_probe_stage=0' "$ROOT/bridge/nspire-navnet-helper/NspireRemoteControl.java"; then
  echo "FAIL: Java direct-upload path does not gate probe manifests" >&2
  exit 1
fi
if ! grep -q 'manifest missing valid ngc_task_handoff' "$ROOT/bridge/nspire-navnet-helper/NspireRemoteControl.java" || \
   ! grep -q 'NSPIRE_ALLOW_NGC_TASK_HANDOFF_UPLOAD' "$ROOT/bridge/nspire-navnet-helper/NspireRemoteControl.java" || \
   ! grep -q 'Refusing private NGC task-handoff candidate' "$ROOT/scripts/deploy-program-nspire.sh"; then
  echo "FAIL: resident task-handoff candidate is not explicitly upload-gated" >&2
  exit 1
fi
if ! grep -q '1a5c052f6fd68233275451f3c028514c3e2dff1eeecc89da74a2648c97e14e1a' "$ROOT/scripts/deploy-program-nspire.sh" || \
   ! grep -q '1a5c052f6fd68233275451f3c028514c3e2dff1eeecc89da74a2648c97e14e1a' "$ROOT/bridge/nspire-navnet-helper/NspireRemoteControl.java"; then
  echo "FAIL: physically rejected resident task-handoff SHA is not blocked in both upload paths" >&2
  exit 1
fi
if ! grep -q '4092c01a6010b0e562fb1ca95e5573b0b5ee4cf929fb1281998b93c04398013e' "$ROOT/scripts/deploy-program-nspire.sh" || \
   ! grep -q '4092c01a6010b0e562fb1ca95e5573b0b5ee4cf929fb1281998b93c04398013e' "$ROOT/bridge/nspire-navnet-helper/NspireRemoteControl.java"; then
  echo "FAIL: physically rejected crt0-return task-handoff SHA is not blocked in both upload paths" >&2
  exit 1
fi
if ! grep -q 'f3e958e3aff470685ca5e5bd545f8a3478097ed8152ca9a8d24c7c5b3e14e822' "$ROOT/scripts/deploy-program-nspire.sh" || \
   ! grep -q 'f3e958e3aff470685ca5e5bd545f8a3478097ed8152ca9a8d24c7c5b3e14e822' "$ROOT/bridge/nspire-navnet-helper/NspireRemoteControl.java" || \
   ! grep -q 'KNOWN_BLOCKED_TASK_HANDOFF_PRIORITY255_NO_LAUNCH' "$ROOT/scripts/audit-device-artifact.sh"; then
  echo "FAIL: physically rejected priority-255 task-handoff SHA is not blocked and classified" >&2
  exit 1
fi
if ! grep -q '3b6f94808c9e34d920d80a59bfea1ae4aaefc7e9857c98021400fd01b1f25cf1' "$ROOT/scripts/deploy-program-nspire.sh" || \
   ! grep -q '3b6f94808c9e34d920d80a59bfea1ae4aaefc7e9857c98021400fd01b1f25cf1' "$ROOT/bridge/nspire-navnet-helper/NspireRemoteControl.java" || \
   ! grep -q 'KNOWN_BLOCKED_TASK_HANDOFF_PRIORITY20_FREEZE' "$ROOT/scripts/audit-device-artifact.sh"; then
  echo "FAIL: physically rejected priority-20 task-handoff SHA is not blocked and classified" >&2
  exit 1
fi
if [[ ! -x "$ROOT/scripts/deploy-ngc-entry-stage17.sh" ]] || \
   ! grep -q 'EXPECTED_SHA="8fd7dacfaa9551e254e0595d21dfe23797f684c1cbb9894b72543a14388a9b94"' "$ROOT/scripts/deploy-ngc-entry-stage17.sh" || \
   ! grep -q 'Data Abort at raw TCT_Schedule' "$ROOT/scripts/deploy-ngc-entry-stage17.sh" || \
   ! grep -q '8fd7dacfaa9551e254e0595d21dfe23797f684c1cbb9894b72543a14388a9b94' "$ROOT/scripts/deploy-program-nspire.sh"; then
  echo "FAIL: stage-17 Data Abort probe is not permanently blocked" >&2
  exit 1
fi
if [[ ! -x "$ROOT/scripts/deploy-ngc-entry-stage14.sh" ]]; then
  echo "FAIL: stage-14 probe upload gate is missing or not executable" >&2
  exit 1
fi
if NSPIRE_ALLOW_NGC_ENTRY_STAGE14_UPLOAD= "$ROOT/scripts/deploy-ngc-entry-stage14.sh" >"$ROOT/.build/ngc-stage14-reject-gate.out" 2>&1; then
  echo "FAIL: stage-14 probe gate allowed an unconfirmed invocation" >&2
  exit 1
fi
if ! grep -q 'NSPIRE_ALLOW_NGC_ENTRY_STAGE14_UPLOAD=1' "$ROOT/.build/ngc-stage14-reject-gate.out"; then
  echo "FAIL: stage-14 gate did not explain the explicit confirmation variable" >&2
  cat "$ROOT/.build/ngc-stage14-reject-gate.out" >&2
  exit 1
fi
rm -f "$ROOT/.build/ngc-stage14-reject-gate.out"
if ! grep -q 'unsupported document format' "$ROOT/.build/ngc-stage8-reject-gate.out"; then
  echo "FAIL: stage-8 gate did not explain physical rejection" >&2
  exit 1
fi
rm -f "$ROOT/.build/ngc-stage8-reject-gate.out"
echo "PASS: NGC stage-candidate upload requires explicit confirmation and exact path/SHA"
