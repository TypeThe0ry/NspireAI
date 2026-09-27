/* Included by main.c after its shared conversation and transport code. */
#include <libndls.h>
#if defined(NSPIRE_NGC_USB_IRQ_WINDOW) || defined(NSPIRE_NGC_USB_IRQ_MENU)
#include "nav_irq_window.h"
#endif
extern unsigned int nl_osid(void);
static Gc chat_gc;
static scr_type_t ngc_screen_type;
static int ngc_lcd_ready;
static int ngc_transport_armed;
#ifdef NSPIRE_NGC_USB_IRQ_MENU
static struct nav_irq_window ngc_menu_irq_window;
static int ngc_menu_irq_window_active;
#endif

/* Never enumerate NavNet during page startup.  NodeEnumInit, Connect, and
 * Read are synchronous OS calls and the SDK does not document a wall-clock
 * bound for them.  The old auto-transport candidate called this path before
 * the Mac bridge was READY and repeatedly wedged the USB endpoint.  Transport
 * is now armed only by one explicit Menu press after the host bridge is
 * already running; the first failure is held until the next explicit retry. */
#define NGC_TRANSPORT_DEFAULT 0

static int ngc_prepare_lcd(void) {
    if (ngc_lcd_ready) return 1;
    ngc_screen_type = lcd_type();
    if (ngc_screen_type == SCR_TYPE_INVALID)
        ngc_screen_type = has_colors ? SCR_320x240_565 : SCR_320x240_4;
    if (!lcd_init(ngc_screen_type)) return 0;
    ngc_lcd_ready = 1;
    return 1;
}

static void ngc_line(int y, const char *text) {
    char utf16[LINE_CAP * 2];
    ascii2utf16(utf16, text, sizeof(utf16));
    gui_gc_drawString(chat_gc, utf16, 6, y, 0);
}

static void ngc_draw(void) {
    int first = history_count > 13 ? history_count - 13 : 0;
    gui_gc_begin(chat_gc);
    gui_gc_setRegion(chat_gc, 0, 0, 320, 240, 0, 0, 320, 240);
    gui_gc_clipRect(chat_gc, 0, 0, 320, 240, GC_CRO_SET);
    gui_gc_setColorRGB(chat_gc, 255, 255, 255);
    gui_gc_fillRect(chat_gc, 0, 0, 320, 240);
    gui_gc_setColorRGB(chat_gc, 0, 0, 0);
    gui_gc_setFont(chat_gc, Regular9);
    ngc_line(14, "NspireAI NGC - EXPERIMENTAL");
    ngc_line(29, status_text);
    for (int i = first; i < history_count; ++i)
        ngc_line(46 + 12 * (i - first), history[i]);
    ngc_line(226, input_text + (input_len > 45 ? input_len - 45 : 0));
    gui_gc_clipRect(chat_gc, 0, 0, 0, 0, GC_CRO_RESET);
    gui_gc_finish(chat_gc);
    /* libndls' gui_gc_blit_to_screen() is compiled with OLD_SCREEN_API and
     * explicitly warns that its raw framebuffer copy is broken on HW-W/CX II.
     * Pull the GC's off-screen buffer using the same SDK layout, then hand it
     * to the new lcd_blit syscall so the loader metadata and pixel format stay
     * aligned on the CX II. */
    {
        char *off_screen = (((((char *****)chat_gc)[9])[0])[0x8])[0];
        lcd_blit(off_screen, ngc_screen_type);
    }
    ngc_ui_dirty = 0;
}

/* No touchpad arrows: these keys use the SDK's matrix-read path. No repeats
 * or sleep/idle calls. Full keyboard and font behavior still needs device QA. */
