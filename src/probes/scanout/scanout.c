/* Private scan-out orientation probe (CX II CAS 6.2.0.333).
 * The LCD reads its buffer in the panel's native portrait order (CPL=240,
 * LPP=320); 0xA8000000 is "Magic VRAM" that rotates landscape writes for the
 * OS.  A private SDRAM buffer therefore has to be filled in portrait order.
 * Runs in main() with IRQs masked.  Shows one landscape test picture through
 * four candidate mappings, 3 s each, then restores the OS buffer:
 *   white background, red block top-left, green block top-right,
 *   blue block bottom-left, and N white-on-black squares along the top
 *   starting at the left for candidate N.
 * The user reports which N looks upright (red top-left, squares left to
 * right along the top edge). */
#include <os.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>

extern unsigned int nl_osid(void);
#define LCD_BASE (*(volatile uint32_t *)0xC0000010u)
#define W 320
#define H 240

static void clean_dcache(void) {
    unsigned zero = 0;
    __asm volatile(
        "0: mrc p15, 0, r15, c7, c10, 3\n"
        "   bne 0b\n"
        "   mcr p15, 0, %0, c7, c10, 4\n"
        : : "r"(zero) : "cc", "memory");
}

static uint16_t pixel(int x, int y, int n) {
    if (x < 40 && y < 40) return 0xF800;             /* red, top-left */
    if (x >= W - 40 && y < 40) return 0x07E0;        /* green, top-right */
    if (x < 40 && y >= H - 40) return 0x001F;        /* blue, bottom-left */
    if (y >= 50 && y < 80 && x >= 50 && x < 50 + n * 40) {
        int k = (x - 50) % 40;
        return k < 30 ? 0x0000 : 0xFFFF;             /* N black squares */
    }
    return 0xFFFF;
}

static int scan_index(int x, int y, int mapping) {
    switch (mapping) {
    case 1: return x * 240 + y;
    case 2: return x * 240 + (239 - y);
    case 3: return (319 - x) * 240 + y;
    default: return (319 - x) * 240 + (239 - y);
    }
}

static void hold_seconds(int s) {
    volatile uint32_t *rtc = (volatile uint32_t *)0x90090000u;
    uint32_t start = *rtc;
    while (*rtc - start < (uint32_t)s) { }
}

int main(void) {
    if (nl_osid() != 46u) return 1;
    uint8_t *raw = malloc(W * H * 2 + 64);
    if (!raw) return 1;
    uint16_t *buf = (uint16_t *)(((uintptr_t)raw + 31) & ~(uintptr_t)31);
    uint32_t os = LCD_BASE;
    for (int m = 1; m <= 4; ++m) {
        for (int y = 0; y < H; ++y)
            for (int x = 0; x < W; ++x)
                buf[scan_index(x, y, m)] = pixel(x, y, m);
        clean_dcache();
        LCD_BASE = (uint32_t)(uintptr_t)buf;
        hold_seconds(3);
        LCD_BASE = os;
    }
    free(raw);
    return 0;
}
