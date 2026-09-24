#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ARTIFACT="$ROOT/.build/ngc-entry-probe/nspire_ai.tns"
META="$ARTIFACT.meta"
EXPECTED_SHA="e9e4938138cdaee41f0fb86c8a858e3356ed4a8066615e658df36fba1b1d21ec"
REMOTE_DEST="${REMOTE_DEST:-/nspire_ai.tns}"
UPLOAD_TIMEOUT_SECONDS="${NSPIRE_UPLOAD_TIMEOUT_SECONDS:-60}"

if [[ "${NSPIRE_ALLOW_NGC_ENTRY_PROBE_UPLOAD:-}" != "1" ]]; then
  echo "Refusing NGC entry probe upload: set NSPIRE_ALLOW_NGC_ENTRY_PROBE_UPLOAD=1 after reviewing SHA $EXPECTED_SHA" >&2
  exit 65
fi
[[ -f "$ARTIFACT" && -f "$META" ]] || { echo "Missing NGC entry probe or manifest" >&2; exit 2; }
grep -qx 'build_status=success' "$META" || { echo "Probe manifest is not successful" >&2; exit 65; }
grep -qx 'ui_backend=TRUE' "$META" || { echo "Probe is not marked ui_backend=TRUE" >&2; exit 65; }
actual_sha="$(shasum -a 256 "$ARTIFACT" | awk '{print $1}')"
[[ "$actual_sha" == "$EXPECTED_SHA" ]] || { echo "Probe SHA mismatch: got $actual_sha expected $EXPECTED_SHA" >&2; exit 65; }
grep -qx "sha256=$EXPECTED_SHA" "$META" || { echo "Probe/manifest SHA mismatch" >&2; exit 65; }
python3 "$ROOT/scripts/check-ngc-relocations.py" "$ROOT/src/program/nspire_ai.elf" "$ARTIFACT"
"$ROOT/scripts/check-nspire-usb-state.sh"
echo "Uploading reviewed NGC entry probe SHA=$EXPECTED_SHA to $REMOTE_DEST"
exec env NSPIRE_REMOTE_TIMEOUT_SECONDS="$UPLOAD_TIMEOUT_SECONDS" \
  NSPIRE_ALLOW_NGC_ENTRY_PROBE_UPLOAD=1 \
  "$ROOT/scripts/run-nspire-remote.sh" upload "$ARTIFACT" "$REMOTE_DEST"
