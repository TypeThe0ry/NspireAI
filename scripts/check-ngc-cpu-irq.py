#!/usr/bin/env python3
"""Audit the opt-in CPU-IRQ-only NGC candidate without enabling it."""

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
    if "NSPIRE_NGC_CPU_IRQ" not in makefile or "NGC_CPU_IRQ" not in build:
        raise SystemExit("FAIL: CPU-IRQ build flag is not propagated")
    if "ngc_cpu_irq=$NGC_CPU_IRQ" not in build:
        raise SystemExit("FAIL: build manifest does not record CPU-IRQ mode")
    if "no override is permitted" not in deploy:
        raise SystemExit("FAIL: deployment does not hard-block the physically failed CPU-IRQ path")
    if "no override is permitted" not in remote:
        raise SystemExit("FAIL: Java deployment does not hard-block the physically failed CPU-IRQ path")
    helper = source.split("static void ngc_enable_cpu_irq", 1)[1].split(
        "static void ngc_restore_cpu_irq", 1
    )[0]
    if "TCT_Local_Control_Interrupts(0)" not in helper:
        raise SystemExit("FAIL: CPU-IRQ helper does not enable CPU IRQs")
    if "controller" in helper or "0x900" in helper:
        raise SystemExit("FAIL: CPU-IRQ-only helper touches controller/timer state")
    body = source.split("int main(void)", 1)[1]
    if body.index("ngc_draw();\n#ifdef NSPIRE_NGC_CPU_IRQ") > body.index(
        "ngc_enable_cpu_irq();"
    ):
        raise SystemExit("FAIL: CPU IRQs are enabled before the first frame")
    if "ngc_restore_cpu_irq();" not in body:
        raise SystemExit("FAIL: CPU IRQ state is not restored on exit")
    print("PASS: CPU-IRQ candidate remains auditable but every upload path hard-blocks it after the physical freeze")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
