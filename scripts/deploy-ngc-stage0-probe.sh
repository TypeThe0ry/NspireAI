#!/usr/bin/env bash
set -euo pipefail

# Stage-0 isolates Zehn/loader entry and immediate return. It calls no LCD,
# GC, drawing, key, RTC, or NavNet API and never replaces the production page.
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
HELPER="${NSPIRE_HELPER_BIN:-$ROOT/bridge/nspire-helper/target/debug/nspireai-usb-helper}"
ARTIFACT="$ROOT/.build/ngc-entry-stage0/nspire_ai.tns"
EXPECTED_SHA="3198cd3b0aa3dcc9b67a46e3b1b2359e66a62ae792255f4da83942d4a3a66f55"
REMOTE="/stage0_probe.tns"

if [[ "${NSPIRE_ALLOW_NGC_STAGE0_PROBE_UPLOAD:-}" != "1" ]]; then
  echo "Refusing stage-0 probe upload: set NSPIRE_ALLOW_NGC_STAGE0_PROBE_UPLOAD=1 after reviewing SHA $EXPECTED_SHA" >&2
  exit 65
fi
[[ -x "$HELPER" ]] || { echo "Ndless helper not found: $HELPER" >&2; exit 2; }
[[ -f "$ARTIFACT" ]] || { echo "Missing stage-0 artifact: $ARTIFACT" >&2; exit 2; }
actual_sha="$(shasum -a 256 "$ARTIFACT" | awk '{print $1}')"
[[ "$actual_sha" == "$EXPECTED_SHA" ]] || { echo "Stage-0 SHA mismatch: got $actual_sha expected $EXPECTED_SHA" >&2; exit 65; }
"$ROOT/scripts/check-nspire-usb-state.sh"

VERIFY_DIR="$(mktemp -d /tmp/nspire-stage0-probe-verify.XXXXXX)"
cleanup() { rm -rf "$VERIFY_DIR"; }
trap cleanup EXIT
"$HELPER" --upload "$ARTIFACT" "$REMOTE"
"$HELPER" --download "$REMOTE" "$VERIFY_DIR/stage0_probe.tns"
readback_sha="$(shasum -a 256 "$VERIFY_DIR/stage0_probe.tns" | awk '{print $1}')"
[[ "$readback_sha" == "$EXPECTED_SHA" ]] || {
  echo "Stage-0 readback SHA mismatch: got $readback_sha expected $EXPECTED_SHA" >&2
  exit 65
}
echo "VERIFIED $REMOTE sha256=$readback_sha bytes=$(wc -c < "$VERIFY_DIR/stage0_probe.tns" | tr -d ' ')"
