#!/usr/bin/env python3
"""Ensure the physically failed CPU-IRQ experiment cannot be rebuilt/uploaded."""

from pathlib import Path


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    makefile = (root / "src/program/Makefile").read_text()
    source = (root / "src/program/ui_ngc.h").read_text()
    build = (root / "scripts/build-program-docker.sh").read_text()
    deploy = (root / "scripts/deploy-program-nspire.sh").read_text()
    remote = (root / "bridge/nspire-navnet-helper/NspireRemoteControl.java").read_text()
    if "NGC_CPU_IRQ ?= FALSE" not in makefile:
        raise SystemExit("FAIL: CPU-IRQ candidate is not disabled by default")
    if "NGC_CPU_IRQ" not in makefile or "NGC_CPU_IRQ" not in build:
        raise SystemExit("FAIL: CPU-IRQ rejection setting is not propagated")
    if "ngc_cpu_irq=$NGC_CPU_IRQ" not in build:
        raise SystemExit("FAIL: build manifest does not record CPU-IRQ mode")
    if "no override is permitted" not in deploy:
        raise SystemExit("FAIL: deployment does not hard-block the physically failed CPU-IRQ path")
    if "no override is permitted" not in remote:
        raise SystemExit("FAIL: Java deployment does not hard-block the physically failed CPU-IRQ path")
    if "TCT_Local_Control_Interrupts(0)" in source:
        raise SystemExit("FAIL: NGC UI still contains the physically failed CPU-IRQ re-enable path")
    if "permanently disabled" not in makefile or "Refusing to build CPU-IRQ candidate" not in build:
        raise SystemExit("FAIL: build paths do not permanently reject CPU-IRQ candidates")
    if "no override is permitted" not in deploy or "no override is permitted" not in remote:
        raise SystemExit("FAIL: upload paths do not hard-block the physically failed CPU-IRQ path")
    print("PASS: CPU-IRQ experiment is removed from NGC UI and blocked by build/upload paths")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
