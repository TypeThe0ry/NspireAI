#!/usr/bin/env bash
# Produces an ELF only. Never packages, uploads, bootstraps or edits the SDK.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
mkdir -p "$ROOT/.build/ngc-control"
docker run --rm -v "$ROOT:/work:ro" \
  -v "$ROOT/.build/ngc-control:/out" debian:bookworm-slim bash -lc '
    set -euo pipefail
    apt-get update -qq
    DEBIAN_FRONTEND=noninteractive apt-get install -y -qq \
      gcc-arm-none-eabi libnewlib-arm-none-eabi >/tmp/apt.log
    sdk=/work/.deps/ndless/ndless-sdk
    arm-none-eabi-gcc -mcpu=arm926ej-s -marm -D_TINSPIRE -Os \
      -fPIE -ffunction-sections -fdata-sections -Wall -Wextra -Werror \
      -I"$sdk/include" -c /work/src/program/diagnostic_ngc.c -o /out/control.o
    # Audit the source object, not the final whole-archive image: libsyscalls
    # carries generic IRQ helpers even when this control never references
    # them. This catches an accidental transport/timer dependency without
    # mistaking unused SDK archive members for a call from the probe.
    if arm-none-eabi-nm -u /out/control.o | grep -E " (SDL_[[:alnum:]_]*|TI_NN_[[:alnum:]_]*|msleep|idle|TCT_Local_Control_Interrupts)$"; then
      echo "Unexpected transport/timer/IRQ reference in control object" >&2
      exit 1
    fi
    arm-none-eabi-gcc -mcpu=arm926ej-s -marm -nostartfiles \
      -Wl,-T,"$sdk/system/ldscript",--gc-sections,-Map,/out/control.map \
      "$sdk/system/crt0.o" "$sdk/system/crti.o" /out/control.o \
      "$sdk/system/crtn.o" -L"$sdk/lib" \
      -Wl,--whole-archive -lsyscalls -Wl,--no-whole-archive \
      -Wl,--start-group -lndls -lc -lgcc -Wl,--end-group \
      -o /out/control.elf
    arm-none-eabi-nm /out/control.elf > /out/control.symbols
    if grep -E " (SDL_[[:alnum:]_]*|TI_NN_[[:alnum:]_]*|msleep|idle|gui_gc_blit_to_screen)$" /out/control.symbols; then
      echo "Unexpected transport/timer/legacy-blit symbol in control ELF" >&2
      exit 1
    fi
    grep -Eq " (lcd_blit|lcd_type)$" /out/control.symbols
    arm-none-eabi-size /out/control.elf
    sha256sum /out/control.elf
  '
echo "Offline ELF only: $ROOT/.build/ngc-control/control.elf (not hardware validated)"
