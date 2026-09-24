#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ARTIFACT="$ROOT/.build/ngc-entry-stage6/nspire_ai.tns"
META="$ARTIFACT.meta"
EXPECTED_SHA="0bf950738fcd16e69fe97163cbcf69b6edd96057415ddd14b2a2687d522e91ed"
if [[ "${NSPIRE_ALLOW_NGC_ENTRY_STAGE6_UPLOAD:-}" != "1" ]]; then
  echo "Refusing NGC entry stage-6 upload: set NSPIRE_ALLOW_NGC_ENTRY_STAGE6_UPLOAD=1 after reviewing SHA $EXPECTED_SHA" >&2
  exit 65
fi
[[ -f "$ARTIFACT" && -f "$META" ]] || { echo "Missing NGC entry stage-6 artifact/manifest" >&2; exit 2; }
grep -qx 'build_status=success' "$META" || { echo "Stage-6 manifest is not successful" >&2; exit 65; }
actual_sha="$(shasum -a 256 "$ARTIFACT" | awk '{print $1}')"
[[ "$actual_sha" == "$EXPECTED_SHA" ]] || { echo "Stage-6 SHA mismatch: got $actual_sha expected $EXPECTED_SHA" >&2; exit 65; }
grep -qx "sha256=$EXPECTED_SHA" "$META" || { echo "Stage-6 manifest SHA mismatch" >&2; exit 65; }
python3 "$ROOT/scripts/check-ngc-relocations.py" "$ROOT/src/program/nspire_ai.elf" "$ARTIFACT"
"$ROOT/scripts/check-nspire-usb-state.sh"
echo "Uploading reviewed NGC entry stage-6 SHA=$EXPECTED_SHA to /nspire_ai.tns"
exec env NSPIRE_REMOTE_TIMEOUT_SECONDS="${NSPIRE_UPLOAD_TIMEOUT_SECONDS:-60}" \
  NSPIRE_ALLOW_NGC_ENTRY_STAGE6_UPLOAD=1 \
  "$ROOT/scripts/run-nspire-remote.sh" upload "$ARTIFACT" /nspire_ai.tns
