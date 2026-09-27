#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ARTIFACT="$ROOT/.build/ngc-entry-stage12/nspire_ai.tns"
EXPECTED_SHA="ea71bdf2f15c2fdc37c8a91cc9259d5463c4be27cb654c87d50a83c7ce8e2b0f"
REMOTE_DEST="${REMOTE_DEST:-/nspire_ai.tns}"

echo "Refusing NGC entry stage-12 upload: this exact probe froze on launch before CONNECTED on 2026-09-27" >&2
exit 65

if [[ "${NSPIRE_ALLOW_NGC_ENTRY_STAGE12_UPLOAD:-}" != "1" ]]; then
  echo "Refusing NGC entry stage-12 upload: set NSPIRE_ALLOW_NGC_ENTRY_STAGE12_UPLOAD=1 after reviewing SHA $EXPECTED_SHA" >&2
  exit 65
fi
[[ -f "$ARTIFACT" ]] || {
  echo "Missing NGC entry stage-12 artifact: $ARTIFACT" >&2
  exit 2
}
actual_sha="$(shasum -a 256 "$ARTIFACT" | awk '{print $1}')"
[[ "$actual_sha" == "$EXPECTED_SHA" ]] || {
  echo "Stage-12 SHA mismatch: got $actual_sha expected $EXPECTED_SHA" >&2
  exit 65
}
python3 "$ROOT/scripts/check-ngc-relocations.py" "$ROOT/src/program/nspire_ai.elf" "$ARTIFACT"
"$ROOT/scripts/check-nspire-usb-state.sh"
echo "Uploading reviewed NGC entry stage-12 SHA=$EXPECTED_SHA to $REMOTE_DEST"
exec env NSPIRE_REMOTE_TIMEOUT_SECONDS="${NSPIRE_UPLOAD_TIMEOUT_SECONDS:-60}" \
  NSPIRE_ALLOW_NGC_ENTRY_STAGE12_UPLOAD=1 \
  "$ROOT/scripts/run-nspire-remote.sh" upload "$ARTIFACT" "$REMOTE_DEST"
