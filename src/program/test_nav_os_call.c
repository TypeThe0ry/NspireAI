#include <assert.h>
#include <stdint.h>
static int mask = -1, calls, expected_mask = -1;
#include "nav_os_call.h"
static int16_t operation(void) {
    assert(mask == expected_mask);
    calls++;
    return -274;
}
int main(void) {
    int16_t result = NAV_OS_CALL(operation());
    assert(result == -274 && calls == 1 && mask == -1);
    mask = 0;
    expected_mask = 0;
    assert(NAV_OS_CALL(operation()) == -274 && mask == 0);
    int value = 42;
    int *pointer = NAV_OS_CALL(&value);
    assert(pointer == &value && mask == 0);
    return 0;
}
