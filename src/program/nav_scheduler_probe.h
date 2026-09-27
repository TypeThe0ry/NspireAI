#ifndef NSPIRE_NAV_SCHEDULER_PROBE_H
#define NSPIRE_NAV_SCHEDULER_PROBE_H

/*
 * Offline/diagnostic only.  The CX II CAS 6.2.0.333 IDC file names the
 * Nucleus cooperative scheduler as TCT_Schedule at this address.  This is
 * deliberately not part of the production page: the address is OS-specific
 * and has not been proven safe while Ndless owns the screen.
 */
#include <stdint.h>

extern unsigned int nl_osid(void);

static int nav_scheduler_probe_call(void) {
    if (nl_osid() != 46u) return 0;
    ((void (*)(void))(uintptr_t)0x10623ABCu)();
    return 1;
}

#endif
