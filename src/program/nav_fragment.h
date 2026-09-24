#ifndef NSPIRE_NAV_FRAGMENT_H
#define NSPIRE_NAV_FRAGMENT_H
#include <stddef.h>
#include <stdint.h>

/* Validate before copying; subtraction avoids offset/length overflow. */
static inline int nav_fragment_valid(uint8_t opcode, uint32_t total,
        uint32_t offset, size_t chunk, size_t capacity, size_t next,
        size_t accumulated, size_t expected_total, int pending) {
    return pending && (opcode == 4 || opcode == 5) && total > 0 &&
        total <= capacity && chunk > 0 && offset <= total &&
        chunk <= total - offset && offset == next && accumulated == next &&
        (offset == 0 || total == expected_total);
}
#endif
