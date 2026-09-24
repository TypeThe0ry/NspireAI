#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ARTIFACT="$ROOT/.build/ngc-lcdinit-trial/nspire_ai.tns"
ELF="$ROOT/.build/ngc-lcdinit-trial/nspire_ai.elf"
META="$ARTIFACT.meta"
EXPECTED_SHA="34345e069a1bbaacd1b4b289e37ada9d04d8f1105c11fa259b5d519937998f26"

for path in "$ARTIFACT" "$ELF" "$META"; do
  [[ -f "$path" ]] || { echo "FAIL: missing NGC lcd-init candidate file $path" >&2; exit 1; }
done

actual_sha="$(shasum -a 256 "$ARTIFACT" | awk '{print $1}')"
[[ "$actual_sha" == "$EXPECTED_SHA" ]] || {
  echo "FAIL: candidate SHA mismatch: got $actual_sha expected $EXPECTED_SHA" >&2
  exit 1
}
grep -qx "sha256=$EXPECTED_SHA" "$META"
grep -qx 'ui_backend=TRUE' "$META"
grep -qx 'build_status=success' "$META"

NM="${ARM_NM:-arm-none-eabi-nm}"
command -v "$NM" >/dev/null 2>&1 || { echo "FAIL: arm-none-eabi-nm not found" >&2; exit 1; }
symbols="$($NM "$ELF")"
for symbol in lcd_type lcd_init lcd_blit TI_NN_NodeEnumInit TI_NN_Read gettimeofday; do
  grep -Eq " [Tt] $symbol$" <<<"$symbols" || {
    echo "FAIL: candidate ELF is missing $symbol" >&2
    exit 1
  }
done
if grep -Eq ' (SDL_[[:alnum:]_]*|idle|msleep|gui_gc_blit_to_screen)$' <<<"$symbols"; then
  echo "FAIL: candidate ELF contains a blocked SDL/idle/legacy-blit symbol" >&2
  exit 1
fi

echo "PASS: NGC lcd-init candidate SHA, manifest, and ELF symbol audit"
