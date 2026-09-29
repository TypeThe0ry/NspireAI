#!/usr/bin/env python3
"""Find TI-Nspire handhelds on USB wherever they are plugged in.

Read-only.  Walks the whole USB tree, so a handheld behind a dock, a hub or
a chain of hubs is found the same way as one on a built-in port.  A
handheld is identified by its own descriptor (TI vendor 0x0451 with a
TI-Nspire product id, or a TI-Nspire product name); other TI devices, such
as the TPS DMC power controller inside Thunderbolt docks (0x0451:0xACE1),
are reported but never mistaken for a handheld.

Output (first line is the contract used by the other scripts):
  STATE=CX2_USB_CANDIDATE product=0xE022     a CX II handheld is present
  STATE=NSPIRE_USB_CANDIDATE product=0x....  another TI-Nspire model
  STATE=NO_NSPIRE ...                        none found
followed by one DEVICE= line per handheld with its path through the hubs.
Exit status 0 when a handheld is present.  `--json` prints the details.
"""
from __future__ import annotations

import json
import platform
import plistlib
import subprocess
import sys
from pathlib import Path

TI_VENDOR = 0x0451
NSPIRE_PRODUCTS = {
    0xE012: "TI-Nspire (CX and earlier)",
    0xE022: "TI-Nspire CX II",
}
CX2_PRODUCT = 0xE022


def is_handheld(device: dict) -> bool:
    if device["vendor"] == TI_VENDOR and device["product"] in NSPIRE_PRODUCTS:
        return True
    return device["vendor"] == TI_VENDOR and "nspire" in device["name"].lower()


def macos_devices() -> list[dict]:
    raw = subprocess.run(["ioreg", "-p", "IOUSB", "-l", "-w0", "-a"],
                         capture_output=True, timeout=20).stdout
    if not raw:
        return []
    found: list[dict] = []

    def walk(node: dict, path: list[str]) -> None:
        vendor, product = node.get("idVendor"), node.get("idProduct")
        name = (node.get("USB Product Name") or node.get("kUSBProductString")
                or node.get("IORegistryEntryName") or "")
        here = path
        if isinstance(vendor, int) and isinstance(product, int):
            found.append({
                "vendor": vendor, "product": product, "name": str(name),
                "serial": str(node.get("USB Serial Number") or node.get("kUSBSerialNumberString") or ""),
                "location": node.get("locationID"),
                "speed": node.get("Device Speed", node.get("UsbLinkSpeed")),
                "path": list(path),
            })
            here = path + [str(name) or f"{vendor:04x}:{product:04x}"]
        for child in node.get("IORegistryEntryChildren", []):
            walk(child, here)

    tree = plistlib.loads(raw)
    for root in tree if isinstance(tree, list) else [tree]:
        walk(root, [])
    return found


def linux_devices() -> list[dict]:
    found: list[dict] = []
    base = Path("/sys/bus/usb/devices")
    if not base.is_dir():
        return found

    def read(path: Path) -> str:
        try:
            return path.read_text().strip()
        except OSError:
            return ""

    names: dict[str, str] = {}
    for entry in sorted(base.iterdir()):
        vendor, product = read(entry / "idVendor"), read(entry / "idProduct")
        if not vendor or not product:
            continue
        name = read(entry / "product") or f"{vendor}:{product}"
        names[entry.name] = name
        # "1-4.2.1" sits behind "1-4.2", which sits behind "1-4".
        parents, key = [], entry.name
        while "." in key:
            key = key.rsplit(".", 1)[0]
            parents.insert(0, names.get(key, key))
        found.append({
            "vendor": int(vendor, 16), "product": int(product, 16), "name": name,
            "serial": read(entry / "serial"), "location": entry.name,
            "speed": read(entry / "speed"), "path": parents,
        })
    return found


def main(argv: list[str]) -> int:
    system = platform.system()
    try:
        devices = macos_devices() if system == "Darwin" else linux_devices()
    except Exception as exc:  # a probe must never crash its caller
        print(f"STATE=USB_PROBE_FAILED reason={type(exc).__name__}")
        return 2
    handhelds = [d for d in devices if is_handheld(d)]
    others = [d for d in devices if d["vendor"] == TI_VENDOR and not is_handheld(d)]
    if "--json" in argv:
        print(json.dumps({"handhelds": handhelds, "other_ti_devices": others}, indent=1))
        return 0 if handhelds else 1
    if not handhelds:
        if not devices:
            print("STATE=NO_USB_TREE")
        elif others:
            seen = ",".join(f"0x{d['product']:04X}" for d in others)
            print(f"STATE=NO_NSPIRE other_ti_devices={seen}")
            print("ACTION=plug-the-calculator-into-any-usb-port-hub-or-dock-and-wake-it")
        else:
            print("STATE=NO_NSPIRE")
            print("ACTION=plug-the-calculator-into-any-usb-port-hub-or-dock-and-wake-it")
        return 1
    # Prefer a CX II, which is what the page targets.
    handhelds.sort(key=lambda d: d["product"] != CX2_PRODUCT)
    first = handhelds[0]
    state = "CX2_USB_CANDIDATE" if first["product"] == CX2_PRODUCT else "NSPIRE_USB_CANDIDATE"
    print(f"STATE={state} product=0x{first['product']:04X}")
    print("ACTION=run-nspireai-usb-helper-info-then-deploy")
    for device in handhelds:
        via = " > ".join(device["path"]) or "built-in port"
        location = device["location"]
        where = f"0x{location:08x}" if isinstance(location, int) else str(location)
        print(f"DEVICE=0x{device['vendor']:04X}:0x{device['product']:04X} name={device['name']!r} "
              f"serial={device['serial'] or '-'} location={where} via={via!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
