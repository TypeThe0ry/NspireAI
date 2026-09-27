#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ARTIFACT="$ROOT/.build/ngc-entry-stage14/nspire_ai.tns"
EXPECTED_SHA="31d1d46554d72cf0958a55a84d9e93858cd849cab437f804b5062e640e07edd3"
REMOTE_DEST="${REMOTE_DEST:-/nspire_ai.tns}"

if [[ "${NSPIRE_ALLOW_NGC_ENTRY_STAGE14_UPLOAD:-}" != "1" ]]; then
  echo "Refusing NGC entry stage-14 upload: set NSPIRE_ALLOW_NGC_ENTRY_STAGE14_UPLOAD=1 after reviewing SHA $EXPECTED_SHA" >&2
  exit 65
fi
[[ -f "$ARTIFACT" ]] || { echo "Missing NGC entry stage-14 artifact: $ARTIFACT" >&2; exit 2; }
actual_sha="$(shasum -a 256 "$ARTIFACT" | awk '{print $1}')"
[[ "$actual_sha" == "$EXPECTED_SHA" ]] || { echo "Stage-14 SHA mismatch: got $actual_sha expected $EXPECTED_SHA" >&2; exit 65; }
python3 "$ROOT/scripts/check-ngc-relocations.py" "$ROOT/src/program/nspire_ai.elf" "$ARTIFACT"
"$ROOT/scripts/check-nspire-usb-state.sh"
echo "Uploading reviewed NGC entry stage-14 SHA=$EXPECTED_SHA to $REMOTE_DEST"
exec env NSPIRE_REMOTE_TIMEOUT_SECONDS="${NSPIRE_UPLOAD_TIMEOUT_SECONDS:-60}" \
  NSPIRE_ALLOW_NGC_ENTRY_STAGE14_UPLOAD=1 \
  "$ROOT/scripts/run-nspire-remote.sh" upload "$ARTIFACT" "$REMOTE_DEST"
