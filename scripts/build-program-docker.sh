#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
UI_NGC="${NSPIRE_UI_NGC:-FALSE}"
NGC_PROBE="${NSPIRE_NGC_PROBE:-FALSE}"
NGC_PROBE_STAGE="${NSPIRE_NGC_PROBE_STAGE:-0}"
NGC_AUTO_TRANSPORT="${NSPIRE_NGC_AUTO_TRANSPORT:-FALSE}"
NGC_CPU_IRQ="${NSPIRE_NGC_CPU_IRQ:-FALSE}"
NGC_USB_IRQ_WINDOW="${NSPIRE_NGC_USB_IRQ_WINDOW:-FALSE}"
NGC_USB_IRQ_MENU="${NSPIRE_NGC_USB_IRQ_MENU:-FALSE}"
case "$UI_NGC" in TRUE|FALSE) ;; *) echo "NSPIRE_UI_NGC must be TRUE or FALSE" >&2; exit 2;; esac
case "$NGC_PROBE" in TRUE|FALSE) ;; *) echo "NSPIRE_NGC_PROBE must be TRUE or FALSE" >&2; exit 2;; esac
case "$NGC_PROBE_STAGE" in 0|1|2|3|4|5|6|7|8|9|10|11) ;; *) echo "NSPIRE_NGC_PROBE_STAGE must be 0 through 11" >&2; exit 2;; esac
case "$NGC_AUTO_TRANSPORT" in TRUE|FALSE) ;; *) echo "NSPIRE_NGC_AUTO_TRANSPORT must be TRUE or FALSE" >&2; exit 2;; esac
case "$NGC_CPU_IRQ" in TRUE|FALSE) ;; *) echo "NSPIRE_NGC_CPU_IRQ must be TRUE or FALSE" >&2; exit 2;; esac
case "$NGC_USB_IRQ_WINDOW" in TRUE|FALSE) ;; *) echo "NSPIRE_NGC_USB_IRQ_WINDOW must be TRUE or FALSE" >&2; exit 2;; esac
case "$NGC_USB_IRQ_MENU" in TRUE|FALSE) ;; *) echo "NSPIRE_NGC_USB_IRQ_MENU must be TRUE or FALSE" >&2; exit 2;; esac
if [[ "$NGC_USB_IRQ_MENU" == TRUE ]]; then
  echo "Refusing to build the physically-crashing Menu-gated IRQ candidate" >&2
  exit 65
fi
if ! command -v docker >/dev/null 2>&1; then
  echo "Docker is required for the reproducible Ndless ARM build" >&2
  exit 2
fi

"$ROOT/scripts/bootstrap-upstreams.sh"
git -C "$ROOT/.deps/ndless" submodule update --init --recursive

docker run --rm \
  -e "UI_NGC=$UI_NGC" \
  -e "NGC_PROBE=$NGC_PROBE" \
  -e "NGC_PROBE_STAGE=$NGC_PROBE_STAGE" \
  -e "NGC_AUTO_TRANSPORT=$NGC_AUTO_TRANSPORT" \
  -e "NGC_CPU_IRQ=$NGC_CPU_IRQ" \
  -e "NGC_USB_IRQ_WINDOW=$NGC_USB_IRQ_WINDOW" \
  -e "NGC_USB_IRQ_MENU=$NGC_USB_IRQ_MENU" \
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
    # Do not reuse main.o across SDL/NGC flag changes; the object must be
    # rebuilt when UI_NGC changes.
    make -C /work/src/program clean
    make -C /work/src/program UI_NGC="$UI_NGC" NGC_PROBE="$NGC_PROBE" NGC_PROBE_STAGE="$NGC_PROBE_STAGE" NGC_AUTO_TRANSPORT="$NGC_AUTO_TRANSPORT" NGC_CPU_IRQ="$NGC_CPU_IRQ" NGC_USB_IRQ_WINDOW="$NGC_USB_IRQ_WINDOW" NGC_USB_IRQ_MENU="$NGC_USB_IRQ_MENU"
  '

mkdir -p "$ROOT/dist"
cp "$ROOT/src/program/nspire_ai.tns" "$ROOT/dist/nspire_ai.tns"
ARTIFACT_SHA="$(shasum -a 256 "$ROOT/dist/nspire_ai.tns" | awk '{print $1}')"
cat > "$ROOT/dist/nspire_ai.tns.meta" <<EOF
sha256=$ARTIFACT_SHA
ui_backend=$UI_NGC
ngc_auto_transport=$NGC_AUTO_TRANSPORT
ngc_cpu_irq=$NGC_CPU_IRQ
ngc_irq_window=$NGC_USB_IRQ_WINDOW
ngc_irq_menu=$NGC_USB_IRQ_MENU
build_status=success
EOF
printf 'built %s\n' "$ROOT/dist/nspire_ai.tns"
printf 'ui_backend=%s\n' "$UI_NGC"
printf 'ngc_probe=%s\n' "$NGC_PROBE"
printf 'ngc_probe_stage=%s\n' "$NGC_PROBE_STAGE"
printf 'ngc_auto_transport=%s\n' "$NGC_AUTO_TRANSPORT"
printf 'ngc_cpu_irq=%s\n' "$NGC_CPU_IRQ"
printf 'ngc_irq_window=%s\n' "$NGC_USB_IRQ_WINDOW"
printf 'ngc_irq_menu=%s\n' "$NGC_USB_IRQ_MENU"
