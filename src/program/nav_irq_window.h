#ifndef NSPIRE_NAV_IRQ_WINDOW_H
#define NSPIRE_NAV_IRQ_WINDOW_H

/*
 * Experimental only. Ndless's program loader enters a standalone program
 * with CPU interrupts masked. A long-running program consequently prevents
 * the OS USB/NavNet work from progressing. This helper uses the same source
 * interrupt-controller sequence as Ndless's own msleep()/idle() code: mask
 * timer IRQ 19 at the controller, briefly restore CPU IRQ delivery, and
 * restore both masks before returning to the loader.
 *
 * It is deliberately not part of the default build. The earlier per-NavNet
 * interrupt-scope candidate froze/crashed on the handheld. The Menu-gated
 * variant was also physically tested and froze/crashed; this file is now
 * retained only for offline source analysis, never for deployment.
 */

#include <libndls.h>
#include <os.h>

struct nav_irq_window {
    volatile unsigned *controller;
    unsigned saved_controller_mask;
    int saved_cpu_mask;
    int active;
};

static int nav_irq_window_enter(struct nav_irq_window *window) {
    if (!window) return 0;
    window->controller = IO(0xDC000008, 0xDC000010);
    window->saved_controller_mask = window->controller[0];

    /* Keep SP804 timer 19 masked; this is the timer-neutral part. */
    window->controller[1] = ~(1u << 19);
    window->saved_cpu_mask = TCT_Local_Control_Interrupts(0);
    window->active = 1;
    return 1;
}

static void nav_irq_window_leave(struct nav_irq_window *window) {
    if (!window || !window->active) return;

    /* Stop IRQ delivery before restoring the controller's previous state. */
    (void)TCT_Local_Control_Interrupts(-1);
    window->controller[1] = 0xFFFFFFFFu;
    window->controller[0] = window->saved_controller_mask;
    TCT_Local_Control_Interrupts(window->saved_cpu_mask);
    window->active = 0;
}

#endif
