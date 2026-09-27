#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ARTIFACT="$ROOT/.build/ngc-entry-stage13/nspire_ai.tns"
EXPECTED_SHA="7afc998f9014236dbf45dd1cb33b74b5437329460c066516a66e25114163ba12"
REMOTE_DEST="${REMOTE_DEST:-/nspire_ai.tns}"

echo "Refusing NGC entry stage-13 upload: this exact production-cadence probe froze after launch before CONNECTED on 2026-09-27" >&2
exit 65

if [[ "${NSPIRE_ALLOW_NGC_ENTRY_STAGE13_UPLOAD:-}" != "1" ]]; then
  echo "Refusing NGC entry stage-13 upload: set NSPIRE_ALLOW_NGC_ENTRY_STAGE13_UPLOAD=1 after reviewing SHA $EXPECTED_SHA" >&2
  exit 65
fi
[[ -f "$ARTIFACT" ]] || { echo "Missing NGC entry stage-13 artifact: $ARTIFACT" >&2; exit 2; }
actual_sha="$(shasum -a 256 "$ARTIFACT" | awk '{print $1}')"
[[ "$actual_sha" == "$EXPECTED_SHA" ]] || { echo "Stage-13 SHA mismatch: got $actual_sha expected $EXPECTED_SHA" >&2; exit 65; }
python3 "$ROOT/scripts/check-ngc-relocations.py" "$ROOT/src/program/nspire_ai.elf" "$ARTIFACT"
"$ROOT/scripts/check-nspire-usb-state.sh"
echo "Uploading reviewed NGC entry stage-13 SHA=$EXPECTED_SHA to $REMOTE_DEST"
exec env NSPIRE_REMOTE_TIMEOUT_SECONDS="${NSPIRE_UPLOAD_TIMEOUT_SECONDS:-60}" \
  NSPIRE_ALLOW_NGC_ENTRY_STAGE13_UPLOAD=1 \
  "$ROOT/scripts/run-nspire-remote.sh" upload "$ARTIFACT" "$REMOTE_DEST"
