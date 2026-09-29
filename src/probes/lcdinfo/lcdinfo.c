/* LCD scan-out probe (CX II CAS 6.2.0.333).  Runs entirely in main(), where
 * the loader keeps IRQs masked, so the OS cannot repaint meanwhile.
 * 1. Logs the LCD controller registers, the OS framebuffer address, the MMU
 *    translation table base and the first/second-level descriptors for the
 *    OS framebuffer and for heap buffers to /documents/lcdinfo.tns.
 * 2. Shows a test picture from three candidate buffers for 3 s each, then
 *    restores the OS buffer: top half red, bottom half blue, and N white
 *    squares at the top left for variant N:
 *      1 = plain malloc buffer
 *      2 = malloc buffer aligned to 4 KB
 *      3 = the OS framebuffer itself (reference: must look right)
 * The user reports which variants look right on the real screen. */
#include <os.h>
#include <libndls.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

extern unsigned int nl_osid(void);
#define LCD_REG(o) (*(volatile uint32_t *)(0xC0000000u + (o)))
#define W 320
#define H 240

static FILE *log_file;

static void logx(const char *name, uint32_t v) {
    char line[64];
    static const char hex[] = "0123456789abcdef";
    int n = 0;
    while (*name && n < 40) line[n++] = *name++;
    line[n++] = '=';
    for (int i = 7; i >= 0; --i) line[n++] = hex[(v >> (i * 4)) & 15];
    line[n++] = '\n';
    line[n] = '\0';
    if (log_file) fputs(line, log_file);
}

static void describe(const char *name, uint32_t va, uint32_t ttb) {
    logx(name, va);
    uint32_t *l1t = (uint32_t *)(uintptr_t)(ttb & ~0x3FFFu);
    uint32_t l1 = l1t[va >> 20];
    logx("  l1", l1);
    if ((l1 & 3) == 2) {
        logx("  pa(section)", (l1 & 0xFFF00000u) | (va & 0x000FFFFFu));
    } else if ((l1 & 3) == 1) {
        uint32_t base = l1 & ~0x3FFu;
        if (base >= 0x10000000u && base < 0x14000000u) {
            uint32_t l2 = ((uint32_t *)(uintptr_t)base)[(va >> 12) & 0xFF];
            logx("  l2(coarse)", l2);
        }
    } else if ((l1 & 3) == 3) {
        uint32_t base = l1 & ~0xFFFu;
        if (base >= 0x10000000u && base < 0x14000000u) {
            uint32_t l2 = ((uint32_t *)(uintptr_t)base)[(va >> 10) & 0x3FF];
            logx("  l2(fine)", l2);
        }
    }
}

static void clean_dcache(void) {
    unsigned zero = 0;
    __asm volatile(
        "0: mrc p15, 0, r15, c7, c10, 3\n"
        "   bne 0b\n"
        "   mcr p15, 0, %0, c7, c10, 4\n"
        : : "r"(zero) : "cc", "memory");
}

static void paint(uint16_t *buf, int hww, int variant) {
    for (int y = 0; y < H; ++y)
        for (int x = 0; x < W; ++x) {
            uint16_t c = y < H / 2 ? 0xF800 : 0x001F;
            if (y >= 8 && y < 24 && x >= 8 && x < 8 + variant * 24 && (x - 8) % 24 < 16)
                c = 0xFFFF;
            if (hww) buf[x * 240 + y] = c;
            else buf[y * W + x] = c;
        }
}

static void hold_seconds(int s) {
    volatile uint32_t *rtc = (volatile uint32_t *)0x90090000u;
    uint32_t start = *rtc;
    while (*rtc - start < (uint32_t)s) { }
}

int main(void) {
    if (nl_osid() != 46u) return 1;
    log_file = fopen("/documents/lcdinfo.tns", "wb");
    uint32_t ttb;
    __asm volatile("mrc p15, 0, %0, c2, c0, 0" : "=r"(ttb));
    int hww = lcd_type() == SCR_240x320_565;
    logx("hww", hww);
    for (unsigned o = 0; o <= 0x2C; o += 4) {
        char name[8] = "lcd+00";
        name[4] = "0123456789abcdef"[o >> 4];
        name[5] = "0123456789abcdef"[o & 15];
        logx(name, LCD_REG(o));
    }
    logx("ttb", ttb);
    uint32_t osfb = LCD_REG(0x10);
    describe("osfb", osfb, ttb);
    uint16_t *plain = malloc(W * H * 2);
    uint8_t *raw = malloc(W * H * 2 + 4096);
    uint16_t *aligned = raw ? (uint16_t *)(((uintptr_t)raw + 4095) & ~(uintptr_t)4095) : NULL;
    if (plain) describe("plain", (uint32_t)(uintptr_t)plain, ttb);
    if (aligned) describe("aligned", (uint32_t)(uintptr_t)aligned, ttb);
    if (log_file) fclose(log_file);
    log_file = NULL;

    uint16_t *os = (uint16_t *)(uintptr_t)osfb;
    uint16_t *saved = malloc(W * H * 2);
    if (saved) memcpy(saved, os, W * H * 2);

    uint16_t *bufs[3] = {plain, aligned, os};
    for (int v = 0; v < 3; ++v) {
        if (!bufs[v]) continue;
        paint(bufs[v], hww, v + 1);
        clean_dcache();
        LCD_REG(0x10) = (uint32_t)(uintptr_t)bufs[v];
        hold_seconds(3);
        LCD_REG(0x10) = osfb;
    }
    if (saved) {
        memcpy(os, saved, W * H * 2);
        clean_dcache();
    }
    free(saved);
    free(plain);
    free(raw);
    return 0;
}
