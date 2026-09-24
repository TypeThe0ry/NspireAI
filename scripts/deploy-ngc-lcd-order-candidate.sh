#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ARTIFACT="$ROOT/dist/nspire_ai.tns"
META="$ARTIFACT.meta"
EXPECTED_SHA="b821080614c2d3eb839b38f8a1f45105485f7a4bee26f49dea149bba82e42d20"
REMOTE_DEST="${REMOTE_DEST:-/nspire_ai.tns}"
UPLOAD_TIMEOUT_SECONDS="${NSPIRE_UPLOAD_TIMEOUT_SECONDS:-60}"

echo "Refusing NGC lcd-order candidate upload: handheld rejected SHA $EXPECTED_SHA as unsupported document format on 2026-09-24" >&2
exit 65
[[ -f "$ARTIFACT" && -f "$META" ]] || { echo "Missing lcd-order candidate or manifest" >&2; exit 2; }
grep -qx 'build_status=success' "$META" || { echo "Candidate manifest is not successful" >&2; exit 65; }
grep -qx 'ui_backend=TRUE' "$META" || { echo "Candidate is not marked ui_backend=TRUE" >&2; exit 65; }
actual_sha="$(shasum -a 256 "$ARTIFACT" | awk '{print $1}')"
[[ "$actual_sha" == "$EXPECTED_SHA" ]] || { echo "Candidate SHA mismatch: got $actual_sha expected $EXPECTED_SHA" >&2; exit 65; }
grep -qx "sha256=$EXPECTED_SHA" "$META" || { echo "Candidate/manifest SHA mismatch" >&2; exit 65; }
python3 "$ROOT/scripts/check-ngc-relocations.py" "$ROOT/src/program/nspire_ai.elf" "$ARTIFACT"
"$ROOT/scripts/check-nspire-usb-state.sh"
echo "Uploading reviewed NGC lcd-order candidate SHA=$EXPECTED_SHA to $REMOTE_DEST"
exec env NSPIRE_REMOTE_TIMEOUT_SECONDS="$UPLOAD_TIMEOUT_SECONDS" \
  NSPIRE_ALLOW_LCD_ORDER_CANDIDATE_UPLOAD=1 \
  "$ROOT/scripts/run-nspire-remote.sh" upload "$ARTIFACT" "$REMOTE_DEST"
