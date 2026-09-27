#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ARTIFACT="$ROOT/.build/ngc-stage17/nspire_ai.tns"
META="$ARTIFACT.meta"
EXPECTED_SHA="8fd7dacfaa9551e254e0595d21dfe23797f684c1cbb9894b72543a14388a9b94"

if [[ "${NSPIRE_ALLOW_NGC_ENTRY_STAGE17_UPLOAD:-}" != "1" ]]; then
  echo "Refusing NGC entry stage-17 upload: set NSPIRE_ALLOW_NGC_ENTRY_STAGE17_UPLOAD=1 after reviewing SHA $EXPECTED_SHA" >&2
  exit 65
fi
[[ -f "$ARTIFACT" && -f "$META" ]] || { echo "Missing NGC entry stage-17 artifact/manifest" >&2; exit 2; }
grep -qx 'build_status=success' "$META" || { echo "Stage-17 manifest is not successful" >&2; exit 65; }
grep -qx 'ngc_probe=TRUE' "$META" || { echo "Stage-17 manifest is not a probe build" >&2; exit 65; }
grep -qx 'ngc_probe_stage=17' "$META" || { echo "Stage-17 manifest stage mismatch" >&2; exit 65; }
actual_sha="$(shasum -a 256 "$ARTIFACT" | awk '{print $1}')"
[[ "$actual_sha" == "$EXPECTED_SHA" ]] || { echo "Stage-17 SHA mismatch: got $actual_sha expected $EXPECTED_SHA" >&2; exit 65; }
grep -qx "sha256=$EXPECTED_SHA" "$META" || { echo "Stage-17 manifest SHA mismatch" >&2; exit 65; }
python3 "$ROOT/scripts/check-ngc-relocations.py" "$ROOT/src/program/nspire_ai.elf" "$ARTIFACT"
"$ROOT/scripts/check-nspire-usb-state.sh"
echo "Uploading reviewed NGC entry stage-17 SHA=$EXPECTED_SHA to /nspire_ai.tns"
exec env NSPIRE_REMOTE_TIMEOUT_SECONDS="${NSPIRE_UPLOAD_TIMEOUT_SECONDS:-60}" \
  NSPIRE_ALLOW_NGC_PROBE_UPLOAD=1 \
  NSPIRE_ALLOW_NGC_ENTRY_STAGE17_UPLOAD=1 \
  "$ROOT/scripts/run-nspire-remote.sh" upload "$ARTIFACT" /nspire_ai.tns
