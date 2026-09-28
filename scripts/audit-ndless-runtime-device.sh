#!/usr/bin/env bash
set -euo pipefail

# Read-only audit of the Ndless runtime currently stored on the handheld.
# A reset/reinstall can silently restore the stock runtime; launching a
# standalone page under that runtime re-enters the IRQ-masked loader boundary.
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TIMEOUT_SECONDS="${NSPIRE_REMOTE_TIMEOUT_SECONDS:-20}"
STOCK_SHA="5994e5096d16e2c4289bc9bc976f0e50f6703c6c603cf9476e043c5a41a41330"
BOUNDARY_SHA="e930ed866d08e44063539ecc7fd40d5611b272b64bfb6621f04f90fd34aadb0f"

if [[ ! "$TIMEOUT_SECONDS" =~ ^[0-9]+([.][0-9]+)?$ ]] || [[ "$TIMEOUT_SECONDS" == 0 || "$TIMEOUT_SECONDS" == 0.* ]]; then
  echo "NSPIRE_REMOTE_TIMEOUT_SECONDS must be a positive number" >&2
  exit 2
fi

TMP_DIR="$(mktemp -d /tmp/nspire-runtime-audit.XXXXXX)"
cleanup() { rm -rf "$TMP_DIR"; }
trap cleanup EXIT

NSPIRE_REMOTE_TIMEOUT_SECONDS="$TIMEOUT_SECONDS" \
  "$ROOT/scripts/run-nspire-remote.sh" download /ndless/ndless_resources.tns "$TMP_DIR/ndless_resources.tns" \
  >"$TMP_DIR/remote.log"

SHA="$(shasum -a 256 "$TMP_DIR/ndless_resources.tns" | awk '{print $1}')"
BYTES="$(wc -c < "$TMP_DIR/ndless_resources.tns" | tr -d ' ')"
echo "REMOTE_ARTIFACT=/ndless/ndless_resources.tns"
echo "REMOTE_BYTES=$BYTES"
echo "REMOTE_SHA256=$SHA"
case "$SHA" in
  "$STOCK_SHA") echo "RUNTIME_CLASS=STOCK_NDLESS_R2022";;
  "$BOUNDARY_SHA") echo "RUNTIME_CLASS=LOADER_IRQ_BOUNDARY_VARIANT";;
  *) echo "RUNTIME_CLASS=UNKNOWN_NOT_APPROVED";;
esac
cat "$TMP_DIR/remote.log"
