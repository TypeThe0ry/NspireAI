#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
WRAPPER="$ROOT/scripts/upload-and-verify-nspire.sh"
FAKE="$ROOT/scripts/fixtures/fake-n-link-upload.sh"
ARTIFACT="$ROOT/scripts/check-nspire-usb-state.sh"

FAKE_N_LINK_MODE=match FAKE_N_LINK_ARTIFACT="$ARTIFACT" \
  "$WRAPPER" "$FAKE" "$ARTIFACT" / 2 | grep -q '^VERIFIED /check-nspire-usb-state.sh sha256='

if FAKE_N_LINK_MODE=missing FAKE_N_LINK_ARTIFACT="$ARTIFACT" \
  "$WRAPPER" "$FAKE" "$ARTIFACT" / 2 >/dev/null 2>&1; then
  echo 'FAIL: missing device readback accepted' >&2
  exit 1
fi

if FAKE_N_LINK_MODE=mismatch FAKE_N_LINK_ARTIFACT="$ARTIFACT" \
  "$WRAPPER" "$FAKE" "$ARTIFACT" / 2 >/dev/null 2>&1; then
  echo 'FAIL: mismatched device readback accepted' >&2
  exit 1
fi

echo 'PASS: upload requires matching device readback despite zero-exit CLI'
