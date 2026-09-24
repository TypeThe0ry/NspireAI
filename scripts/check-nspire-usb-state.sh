#!/usr/bin/env bash
set -euo pipefail

# Read-only gate for the physical acceptance test.  A TPS DMC controller inside
# a CalDigit/Thunderbolt dock can remain visible on USB while the NavNet helper
# correctly says "no TI-Nspire USB device"; never mistake that controller for
# the handheld.
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

if grep -Fq 'TPS DMC Family' <<<"$USB_TREE" || \
   grep -Fq '"idProduct" = 44257' <<<"$USB_TREE"; then
  if grep -Fq 'CalDigit' <<<"$USB_TREE" || grep -Fq 'TS4 USB' <<<"$USB_TREE"; then
    echo 'STATE=NO_NSPIRE_DOCK_DMC_CONTROLLER product=0xACE1 vendor=0x0451'
    echo 'ACTION=connect-the-calculator-directly-or-through-a-known-good-data-path'
  else
    echo 'STATE=NON_NSPIRE_DMC_INTERFACE product=0xACE1 vendor=0x0451'
    echo 'ACTION=do-not-deploy-until-a-TI-Nspire-CX-II-interface-appears'
  fi
  exit 1
fi

echo 'STATE=UNKNOWN_USB_DEVICE'
exit 1
