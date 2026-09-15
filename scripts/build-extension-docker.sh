#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
if ! command -v docker >/dev/null 2>&1; then
  echo "Docker is required for the reproducible Ndless ARM build" >&2
  exit 2
fi

"$ROOT/scripts/bootstrap-upstreams.sh"
git -C "$ROOT/.deps/ndless" submodule update --init --recursive

# Debian supplies an ARM GCC + newlib sysroot. The current Ndless SDK's linker
# script uses REVERSE(), which binutils 2.40 in Debian does not accept; the
# equivalent ordered fini-array wildcard is sufficient for this extension.
docker run --rm \
  -v "$ROOT:/work" \
  -v "$ROOT/.deps/ndless/ndless-sdk:/sdk" \
  debian:bookworm-slim bash -lc '
    set -e
    apt-get update -qq
    DEBIAN_FRONTEND=noninteractive apt-get install -y -qq \
      make gcc-arm-none-eabi binutils-arm-none-eabi libnewlib-arm-none-eabi \
      libboost-program-options-dev libboost-program-options1.74.0 > /tmp/apt.log
    export PATH=/sdk/bin:$PATH
    sed -i "s/-D_TINSPIRE /-D_TINSPIRE -DPATH_MAX=1024 /g" /sdk/libsyscalls/Makefile
    sed -i "s/KEEP(\\*(SORT_BY_INIT_PRIORITY(REVERSE(\\.fini_array\\.\\*))))/KEEP(*(.fini_array.*))/" /sdk/system/ldscript
    make -C /sdk/thirdparty all
    make -C /sdk/libsyscalls
    make -C /sdk/libndls
    make -C /sdk/tools/genzehn
    make -C /sdk/tools/zehn_loader
    make -C /work/src/extension
  '

mkdir -p "$ROOT/dist"
cp "$ROOT/src/extension/nspire_ai.luax.tns" "$ROOT/dist/nspire_ai.luax.tns"
printf 'built %s\n' "$ROOT/dist/nspire_ai.luax.tns"
