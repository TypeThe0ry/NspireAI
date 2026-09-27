#!/usr/bin/env python3
"""Audit the opt-in resident NGC task-handoff candidate."""

from pathlib import Path


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    makefile = (root / "src/program/Makefile").read_text()
    build = (root / "scripts/build-program-docker.sh").read_text()
    header = (root / "src/program/nav_task_handoff.h").read_text()
    ui = (root / "src/program/ui_ngc.h").read_text()
    remote = (root / "bridge/nspire-navnet-helper/NspireRemoteControl.java").read_text()
    idc = (root / ".deps/ndless/ndless/src/tools/MakeSyscalls/idc/OS_cascx2-6.2.0.333.idc").read_text()
    loader = (root / ".deps/ndless/ndless/src/resources/ploaderhook.c").read_text()

    required = [
        "NGC_TASK_HANDOFF",
        "NSPIRE_NGC_TASK_HANDOFF",
        "ngc_task_handoff=",
    ]
    for marker in required:
        if marker not in makefile + build + remote:
            raise SystemExit(f"FAIL: missing task-handoff build/upload marker {marker}")
    if "0X1042A8C8" not in idc or "TCC_Create_Task" not in idc:
        raise SystemExit("FAIL: CX II CAS TCC_Create_Task address is not present in pinned IDC")
    if "NSPIRE_CX2_CAS_TCC_CREATE_TASK ((uintptr_t)0x1042A8C8u)" not in header:
        raise SystemExit("FAIL: task ABI is not pinned to the CAS 6.2.0.333 address")
    if "#define NSPIRE_TASK_PRIORITY 255u" not in header:
        raise SystemExit("FAIL: task handoff priority must avoid preempting the IRQ-masked loader callback")
    if "nl_set_resident();" not in ui or "return EXIT_SUCCESS;" not in ui:
        raise SystemExit("FAIL: task handoff does not return through crt0 after retaining the Zehn image")
    if "_exit(EXIT_SUCCESS);" in ui:
        raise SystemExit("FAIL: task handoff bypasses the loader IRQ-restore path with _exit")
    if "TCT_Local_Control_Interrupts(0)" in header or "idle()" in header or "msleep" in header:
        raise SystemExit("FAIL: task handoff header contains an IRQ/timer/WFI workaround")
    mask = loader.index("TCT_Local_Control_Interrupts(-1)")
    entry = loader.index("ret = entry(argc, argv);")
    restore = loader.index("TCT_Local_Control_Interrupts(intmask)")
    if not mask < entry < restore:
        raise SystemExit("FAIL: loader IRQ ordering changed")
    print("PASS: resident task handoff is opt-in, OS-pinned, and upload-gated")
    print("PASS: nl_set_resident retains the Zehn image across loader return")
    print("RESULT: hardware ABI and physical USB loop remain unverified; do not upload without explicit override")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
