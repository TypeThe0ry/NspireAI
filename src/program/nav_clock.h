#ifndef NSPIRE_NAV_CLOCK_H
#define NSPIRE_NAV_CLOCK_H
#include <stdint.h>

/* Intervals must be < 2^31 ms. Signed subtraction handles uint32 wrap. */
static inline int nav_deadline_reached(uint32_t now, uint32_t deadline) {
    return (int32_t)(now - deadline) >= 0;
}

/* RTC backend is experimental and second-resolution. It does not make SDL
 * initialization timer-neutral and does not enable OS USB scheduling. */
#ifdef NSPIRE_NAV_CLOCK_RTC
#include <sys/time.h>
static inline uint32_t nav_clock_ms(void) {
    struct timeval tv = {0};
    gettimeofday(&tv, 0);
    return (uint32_t)tv.tv_sec * UINT32_C(1000);
}
#else
static inline uint32_t nav_clock_ms(void) { return SDL_GetTicks(); }
#endif
#endif
