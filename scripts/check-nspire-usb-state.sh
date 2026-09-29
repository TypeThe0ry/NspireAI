#!/usr/bin/env bash
set -euo pipefail

# Read-only gate: is a TI-Nspire handheld on USB?  The probe walks the whole
# USB tree, so the handheld is found on a built-in port, behind a dock, or
# behind any chain of hubs, and the TPS DMC power controller inside
# Thunderbolt docks (also TI vendor 0x0451) is never mistaken for it.
# Output contract (first line): STATE=CX2_USB_CANDIDATE product=0xE022
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
if command -v python3 >/dev/null 2>&1; then
  exec python3 "$ROOT/scripts/probe-nspire-usb.py" "$@"
fi

# Fallback without Python (macOS only): text match on the IORegistry.
USB_TREE="$(ioreg -p IOUSB -l -w0 2>/dev/null || true)"
if [[ -z "$USB_TREE" ]]; then
  echo 'STATE=NO_USB_TREE'
  exit 1
fi
if grep -Fq '"idVendor" = 1105' <<<"$USB_TREE" && \
   (grep -Fq '"idProduct" = 57378' <<<"$USB_TREE" || grep -Fq 'TI-Nspire CX' <<<"$USB_TREE"); then
  echo 'STATE=CX2_USB_CANDIDATE product=0xE022'
  echo 'ACTION=run-nspireai-usb-helper-info-then-deploy'
  exit 0
fi
echo 'STATE=NO_NSPIRE'
echo 'ACTION=plug-the-calculator-into-any-usb-port-hub-or-dock-and-wake-it'
exit 1
