/* USB baseline probe: proves that a stock-Ndless program actually ran and
 * whether the TI USB stack stays responsive while it runs.  The plain build
 * makes no LCD, GC, key scan, RTC, NavNet, task or IRQ call.  PROBE_LCD adds
 * only the NGC page's lcd_type/lcd_init setup and SCR_TYPE_INVALID teardown;
 * PROBE_GC additionally takes the global GC and draws one frame (stage-3).
 * The host polls for the marker files to see how far the program got. */
#include <stdio.h>
#if defined(PROBE_LCD) || defined(PROBE_GC)
#include <libndls.h>
#endif
#ifdef PROBE_GC
#include <ngc.h>
#define PROBE_LCD
#endif
#ifndef HOLD_ROUNDS
#define HOLD_ROUNDS 4
#endif
#ifndef HOLD_ITERS
#define HOLD_ITERS 1200000000UL
#endif

static void write_marker(const char *path, const char *text) {
    FILE *f = fopen(path, "wb");
    if (!f) return;
    fputs(text, f);
    fclose(f);
}

/* Long enough (minutes) for the host to poll USB while the program runs. */
static void hold(void) {
    for (int round = 0; round < HOLD_ROUNDS; ++round)
        for (volatile unsigned long i = 0; i < HOLD_ITERS; ++i) { }
}

int main(void) {
    write_marker("/documents/marker_a.tns", "entry\n");
#ifdef PROBE_LCD
    scr_type_t type = lcd_type();
    if (type == SCR_TYPE_INVALID) type = SCR_320x240_565;
    if (!lcd_init(type)) {
        write_marker("/documents/marker_l.tns", "lcd_init failed\n");
        return 1;
    }
    write_marker("/documents/marker_l.tns", "lcd_init ok\n");
#endif
#ifdef PROBE_GC
    Gc gc = gui_gc_global_GC();
    if (!gc) {
        write_marker("/documents/marker_g.tns", "no gc\n");
        lcd_init(SCR_TYPE_INVALID);
        return 1;
    }
    gui_gc_begin(gc);
    gui_gc_setColorRGB(gc, 0, 128, 0);
    gui_gc_fillRect(gc, 0, 0, 320, 240);
    gui_gc_finish(gc);
    gui_gc_blit_to_screen(gc);
    write_marker("/documents/marker_g.tns", "frame drawn\n");
#endif
    hold();
    write_marker("/documents/marker_b.tns", "done\n");
#ifdef PROBE_LCD
    lcd_init(SCR_TYPE_INVALID);
    write_marker("/documents/marker_c.tns", "lcd released\n");
#endif
    return 0;
}
