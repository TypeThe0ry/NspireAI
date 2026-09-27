#!/usr/bin/env python3
"""Audit the local Ndless loader/task boundary without touching a device.

This is deliberately an evidence check, not a task-spawn implementation. A
standalone entrypoint must not assume that an OS IDC label is a callable SDK
ABI or that a task created from a resident image will outlive loader cleanup.
"""

from pathlib import Path


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    loader = (root / ".deps/ndless/ndless/src/resources/ploaderhook.c").read_text()
    decls = (root / ".deps/ndless/ndless-sdk/include/syscall-decls.h").read_text()
    nucleus = (root / ".deps/ndless/ndless-sdk/include/nucleus.h").read_text()
    idc = (root / ".deps/ndless/ndless/src/tools/MakeSyscalls/idc/OS_cascx2-6.2.0.333.idc").read_text()

    entry = loader.index("ret = entry(argc, argv);")
    mask = loader.index("TCT_Local_Control_Interrupts(-1)")
    restore = loader.index("TCT_Local_Control_Interrupts(intmask)")
    if not mask < entry < restore:
        raise SystemExit("FAIL: loader IRQ mask/restore ordering changed")

    if "TCC_Create_Task" in decls or "TCC_Resume_Task" in decls:
        raise SystemExit("FAIL: public syscall declarations unexpectedly expose raw task creation")
    if "TCC_Create_Task" in nucleus or "TCC_Resume_Task" in nucleus:
        raise SystemExit("FAIL: nucleus.h unexpectedly exposes an undocumented task ABI")
    if "TCC_Create_Task" not in idc or "TCC_Resume_Task" not in idc:
        raise SystemExit("FAIL: target OS IDC task labels are missing; audit input changed")

    print("PASS: Ndless loader masks IRQs across entry and restores them only after return")
    print("PASS: CX II CAS 6.2 task-create/resume labels exist only in IDC, not the public SDK ABI")
    print("RESULT: no supported resident-task handoff is proven; do not upload a raw-address candidate")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
