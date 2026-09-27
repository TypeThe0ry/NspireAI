#!/usr/bin/env bash
set -euo pipefail

# This is an explicit, opt-in deployment of the paired runtime/page candidate.
# It never touches dist/ and verifies every write with a device readback SHA.
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
HELPER="${NSPIRE_HELPER_BIN:-$ROOT/bridge/nspire-helper/target/debug/nspireai-usb-helper}"
RUNTIME="$ROOT/.build/runtime-boundary-variant/ndless_resources.tns"
PAGE="$ROOT/.build/runtime-boundary-variant/nspire_ai.tns"
EXPECTED_RUNTIME="e930ed866d08e44063539ecc7fd40d5611b272b64bfb6621f04f90fd34aadb0f"
EXPECTED_PAGE="4420b04290fc588805ed0439ca1aa6357ab19f57b77ad8bbd5f465a5aaa7baba"

if [[ "${NSPIRE_ALLOW_RUNTIME_BOUNDARY_UPLOAD:-}" != 1 ]]; then
  echo "Refusing runtime-boundary upload: set NSPIRE_ALLOW_RUNTIME_BOUNDARY_UPLOAD=1" >&2
  exit 65
fi
[[ -x "$HELPER" ]] || { echo "Ndless helper not found: $HELPER" >&2; exit 2; }
[[ -f "$RUNTIME" && -f "$PAGE" ]] || { echo "candidate pair is missing; build it first" >&2; exit 2; }

actual_runtime="$(shasum -a 256 "$RUNTIME" | awk '{print $1}')"
actual_page="$(shasum -a 256 "$PAGE" | awk '{print $1}')"
[[ "$actual_runtime" == "$EXPECTED_RUNTIME" ]] || { echo "runtime SHA mismatch" >&2; exit 65; }
[[ "$actual_page" == "$EXPECTED_PAGE" ]] || { echo "page SHA mismatch" >&2; exit 65; }

N_LINK_BIN="${N_LINK_BIN:-$ROOT/.deps/n-link/desktop/src-tauri/target/release/n-link}"
N_LINK_BIN="$N_LINK_BIN" "$ROOT/scripts/check-nspire-usb-state.sh" >/dev/stderr

VERIFY_DIR="$(mktemp -d /tmp/nspire-runtime-boundary-verify.XXXXXX)"
cleanup() { rm -rf "$VERIFY_DIR"; }
trap cleanup EXIT

verify_one() {
  local local_path="$1" remote_path="$2" name expected actual
  name="$(basename "$local_path")"
  expected="$(shasum -a 256 "$local_path" | awk '{print $1}')"
  echo "UPLOAD $remote_path" >&2
  "$HELPER" --upload "$local_path" "$remote_path"
  "$HELPER" --download "$remote_path" "$VERIFY_DIR/$name"
  actual="$(shasum -a 256 "$VERIFY_DIR/$name" | awk '{print $1}')"
  [[ "$actual" == "$expected" ]] || {
    echo "readback SHA mismatch for $remote_path: $actual != $expected" >&2
    exit 65
  }
  echo "VERIFIED $remote_path sha256=$actual"
}

verify_one "$RUNTIME" /ndless/ndless_resources.tns
verify_one "$PAGE" /nspire_ai.tns
