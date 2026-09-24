/* Offline-only control for isolating SDL from the NavNet investigation.
 * NOT the chat application, NOT validated on hardware, NOT auto-deployed.
 * No IRQ changes, SDL, NavNet, sleeps or hardware timer writes here.
 * Ndless still masks interrupts before entry: USB progress is NOT promised.
 * Busy keyboard polling is intentional for this short diagnostic only.
 */
#include <os.h>
#include <libndls.h>
#include <sys/time.h>
/* Exported by pinned libsyscalls/osvar.cpp, omitted from public headers. */
extern unsigned int nl_osid(void);

void _fini(void) __attribute__((weak));
void _fini(void) {}

static void line(Gc gc, int y, const char *text) {
    char utf16[160];
    ascii2utf16(utf16, text, sizeof(utf16));
    gui_gc_drawString(gc, utf16, 12, y, 0);
}

static void draw(Gc gc, unsigned presses) {
    char count[64];
    gui_gc_begin(gc);
    gui_gc_setRegion(gc, 0, 0, 320, 240, 0, 0, 320, 240);
    gui_gc_clipRect(gc, 0, 0, 320, 240, GC_CRO_SET);
    gui_gc_setColorRGB(gc, 255, 255, 255);
    gui_gc_fillRect(gc, 0, 0, 320, 240);
    gui_gc_setColorRGB(gc, 0, 0, 0);
    gui_gc_setFont(gc, Bold9);
    line(gc, 28, "NspireAI NGC CONTROL - NOT CHAT");
    line(gc, 56, "No SDL / NavNet / IRQ changes");
    line(gc, 84, "Enter: key test     Esc: exit");
    snprintf(count, sizeof(count), "Key transitions: %u", presses);
    line(gc, 112, count);
    line(gc, 140, "USB coexistence NOT verified");
    line(gc, 168, "RTC limit: 15 seconds; no watchdog");
    gui_gc_clipRect(gc, 0, 0, 0, 0, GC_CRO_RESET);
    gui_gc_finish(gc);
    /* The SDK's old gui_gc_blit_to_screen() copies SCREEN_BASE_ADDRESS and
     * is explicitly not portable to HW-W/CX II. Use the same new LCD API as
     * the production NGC UI so this control remains a valid startup probe. */
    {
        char *off_screen = (((((char *****)gc)[9])[0])[0x8])[0];
        scr_type_t type = lcd_type();
        if (type == SCR_TYPE_INVALID)
            type = has_colors ? SCR_320x240_565 : SCR_320x240_4;
        lcd_blit(off_screen, type);
    }
}

int main(void) {
    Gc gc;
    unsigned presses = 0;
    int enter_was_down = 1;
    struct timeval started, now;
    /* Do not call model-specific graphics on an unaudited OS mapping. */
    if (nl_osid() != 46) return EXIT_FAILURE;
    gc = gui_gc_global_GC();
    if (!gc) return EXIT_FAILURE;
    if (gettimeofday(&started, NULL) != 0) return EXIT_FAILURE;
    draw(gc, presses);
    while (!isKeyPressed(KEY_NSPIRE_ESC)) {
        if (gettimeofday(&now, NULL) != 0) break;
        /* RTC is only second-resolution and can change; fail closed on
         * backwards movement. This cannot recover a stuck graphics call. */
        if (now.tv_sec < started.tv_sec || now.tv_sec - started.tv_sec >= 15) break;
        int enter_down = isKeyPressed(KEY_NSPIRE_ENTER);
        if (enter_down && !enter_was_down) draw(gc, ++presses);
        enter_was_down = enter_down;
    }
    /* Global GC belongs to the OS. Loader restores screen/IRQ on return. */
    return EXIT_SUCCESS;
}
