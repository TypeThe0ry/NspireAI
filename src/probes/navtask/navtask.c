/* NavNet-from-task probe.  main() creates a resident priority-250 Nucleus
 * task (the architecture proven by src/probes/marker STEP=task) and returns
 * so the OS UI task keeps servicing USB.  The task then connects to the Mac
 * bridge on service 0x5001, sends an NSAI PING, answers host PINGs, waits for
 * the PONG, sends one REQUEST and waits for the RESPONSE.  Every step appends
 * one line to /documents/navlog.tns, which the host reads back over USB.
 * No LCD, key scan, or IRQ manipulation is involved. */
#include <os.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#define SERVICE_ID 0x5001
#define TASK_PRIORITY 250u
#define LOG_PATH "/documents/navlog.tns"
#define HEADER_SIZE 16
#define MAX_FRAME 240
#define OP_PING 1
#define OP_PONG 2
#define OP_REQUEST 3
#define OP_RESPONSE 4
#define OP_ERROR 5

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
static unsigned char task_stack[16 * 1024] __attribute__((aligned(8)));

/* Minimal logger: only fopen/fputs/fclose, which the marker task probe proved
 * safe inside a Nucleus task.  The first navtask run wrote nothing from its
 * task when this used fprintf.  Supports "%d" and "%x" for two ints. */
static void fmt_int(char **out, int v, int hex) {
    char tmp[12];
    int n = 0;
    unsigned u = (!hex && v < 0) ? (unsigned)(-v) : (unsigned)v;
    if (!hex && v < 0) *(*out)++ = '-';
    do { unsigned d = hex ? (u & 15) : (u % 10); tmp[n++] = d < 10 ? '0' + d : 'a' + d - 10; u = hex ? (u >> 4) : (u / 10); } while (u);
    while (n) *(*out)++ = tmp[--n];
}

static void log_line(const char *fmt, int a, int b) {
    char line[128], *o = line;
    int args[2] = {a, b}, used = 0;
    for (const char *p = fmt; *p && o < line + 100; ++p) {
        if (p[0] == '%' && (p[1] == 'd' || p[1] == 'x') && used < 2) {
            fmt_int(&o, args[used++], p[1] == 'x');
            ++p;
        } else {
            *o++ = *p;
        }
    }
    *o++ = '\n';
    *o = '\0';
    FILE *f = fopen(LOG_PATH, "ab");
    if (!f) return;
    fputs(line, f);
    fclose(f);
}

static void spin(unsigned long n) {
    for (volatile unsigned long i = 0; i < n; ++i) { }
}

static void put_u32(unsigned char *p, uint32_t v) {
    p[0] = v >> 24; p[1] = v >> 16; p[2] = v >> 8; p[3] = v;
}

static uint32_t get_u32(const unsigned char *p) {
    return ((uint32_t)p[0] << 24) | ((uint32_t)p[1] << 16) |
           ((uint32_t)p[2] << 8) | p[3];
}

static int send_frame(nn_ch_t ch, int opcode, uint32_t request,
                      const char *payload) {
    unsigned char frame[MAX_FRAME];
    size_t len = payload ? strlen(payload) : 0;
    memcpy(frame, "NSAI", 4);
    frame[4] = 1;
    frame[5] = (unsigned char)opcode;
    put_u32(frame + 6, request);
    frame[10] = 0; frame[11] = 1; /* conversation 1 */
    put_u32(frame + 12, (uint32_t)len);
    if (len) memcpy(frame + HEADER_SIZE, payload, len);
    return (int16_t)TI_NN_Write(ch, frame, HEADER_SIZE + len);
}