static int ngc_keys(void) {
    static const t_key *keys[] = {
        &KEY_NSPIRE_A, &KEY_NSPIRE_B, &KEY_NSPIRE_C, &KEY_NSPIRE_D,
        &KEY_NSPIRE_E, &KEY_NSPIRE_F, &KEY_NSPIRE_G, &KEY_NSPIRE_H,
        &KEY_NSPIRE_I, &KEY_NSPIRE_J, &KEY_NSPIRE_K, &KEY_NSPIRE_L,
        &KEY_NSPIRE_M, &KEY_NSPIRE_N, &KEY_NSPIRE_O, &KEY_NSPIRE_P,
        &KEY_NSPIRE_Q, &KEY_NSPIRE_R, &KEY_NSPIRE_S, &KEY_NSPIRE_T,
        &KEY_NSPIRE_U, &KEY_NSPIRE_V, &KEY_NSPIRE_W, &KEY_NSPIRE_X,
        &KEY_NSPIRE_Y, &KEY_NSPIRE_Z,
        &KEY_NSPIRE_0, &KEY_NSPIRE_1, &KEY_NSPIRE_2, &KEY_NSPIRE_3,
        &KEY_NSPIRE_4, &KEY_NSPIRE_5, &KEY_NSPIRE_6, &KEY_NSPIRE_7,
        &KEY_NSPIRE_8, &KEY_NSPIRE_9, &KEY_NSPIRE_SPACE,
        &KEY_NSPIRE_PERIOD, &KEY_NSPIRE_COMMA, &KEY_NSPIRE_PLUS,
        &KEY_NSPIRE_MINUS, &KEY_NSPIRE_EQU,
        &KEY_NSPIRE_ENTER, &KEY_NSPIRE_DEL, &KEY_NSPIRE_MENU
    };
    static const char chars[] = "abcdefghijklmnopqrstuvwxyz0123456789 .,+-=";
    static unsigned char previous[sizeof(keys) / sizeof(keys[0])];
    static int initialized;
    int changed = 0;
    /* Matrix scanning is a relatively expensive syscall on CX II.  The old
     * loop called isKeyPressed() for every key on every spin, even while the
     * page was idle; that monopolized the standalone task and starved the
     * OS/USB work queue before transport was ever armed.  Use the cheap
     * aggregate test as the fast path.  Clear edge state when no key is down
     * so the next press is still reported after a skipped scan. */
    if (!any_key_pressed()) {
        memset(previous, 0, sizeof(previous));
        initialized = 1;
        return 0;
    }
    if (isKeyPressed(KEY_NSPIRE_ESC)) { done = 1; return 1; }
    for (unsigned i = 0; i < sizeof(keys) / sizeof(keys[0]); ++i) {
        int down = (isKeyPressed)(keys[i]);
        if (initialized && down && !previous[i]) {
            changed = 1;
            if (keys[i] == &KEY_NSPIRE_ENTER) (void)send_prompt();
            else if (keys[i] == &KEY_NSPIRE_DEL) {
                if (input_len) input_text[--input_len] = 0;
            } else if (keys[i] == &KEY_NSPIRE_MENU) {
                /* A held transport remains logically armed after its first
                 * failed attempt.  Treat the next Menu press as the single
                 * explicit retry instead of requiring an off/on double press. */
                if (ngc_transport_armed && nav_transport_is_blocked()) {
#ifdef NSPIRE_NGC_MENU_LOCAL_SERVICE
                    if (!nav_start_local_service()) {
                        nav_transport_hold();
                        changed = 1;
                        previous[i] = down;
                        continue;
                    }
#endif
                    nav_transport_rearm();
                    set_status("USB retry armed; bridge must be READY");
                    changed = 1;
                    previous[i] = down;
                    continue;
                }
                /* Never turn an active channel into a hidden stale channel
                 * by treating Menu as a disconnect button.  The known-safe
                 * recovery path for an active connection is the normal
                 * protocol/ESC teardown, not a second synchronous NavNet
                 * syscall from the key scanner. */
                if (ngc_transport_armed && nav_connected) {
                    set_status("USB active; Menu keeps bridge connected");
                    changed = 1;
                    previous[i] = down;
                    continue;
                }
                ngc_transport_armed = !ngc_transport_armed;
                if (ngc_transport_armed) {
#ifdef NSPIRE_NGC_MENU_LOCAL_SERVICE
                    /* Historical NavNet clients start a calculator-side
                     * service before enumerating the Mac peer.  Keep this
                     * candidate behind the explicit Menu arm so page launch
                     * remains USB-idle and the host bridge is already READY. */
                    if (!nav_start_local_service()) {
                        nav_transport_hold();
                        changed = 1;
                        previous[i] = down;
                        continue;
                    }
#endif
#ifdef NSPIRE_NGC_USB_IRQ_MENU
                    if (!ngc_menu_irq_window_active &&
                        nav_irq_window_enter(&ngc_menu_irq_window)) {
                        ngc_menu_irq_window_active = 1;
                    }
                    set_status(ngc_menu_irq_window_active
                                   ? "USB armed; IRQ window active; Menu disables"
                                   : "USB armed; connecting... Menu disables");
#else
                    set_status("USB armed once; bridge must already be READY");
#endif
                    nav_transport_rearm();
                } else {
#ifdef NSPIRE_NGC_USB_IRQ_MENU
                    if (ngc_menu_irq_window_active) {
                        nav_irq_window_leave(&ngc_menu_irq_window);
                        ngc_menu_irq_window_active = 0;
                    }
#endif
                    nav_transport_hold();
                    set_status("USB idle; Menu enables one retry");
                }
            }
            else if (keys[i] == &KEY_NSPIRE_N && isKeyPressed(KEY_NSPIRE_CTRL))
                reset_conversation();
            else {
                unsigned char c = chars[i];
                if (c >= 'a' && c <= 'z' && isKeyPressed(KEY_NSPIRE_SHIFT)) c -= 32;
                append_input(c);
            }
        }
        previous[i] = down;
    }
    initialized = 1;
    return changed;
}

