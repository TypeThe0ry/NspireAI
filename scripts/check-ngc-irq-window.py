#!/usr/bin/env python3
"""Audit the opt-in NGC IRQ-window candidate without enabling it by default."""

from pathlib import Path


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    helper = (root / "src/program/nav_irq_window.h").read_text()
    makefile = (root / "src/program/Makefile").read_text()
    build = (root / "scripts/build-program-docker.sh").read_text()
    deploy = (root / "scripts/deploy-program-nspire.sh").read_text()
    remote = (root / "bridge/nspire-navnet-helper/NspireRemoteControl.java").read_text()
    if "window->controller[1] = ~(1u << 19);" not in helper:
        raise SystemExit("FAIL: IRQ window does not mask timer 19")
    if "TCT_Local_Control_Interrupts(0)" not in helper:
        raise SystemExit("FAIL: IRQ window does not enable CPU IRQ delivery")
    if "TCT_Local_Control_Interrupts(-1)" not in helper:
        raise SystemExit("FAIL: IRQ window does not close with CPU IRQs masked")
    if "NGC_USB_IRQ_WINDOW ?= FALSE" not in makefile:
        raise SystemExit("FAIL: IRQ window lacks a disabled build default")
    if "NGC_USB_IRQ_WINDOW" not in build:
        raise SystemExit("FAIL: Docker build does not propagate IRQ-window setting")
    gate = "NSPIRE_ALLOW_NGC_IRQ_WINDOW_UPLOAD"
    if gate not in deploy or "ngc_irq_window" not in deploy:
        raise SystemExit("FAIL: N-Link deployment lacks the IRQ-window opt-in gate")
    if gate not in remote or "ngc_irq_window=TRUE" not in remote:
        raise SystemExit("FAIL: Java deployment lacks the IRQ-window opt-in gate")
    rejected_sha = "c8c564c2910a2f907fc792b47329a591cbc93dcbfc9e8f61327e73d2ac75aadf"
    if "ngc_irq_menu" not in deploy or rejected_sha not in deploy:
        raise SystemExit("FAIL: N-Link deployment does not permanently reject the crashing Menu candidate")
    if "ngc_irq_menu=TRUE" not in remote or rejected_sha not in remote:
        raise SystemExit("FAIL: Java deployment does not permanently reject the crashing Menu candidate")
    if "physically-crashing" not in deploy or "physically-crashing" not in remote:
        raise SystemExit("FAIL: Menu candidate rejection does not record the physical crash")
    if "NGC_USB_IRQ_MENU" not in build or "Refusing to build the physically-crashing" not in build:
        raise SystemExit("FAIL: Docker build does not refuse the crashing Menu candidate")
    print("PASS: IRQ-window candidate is opt-in; the physically-crashing Menu path is permanently upload-blocked")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
