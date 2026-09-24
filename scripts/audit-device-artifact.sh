#!/usr/bin/env bash
set -euo pipefail

# Read-only audit of the package currently stored on the handheld.  This is
# deliberately separate from every deployment path: it downloads one file to
# a temporary directory and never sends a package or a key to the calculator.
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TIMEOUT_SECONDS="${NSPIRE_REMOTE_TIMEOUT_SECONDS:-20}"
KNOWN_BLOCKED_SHA="e0282e26c2d5017b76e893a15d51aa8c44a78f94cebcbf23a8d1ff651029d75c"
STAGE_CANDIDATE_SHA="91820a3ac0ec564a995f0136e64b2c8d384ce9507ea8d410d9f99bb97fb70858"
LCDINIT_CANDIDATE_SHA="34345e069a1bbaacd1b4b289e37ada9d04d8f1105c11fa259b5d519937998f26"

if [[ ! "$TIMEOUT_SECONDS" =~ ^[0-9]+([.][0-9]+)?$ ]] || [[ "$TIMEOUT_SECONDS" == 0 || "$TIMEOUT_SECONDS" == 0.* ]]; then
  echo "NSPIRE_REMOTE_TIMEOUT_SECONDS must be a positive number" >&2
  exit 2
fi

TMP_DIR="$(mktemp -d /tmp/nspire-device-artifact-audit.XXXXXX)"
cleanup() { rm -rf "$TMP_DIR"; }
trap cleanup EXIT

NSPIRE_REMOTE_TIMEOUT_SECONDS="$TIMEOUT_SECONDS" \
  "$ROOT/scripts/run-nspire-remote.sh" download /nspire_ai.tns "$TMP_DIR/nspire_ai.tns" \
  >"$TMP_DIR/remote.log"

SHA="$(shasum -a 256 "$TMP_DIR/nspire_ai.tns" | awk '{print $1}')"
BYTES="$(wc -c < "$TMP_DIR/nspire_ai.tns" | tr -d ' ')"
echo "REMOTE_ARTIFACT=/nspire_ai.tns"
echo "REMOTE_BYTES=$BYTES"
echo "REMOTE_SHA256=$SHA"
case "$SHA" in
  "$KNOWN_BLOCKED_SHA") echo "REMOTE_CLASS=KNOWN_BLOCKED_E028_LCD_BLIT";;
  "$LCDINIT_CANDIDATE_SHA") echo "REMOTE_CLASS=LCDINIT_STAGE_CANDIDATE";;
  "$STAGE_CANDIDATE_SHA") echo "REMOTE_CLASS=SUPERSEDED_STAGE_ISOLATION_CANDIDATE";;
  *) echo "REMOTE_CLASS=UNKNOWN_NOT_APPROVED";;
esac
cat "$TMP_DIR/remote.log"
