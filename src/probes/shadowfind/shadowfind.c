/* Finds the OS's off-screen composition buffer (CX II CAS 6.2.0.333).
 * The LCD scans out of on-chip SRAM at 0xA8000000 and the OS keeps copying
 * its picture there; SDRAM buffers cannot be scanned out (lcdinfo probe).
 * Runs in main() with IRQs masked, so the OS is frozen: takes a 64-byte
 * sample of an interesting row of the visible frame, searches SDRAM for it,
 * checks each hit as the base of a full 320x240 RGB565 frame, and dumps the
 * start of the OS global GC.  Writes /documents/shadow.tns. */
#include <os.h>
#include <libndls.h>
#include <ngc.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

extern unsigned int nl_osid(void);
#define W 320
#define H 240
static FILE *out;

static void logx(const char *name, uint32_t v) {
    static const char hex[] = "0123456789abcdef";
    char line[64];
    int n = 0;
    while (*name && n < 40) line[n++] = *name++;
    line[n++] = '=';
    for (int i = 7; i >= 0; --i) line[n++] = hex[(v >> (i * 4)) & 15];
    line[n++] = '\n';
    line[n] = '\0';
    fputs(line, out);
}

int main(void) {
    if (nl_osid() != 46u) return 1;
    out = fopen("/documents/shadow.tns", "wb");
    if (!out) return 1;
    const uint16_t *lcd = (const uint16_t *)(uintptr_t)*(volatile uint32_t *)0xC0000010u;
    logx("lcd", (uint32_t)(uintptr_t)lcd);

    /* Pick a row segment with several distinct pixel values. */
    int row = -1, col = 0;
    for (int y = 20; y < H - 20 && row < 0; y += 7)
        for (int x = 0; x + 32 <= W && row < 0; x += 32) {
            int changes = 0;
            for (int i = 1; i < 32; ++i) changes += lcd[y * W + x + i] != lcd[y * W + x + i - 1];
            if (changes >= 6) { row = y; col = x; }
        }
    logx("row", (uint32_t)row);
    logx("col", (uint32_t)col);
    if (row < 0) { fclose(out); return 0; }
    const uint16_t *sample = &lcd[row * W + col];
    uint32_t first = ((const uint32_t *)sample)[0];
    uint32_t sample_off = (uint32_t)(row * W + col) * 2u;

    /* Only scan 1 MB regions mapped as sections; skip anything else so an
     * unmapped address cannot abort. */
    uint32_t ttb;
    __asm volatile("mrc p15, 0, %0, c2, c0, 0" : "=r"(ttb));
    const uint32_t *l1t = (const uint32_t *)(uintptr_t)(ttb & ~0x3FFFu);
    uint32_t skipped = 0;
    int hits = 0;
    for (uint32_t a = 0x10000000u; a < 0x14000000u - 64 && hits < 24; a += 4) {
        if ((a & 0xFFFFFu) == 0 && (l1t[a >> 20] & 3u) != 2u) {
            ++skipped;
            a += 0x100000u - 4;
            continue;
        }
        if (*(const uint32_t *)(uintptr_t)a != first) continue;
        if (memcmp((const void *)(uintptr_t)a, sample, 64) != 0) continue;
        uint32_t base = a - sample_off;
        uint32_t same = 0;
        if (base >= 0x10000000u && base + W * H * 2 <= 0x14000000u) {
            const uint16_t *cand = (const uint16_t *)(uintptr_t)base;
            for (int i = 0; i < W * H; i += 16) same += cand[i] == lcd[i];
        }
        logx("hit", a);
        logx("  base", base);
        logx("  same/4800", same);
        ++hits;
    }
    logx("hits", (uint32_t)hits);
    logx("skipped_mb", skipped);

    Gc gc = gui_gc_global_GC();
    logx("gc", (uint32_t)(uintptr_t)gc);
    if (gc) {
        const uint32_t *g = (const uint32_t *)gc;
        for (int i = 0; i < 48; ++i) logx("  gc_word", g[i]);
    }
    fclose(out);
    return 0;
}
