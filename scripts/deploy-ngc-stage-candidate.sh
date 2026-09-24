#!/usr/bin/env bash
set -euo pipefail

# This is intentionally a separate, opt-in deployment path. The normal
# deploy-program-nspire.sh only handles dist/nspire_ai.tns; this wrapper can
# never silently pick that stale artifact or a superseded LCD candidate.
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ARTIFACT="$ROOT/.build/ngc-lcdinit-trial/nspire_ai.tns"
META="$ARTIFACT.meta"
EXPECTED_SHA="34345e069a1bbaacd1b4b289e37ada9d04d8f1105c11fa259b5d519937998f26"
REMOTE_DEST="${REMOTE_DEST:-/nspire_ai.tns}"
UPLOAD_TIMEOUT_SECONDS="${NSPIRE_UPLOAD_TIMEOUT_SECONDS:-45}"

if [[ "${NSPIRE_ALLOW_STAGE_CANDIDATE_UPLOAD:-}" != "1" ]]; then
  echo "Refusing stage-candidate upload: set NSPIRE_ALLOW_STAGE_CANDIDATE_UPLOAD=1 after reviewing SHA $EXPECTED_SHA" >&2
  exit 65
fi
if [[ ! -f "$ARTIFACT" || ! -f "$META" ]]; then
  echo "Missing lcd-init candidate or manifest: $ARTIFACT" >&2
  exit 2
fi
if ! grep -qx 'build_status=success' "$META"; then
  echo "Candidate manifest is not a successful build; refusing upload" >&2
  exit 65
fi
if ! grep -qx 'ui_backend=TRUE' "$META"; then
  echo "Candidate manifest is not marked ui_backend=TRUE; refusing upload" >&2
  exit 65
fi
ARTIFACT_SHA="$(shasum -a 256 "$ARTIFACT" | awk '{print $1}')"
if [[ "$ARTIFACT_SHA" != "$EXPECTED_SHA" ]]; then
  echo "Stage candidate SHA mismatch: got $ARTIFACT_SHA expected $EXPECTED_SHA" >&2
  exit 65
fi
if ! grep -qx "sha256=$EXPECTED_SHA" "$META"; then
  echo "Candidate/manifest SHA mismatch; refusing upload" >&2
  exit 65
fi
if [[ ! "$UPLOAD_TIMEOUT_SECONDS" =~ ^[0-9]+([.][0-9]+)?$ ]] || [[ "$UPLOAD_TIMEOUT_SECONDS" == 0 || "$UPLOAD_TIMEOUT_SECONDS" == 0.* ]]; then
  echo "NSPIRE_UPLOAD_TIMEOUT_SECONDS must be a positive number" >&2
  exit 2
fi

# Read-only physical gate.  TI Student Software owns the USB interface in the
# working session, so use its Java NavNet client rather than opening a second
# raw N-Link handle.
"$ROOT/scripts/check-nspire-usb-state.sh"

echo "Uploading reviewed NGC lcd-init stage candidate SHA=$EXPECTED_SHA to $REMOTE_DEST"
exec env NSPIRE_REMOTE_TIMEOUT_SECONDS="$UPLOAD_TIMEOUT_SECONDS" \
  NSPIRE_ALLOW_STAGE_CANDIDATE_UPLOAD=1 \
  "$ROOT/scripts/run-nspire-remote.sh" upload "$ARTIFACT" "$REMOTE_DEST"
