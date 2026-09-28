#!/usr/bin/env bash
# Build the stock-Ndless USB baseline marker probe (src/probes/marker).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
"$ROOT/scripts/bootstrap-upstreams.sh"
git -C "$ROOT/.deps/ndless" submodule update --init --recursive
docker run --rm -v "$ROOT:/work" -v "$ROOT/.deps/ndless/ndless-sdk:/sdk" \
  debian:bookworm-slim bash -lc '
    set -e
    apt-get update -qq
    DEBIAN_FRONTEND=noninteractive apt-get install -y -qq \
      make gcc-arm-none-eabi binutils-arm-none-eabi libnewlib-arm-none-eabi \
      libboost-program-options-dev libboost-program-options1.74.0 > /tmp/apt.log
    export PATH=/sdk/bin:$PATH
    sed -i "s/-D_TINSPIRE /-D_TINSPIRE -DPATH_MAX=1024 /g" /sdk/libsyscalls/Makefile
    sed -i "s/KEEP(\\*(SORT_BY_INIT_PRIORITY(REVERSE\\(\\.fini_array\\.\\*\\))))/KEEP(*(.fini_array.*))/" /sdk/system/ldscript
    make -C /sdk/thirdparty all
    make -C /sdk/libsyscalls
    make -C /sdk/libndls
    make -C /sdk/tools/genzehn
    make -C /sdk/tools/zehn_loader
    make -C /work/src/probes/marker clean && make -C /work/src/probes/marker STEP=plain && make -C /work/src/probes/marker STEP=lcd && make -C /work/src/probes/marker STEP=gc && make -C /work/src/probes/marker STEP=irq && make -C /work/src/probes/marker STEP=task && make -C /work/src/probes/navtask clean all && make -C /work/src/probes/navsvc clean all
  '
shasum -a 256 "$ROOT"/src/probes/marker/*.tns "$ROOT"/src/probes/navtask/*.tns "$ROOT"/src/probes/navsvc/*.tns
