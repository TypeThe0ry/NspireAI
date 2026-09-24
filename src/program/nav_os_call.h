#ifndef NSPIRE_NAV_OS_CALL_H
#define NSPIRE_NAV_OS_CALL_H

/* Do not change IRQ state here. The 2026-09-22 scoped enable experiment
 * froze/crashed on the handheld. The OS dialog's convention is not evidence
 * of safety for NavNet calls inside an SDL program. This passthrough restores
 * pre-experiment behavior; it does NOT fix the missing USB progress.
 */
#define NAV_OS_CALL(expression) (expression)

#endif
