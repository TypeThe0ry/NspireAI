#!/usr/bin/env bash
# Upload the chat page (src/page) to the calculator and verify it byte for byte.
#   deploy-page.sh            upload src/page/nspire_ai.tns as /nspire_ai.tns
#   deploy-page.sh autotest   upload the self-testing build as /nspire_ai_autotest.tns
#   deploy-page.sh dev        upload the service-0x5011 build as /nspire_ai_dev.tns
# Build first with scripts/build-marker-probe.sh.  Uses the raw USB helper,
# which can share the bus with an idle TI desktop app but not with a running
# bridge.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
H="$ROOT/bridge/nspire-helper/target/debug/nspireai-usb-helper"
case "${1:-page}" in
  page) SRC="$ROOT/src/page/nspire_ai.tns"; DEST=/nspire_ai.tns ;;
  autotest) SRC="$ROOT/src/page/nspire_ai_autotest.tns"; DEST=/nspire_ai_autotest.tns ;;
  dev) SRC="$ROOT/src/page/nspire_ai_dev.tns"; DEST=/nspire_ai_dev.tns ;;
  dev2) SRC="$ROOT/src/page/nspire_ai_dev2.tns"; DEST=/nspire_ai_dev2.tns ;;
  *) echo "usage: $0 [page|autotest|dev|dev2]" >&2; exit 2 ;;
esac
[[ -f "$SRC" ]] || { echo "missing $SRC; run scripts/build-marker-probe.sh" >&2; exit 2; }
python3 "$ROOT/scripts/check-ngc-memory-budget.py" "$SRC"
retry() { for _ in 1 2 3 4 5; do "$@" >/dev/null 2>&1 && return 0; sleep 3; done; return 1; }
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
retry "$H" --upload "$SRC" "$DEST" || { echo "upload failed" >&2; exit 1; }
retry "$H" --download "$DEST" "$TMP/readback" || { echo "readback failed" >&2; exit 1; }
WANT="$(shasum -a 256 "$SRC" | awk '{print $1}')"
GOT="$(shasum -a 256 "$TMP/readback" | awk '{print $1}')"
[[ "$WANT" == "$GOT" ]] || { echo "readback mismatch: $GOT != $WANT" >&2; exit 1; }
echo "DEPLOYED $DEST sha256=$WANT"
