#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ARTIFACT="$ROOT/.build/ngc-entry-stage1/nspire_ai.tns"
META="$ARTIFACT.meta"
EXPECTED_SHA="66f822dab397de642ea7d7f723451a8c1d1c8f944862c5e7808646fab7045a05"
REMOTE_DEST="${REMOTE_DEST:-/nspire_ai.tns}"
if [[ "${NSPIRE_ALLOW_NGC_ENTRY_STAGE1_UPLOAD:-}" != "1" ]]; then
  echo "Refusing NGC entry stage-1 upload: set NSPIRE_ALLOW_NGC_ENTRY_STAGE1_UPLOAD=1 after reviewing SHA $EXPECTED_SHA" >&2
  exit 65
fi
[[ -f "$ARTIFACT" && -f "$META" ]] || { echo "Missing NGC entry stage-1 artifact/manifest" >&2; exit 2; }
grep -qx 'build_status=success' "$META" || { echo "Stage-1 manifest is not successful" >&2; exit 65; }
actual_sha="$(shasum -a 256 "$ARTIFACT" | awk '{print $1}')"
[[ "$actual_sha" == "$EXPECTED_SHA" ]] || { echo "Stage-1 SHA mismatch: got $actual_sha expected $EXPECTED_SHA" >&2; exit 65; }
grep -qx "sha256=$EXPECTED_SHA" "$META" || { echo "Stage-1 manifest SHA mismatch" >&2; exit 65; }
python3 "$ROOT/scripts/check-ngc-relocations.py" "$ROOT/src/program/nspire_ai.elf" "$ARTIFACT"
"$ROOT/scripts/check-nspire-usb-state.sh"
echo "Uploading reviewed NGC entry stage-1 SHA=$EXPECTED_SHA to $REMOTE_DEST"
exec env NSPIRE_REMOTE_TIMEOUT_SECONDS="${NSPIRE_UPLOAD_TIMEOUT_SECONDS:-60}" \
  NSPIRE_ALLOW_NGC_ENTRY_STAGE1_UPLOAD=1 \
  "$ROOT/scripts/run-nspire-remote.sh" upload "$ARTIFACT" "$REMOTE_DEST"
