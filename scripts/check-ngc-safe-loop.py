#!/usr/bin/env python3
"""Keep the default NGC page launch path timer-neutral and transport-gated."""

from pathlib import Path


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    source = root / "src/program/ui_ngc.h"
    text = source.read_text()
    main_text = (root / "src/program/main.c").read_text()
    production = text.split("#else\n    uint32_t last_tick;", 1)[1].split("#endif\n}", 1)[0]
    if "msleep(" in production:
        raise SystemExit("FAIL: default NGC production loop must not call msleep")
    if "if (ngc_transport_armed && !nav_transport_is_blocked() && now != last_tick)" not in production:
        raise SystemExit("FAIL: NavNet polling is not gated by explicit transport arming")
    if "ngc_transport_armed = !ngc_transport_armed" not in text:
        raise SystemExit("FAIL: Menu transport toggle is missing")
    if "#define NGC_TRANSPORT_DEFAULT 0" not in text:
        raise SystemExit("FAIL: NGC startup must keep transport disarmed")
    if "nav_transport_is_blocked()" not in production:
        raise SystemExit("FAIL: NGC production loop lacks the transport fault hold")
    if "nav_transport_rearm();" not in text:
        raise SystemExit("FAIL: Menu retry does not explicitly rearm transport")
    if "if (!any_key_pressed())" not in text or "memset(previous, 0, sizeof(previous));" not in text:
        raise SystemExit("FAIL: NGC key loop lacks the aggregate no-key fast path")
    if "if (++scheduler_spin >= 128u)" not in text:
        raise SystemExit("FAIL: NGC loop samples the RTC on every spin")
    if "if (++key_sample_spin >= 512u)" not in production:
        raise SystemExit("FAIL: NGC loop still scans the full key matrix on every spin")
    if "static void nav_bootstrap_service_callback" in main_text:
        callback = main_text.split("static void nav_bootstrap_service_callback", 1)[1]
        callback = callback.split("static void nav_local_service_poll", 1)[0]
        if "TI_NN_Read" in callback or "TI_NN_Write" in callback:
            raise SystemExit("FAIL: NavNet service callback must not synchronously read/write on CX II")
    print("PASS: NGC startup is USB-idle and first NavNet failure is held")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
