/* USB baseline probe: proves that a stock-Ndless program actually ran and
 * whether the TI USB stack stays responsive while it runs.  The plain build
 * makes no LCD, GC, key scan, RTC, NavNet, task or IRQ call.  PROBE_LCD adds
 * only the NGC page's lcd_type/lcd_init setup and SCR_TYPE_INVALID teardown;
 * PROBE_GC additionally takes the global GC and draws one frame (stage-3).
 * PROBE_IRQ re-enables CPU interrupts (the Ndless loader masks them for the
 * whole program) during the hold, leaving the OS controller mask untouched,
 * so the OS timer and USB interrupts can preempt the busy loop.
 * PROBE_SLEEP keeps the hold in main() (the OS UI task) but re-enables IRQs
 * and blocks in Nucleus TCC_Task_Sleep (0x1042A1C4 on CX II CAS 6.2.0.333,
 * identified from the usbd_delay_ms disassembly) instead of busy-waiting.
 * PROBE_TASK makes main() return to the OS right away: the image stays
 * resident and the hold runs in a Nucleus task at TASK_PRIORITY (below the
 * OS worker tasks, above idle), created through the CX II CAS 6.2.0.333
 * TCC_Create_Task address from Ndless's own IDC map.
 * The host polls for the marker files to see how far the program got. */
#include <stdio.h>
#if defined(PROBE_LCD) || defined(PROBE_GC)
#include <libndls.h>
#endif
#ifdef PROBE_GC
#include <ngc.h>
#define PROBE_LCD
#endif
#ifdef PROBE_IRQ
#include <os.h>
#endif
#ifdef PROBE_SLEEP
#include <stdint.h>
#include <os.h>
extern unsigned int nl_osid(void);
typedef void (*tcc_task_sleep_t)(unsigned ticks);
#define CX2_CAS_TCC_TASK_SLEEP ((tcc_task_sleep_t)(uintptr_t)0x1042A1C4u)
#define CX2_CAS_TICKS_PER_SECOND (*(volatile unsigned *)(uintptr_t)0x11331190u)
#endif
#ifdef PROBE_TASK
#include <stdint.h>
#include <os.h>
#ifndef TASK_PRIORITY
#define TASK_PRIORITY 250u
#endif
typedef void (*task_entry_t)(unsigned argc, void *argv);
typedef int (*tcc_create_task_t)(void *task, char *name, task_entry_t entry,
                                 unsigned argc, void *argv, void *stack,
                                 unsigned stack_size, unsigned priority,
                                 unsigned time_slice, unsigned preempt,
                                 unsigned auto_start);
extern unsigned int nl_osid(void); /* Ndless ext syscall, not in SDK headers */
#define CX2_CAS_6_2_0_333_OSID 46u
#define CX2_CAS_TCC_CREATE_TASK ((tcc_create_task_t)(uintptr_t)0x1042A8C8u)
#define NU_PREEMPT 10u
#define NU_START 12u
static unsigned char task_control[1024] __attribute__((aligned(8)));
static unsigned char task_stack[8 * 1024] __attribute__((aligned(8)));
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

#ifdef PROBE_TASK
static void task_main(unsigned argc, void *argv) {
    (void)argc; (void)argv;
    write_marker("/documents/marker_t.tns", "task running\n");
    hold();
    write_marker("/documents/marker_b.tns", "done\n");
    TCC_Terminate_Task(TCC_Current_Task_Pointer());
}
#endif

int main(void) {
    write_marker("/documents/marker_a.tns", "entry\n");
#ifdef PROBE_TASK
    if (nl_osid() != CX2_CAS_6_2_0_333_OSID) {
        write_marker("/documents/marker_c.tns", "wrong os\n");
        return 1;
    }
    int status = CX2_CAS_TCC_CREATE_TASK(task_control, (char *)"marker",
                                         task_main, 0, NULL, task_stack,
                                         sizeof(task_stack), TASK_PRIORITY,
                                         0, NU_PREEMPT, NU_START);
    if (status != 0) {
        write_marker("/documents/marker_c.tns", "create failed\n");
        return 1;
    }
    nl_set_resident();
    write_marker("/documents/marker_c.tns", "main returned\n");
    return 0;
#endif
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
#ifdef PROBE_SLEEP
    if (nl_osid() != 46u) return 1;
    unsigned ticks = CX2_CAS_TICKS_PER_SECOND;
    {
        char text[32];
        sprintf(text, "ticks/s=%u\n", ticks);
        write_marker("/documents/marker_i.tns", text);
    }
    if (ticks == 0 || ticks > 10000) ticks = 100;
    int saved_sleep_irq = TCT_Local_Control_Interrupts(0);
    for (int second = 0; second < 150; ++second)
        CX2_CAS_TCC_TASK_SLEEP(ticks);
    TCT_Local_Control_Interrupts(saved_sleep_irq);
    write_marker("/documents/marker_b.tns", "done\n");
    return 0;
#endif
#ifdef PROBE_IRQ
    int saved = TCT_Local_Control_Interrupts(0);
    write_marker("/documents/marker_i.tns", "irq on\n");
#endif
    hold();
#ifdef PROBE_IRQ
    TCT_Local_Control_Interrupts(saved);
#endif
    write_marker("/documents/marker_b.tns", "done\n");
#ifdef PROBE_LCD
    lcd_init(SCR_TYPE_INVALID);
    write_marker("/documents/marker_c.tns", "lcd released\n");
#endif
    return 0;
}
