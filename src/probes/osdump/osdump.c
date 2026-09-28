/* Dumps OS code ranges to /documents/osdump.tns so the host can disassemble
 * them (CX II CAS 6.2.0.333 only).  Runs entirely in main(): no task, no
 * NavNet.  Each range is written as a 8-byte header (start, length, both
 * little endian) followed by the raw bytes. */
#include <os.h>
#include <stdint.h>
#include <stdio.h>

extern unsigned int nl_osid(void);

static const uint32_t ranges[][2] = {
    {0x1048A300u, 0x400u},  /* usbd_delay_ms at 0x1048A464 */
    {0x10429800u, 0x1200u}, /* TCC_* incl. Create 0x1042A8C8 */
    {0x1042BC00u, 0x800u},  /* TCT_* control / protect */
    {0x103A5E00u, 0xA00u},  /* QUC_Receive/Send_To_Queue */
};

int main(void) {
    if (nl_osid() != 46u) return 1;
    FILE *f = fopen("/documents/osdump.tns", "wb");
    if (!f) return 1;
    for (unsigned i = 0; i < sizeof(ranges) / sizeof(ranges[0]); ++i) {
        uint32_t header[2] = {ranges[i][0], ranges[i][1]};
        fwrite(header, sizeof(header), 1, f);
        fwrite((const void *)(uintptr_t)ranges[i][0], 1, ranges[i][1], f);
    }
    fclose(f);
    return 0;
}
