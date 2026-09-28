/* USB baseline probe: proves that a stock-Ndless program actually ran and
 * whether the TI USB stack stays responsive while it runs.  No LCD, GC,
 * key scan, RTC, NavNet, task or IRQ call is made.  The host checks for
 * marker_a (entry reached) and marker_b (hold finished, clean return). */
#include <stdio.h>

static void write_marker(const char *path, const char *text) {
    FILE *f = fopen(path, "wb");
    if (!f) return;
    fputs(text, f);
    fclose(f);
}

int main(void) {
    write_marker("/documents/marker_a.tns", "entry\n");
    for (volatile unsigned long i = 0; i < 400000000UL; ++i) { }
    write_marker("/documents/marker_b.tns", "done\n");
    return 0;
}
