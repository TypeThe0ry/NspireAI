#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ARTIFACT="$ROOT/.build/ngc-entry-stage7/nspire_ai.tns"
META="$ARTIFACT.meta"
EXPECTED_SHA="0d3156b7856b10e41a62578925b532972c0f1413fe053c48fb5c6149ce02cb99"
if [[ "${NSPIRE_ALLOW_NGC_ENTRY_STAGE7_UPLOAD:-}" != "1" ]]; then
  echo "Refusing NGC entry stage-7 upload: set NSPIRE_ALLOW_NGC_ENTRY_STAGE7_UPLOAD=1 after reviewing SHA $EXPECTED_SHA" >&2
  exit 65
fi
[[ -f "$ARTIFACT" && -f "$META" ]] || { echo "Missing NGC entry stage-7 artifact/manifest" >&2; exit 2; }
grep -qx 'build_status=success' "$META" || { echo "Stage-7 manifest is not successful" >&2; exit 65; }
actual_sha="$(shasum -a 256 "$ARTIFACT" | awk '{print $1}')"
[[ "$actual_sha" == "$EXPECTED_SHA" ]] || { echo "Stage-7 SHA mismatch: got $actual_sha expected $EXPECTED_SHA" >&2; exit 65; }
grep -qx "sha256=$EXPECTED_SHA" "$META" || { echo "Stage-7 manifest SHA mismatch" >&2; exit 65; }
python3 "$ROOT/scripts/check-ngc-relocations.py" "$ROOT/src/program/nspire_ai.elf" "$ARTIFACT"
"$ROOT/scripts/check-nspire-usb-state.sh"
echo "Uploading reviewed NGC entry stage-7 SHA=$EXPECTED_SHA to /nspire_ai.tns"
exec env NSPIRE_REMOTE_TIMEOUT_SECONDS="${NSPIRE_UPLOAD_TIMEOUT_SECONDS:-60}" \
  NSPIRE_ALLOW_NGC_ENTRY_STAGE7_UPLOAD=1 \
  "$ROOT/scripts/run-nspire-remote.sh" upload "$ARTIFACT" /nspire_ai.tns
