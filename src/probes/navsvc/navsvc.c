/* NavNet service-from-task probe.  main() (the OS UI task) logs to a file,
 * creates a resident priority-250 Nucleus task and returns.  The task never
 * touches the file system: a task that used fopen and then terminated was
 * followed by a crash on the next resident-task launch in the same boot, and
 * there is no exported way to release its file-system user slot.
 *
 * Direction: the calculator registers service 0x5003 and the Mac Java
 * helper in NSPIRE_CLIENT_SERVICE_ID mode connects to it with
 * NavNet.connect (host-as-client, the direction TI's own host tools use).
 * The host-as-service direction reached CONNECTED but every read returned
 * -257; raw libnspire packets never trigger a TI_NN_StartService callback
 * because libnspire sends no connection request.
 *
 * Handshake: once the channel arrives the calculator sends PING #1, answers
 * any host PING, sends REQUEST #2 after the PONG, waits for its RESPONSE, then
 * sends REQUEST #3 "got:<response>" so the bridge log proves the calculator
 * received the response (bytes = 4 + response length).
 *
 * Without file access the task reports its progress as a status grid poked
 * straight into the OS framebuffer (no lcd_init, no GC): one row per step,
 * a lead cell (blue = not reached, green = ok, red = failed) and 16 cells
 * holding the step's value as bits (white = 1, dark grey = 0), top-left.
 */
#include <os.h>
#include <libndls.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#define SERVICE_ID 0x5003
#define TASK_PRIORITY 250u
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
static volatile nn_ch_t service_channel;
static volatile int service_callbacks;

enum { ROW_ALIVE, ROW_START, ROW_CALLBACKS, ROW_READS_OK, ROW_LAST_READ,
       ROW_PINGS, ROW_REQUEST, ROW_RESPONSE, ROW_PROOF, ROW_COUNT };
#define CELL 6
static int hww;

static void put_pixel(int x, int y, uint16_t color) {
    uint16_t *fb = (uint16_t *)REAL_SCREEN_BASE_ADDRESS;
    if (hww) fb[x * 240 + y] = color;
    else fb[y * 320 + x] = color;
}

static void fill_cell(int col, int row, uint16_t color) {
    for (int y = row * CELL; y < row * CELL + CELL - 1; ++y)
        for (int x = col * CELL; x < col * CELL + CELL - 1; ++x)
            put_pixel(x, y, color);
}

/* state: 0 not reached, 1 ok, 2 failed */
static void show(int row, int state, int value) {
    static const uint16_t lead[3] = {0x001F, 0x07E0, 0xF800};
    fill_cell(0, row, lead[state]);
    for (int bit = 0; bit < 16; ++bit)
        fill_cell(1 + bit, row, ((uint16_t)value >> (15 - bit)) & 1 ? 0xFFFF : 0x4208);
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
                      const void *payload, size_t len) {
    unsigned char frame[MAX_FRAME];
    if (len > MAX_FRAME - HEADER_SIZE) len = MAX_FRAME - HEADER_SIZE;
    memcpy(frame, "NSAI", 4);
    frame[4] = 1;
    frame[5] = (unsigned char)opcode;
    put_u32(frame + 6, request);
    frame[10] = 0; frame[11] = 1; /* conversation 1 */
    put_u32(frame + 12, (uint32_t)len);
    if (len) memcpy(frame + HEADER_SIZE, payload, len);
    return (int16_t)TI_NN_Write(ch, frame, HEADER_SIZE + len);
}

/* TI NavNet service model: the callback is the connection handler and the
 * connection lives only while it runs.  Returning right away (as both the
 * earlier host helper and the first navsvc did) closes the channel, which is
 * the -257 both sides kept seeing.  So the whole session runs here, in the
 * OS NavNet context; the resident task only registers the service. */
static volatile int session_done;

