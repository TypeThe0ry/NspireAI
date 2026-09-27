#!/usr/bin/env python3
"""Keep the unverified TCT_Schedule probe opt-in and OS-pinned."""

from pathlib import Path


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    source = (root / "src/program/ui_ngc.h").read_text()
    header = (root / "src/program/nav_scheduler_probe.h").read_text()
    build = (root / "scripts/build-program-docker.sh").read_text()
    idc = (root / ".deps/ndless/ndless/src/tools/MakeSyscalls/idc/OS_cascx2-6.2.0.333.idc").read_text()
    if "NGC_PROBE_STAGE" not in build or "|17" not in build:
        raise SystemExit("FAIL: scheduler probe stage is not build-gated")
    if "NSPIRE_NGC_PROBE_STAGE == 17" not in source:
        raise SystemExit("FAIL: resident scheduler probe is missing")
    if "TCT_Schedule" not in idc or "0X10623ABC" not in idc:
        raise SystemExit("FAIL: CX II CAS scheduler address is not pinned in Ndless IDC")
    if "0x10623ABCu" not in header or "nl_osid() != 46u" not in header:
        raise SystemExit("FAIL: scheduler probe is not pinned to OS index 46")
    if '#ifdef NSPIRE_NGC_PROBE' not in source or '#include "nav_scheduler_probe.h"' not in source:
        raise SystemExit("FAIL: scheduler helper is not probe-only")
    print("PASS: TCT_Schedule probe is opt-in and pinned to CX II CAS OS 46")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
