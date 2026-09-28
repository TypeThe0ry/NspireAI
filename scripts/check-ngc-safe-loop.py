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
    if "USB armed by Enter; bridge must be READY" not in text:
        raise SystemExit("FAIL: Enter transport arm is missing")
    if "KEY_NSPIRE_HOME" not in text:
        raise SystemExit("FAIL: Home teardown key guard is missing")
    if "&KEY_NSPIRE_MENU" in text:
        raise SystemExit("FAIL: CX II Menu key must not be read by the NGC matrix scanner")
    if "#define NGC_TRANSPORT_DEFAULT 0" not in text:
        raise SystemExit("FAIL: NGC startup must keep transport disarmed")
    if "nav_transport_is_blocked()" not in production:
        raise SystemExit("FAIL: NGC production loop lacks the transport fault hold")
    if "nav_transport_rearm();" not in text:
        raise SystemExit("FAIL: Menu retry does not explicitly rearm transport")
    if "!any_key_pressed()" not in text or "!is_touchpad && !any_key_pressed()" not in text or \
            "memset(previous, 0, sizeof(previous));" not in text:
        raise SystemExit("FAIL: NGC key loop lacks the aggregate no-key fast path")
    if "if (++scheduler_spin >= 128u)" not in text:
        raise SystemExit("FAIL: NGC loop samples the RTC on every spin")
    if "if (++key_sample_spin >= 512u)" not in production:
        raise SystemExit("FAIL: NGC loop still scans the full key matrix on every spin")
    if "idle();" in production:
        raise SystemExit("FAIL: standalone NGC production loop must not call idle/WFI with loader IRQs masked")
    if "for (volatile unsigned spin = 0; spin < 256; ++spin)" not in production:
        raise SystemExit("FAIL: NGC loop lacks its bounded non-WFI scheduler slice")
    if "if (!ngc_transport_armed)" not in text:
        raise SystemExit("FAIL: Enter arm guard is missing")
    if "#define NAV_ERR_INCOMPLETE_TRANSACTION (-258)" not in main_text or \
            "#define NAV_ERR_BUSY (-269)" not in main_text:
        raise SystemExit("FAIL: transient NavNet read statuses are not named")
    read_body = main_text.split("static void nav_poll", 1)[1].split(
        "static void append_input", 1)[0]
    if "status == NAV_ERR_INCOMPLETE_TRANSACTION || status == NAV_ERR_BUSY" not in read_body:
        raise SystemExit("FAIL: transient NavNet read statuses still tear down the channel")
    if "nav_read_at = nav_clock_ms() + NAV_READ_ARM_DELAY_MS;" not in read_body:
        raise SystemExit("FAIL: transient NavNet read statuses lack retry backoff")
    ping_body = read_body.split(
        "if (!nav_ping_sent && nav_deadline_reached(now, nav_ping_at))", 1
    )[1].split("if (!nav_ping_sent ||", 1)[0]
    write_pos = ping_body.find("nav_write_frame(OP_PING")
    arm_pos = ping_body.find("nav_ping_sent = 1;")
    if write_pos < 0 or arm_pos < 0 or write_pos > arm_pos:
        raise SystemExit("FAIL: PING must arm the read path only after TI_NN_Write succeeds")
    write_body = main_text.split("static int nav_write_frame", 1)[1].split(
        "static void reset_conversation", 1)[0]
    if 'if (status == -257)' not in write_body or \
            'nav_abandon_channel("write invalid connection")' not in write_body:
        raise SystemExit("FAIL: invalid NavNet write must abandon stale handle without Disconnect")
    connect_body = main_text.split("static int nav_try_connect", 1)[1].split(
        "static int nav_write_frame", 1)[0]
    enum_done = connect_body.find("TI_NN_NodeEnumDone")
    connect_call = connect_body.find("TI_NN_Connect")
    if enum_done < 0 or connect_call < 0 or enum_done > connect_call:
        raise SystemExit("FAIL: NavNet connect must follow NodeEnumDone")
    if "static void nav_bootstrap_service_callback" in main_text:
        callback = main_text.split("static void nav_bootstrap_service_callback", 1)[1]
        callback = callback.split("static void nav_local_service_poll", 1)[0]
        if "TI_NN_Read" in callback or "TI_NN_Write" in callback:
            raise SystemExit("FAIL: NavNet service callback must not synchronously read/write on CX II")
    connect_body = main_text.split("static int nav_try_connect", 1)[1].split(
        "static int nav_write_frame", 1
    )[0]
    if 'if (status == -274)' not in connect_body or \
            'USB peer absent; retrying enum -274' not in connect_body:
        raise SystemExit("FAIL: empty NavNet enumeration must retry without Menu")
    retry_block = connect_body.split('if (status == -274)', 1)[1].split('} else {', 1)[0]
    if 'nav_transport_hold()' in retry_block:
        raise SystemExit("FAIL: enum -274 still holds transport")
    print("PASS: NGC startup is USB-idle and empty NavNet enumeration retries automatically")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