static void service_callback(nn_ch_t ch, void *data) {
    (void)data;
    unsigned char frame[MAX_FRAME + 16];
    char proof[MAX_FRAME];
    int state = 0; /* 0 wait pong, 1 request sent, 2 proof sent */
    int reads_ok = 0, pings = 0;

    service_callbacks++;
    service_channel = ch;
    show(ROW_CALLBACKS, 1, service_callbacks);
    int w0 = send_frame(ch, OP_PING, 1, "PING", 4);
    show(ROW_PINGS, w0 < 0 ? 2 : 1, w0);

    for (int i = 0; i < 120 && state < 2; ++i) {
        uint32_t received = 0;
        int status = (int16_t)TI_NN_Read(ch, 500, frame, sizeof(frame),
                                         (uint32_t)&received);
        show(ROW_LAST_READ, status < 0 ? 2 : 1, status);
        if (status < 0) {
            if (status == -257) break;
            continue;
        }
        if (received < HEADER_SIZE || memcmp(frame, "NSAI", 4) != 0) continue;
        show(ROW_READS_OK, 1, ++reads_ok);
        int opcode = frame[5];
        uint32_t request = get_u32(frame + 6);
        size_t length = get_u32(frame + 12);
        if (length > received - HEADER_SIZE) length = received - HEADER_SIZE;
        if (opcode == OP_PING) {
            show(ROW_PINGS, 1, ++pings);
            send_frame(ch, OP_PONG, request, "PONG", 4);
        } else if (opcode == OP_PONG && state == 0) {
            const char *hello = "hello from task";
            int w = send_frame(ch, OP_REQUEST, 2, hello, strlen(hello));
            show(ROW_REQUEST, w < 0 ? 2 : 1, w);
            state = 1;
        } else if (state == 1 && request == 2 &&
                   (opcode == OP_RESPONSE || opcode == OP_ERROR)) {
            show(ROW_RESPONSE, opcode == OP_RESPONSE ? 1 : 2, (int)length);
            memcpy(proof, "got:", 4);
            if (length > sizeof(proof) - 4 - HEADER_SIZE)
                length = sizeof(proof) - 4 - HEADER_SIZE;
            memcpy(proof + 4, frame + HEADER_SIZE, length);
            int w = send_frame(ch, OP_REQUEST, 3, proof, 4 + length);
            show(ROW_PROOF, w < 0 ? 2 : 1, w);
            state = 2;
        }
    }
    /* Keep the connection open briefly so the proof frame drains. */
    for (int i = 0; i < 4; ++i) {
        uint32_t received = 0;
        (void)TI_NN_Read(ch, 500, frame, sizeof(frame), (uint32_t)&received);
    }
    session_done = 1;
}

static void task_main(unsigned argc, void *argv) {
    (void)argc; (void)argv;

    spin(50000000UL);
    hww = lcd_type() == SCR_240x320_565;
    for (int row = 0; row < ROW_COUNT; ++row) show(row, 0, 0);
    show(ROW_ALIVE, 1, 1);
    int started = (int16_t)TI_NN_StartService(SERVICE_ID, NULL, service_callback);
    show(ROW_START, started < 0 ? 2 : 1, started);
    if (started < 0) goto out;

    /* Wait up to several minutes for a host session to finish. */
    for (unsigned long waited = 0; !session_done && waited < 600; ++waited) {
        show(ROW_ALIVE, 1, (int)waited);
        spin(20000000UL);
    }
    spin(100000000UL);
    TI_NN_StopService(SERVICE_ID);
out:
    TCC_Terminate_Task(TCC_Current_Task_Pointer());
}

static void log_main(const char *text) {
    FILE *f = fopen("/documents/navsvc_log.tns", "ab");
    if (!f) return;
    fputs(text, f);
    fclose(f);
}

int main(void) {
    remove("/documents/navsvc_log.tns");
    log_main("main entry\n");
    if (nl_osid() != CX2_CAS_6_2_0_333_OSID) { log_main("wrong os\n"); return 1; }
    int status = CX2_CAS_TCC_CREATE_TASK(task_control, (char *)"navsvc",
                                         task_main, 0, NULL, task_stack,
                                         sizeof(task_stack), TASK_PRIORITY,
                                         0, NU_PREEMPT, NU_START);
    log_main(status == 0 ? "task created\n" : "create failed\n");
    if (status != 0) return 1;
    nl_set_resident();
    return 0;
}
