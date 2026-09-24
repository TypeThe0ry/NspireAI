#include <assert.h>
#include <stdint.h>
static uint32_t SDL_GetTicks(void) { return 123; }
#include "nav_clock.h"

int main(void) {
    assert(nav_clock_ms() == 123);
    assert(!nav_deadline_reached(99, 100));
    assert(nav_deadline_reached(100, 100));
    assert(nav_deadline_reached(101, 100));
    assert(!nav_deadline_reached(UINT32_MAX - 5, 10));
    assert(nav_deadline_reached(10, UINT32_MAX - 5));
    assert(!nav_deadline_reached(1000, 2000));
    assert(nav_deadline_reached(2000, 1250));
    return 0;
}
