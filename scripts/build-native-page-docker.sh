#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
if ! command -v docker >/dev/null 2>&1; then
  echo "Docker is required for the resident Lua/NavNet module build" >&2
  exit 2
fi

"$ROOT/scripts/bootstrap-upstreams.sh"
git -C "$ROOT/.deps/ndless" submodule update --init --recursive

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
    sed -i "s/KEEP(\\*(SORT_BY_INIT_PRIORITY(REVERSE\\(\\.fini_array\\.\\*\\))))/KEEP(*(.fini_array.*))/" /sdk/system/ldscript
    make -C /sdk/thirdparty all
    make -C /sdk/libsyscalls
    make -C /sdk/libndls
    make -C /sdk/tools/genzehn
    make -C /sdk/tools/zehn_loader
    make -C /sdk/tools/luna
    make -C /work/src/native_page clean
    make -C /work/src/native_page LUNA=/sdk/tools/luna/luna
  '

OUT="$ROOT/dist/native-page"
mkdir -p "$OUT"
cp "$ROOT/src/native_page/nspire_ai.tns" "$OUT/nspire_ai.tns"
cp "$ROOT/src/native_page/nspire_ai_nav.luax.tns" "$OUT/nspire_ai_nav.luax.tns"
PAGE_SHA="$(shasum -a 256 "$OUT/nspire_ai.tns" | awk '{print $1}')"
EXT_SHA="$(shasum -a 256 "$OUT/nspire_ai_nav.luax.tns" | awk '{print $1}')"
cat > "$OUT/manifest.txt" <<EOF
page_sha256=$PAGE_SHA
extension_sha256=$EXT_SHA
page_backend=resident-lua-native-controls
transport=navnet-service-0x5001
build_status=success
EOF
printf 'built %s\n' "$OUT"
