#!/usr/bin/env bash
set -euo pipefail

# Upload the stage-2 LCD/GC boundary probe to an isolated document name.  It
# must never replace the production page: the probe returns after lcd_init()
# and gui_gc_global_GC() and deliberately performs no draw or NavNet call.
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
HELPER="${NSPIRE_HELPER_BIN:-$ROOT/bridge/nspire-helper/target/debug/nspireai-usb-helper}"
ARTIFACT="$ROOT/.build/ngc-entry-stage2/nspire_ai.tns"
EXPECTED_SHA="cb1002135d4f669b71dd0fb0f5023e66c2459eb682905a194196d79f49786999"
REMOTE="/stage2_probe.tns"

if [[ "${NSPIRE_ALLOW_NGC_STAGE2_PROBE_UPLOAD:-}" != "1" ]]; then
  echo "Refusing stage-2 probe upload: set NSPIRE_ALLOW_NGC_STAGE2_PROBE_UPLOAD=1 after reviewing SHA $EXPECTED_SHA" >&2
  exit 65
fi
[[ -x "$HELPER" ]] || { echo "Ndless helper not found: $HELPER" >&2; exit 2; }
[[ -f "$ARTIFACT" ]] || { echo "Missing stage-2 artifact: $ARTIFACT" >&2; exit 2; }
actual_sha="$(shasum -a 256 "$ARTIFACT" | awk '{print $1}')"
[[ "$actual_sha" == "$EXPECTED_SHA" ]] || { echo "Stage-2 SHA mismatch: got $actual_sha expected $EXPECTED_SHA" >&2; exit 65; }
"$ROOT/scripts/check-nspire-usb-state.sh"

VERIFY_DIR="$(mktemp -d /tmp/nspire-stage2-probe-verify.XXXXXX)"
cleanup() { rm -rf "$VERIFY_DIR"; }
trap cleanup EXIT
"$HELPER" --upload "$ARTIFACT" "$REMOTE"
"$HELPER" --download "$REMOTE" "$VERIFY_DIR/stage2_probe.tns"
readback_sha="$(shasum -a 256 "$VERIFY_DIR/stage2_probe.tns" | awk '{print $1}')"
[[ "$readback_sha" == "$EXPECTED_SHA" ]] || {
  echo "Stage-2 readback SHA mismatch: got $readback_sha expected $EXPECTED_SHA" >&2
  exit 65
}
echo "VERIFIED $REMOTE sha256=$readback_sha bytes=$(wc -c < "$VERIFY_DIR/stage2_probe.tns" | tr -d ' ')"
