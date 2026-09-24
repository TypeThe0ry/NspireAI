#!/usr/bin/env python3
"""Check the NGC startup staging invariant without running calculator code."""
from pathlib import Path


def main() -> None:
    source = Path(__file__).resolve().parents[1] / "src/program/ui_ngc.h"
    text = source.read_text()
    body = text.split("int main(void)", 1)[1]
    first_draw = body.index("    ngc_draw();")
    first_clock = body.index("    last_tick = nav_clock_ms();")
    if first_draw >= first_clock:
        raise SystemExit("NGC startup regression: RTC is read before the first frame")
    if "nl_osid() != 46" in body and "return EXIT_FAILURE" in body.split("nl_osid() != 46", 1)[1].split("\n", 1)[0]:
        raise SystemExit("NGC startup regression: OS-id mismatch must not return the generic loader failure")
    if "if (!chat_gc) return EXIT_FAILURE;" not in body:
        raise SystemExit("NGC startup guard missing: global GC check changed")
    prepare = body.index("    if (!ngc_prepare_lcd()) return EXIT_FAILURE;")
    if prepare >= first_draw:
        raise SystemExit("NGC startup regression: first frame can run before lcd_init")
    gc = body.index("    chat_gc = gui_gc_global_GC();")
    if prepare >= gc:
        raise SystemExit("NGC startup regression: GC acquisition must follow lcd_init")
    helper = text.split("static int ngc_prepare_lcd(void)", 1)[1].split("static void ngc_line", 1)[0]
    if helper.index("ngc_screen_type = lcd_type();") >= helper.index("if (!lcd_init(ngc_screen_type)) return 0;"):
        raise SystemExit("NGC LCD setup regression: lcd_type/lcd_init order changed")
    print("PASS: NGC lcd_init and first frame precede first RTC read; OS-id mismatch is non-fatal and GC guard remains")


if __name__ == "__main__":
    main()
