#!/usr/bin/env python3
"""Fail when the page calls an OS function missing from the OS symbol map.

Ndless resolves syscalls by number at run time; one that the CX II CAS
6.2.0.333 map does not define jumps into nowhere and takes the OS down
(TI_NN_GetConnMaxPktSize did, on 2026-09-29).  Source-level check: every
TI_NN_*/TCT_*/TCC_*/touchpad_* call in the given C files must be in the map.
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
IDC = ROOT / ".deps/ndless/ndless/src/tools/MakeSyscalls/idc/OS_cascx2-6.2.0.333.idc"
# SDK names that map onto differently named OS functions.
ALIASES = {
    "TI_NN_StartService": "TI_NN_SS_StartService",
    "TI_NN_StopService": "TI_NN_SS_StopService",
}
# libndls wrappers around a mapped OS function.
WRAPPERS = {"touchpad_scan": "touchpad_read"}
# Macros of this project that hold a private address, checked elsewhere.
IGNORED = {"TCC_TASK_SLEEP", "TCC_CREATE_TASK"}


def main(paths: list[str]) -> int:
    known = set(re.findall(r'MakeName\(0X[0-9A-F]+, "([^"]+)"\)', IDC.read_text()))
    failed = False
    for path in paths:
        text = Path(path).read_text()
        text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
        for name in sorted(set(re.findall(r"\b((?:TI_NN|TCT|TCC|touchpad)_\w+)\s*\(", text))):
            if name in IGNORED:
                continue
            target = WRAPPERS.get(name, ALIASES.get(name, name))
            if target not in known:
                print(f"FAIL: {path}: {name} is not in the OS 6.2.0.333 symbol map")
                failed = True
    if not failed:
        print("PASS: every OS call used by " + ", ".join(paths) + " is in the 6.2.0.333 map")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:] or [str(ROOT / "src/page/page.c")]))
