#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ARTIFACT="$ROOT/.build/ngc-enum-retry/nspire_ai_enum_retry.tns"
EXPECTED_SHA="75c00b5ed6ff518378ff3486bc2f0fd7e4d82af6ec378211df522977692259e2"
N_LINK_BIN="${N_LINK_BIN:-$ROOT/.deps/n-link/desktop/src-tauri/target/release/n-link}"
REMOTE_DIR="${REMOTE_DIR:-/}"
TIMEOUT_SECONDS="${NSPIRE_UPLOAD_TIMEOUT_SECONDS:-60}"

if [[ "${NSPIRE_ALLOW_NGC_ENUM_RETRY_UPLOAD:-}" != "1" ]]; then
  echo "Refusing enum-retry upload: set NSPIRE_ALLOW_NGC_ENUM_RETRY_UPLOAD=1 after reviewing SHA $EXPECTED_SHA" >&2
  exit 65
fi
[[ -x "$N_LINK_BIN" ]] || { echo "Missing n-link: $N_LINK_BIN" >&2; exit 2; }
[[ -f "$ARTIFACT" ]] || { echo "Missing candidate: $ARTIFACT" >&2; exit 2; }
actual_sha="$(shasum -a 256 "$ARTIFACT" | awk '{print $1}')"
[[ "$actual_sha" == "$EXPECTED_SHA" ]] || {
  echo "Candidate SHA mismatch: got $actual_sha expected $EXPECTED_SHA" >&2
  exit 65
}
"$ROOT/scripts/check-nspire-usb-state.sh"
exec "$ROOT/scripts/upload-and-verify-nspire.sh" \
  "$N_LINK_BIN" "$ARTIFACT" "$REMOTE_DIR" "$TIMEOUT_SECONDS"
