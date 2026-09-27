#ifndef NSPIRE_NAV_TASK_HANDOFF_H
#define NSPIRE_NAV_TASK_HANDOFF_H

/*
 * Opt-in CX II CAS 6.2.0.333 task handoff.
 *
 * Ndless's document hook masks IRQs around a standalone entry call and only
 * restores them after that call returns.  The normal NGC page therefore cannot
 * own a long-running USB loop.  The loader exposes nl_set_resident(), which
 * keeps the loaded Zehn image alive, and the pinned CX II CAS IDC map exposes
 * the underlying Nucleus TCC_Create_Task routine.  The task-handoff entry must
 * return through crt0 after nl_set_resident(); calling _exit would bypass the
 * loader's IRQ-restore path.  This header contains the smallest private ABI
 * needed to create one preemptible task; it is not part of the public SDK and
 * is never enabled by a normal build.
 *
 * The ABI and TCB layout are still unverified on hardware.  Keep this mode
 * behind NSPIRE_NGC_TASK_HANDOFF, require OS index 46, and reject its manifest
 * in both upload paths unless a separate explicit diagnostic override is set.
 */

#include <stdint.h>

extern unsigned int nl_osid(void);
extern void nl_set_resident(void);
extern int TCC_Terminate_Task(void *task);
extern void *TCC_Current_Task_Pointer(void);

typedef void (*nav_task_entry_t)(unsigned argc, void *argv);
typedef int (*nav_tcc_create_task_t)(
    void *task,
    char *name,
    nav_task_entry_t entry,
    unsigned argc,
    void *argv,
    void *stack,
    unsigned stack_size,
    unsigned priority,
    unsigned time_slice,
    unsigned preempt,
    unsigned auto_start);

/* OS_cascx2-6.2.0.333.idc: TCC_Create_Task = 0x1042A8C8. */
#define NSPIRE_CX2_CAS_TCC_CREATE_TASK ((uintptr_t)0x1042A8C8u)
#define NSPIRE_CX2_CAS_OS_ID 46u
#define NSPIRE_TASK_STACK_SIZE (6u * 1024u)
#define NSPIRE_TASK_CONTROL_SIZE 1024u
#define NSPIRE_TASK_PRIORITY 255u

/* Nucleus PLUS constants.  They are intentionally local because the Ndless
 * SDK only exposes the opaque NU_TASK type and not the task-create ABI. */
#define NSPIRE_NU_PREEMPT 10u
#define NSPIRE_NU_START 12u

static unsigned char nav_task_control[NSPIRE_TASK_CONTROL_SIZE]
    __attribute__((aligned(8)));
static unsigned char nav_task_stack[NSPIRE_TASK_STACK_SIZE]
    __attribute__((aligned(8)));

static int nav_task_create(nav_task_entry_t entry) {
    nav_tcc_create_task_t create;
    int status;

    if (nl_osid() != NSPIRE_CX2_CAS_OS_ID || !entry) return 0;
    create = (nav_tcc_create_task_t)NSPIRE_CX2_CAS_TCC_CREATE_TASK;
    status = create((void *)nav_task_control,
                    (char *)"NspireAI",
                    entry,
                    0u,
                    NULL,
                    (void *)nav_task_stack,
                    (unsigned)sizeof(nav_task_stack),
                    NSPIRE_TASK_PRIORITY,
                    0u,
                    NSPIRE_NU_PREEMPT,
                    NSPIRE_NU_START);
    return status == 0;
}

static void nav_task_finish(void) {
    void *current = TCC_Current_Task_Pointer();
    if (current) (void)TCC_Terminate_Task(current);
}

#endif
