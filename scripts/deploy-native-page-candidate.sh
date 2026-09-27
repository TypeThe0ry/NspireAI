#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
if [[ "${NSPIRE_ALLOW_NATIVE_PAGE_UPLOAD:-0}" != 1 ]]; then
  echo "Refusing resident Lua page upload; set NSPIRE_ALLOW_NATIVE_PAGE_UPLOAD=1 after reviewing both artifacts" >&2
  exit 65
fi

PAGE="$ROOT/dist/native-page/nspire_ai.tns"
EXT="$ROOT/dist/native-page/nspire_ai_nav.luax.tns"
for file in "$PAGE" "$EXT"; do
  [[ -f "$file" ]] || { echo "missing native-page artifact: $file" >&2; exit 2; }
  meta="$file.meta"
  [[ -f "$meta" ]] || { echo "missing native-page manifest: $meta" >&2; exit 2; }
  grep -q '^build_status=success$' "$meta" || { echo "native-page manifest is not successful: $meta" >&2; exit 65; }
  actual="$(shasum -a 256 "$file" | awk '{print $1}')"
  grep -q "^sha256=$actual$" "$meta" || { echo "native-page hash mismatch: $file" >&2; exit 65; }
done

upload_one() {
  local source="$1" remote="$2"
  "$ROOT/scripts/run-nspire-remote.sh" upload "$source" "$remote"
}

# Install the resident module first. The page is uploaded last so it cannot be
# opened while its nrequire dependency is absent.
upload_one "$EXT" /nspire_ai_nav.luax.tns
upload_one "$PAGE" /nspire_ai.tns
echo "Native page candidate uploaded; do not open it until both readbacks pass."
