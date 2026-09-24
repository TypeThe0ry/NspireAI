#include <assert.h>
#include "nav_fragment.h"
int main(void) {
    assert(nav_fragment_valid(4, 400, 0, 214, 16384, 0, 0, 0, 1));
    assert(nav_fragment_valid(4, 400, 214, 186, 16384, 214, 214, 400, 1));
    assert(!nav_fragment_valid(4, 401, 214, 187, 16384, 214, 214, 400, 1));
    assert(!nav_fragment_valid(7, 400, 0, 214, 16384, 0, 0, 0, 1));
    assert(!nav_fragment_valid(4, 400, 0, 0, 16384, 0, 0, 0, 1));
    assert(!nav_fragment_valid(4, 400, 0, 214, 16384, 0, 0, 0, 0));
    assert(!nav_fragment_valid(4, 400, 214, 187, 16384, 214, 214, 400, 1));
    assert(!nav_fragment_valid(4, 400, 214, 186, 16384, 214, 215, 400, 1));
    assert(!nav_fragment_valid(4, UINT32_MAX, UINT32_MAX, 1, 16384, 0, 0, 0, 1));
    return 0;
}