int main(void) {
#ifdef NSPIRE_NGC_PROBE
    /* Incremental entry probe. Stage 0 calls no Ndless UI API; stage 1 adds
     * lcd_type/lcd_init; stage 2 acquires the global GUI GC after LCD setup;
     * stage 3 performs one frame draw; stage 4 adds set_status; stage 5 adds
     * one RTC clock read; stage 6 performs one NavNet enumeration attempt;
     * stage 7 performs one matrix-key scan; stage 8 enumerates NavNet then
     * redraws the status/history page; stage 9 repeats matrix-key scanning
     * 1000 times to expose a busy-loop/key-syscall failure; stage 10 keeps
     * the process alive for 30 seconds with RTC+key scanning but no NavNet
     * Read, reproducing the long-running UI scheduler shape; stage 11 adds
     * one real nav_poll (PING/Read) after the bridge connection attempt;
     * stage 12 combines one frame with the long-running loop and no NavNet;
     * stage 13 repeats that test with the production low-frequency RTC cadence;
     * stage 14 removes matrix-key scanning from that long-running path.
     * stage 15 removes both NavNet and matrix-key work and calls Ndless's
     * timer-only idle() once per loop, isolating whether a scheduler yield
     * (rather than the RTC/key code) keeps the USB screen path responsive.
     * Each stage returns immediately so the first
     * unsupported-document result identifies the failing API boundary. */
#if NSPIRE_NGC_PROBE_STAGE == 12
    /* Combined production-shaped probe: initialize LCD/GC, draw exactly one
     * frame, then keep the page alive for 30 seconds with the same bounded
     * matrix/RTC cadence but no NavNet call. Stages 0-10 proved these pieces
     * independently; this stage isolates the long-running post-frame path
     * from the rejected get_event() experiment and from NavNet ownership. */
    if (!ngc_prepare_lcd()) return EXIT_FAILURE;
    chat_gc = gui_gc_global_GC();
    if (!chat_gc) return EXIT_FAILURE;
    set_status("NGC probe stage 12");
    ngc_draw();
    {
        uint32_t probe_until = nav_clock_ms() + 30000;
        unsigned probe_spin = 0;
        unsigned probe_clock_spin = 0;
        uint32_t probe_last = nav_clock_ms();
        while (!nav_deadline_reached(nav_clock_ms(), probe_until)) {
            if (++probe_spin >= 512u) {
                probe_spin = 0;
                if (ngc_keys() && ngc_ui_dirty) ngc_draw();
            }
            if (++probe_clock_spin >= 128u) {
                probe_clock_spin = 0;
                probe_last = nav_clock_ms();
            }
            (void)probe_last;
            for (volatile unsigned spin = 0; spin < 256; ++spin)
                __asm volatile("nop");
        }
    }
#elif NSPIRE_NGC_PROBE_STAGE == 13
    /* Production-shaped post-frame probe. Unlike stage 12, the deadline clock
     * is sampled only at the same 128-spin cadence as the real loop; this
     * avoids turning the diagnostic itself into a high-frequency RTC test. */
    if (!ngc_prepare_lcd()) return EXIT_FAILURE;
    chat_gc = gui_gc_global_GC();
    if (!chat_gc) return EXIT_FAILURE;
    set_status("NGC probe stage 13");
    ngc_draw();
    {
        uint32_t last_tick = nav_clock_ms();
        uint32_t probe_until = last_tick + 30000;
        unsigned scheduler_spin = 0;
        unsigned key_sample_spin = 0;
        while (!nav_deadline_reached(last_tick, probe_until)) {
            if (++key_sample_spin >= 512u) {
                key_sample_spin = 0;
                if (ngc_keys() && ngc_ui_dirty) ngc_draw();
            }
            if (++scheduler_spin >= 128u) {
                scheduler_spin = 0;
                last_tick = nav_clock_ms();
            }
            if (ngc_ui_dirty) ngc_draw();
            for (volatile unsigned spin = 0; spin < 256; ++spin)
                __asm volatile("nop");
        }
    }
#elif NSPIRE_NGC_PROBE_STAGE == 14
    /* Post-frame residency probe without matrix-key syscalls. This keeps the
     * same LCD/GC frame and 128-spin RTC cadence as stage 13, but removes
     * ngc_keys() entirely so a freeze can be attributed to the raw loop/RTC
     * path rather than key-matrix ownership. */
    if (!ngc_prepare_lcd()) return EXIT_FAILURE;
    chat_gc = gui_gc_global_GC();
    if (!chat_gc) return EXIT_FAILURE;
    set_status("NGC probe stage 14");
    ngc_draw();
    {
        uint32_t last_tick = nav_clock_ms();
        uint32_t probe_until = last_tick + 30000;
        unsigned scheduler_spin = 0;
        while (!nav_deadline_reached(last_tick, probe_until)) {
            if (++scheduler_spin >= 128u) {
                scheduler_spin = 0;
                last_tick = nav_clock_ms();
            }
            for (volatile unsigned spin = 0; spin < 256; ++spin)
                __asm volatile("nop");
        }
    }
#elif NSPIRE_NGC_PROBE_STAGE == 15
    /* Scheduler-yield probe.  This is deliberately not a production path:
     * idle() masks every IRQ except Ndless's timer interrupt and its CX II
     * interaction with NavNet is not established.  It is useful only as a
     * no-NavNet/no-key discriminator for the stage-14 USB/screen stall. */
    if (!ngc_prepare_lcd()) return EXIT_FAILURE;
    chat_gc = gui_gc_global_GC();
    if (!chat_gc) return EXIT_FAILURE;
    set_status("NGC probe stage 15");
    ngc_draw();
    {
        uint32_t last_tick = nav_clock_ms();
        uint32_t probe_until = last_tick + 30000;
        unsigned scheduler_spin = 0;
        while (!nav_deadline_reached(last_tick, probe_until)) {
            if (++scheduler_spin >= 128u) {
                scheduler_spin = 0;
                last_tick = nav_clock_ms();
            }
            idle();
        }
    }
#elif NSPIRE_NGC_PROBE_STAGE >= 1
    if (!ngc_prepare_lcd()) return EXIT_FAILURE;
#endif
#if NSPIRE_NGC_PROBE_STAGE >= 2
    chat_gc = gui_gc_global_GC();
    if (!chat_gc) return EXIT_FAILURE;
#endif
#if NSPIRE_NGC_PROBE_STAGE >= 3
    ngc_draw();
#endif
#if NSPIRE_NGC_PROBE_STAGE >= 4
    set_status("NGC probe stage 4");
    ngc_draw();
#endif
#if NSPIRE_NGC_PROBE_STAGE >= 5
    (void)nav_clock_ms();
#endif
#if NSPIRE_NGC_PROBE_STAGE >= 6
    (void)nav_try_connect();
#endif
#if NSPIRE_NGC_PROBE_STAGE >= 7
    (void)ngc_keys();
#endif
#if NSPIRE_NGC_PROBE_STAGE >= 8
    (void)nav_try_connect();
    ngc_draw();
#endif
#if NSPIRE_NGC_PROBE_STAGE >= 9
    for (int probe_i = 0; probe_i < 1000; ++probe_i)
        (void)ngc_keys();
#endif
#if NSPIRE_NGC_PROBE_STAGE >= 10
    {
        uint32_t probe_until = nav_clock_ms() + 30000;
        while (!nav_deadline_reached(nav_clock_ms(), probe_until))
            (void)ngc_keys();
    }
#endif
#if NSPIRE_NGC_PROBE_STAGE >= 11
    (void)nav_try_connect();
    nav_poll();
#endif
    return EXIT_SUCCESS;
#else
    uint32_t last_tick;
    unsigned scheduler_spin = 0;
    unsigned key_sample_spin = 0;
#ifdef NSPIRE_NGC_USB_IRQ_WINDOW
    struct nav_irq_window irq_window = {0};
#endif
    /* Do not turn an OS-index mismatch into Ndless's generic
     * "unsupported document" dialog.  The staged probes already exercise
     * the CX II LCD/GC/RTC/NavNet path, and the SDK screen APIs select the
     * active display at runtime.  Keep the production page observable even
     * if a future CX II OS revision changes nl_osid(). */
    /* The SDK's new screen API must be initialized before acquiring or using
     * the global GUI GC.  lcd_init() can replace the framebuffer backing the
     * GC; taking the GC first leaves a stale internal pointer and the loader
     * reports the resulting crash as the generic unsupported-document dialog.
     * Keep both operations before the first frame and before RTC/USB work. */
    if (!ngc_prepare_lcd()) return EXIT_FAILURE;
    chat_gc = gui_gc_global_GC();
    if (!chat_gc) return EXIT_FAILURE;
    ngc_transport_armed = NGC_TRANSPORT_DEFAULT;
    set_status(ngc_transport_armed
                   ? "NGC/RTC; USB armed by build"
                   : "NGC/RTC; USB idle; Menu enables");
    /* Draw the first frame before touching the RTC.  If the CX II's
     * gettimeofday path is the faulting stage, the user must still get a
     * visible startup marker instead of an indistinguishable return to Home.
     * This does not make RTC or USB scheduling safe; it only separates the
     * entry/GC/LCD stage from the clock/transport stage. */
    nav_retry_at = 0;
    ngc_draw();
#ifdef NSPIRE_NGC_LOCAL_SERVICE
    nav_start_local_service();
    ngc_draw();
#endif
    last_tick = nav_clock_ms();
    nav_retry_at = last_tick + NAV_INITIAL_DELAY_MS;
#ifdef NSPIRE_NGC_USB_IRQ_WINDOW
    if (!nav_irq_window_enter(&irq_window)) {
        set_status("IRQ window unavailable; USB held");
        ngc_draw();
    } else {
        set_status("NGC/RTC; USB IRQ window active");
        ngc_draw();
    }
#endif
    while (!done) {
        int changed = 0;
        /* A stuck/held virtual key makes any_key_pressed() take the expensive
         * full matrix path.  Do not run that path on every tight-loop spin;
         * this bounded sample still keeps normal text input responsive while
         * preventing one launch/menu key from monopolising the task. */
        if (++key_sample_spin >= 512u) {
            key_sample_spin = 0;
            changed = ngc_keys();
        }
        /* RTC access is an OS syscall too.  Sampling it on every tight-loop
         * iteration defeats the no-key fast path and needlessly competes with
         * the handheld's USB work queue.  Keep the UI edge path responsive,
         * but sample the deadline clock at a bounded cadence. */
        uint32_t now = last_tick;
        if (++scheduler_spin >= 128u) {
            scheduler_spin = 0;
            now = nav_clock_ms();
        }
        if (done) break;
#if defined(NSPIRE_NGC_LOCAL_SERVICE) || defined(NSPIRE_NGC_MENU_LOCAL_SERVICE)
        /* Service callbacks only hand off a channel; keep synchronous NavNet
         * I/O in the normal page loop rather than callback context. */
        nav_local_service_poll();
#endif
        /* RTC supplies at most one poll per second. Avoid a busy NavNet loop;
         * this is not a syscall timeout or scheduling solution. */
        if (ngc_transport_armed && !nav_transport_is_blocked() && now != last_tick) {
            last_tick = now;
            (void)nav_try_connect();
            nav_poll();
            changed = 1;
        }
        (void)changed;
        /* Only redraw after a state/input/history change.  The old loop
         * redrew once per RTC tick even when nothing changed, repeatedly
         * entering the raw GC framebuffer path. */
        if (ngc_ui_dirty) ngc_draw();
        /* The stage-15 probe was intended to distinguish NOP-tail starvation
         * from LCD/GC entry, but the post-test file-list capture did not
         * establish that the probe page had actually stayed resident. Keep
         * this yield experimental. While transport is still disarmed there
         * is no active NavNet channel, so yield through Ndless idle().
         * Keep idle() out of the active-channel path: it masks every IRQ
         * except the Ndless timer interrupt and could hide a NavNet event. */
        if (!nav_connected &&
            (!ngc_transport_armed || nav_transport_is_blocked())) {
            idle();
        } else {
            for (volatile unsigned spin = 0; spin < 256; ++spin)
                __asm volatile("nop");
        }
    }
#ifdef NSPIRE_NGC_USB_IRQ_WINDOW
    nav_irq_window_leave(&irq_window);
#endif
#ifdef NSPIRE_NGC_USB_IRQ_MENU
    if (ngc_menu_irq_window_active)
        nav_irq_window_leave(&ngc_menu_irq_window);
#endif
    if (nav_channel) (void)NAV_OS_CALL(TI_NN_Disconnect(nav_channel));
    nav_stop_local_service();
    return EXIT_SUCCESS;
#endif
}
