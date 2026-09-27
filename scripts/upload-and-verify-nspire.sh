#!/usr/bin/env bash
set -euo pipefail

# n-link's CLI can print a transfer error and still exit 0. Treat the device
# readback, not the uploader's exit status, as the successful deployment gate.
if [[ $# != 4 ]]; then
  echo 'usage: upload-and-verify-nspire.sh N_LINK_BIN ARTIFACT REMOTE_DIR TIMEOUT_SECONDS' >&2
  exit 2
fi

N_LINK_BIN="$1"
ARTIFACT="$2"
REMOTE_DIR="$3"
TIMEOUT_SECONDS="$4"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
NAME="$(basename "$ARTIFACT")"
REMOTE_FILE="${REMOTE_DIR%/}/$NAME"
EXPECTED_SHA="$(shasum -a 256 "$ARTIFACT" | awk '{print $1}')"
VERIFY_DIR="$(mktemp -d /tmp/nspire-upload-verify.XXXXXX)"
cleanup() {
  rm -f "$VERIFY_DIR/$NAME"
  rmdir "$VERIFY_DIR"
}
trap cleanup EXIT

python3 "$ROOT/scripts/run-with-timeout.py" "$TIMEOUT_SECONDS" \
  "$N_LINK_BIN" upload "$ARTIFACT" "$REMOTE_DIR"
python3 "$ROOT/scripts/run-with-timeout.py" "$TIMEOUT_SECONDS" \
  "$N_LINK_BIN" download "$REMOTE_FILE" "$VERIFY_DIR"

if [[ ! -f "$VERIFY_DIR/$NAME" ]]; then
  echo "Upload unverified: device readback did not produce $REMOTE_FILE" >&2
  exit 65
fi
READBACK_SHA="$(shasum -a 256 "$VERIFY_DIR/$NAME" | awk '{print $1}')"
if [[ "$READBACK_SHA" != "$EXPECTED_SHA" ]]; then
  echo "Upload unverified: device readback SHA mismatch ($READBACK_SHA != $EXPECTED_SHA)" >&2
  exit 65
fi
echo "VERIFIED $REMOTE_FILE sha256=$READBACK_SHA"