/* Read frames until one with `want` arrives; answer host PINGs on the way. */
static int wait_for(nn_ch_t ch, int want, int attempts) {
    unsigned char frame[MAX_FRAME + 16];
    for (int i = 0; i < attempts; ++i) {
        uint32_t received = 0;
        if (i == 0) log_line("calling read want=%d", want, 0);
        int status = (int16_t)TI_NN_Read(ch, 1000, frame, sizeof(frame),
                                         (uint32_t)&received);
        if (status < 0) {
            if (i < 3 || i % 10 == 0) log_line("read %d status=%d", i, status);
            if (status == -257) return -257;
            spin(20000000UL);
            continue;
        }
        if (received < HEADER_SIZE || memcmp(frame, "NSAI", 4) != 0) {
            log_line("read %d bad frame bytes=%d", i, (int)received);
            continue;
        }
        int opcode = frame[5];
        int length = (int)get_u32(frame + 12);
        log_line("rx opcode=%d bytes=%d", opcode, length);
        if (opcode == OP_RESPONSE || opcode == OP_ERROR) {
            char text[208];
            int n = length > 200 ? 200 : length;
            memcpy(text, "rx payload: ", 12);
            memcpy(text + 12, frame + HEADER_SIZE, n - (n > 188 ? n - 188 : 0));
            text[12 + (n > 188 ? 188 : n)] = '\0';
            log_line(text, 0, 0);
        }
        if (opcode == OP_PING && want != OP_PING)
            log_line("pong sent=%d (reply to host ping %d)",
                     send_frame(ch, OP_PONG, get_u32(frame + 6), "PONG"), 0);
        if (opcode == want) return 1;
    }
    return 0;
}

static void task_main(unsigned argc, void *argv) {
    (void)argc; (void)argv;
    nn_oh_t op;
    nn_nh_t node = NULL;
    nn_ch_t ch = NULL;
    int status;

    log_line("task running prio=%d service=0x%x", TASK_PRIORITY, SERVICE_ID);
    spin(100000000UL); /* let the OS finish returning to the browser */

    for (int attempt = 0; attempt < 5 && !ch; ++attempt) {
        log_line("attempt %d calling create_op", attempt, 0);
        op = TI_NN_CreateOperationHandle();
        if (!op) { log_line("attempt %d no operation handle%d", attempt, 0); spin(200000000UL); continue; }
        log_line("attempt %d calling enum_init", attempt, 0);
        status = (int16_t)TI_NN_NodeEnumInit(op);
        log_line("attempt %d enum_init=%d", attempt, status);
        if (status >= 0) {
            status = (int16_t)TI_NN_NodeEnumNext(op, &node);
            log_line("attempt %d enum_next=%d", attempt, status);
            TI_NN_NodeEnumDone(op);
        }
        TI_NN_DestroyOperationHandle(op);
        if (status >= 0 && node) {
            log_line("attempt %d calling connect", attempt, 0);
            status = (int16_t)TI_NN_Connect(node, SERVICE_ID, &ch);
            log_line("attempt %d connect=%d", attempt, status);
            if (status < 0) ch = NULL;
        }
        if (!ch) spin(200000000UL);
    }
    if (!ch) {
        log_line("giving up: no channel%d%d", 0, 0);
        TCC_Terminate_Task(TCC_Current_Task_Pointer());
        return;
    }

    log_line("calling write ping%d", 0, 0);
    log_line("ping write=%d", send_frame(ch, OP_PING, 1, "PING"), 0);
    log_line("pong wait=%d", wait_for(ch, OP_PONG, 30), 0);
    log_line("request write=%d", send_frame(ch, OP_REQUEST, 2, "hello from task"), 0);
    log_line("response wait=%d", wait_for(ch, OP_RESPONSE, 60), 0);

    log_line("disconnect=%d", (int16_t)TI_NN_Disconnect(ch), 0);
    log_line("task done%d%d", 0, 0);
    TCC_Terminate_Task(TCC_Current_Task_Pointer());
}

int main(void) {
    remove(LOG_PATH);
    log_line("main entry osid=%d", (int)nl_osid(), 0);
    if (nl_osid() != CX2_CAS_6_2_0_333_OSID) return 1;
    int status = CX2_CAS_TCC_CREATE_TASK(task_control, (char *)"navtask",
                                         task_main, 0, NULL, task_stack,
                                         sizeof(task_stack), TASK_PRIORITY,
                                         0, NU_PREEMPT, NU_START);
    log_line("create_task=%d", status, 0);
    if (status != 0) return 1;
    nl_set_resident();
    return 0;
}
