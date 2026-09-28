#!/usr/bin/env bash
set -euo pipefail

# Stage-1 isolates lcd_type()/lcd_init(). It never touches the production
# basename and never acquires a GUI GC, draws, scans keys, or calls NavNet.
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
HELPER="${NSPIRE_HELPER_BIN:-$ROOT/bridge/nspire-helper/target/debug/nspireai-usb-helper}"
ARTIFACT="$ROOT/.build/ngc-entry-stage1/nspire_ai.tns"
EXPECTED_SHA="66f822dab397de642ea7d7f723451a8c1d1c8f944862c5e7808646fab7045a05"
REMOTE="/stage1_probe.tns"

if [[ "${NSPIRE_ALLOW_NGC_STAGE1_PROBE_UPLOAD:-}" != "1" ]]; then
  echo "Refusing stage-1 probe upload: set NSPIRE_ALLOW_NGC_STAGE1_PROBE_UPLOAD=1 after reviewing SHA $EXPECTED_SHA" >&2
  exit 65
fi
[[ -x "$HELPER" ]] || { echo "Ndless helper not found: $HELPER" >&2; exit 2; }
[[ -f "$ARTIFACT" ]] || { echo "Missing stage-1 artifact: $ARTIFACT" >&2; exit 2; }
actual_sha="$(shasum -a 256 "$ARTIFACT" | awk '{print $1}')"
[[ "$actual_sha" == "$EXPECTED_SHA" ]] || { echo "Stage-1 SHA mismatch: got $actual_sha expected $EXPECTED_SHA" >&2; exit 65; }
"$ROOT/scripts/check-nspire-usb-state.sh"

VERIFY_DIR="$(mktemp -d /tmp/nspire-stage1-probe-verify.XXXXXX)"
cleanup() { rm -rf "$VERIFY_DIR"; }
trap cleanup EXIT
"$HELPER" --upload "$ARTIFACT" "$REMOTE"
"$HELPER" --download "$REMOTE" "$VERIFY_DIR/stage1_probe.tns"
readback_sha="$(shasum -a 256 "$VERIFY_DIR/stage1_probe.tns" | awk '{print $1}')"
[[ "$readback_sha" == "$EXPECTED_SHA" ]] || {
  echo "Stage-1 readback SHA mismatch: got $readback_sha expected $EXPECTED_SHA" >&2
  exit 65
}
echo "VERIFIED $REMOTE sha256=$readback_sha bytes=$(wc -c < "$VERIFY_DIR/stage1_probe.tns" | tr -d ' ')"
