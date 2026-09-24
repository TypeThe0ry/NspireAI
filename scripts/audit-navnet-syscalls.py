#!/usr/bin/env python3
"""Read-only source/runtime-file mapping audit; does not inspect device RAM."""
import json
import re
import argparse
import hashlib
import struct
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NDLESS = ROOT / ".deps/ndless"
SDK = NDLESS / "ndless-sdk/include"
GENERATOR = NDLESS / "ndless/src/tools/MakeSyscalls"
TARGET = "OS_cascx2-6.2.0.333.idc"
REQUIRED = (
    "TCT_Local_Control_Interrupts", "TI_NN_CreateOperationHandle",
    "TI_NN_DestroyOperationHandle", "TI_NN_NodeEnumInit",
    "TI_NN_NodeEnumNext", "TI_NN_NodeEnumDone", "TI_NN_Connect",
    "TI_NN_Disconnect", "TI_NN_Read", "TI_NN_Write",
)
GRAPHICS = (
    "ascii2utf16", "gui_gc_global_GC_ptr", "gui_gc_begin", "gui_gc_finish",
    "gui_gc_clipRect", "gui_gc_setColorRGB", "gui_gc_setFont",
    "gui_gc_setRegion", "gui_gc_drawString", "gui_gc_fillRect",
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime", type=Path,
                        help="readback runtime binary to scan for a matching address row")
    parser.add_argument("--graphics", action="store_true",
                        help="also audit graphics calls used by the offline NGC control")
    args = parser.parse_args()
    generator = (GENERATOR / "mkSyscalls.php").read_text()
    os_list = generator.split("$idc_files = array(", 1)[1].split(");", 1)[0]
    os_files = re.findall(r'"([^"]+\.idc)"', os_list)
    numbers = {name: int(number) for name, number in re.findall(
        r"^#define e_(\w+) (\d+)", (SDK / "syscall-list.h").read_text(), re.M)}
    labels = {}
    for address, name in re.findall(
            r'\bMakeName\s*\((0[xX][0-9A-Fa-f]+),\s*"([^"]+)"\);',
            (GENERATOR / "idc" / TARGET).read_text()):
        if int(address, 16) != 0xFFFFFFFF:
            labels.setdefault(name, []).append(address)
    rows = []
    for name in REQUIRED + (GRAPHICS if args.graphics else ()):
        if name not in numbers or name not in labels:
            raise SystemExit("Missing required syscall: " + name)
        rows.append({"name": name, "syscall_number": numbers[name],
                     "idc_address": labels[name][-1],
                     "idc_occurrences": len(labels[name])})
    # The upstream generator emits rows with one hex address per syscall.
    # Compare that actual artifact when present; do not substitute IDC evidence.
    table = SDK / "syscall-addrs.h"
    table_status = "absent; installed runtime mapping remains unverified"
    if table.exists():
        initializer = table.read_text().split("=", 1)[1]
        blocks = re.findall(r"\{\s*((?:0[xX][0-9A-Fa-f]+,\s*)+)\}", initializer)
        addresses = re.findall(r"0[xX][0-9A-Fa-f]+", blocks[os_files.index(TARGET)])
        for row in rows:
            actual = addresses[row["syscall_number"]]
            if int(actual, 16) != int(row["idc_address"], 16):
                raise SystemExit("Generated table mismatch: " + row["name"])
        table_status = "matches IDC; installed runtime mapping remains unverified"
    runtime_result = None
    if args.runtime:
        data = args.runtime.read_bytes()
        # Match every audited slot at its syscall-number-relative offset,
        # not isolated address constants, which can occur in other code.
        anchor = rows[0]
        needle = struct.pack("<I", int(anchor["idc_address"], 16))
        matches = []
        cursor = 0
        while True:
            position = data.find(needle, cursor)
            if position < 0:
                break
            cursor = position + 1
            base = position - 4 * anchor["syscall_number"]
            if base >= 0 and all(
                data[base + 4 * row["syscall_number"]:
                     base + 4 * row["syscall_number"] + 4]
                == struct.pack("<I", int(row["idc_address"], 16)) for row in rows):
                matches.append(hex(base))
        runtime_result = {"path": str(args.runtime), "bytes": len(data),
                          "sha256": hashlib.sha256(data).hexdigest(),
                          "matching_row_file_offsets": matches,
                          "limitation": "disk bytes only; not proof of loaded runtime or dispatch"}
        if not matches:
            raise SystemExit("No matching uncompressed runtime row; do not infer an ABI mismatch")
    print(json.dumps({"target": TARGET, "os_index": os_files.index(TARGET),
                      "generated_table": table_status, "syscalls": rows,
                      "runtime_scan": runtime_result,
                      "hardware_safety_verified": False}, indent=2))


if __name__ == "__main__":
    main()
