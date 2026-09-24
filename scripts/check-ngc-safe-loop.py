#!/usr/bin/env python3
"""Keep the default NGC page launch path timer-neutral and transport-gated."""

from pathlib import Path


def main() -> int:
    source = Path(__file__).resolve().parents[1] / "src/program/ui_ngc.h"
    text = source.read_text()
    production = text.split("#else\n    uint32_t last_tick;", 1)[1].split("#endif\n}", 1)[0]
    if "msleep(" in production:
        raise SystemExit("FAIL: default NGC production loop must not call msleep")
    if "if (ngc_transport_armed && now != last_tick)" not in production:
        raise SystemExit("FAIL: NavNet polling is not gated by explicit transport arming")
    if "ngc_transport_armed = !ngc_transport_armed" not in text:
        raise SystemExit("FAIL: Menu transport toggle is missing")
    if "NGC_TRANSPORT_DEFAULT" not in text:
        raise SystemExit("FAIL: compile-time transport default is missing")
    print("PASS: NGC default launch is timer-neutral and NavNet is explicitly gated")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
