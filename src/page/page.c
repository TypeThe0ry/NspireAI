/* NspireAI chat page for TI-Nspire CX II CAS 6.2.0.333 (Ndless OS id 46).
 *
 * Architecture (all pieces physically verified with src/probes, see
 * docs/test-status.md 2026-09-28/29):
 *
 * - The Ndless loader masks IRQs for the whole of main(), and TI's USB and
 *   NavNet stacks need the OS UI task to be free.  main() therefore only
 *   allocates state, creates a resident Nucleus task at priority 250 (below
 *   the OS workers, above idle) and returns.
 * - The page task draws into the OS framebuffer (saved on show, restored on
 *   hide; Esc hides the page and reopening the document shows the same
 *   resident page again), polls the key matrix, and sleeps between frames in
 *   Nucleus TCC_Task_Sleep so it never starves the OS.  While the page is
 *   up it masks the keypad interrupt so the still-running OS document
 *   browser does not act on the same keys.
 * - The calculator registers NavNet service 0x5001 and the Mac helper
 *   connects to it (host-as-client).  The service callback is the
 *   connection: the whole session runs inside it and talks to the page task
 *   through the small shared state below.
 * - The page task never touches the file system: a task that used files and
 *   then terminated crashed the next resident-task launch in the same boot.
 */
#include <os.h>
#include <libndls.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "charmap.h" /* nspire-io 6x8 font, from the Ndless SDK */

#ifndef SERVICE_ID
#define SERVICE_ID 0x5001
#endif
#define TASK_PRIORITY 250u
#define HEADER_SIZE 16
#define MAX_FRAME_PAYLOAD 224
#define FRAGMENT_HEADER_SIZE 10
#define OP_PING 1
#define OP_PONG 2
#define OP_REQUEST 3
#define OP_RESPONSE 4
#define OP_ERROR 5
#define OP_NEW 7
#define OP_FRAGMENT 8

/* ---- CX II CAS 6.2.0.333 private ABI (Ndless IDC map + disassembly) ---- */
typedef void (*task_entry_t)(unsigned argc, void *argv);
typedef int (*tcc_create_task_t)(void *task, char *name, task_entry_t entry,
                                 unsigned argc, void *argv, void *stack,
                                 unsigned stack_size, unsigned priority,
                                 unsigned time_slice, unsigned preempt,
                                 unsigned auto_start);
typedef void (*tcc_task_sleep_t)(unsigned ticks);
extern unsigned int nl_osid(void); /* Ndless ext syscall, not in SDK headers */
#define CX2_CAS_6_2_0_333_OSID 46u
#define TCC_CREATE_TASK ((tcc_create_task_t)(uintptr_t)0x1042A8C8u)
#define TCC_TASK_SLEEP ((tcc_task_sleep_t)(uintptr_t)0x1042A1C4u)
#define NU_PREEMPT 10u
#define NU_START 12u
#define TICKS_PER_FRAME 3 /* 100 ticks/s -> ~30 ms */

/* PL190-style interrupt controller: +0 read/enable, +4 disable. */
#define IRQ_ENABLE (*(volatile uint32_t *)0xDC000010u)
#define IRQ_DISABLE (*(volatile uint32_t *)0xDC000014u)
#define IRQ_KEYPAD (1u << 16)
#define LCD_BASE (*(volatile uint32_t *)0xC0000010u)
#define RTC_SECONDS (*(volatile uint32_t *)0x90090000u) /* as Ndless gettimeofday */

/* Session liveness (seconds).  TI_NN_Read only ever reports -257 when the
 * host really closed the channel; a host that died or restarted without
 * closing leaves a half-open channel, so the page pings when idle and gives
 * the session up when nothing has come back for a while. */
#define IDLE_PING_SECONDS 5
#define SESSION_DEAD_SECONDS 20
#define ANSWER_TIMEOUT_SECONDS 90 /* > the bridge's 45 s API timeout */

/* ---- display ---- */
#define W 320
#define H 240
#define CW 6
#define CH 9
#define COLS (W / CW)          /* 53 */
#define PAGE_MAGIC 0x4E534133u /* "NSA3": layout of struct shared below */
#ifndef BUILD_ID
#define BUILD_ID 0u
#endif

#define RGB(r, g, b) (uint16_t)((((r) >> 3) << 11) | (((g) >> 2) << 5) | ((b) >> 3))
#define C_BG RGB(250, 250, 250)
#define C_TEXT RGB(20, 20, 20)
#define C_USER RGB(20, 70, 170)
#define C_DIM RGB(120, 120, 120)
#define C_BAR RGB(30, 45, 80)
#define C_BAR_TEXT RGB(255, 255, 255)
#define C_OK RGB(40, 170, 80)
#define C_WARN RGB(220, 120, 20)
#define C_INPUT_BG RGB(232, 236, 244)

/* One page per boot.  Resident images are never freed, and a handful of
 * them exhausted the OS heap during testing (every later launch failed with
 * "document format is not supported").  So Esc only hides the page; opening
 * nspire_ai.tns again finds the hidden page through this registry and asks
 * it to come back instead of loading a second copy.  The registry lives in
 * the last LCD palette words: unused in 16-bit colour mode, not touched by
 * the Ndless loader (it writes the first 8 words), cleared by a reset. */
/* Each service id gets its own 4-word slot so the product page and the
 * autotest build never find (or overwrite) each other. */
#define REGISTRY ((volatile uint32_t *)(SERVICE_ID == 0x5001 ? 0xC00003F0u : 0xC00003E0u))
struct shared {
    uint32_t magic;
    uint32_t service;
    uint32_t build;              /* BUILD_ID of the resident code */
    volatile int show_request;   /* a relaunch asks the hidden page to show */
};
static struct shared shared_state = {PAGE_MAGIC, SERVICE_ID, BUILD_ID, 0};

static struct shared *registry_lookup(void) {
    uint32_t p = REGISTRY[1];
    if (REGISTRY[0] != PAGE_MAGIC || REGISTRY[2] != ~p) return NULL;
    if (p < 0x10000000u || p >= 0x14000000u) return NULL; /* SDRAM only */
    struct shared *s = (struct shared *)(uintptr_t)p;
    return s->magic == PAGE_MAGIC && s->service == SERVICE_ID ? s : NULL;
}

static void registry_publish(void) {
    uint32_t p = (uint32_t)(uintptr_t)&shared_state;
    REGISTRY[1] = p;
    REGISTRY[2] = ~p;
    REGISTRY[0] = PAGE_MAGIC;
}

static unsigned char task_control[1024] __attribute__((aligned(8)));
static unsigned char task_stack[8 * 1024] __attribute__((aligned(8)));
/* The page draws straight into the OS framebuffer (whatever the LCD base
 * points at), like ordinary Ndless programs.  v1 pointed the LCD at a
 * malloc'd buffer instead and the handheld showed garbage.  The OS picture
 * is saved on show and restored on hide. */
static uint16_t *fb;             /* current OS framebuffer (LCD base) */
static uint16_t *saved_screen;   /* OS picture while the page is shown */
static int hww;                  /* 240x320 panel: pixel (x,y) at x*240+y */
static int keypad_irq_was_enabled;

/* ---- shared between page task and NavNet callback ---- */
#define TEXT_CAP 3072
static volatile int link_up;
static volatile int quitting;
static volatile int session_active;
static volatile int out_pending;
static char out_text[MAX_FRAME_PAYLOAD];
static volatile uint32_t out_len, out_id;
static volatile int in_ready;
static char in_text[TEXT_CAP];
static volatile uint32_t in_len;
static volatile int in_error;
static volatile uint32_t awaited_id;
static volatile uint32_t session_gen;  /* newest callback wins */
/* Seeded from the RTC at start so a still-running bridge never mistakes the
 * requests of a new boot for ones it already answered. */
static uint16_t conversation_id = 1;

/* ---- chat history ---- */
#define HIST_LINES 120
#define INPUT_CAP 200
static char hist[HIST_LINES][COLS + 1];
static uint8_t hist_kind[HIST_LINES]; /* 0 ai, 1 user, 2 system */
static int hist_count;
static int scroll_back;
static char input[INPUT_CAP + 1];
static int input_len;
static int waiting;
static uint32_t next_id = 2;
static uint32_t waiting_since;
static int dirty = 1;

static void put_u32(unsigned char *p, uint32_t v) {
    p[0] = v >> 24; p[1] = v >> 16; p[2] = v >> 8; p[3] = v;
}

static uint32_t get_u32(const unsigned char *p) {
    return ((uint32_t)p[0] << 24) | ((uint32_t)p[1] << 16) |
           ((uint32_t)p[2] << 8) | p[3];
}

/* ------------------------------------------------------------------ */
/* NavNet session (runs inside the service callback)                   */
/* ------------------------------------------------------------------ */

static int send_frame(nn_ch_t ch, int opcode, uint32_t request,
                      const void *payload, uint32_t len) {
    unsigned char frame[HEADER_SIZE + MAX_FRAME_PAYLOAD];
    if (len > MAX_FRAME_PAYLOAD) len = MAX_FRAME_PAYLOAD;
    memcpy(frame, "NSAI", 4);
    frame[4] = 1;
    frame[5] = (unsigned char)opcode;
    put_u32(frame + 6, request);
    frame[10] = conversation_id >> 8;
    frame[11] = conversation_id & 0xFF;
    put_u32(frame + 12, len);
    if (len) memcpy(frame + HEADER_SIZE, payload, len);
    return (int16_t)TI_NN_Write(ch, frame, HEADER_SIZE + len);
}

static void deliver(const unsigned char *data, uint32_t len, int error) {
    if (in_ready) return; /* page has not consumed the previous one */
    if (len > TEXT_CAP - 1) len = TEXT_CAP - 1;
    memcpy(in_text, data, len);
    in_text[len] = '\0';
    in_len = len;
    in_error = error;
    in_ready = 1;
}

static void service_callback(nn_ch_t ch, void *data) {
    (void)data;
    unsigned char frame[HEADER_SIZE + MAX_FRAME_PAYLOAD + 16];
    uint32_t frag_total = 0, frag_next = 0, frag_id = 0;
    int frag_opcode = 0;

    if (quitting) return;
    /* Newest connection wins: a new callback means the host reconnected,
     * so any older session is stale.  Ask it to leave, then take over. */
    uint32_t gen = ++session_gen;
    for (int i = 0; i < 40 && session_active; ++i) TCC_TASK_SLEEP(5);
    if (session_active || gen != session_gen) return;
    session_active = 1;
    link_up = 1;
    dirty = 1;
    uint32_t last_rx = RTC_SECONDS, last_ping = last_rx;
    send_frame(ch, OP_PING, 1, "PING", 4);

    while (!quitting && gen == session_gen) {
        if (out_pending) {
            int w = send_frame(ch, OP_REQUEST, out_id, out_text, out_len);
            out_pending = 0;
            if (w < 0) {
                static const char msg[] = "send failed";
                deliver((const unsigned char *)msg, sizeof(msg) - 1, 1);
                break;
            }
        }
        uint32_t now = RTC_SECONDS;
        if (now - last_rx >= SESSION_DEAD_SECONDS) break;
        if (now - last_rx >= IDLE_PING_SECONDS && now - last_ping >= IDLE_PING_SECONDS) {
            if (send_frame(ch, OP_PING, 1, "PING", 4) < 0) break;
            last_ping = now;
        }
        uint32_t received = 0;
        /* The timeout argument behaves like seconds (100 blocked the whole
         * session on 2026-09-29), so ask for 1: the loop then comes back at
         * least once a second to send requests and keepalives. */
        int status = (int16_t)TI_NN_Read(ch, 1, frame, sizeof(frame),
                                         (uint32_t)&received);
        if (status < 0) {
            if (status == -257) break;
            TCC_TASK_SLEEP(2); /* never spin in the OS NavNet context */
            continue;
        }
        if (received < HEADER_SIZE || memcmp(frame, "NSAI", 4) != 0 ||
            frame[4] != 1)
            continue;
        last_rx = RTC_SECONDS;
        int opcode = frame[5];
        uint32_t request = get_u32(frame + 6);
        uint32_t length = get_u32(frame + 12);
        const unsigned char *payload = frame + HEADER_SIZE;
        if (length > received - HEADER_SIZE) length = received - HEADER_SIZE;

        if (opcode == OP_PING) {
            if (send_frame(ch, OP_PONG, request, "PONG", 4) < 0) break;
        } else if (opcode == OP_FRAGMENT && length >= FRAGMENT_HEADER_SIZE &&
                   request == awaited_id) {
            int orig = payload[0];
            uint32_t total = get_u32(payload + 2);
            uint32_t offset = get_u32(payload + 6);
            uint32_t chunk = length - FRAGMENT_HEADER_SIZE;
            if (offset == 0) {
                frag_total = total;
                frag_next = 0;
                frag_id = request;
                frag_opcode = orig;
            }
            if (request != frag_id || offset != frag_next || orig != frag_opcode)
                continue;
            /* Reassemble straight into in_text; the page only reads it
             * after in_ready is set, and a new request is only sent once the
             * previous answer was consumed. */
            if (in_ready) continue;
            if (frag_next < TEXT_CAP - 1) {
                uint32_t room = TEXT_CAP - 1 - frag_next;
                memcpy(in_text + frag_next, payload + FRAGMENT_HEADER_SIZE,
                       chunk < room ? chunk : room);
            }
            frag_next += chunk;
            if (frag_next >= frag_total) {
                uint32_t n = frag_total < TEXT_CAP - 1 ? frag_total : TEXT_CAP - 1;
                in_text[n] = '\0';
                in_len = n;
                in_error = frag_opcode != OP_RESPONSE;
                in_ready = 1;
                frag_total = frag_next = 0;
            }
        } else if ((opcode == OP_RESPONSE || opcode == OP_ERROR) &&
                   request == awaited_id) {
            deliver(payload, length, opcode == OP_ERROR);
        }
    }
    /* Returning closes this channel.  A request that was never sent must not
     * be replayed by the next session. */
    out_pending = 0;
    if (gen == session_gen) link_up = 0;
    session_active = 0;
    dirty = 1;
}

/* ------------------------------------------------------------------ */
/* Rendering                                                           */
/* ------------------------------------------------------------------ */

static inline void px(int x, int y, uint16_t c) {
    if (hww) fb[x * 240 + y] = c;
    else fb[y * W + x] = c;
}

static void fill_rect(int x, int y, int w, int h, uint16_t c) {
    for (int yy = y; yy < y + h && yy < H; ++yy)
        for (int xx = x; xx < x + w && xx < W; ++xx)
            px(xx, yy, c);
}

static void draw_char(int x, int y, unsigned char ch, uint16_t fg) {
    const char *glyph = MBCharSet8x6_definition[ch];
    for (int col = 0; col < 6; ++col) {
        unsigned bits = (unsigned char)glyph[col];
        for (int row = 0; row < 8; ++row)
            if (bits & (1u << row) && x + col < W && y + row < H)
                px(x + col, y + row, fg);
    }
}

static void draw_text(int x, int y, const char *s, uint16_t fg) {
    for (; *s && x <= W - CW; ++s, x += CW) draw_char(x, y, (unsigned char)*s, fg);
}

static void hist_add_line(const char *text, int len, int kind) {
    if (hist_count == HIST_LINES) {
        memmove(hist, hist + 1, sizeof(hist[0]) * (HIST_LINES - 1));
        memmove(hist_kind, hist_kind + 1, HIST_LINES - 1);
        hist_count--;
    }
    if (len > COLS) len = COLS;
    memcpy(hist[hist_count], text, len);
    hist[hist_count][len] = '\0';
    hist_kind[hist_count] = kind;
    hist_count++;
}

/* Word-wrap `text` into history lines (prefix on the first line). */
static void hist_add(const char *prefix, const char *text, int kind) {
    char line[COLS + 1];
    int used = 0;
    for (const char *p = prefix; *p && used < COLS; ++p) line[used++] = *p;
    const char *s = text;
    while (*s) {
        if (*s == '\n') {
            hist_add_line(line, used, kind);
            used = 0;
            ++s;
            continue;
        }
        if (*s == '\r') { ++s; continue; }
        const char *word = s;
        int wlen = 0;
        while (word[wlen] && word[wlen] != ' ' && word[wlen] != '\n') ++wlen;
        if (wlen == 0) { /* space */
            if (used < COLS && used > 0) line[used++] = ' ';
            ++s;
            continue;
        }
        if (used + wlen > COLS && used > 0) {
            hist_add_line(line, used, kind);
            used = 0;
        }
        while (wlen > 0) {
            int take = wlen < COLS - used ? wlen : COLS - used;
            for (int i = 0; i < take; ++i) {
                unsigned char c = (unsigned char)s[i];
                line[used++] = (c >= 32 && c < 127) ? (char)c : '?';
            }
            s += take;
            wlen -= take;
            if (wlen > 0) {
                hist_add_line(line, used, kind);
                used = 0;
            }
        }
    }
    if (used > 0 || !*text) hist_add_line(line, used, kind);
    scroll_back = 0;
    dirty = 1;
}

#define HIST_TOP 16
#define INPUT_TOP 204
#define HIST_ROWS ((INPUT_TOP - HIST_TOP - 2) / CH)

static void render(void) {
    fill_rect(0, 0, W, H, C_BG);
    fill_rect(0, 0, W, 13, C_BAR);
    draw_text(4, 3, "NspireAI", C_BAR_TEXT);
    const char *state = link_up ? (waiting ? "thinking..." : "linked")
                                : "waiting for Mac";
    uint16_t dot = link_up ? C_OK : C_WARN;
    fill_rect(W - 6 - CW * (int)strlen(state) - 10, 4, 6, 6, dot);
    draw_text(W - 4 - CW * (int)strlen(state), 3, state, C_BAR_TEXT);

    int first = hist_count - HIST_ROWS - scroll_back;
    if (first < 0) first = 0;
    for (int r = 0; r < HIST_ROWS && first + r < hist_count; ++r) {
        int i = first + r;
        uint16_t c = hist_kind[i] == 1 ? C_USER : hist_kind[i] == 2 ? C_DIM : C_TEXT;
        draw_text(1, HIST_TOP + r * CH, hist[i], c);
    }

    fill_rect(0, INPUT_TOP - 2, W, 1, C_DIM);
    fill_rect(0, INPUT_TOP, W, 2 * CH + 4, C_INPUT_BG);
    /* Show the tail of the input on two rows. */
    char shown[2 * COLS + 1];
    int cap = 2 * COLS - 3;
    int start = input_len > cap ? input_len - cap : 0;
    int n = 0;
    shown[n++] = '>';
    shown[n++] = ' ';
    for (int i = start; i < input_len; ++i) shown[n++] = input[i];
    shown[n++] = waiting ? ' ' : '_';
    shown[n] = '\0';
    char row0[COLS + 1];
    int l0 = n < COLS ? n : COLS;
    memcpy(row0, shown, l0);
    row0[l0] = '\0';
    draw_text(1, INPUT_TOP + 2, row0, C_TEXT);
    if (n > COLS) draw_text(1, INPUT_TOP + 2 + CH, shown + COLS, C_TEXT);

    draw_text(1, H - CH, "enter send  del erase  tab/menu scroll  esc quit", C_DIM);
}

/* The LCD DMA reads RAM, not the data cache.  libndls clear_cache() also
 * invalidates the whole D-cache, which from a preemptible task can throw
 * away another task's writes made between its clean and invalidate steps;
 * cleaning (write back) is all the LCD needs. */
static void clean_dcache(void) {
    unsigned zero = 0;
    __asm volatile(
        "0: mrc p15, 0, r15, c7, c10, 3 @ test and clean D-cache\n"
        "   bne 0b\n"
        "   mcr p15, 0, %0, c7, c10, 4  @ drain write buffer\n"
        : : "r"(zero) : "cc", "memory");
}

static void present(void) {
    clean_dcache();
}

/* ------------------------------------------------------------------ */
/* Input                                                               */
/* ------------------------------------------------------------------ */

struct keymap { const t_key *key; char lower, upper; };
static const struct keymap chars[] = {
    {&KEY_NSPIRE_A, 'a', 'A'}, {&KEY_NSPIRE_B, 'b', 'B'}, {&KEY_NSPIRE_C, 'c', 'C'},
    {&KEY_NSPIRE_D, 'd', 'D'}, {&KEY_NSPIRE_E, 'e', 'E'}, {&KEY_NSPIRE_F, 'f', 'F'},
    {&KEY_NSPIRE_G, 'g', 'G'}, {&KEY_NSPIRE_H, 'h', 'H'}, {&KEY_NSPIRE_I, 'i', 'I'},
    {&KEY_NSPIRE_J, 'j', 'J'}, {&KEY_NSPIRE_K, 'k', 'K'}, {&KEY_NSPIRE_L, 'l', 'L'},
    {&KEY_NSPIRE_M, 'm', 'M'}, {&KEY_NSPIRE_N, 'n', 'N'}, {&KEY_NSPIRE_O, 'o', 'O'},
    {&KEY_NSPIRE_P, 'p', 'P'}, {&KEY_NSPIRE_Q, 'q', 'Q'}, {&KEY_NSPIRE_R, 'r', 'R'},
    {&KEY_NSPIRE_S, 's', 'S'}, {&KEY_NSPIRE_T, 't', 'T'}, {&KEY_NSPIRE_U, 'u', 'U'},
    {&KEY_NSPIRE_V, 'v', 'V'}, {&KEY_NSPIRE_W, 'w', 'W'}, {&KEY_NSPIRE_X, 'x', 'X'},
    {&KEY_NSPIRE_Y, 'y', 'Y'}, {&KEY_NSPIRE_Z, 'z', 'Z'},
    {&KEY_NSPIRE_0, '0', '0'}, {&KEY_NSPIRE_1, '1', '1'}, {&KEY_NSPIRE_2, '2', '2'},
    {&KEY_NSPIRE_3, '3', '3'}, {&KEY_NSPIRE_4, '4', '4'}, {&KEY_NSPIRE_5, '5', '5'},
    {&KEY_NSPIRE_6, '6', '6'}, {&KEY_NSPIRE_7, '7', '7'}, {&KEY_NSPIRE_8, '8', '8'},
    {&KEY_NSPIRE_9, '9', '9'},
    {&KEY_NSPIRE_SPACE, ' ', ' '}, {&KEY_NSPIRE_PERIOD, '.', '.'},
    {&KEY_NSPIRE_COMMA, ',', ','}, {&KEY_NSPIRE_QUESEXCL, '?', '!'},
    {&KEY_NSPIRE_PLUS, '+', '+'}, {&KEY_NSPIRE_MINUS, '-', '-'},
    {&KEY_NSPIRE_NEGATIVE, '-', '-'}, {&KEY_NSPIRE_MULTIPLY, '*', '*'},
    {&KEY_NSPIRE_DIVIDE, '/', '/'}, {&KEY_NSPIRE_EXP, '^', '^'},
    {&KEY_NSPIRE_LP, '(', '('}, {&KEY_NSPIRE_RP, ')', ')'},
    {&KEY_NSPIRE_EQU, '=', '='}, {&KEY_NSPIRE_COLON, ':', ':'},
    {&KEY_NSPIRE_APOSTROPHE, '\'', '"'}, {&KEY_NSPIRE_QUOTE, '"', '"'},
    {&KEY_NSPIRE_LTHAN, '<', '<'}, {&KEY_NSPIRE_GTHAN, '>', '>'},
};
#define NCHARS (int)(sizeof(chars) / sizeof(chars[0]))

/* Arrow keys go through the touchpad (I2C) on CX II, so the page avoids
 * them: Tab scrolls back, Menu scrolls forward.  Everything here is a plain
 * key-matrix read. */
enum { K_ENTER, K_DEL, K_ESC, K_UP, K_DOWN, K_COUNT };

static int matrix_any_pressed(void) {
    for (volatile uint32_t *r = (volatile uint32_t *)0x900E0010u;
         r < (volatile uint32_t *)0x900E0020u; ++r)
        if (*r) return 1;
    return 0;
}
static uint8_t prev_char[NCHARS];
static uint8_t prev_ctl[K_COUNT];
static int del_hold;

static int ctl_down(int k) {
    switch (k) {
    case K_ENTER: return isKeyPressed(KEY_NSPIRE_ENTER) || isKeyPressed(KEY_NSPIRE_RET);
    case K_DEL: return isKeyPressed(KEY_NSPIRE_DEL);
    case K_ESC: return isKeyPressed(KEY_NSPIRE_ESC);
    case K_UP: return isKeyPressed(KEY_NSPIRE_TAB);
    case K_DOWN: return isKeyPressed(KEY_NSPIRE_MENU);
    }
    return 0;
}

static void submit(const char *text, int len) {
    if (len <= 0 || waiting) return;
    if (len > MAX_FRAME_PAYLOAD) len = MAX_FRAME_PAYLOAD;
    char shown[INPUT_CAP + 1];
    memcpy(shown, text, len);
    shown[len] = '\0';
    hist_add("> ", shown, 1);
    if (!link_up) {
        hist_add("", "(not connected to the Mac bridge yet)", 2);
        return;
    }
    memcpy(out_text, text, len);
    out_len = len;
    out_id = next_id++;
    awaited_id = out_id;
    waiting = 1;
    waiting_since = RTC_SECONDS;
    out_pending = 1;
    dirty = 1;
}

/* Returns 0 when the user asked to quit. */
static int poll_keys(void) {
    int shift = isKeyPressed(KEY_NSPIRE_SHIFT);
    for (int i = 0; i < NCHARS; ++i) {
        int down = isKeyPressed(*chars[i].key) ? 1 : 0;
        if (down && !prev_char[i] && !waiting && input_len < INPUT_CAP) {
            input[input_len++] = shift ? chars[i].upper : chars[i].lower;
            input[input_len] = '\0';
            dirty = 1;
        }
        prev_char[i] = down;
    }
    for (int k = 0; k < K_COUNT; ++k) {
        int down = ctl_down(k);
        int pressed = down && !prev_ctl[k];
        if (k == K_DEL) {
            del_hold = down ? del_hold + 1 : 0;
            if ((pressed || del_hold > 15) && input_len > 0 && !waiting) {
                input[--input_len] = '\0';
                dirty = 1;
            }
        } else if (pressed) {
            if (k == K_ESC) { prev_ctl[k] = down; return 0; }
            if (k == K_ENTER && input_len > 0) {
                submit(input, input_len);
                input_len = 0;
                input[0] = '\0';
            }
            if (k == K_UP && scroll_back < hist_count - HIST_ROWS) { scroll_back++; dirty = 1; }
            if (k == K_DOWN && scroll_back > 0) { scroll_back--; dirty = 1; }
        }
        prev_ctl[k] = down;
    }
    return 1;
}

/* ------------------------------------------------------------------ */
/* Page task                                                           */
/* ------------------------------------------------------------------ */

static void wait_keys_released(void) {
    for (int i = 0; i < 200 && matrix_any_pressed(); ++i) TCC_TASK_SLEEP(TICKS_PER_FRAME);
}

/* Follow the LCD base if the OS switches buffers, and redraw now and then
 * in case an OS status update painted over part of the page. */
static void hold_screen(void) {
    static int frames;
    uint16_t *now = (uint16_t *)(uintptr_t)LCD_BASE;
    if (now != fb) {
        fb = now;
        dirty = 1;
    }
    if (++frames >= 16) { /* ~0.5 s */
        frames = 0;
        dirty = 1;
    }
}

static void show_page(void) {
    wait_keys_released(); /* the Enter that opened the document */
#ifndef PAGE_KEEP_KEYPAD_IRQ
    keypad_irq_was_enabled = (IRQ_ENABLE & IRQ_KEYPAD) != 0;
    if (keypad_irq_was_enabled) IRQ_DISABLE = IRQ_KEYPAD;
#endif
    fb = (uint16_t *)(uintptr_t)LCD_BASE;
    memcpy(saved_screen, fb, W * H * 2);
    dirty = 1;
    render();
    present();
    for (int i = 0; i < NCHARS; ++i) prev_char[i] = 1;
    for (int i = 0; i < K_COUNT; ++i) prev_ctl[i] = 1;
}

static void hide_page(void) {
    wait_keys_released(); /* do not leak Esc to the OS browser */
    memcpy(fb, saved_screen, W * H * 2);
    clean_dcache();
#ifndef PAGE_KEEP_KEYPAD_IRQ
    if (keypad_irq_was_enabled) IRQ_ENABLE = IRQ_KEYPAD;
#endif
}

static void task_main(unsigned argc, void *argv) {
    (void)argc; (void)argv;
    int visible = 1, was_linked = 0;
#ifdef PAGE_AUTOTEST
    /* Remote key injection goes to the OS event queue, not the key matrix,
     * so an unattended autotest hides the page on its own when done. */
    int autotest_stage = 0;
    int autotest_frames = 0, autotest_done_at = -1;
#endif

    hww = lcd_type() == SCR_240x320_565;
    uint32_t boot = RTC_SECONDS;
    conversation_id = (uint16_t)(boot | 1u);
    next_id = (boot & 0xFFFFu) << 12 | 2u;
    hist_add("", "NspireAI - type a question and press enter.", 2);
    int started = -1;
    uint32_t start_tried = 0;
    show_page();

    for (;;) {
        if (started < 0 && RTC_SECONDS - start_tried >= 2) {
            /* Keep trying instead of giving up after one failure. */
            started = (int16_t)TI_NN_StartService(SERVICE_ID, NULL, service_callback);
            if (start_tried == 0 || started >= 0)
                hist_add("", started < 0 ? "NavNet service busy; retrying..."
                                         : "Waiting for the Mac bridge...", 2);
            start_tried = RTC_SECONDS;
        }
        if (waiting && RTC_SECONDS - waiting_since >= ANSWER_TIMEOUT_SECONDS) {
            /* Never lock input forever on a lost request or answer. */
            waiting = 0;
            awaited_id = 0;
            hist_add("! ", "no answer from the Mac; try again", 2);
        }
        if (link_up != was_linked) {
            was_linked = link_up;
            hist_add("", was_linked ? "Connected to the Mac bridge."
                                    : "Mac bridge disconnected; waiting...", 2);
            if (!was_linked) waiting = 0;
        }
        if (in_ready) {
            /* Answers keep arriving while the page is hidden. */
            int in_error_seen = in_error;
            (void)in_error_seen;
            hist_add(in_error ? "! " : "", in_text, in_error ? 2 : 0);
            in_ready = 0;
            waiting = 0;
#ifdef PAGE_AUTOTEST
            if (autotest_stage == 1 && !in_error_seen) {
                /* Proof for the host log: send the response length back. */
                char ack[48] = "autotest got ";
                int n = 13, v = (int)in_len;
                char digits[12];
                int d = 0;
                do { digits[d++] = '0' + v % 10; v /= 10; } while (v);
                while (d) ack[n++] = digits[--d];
                ack[n] = '\0';
                submit(ack, n);
                autotest_stage = 2;
            } else if (autotest_stage == 2) {
                autotest_stage = 3;
                autotest_done_at = autotest_frames;
            }
#endif
        }

        if (!visible) {
            if (shared_state.show_request) {
                shared_state.show_request = 0;
                show_page();
                visible = 1;
#ifdef PAGE_AUTOTEST
                autotest_stage = 0;
                autotest_frames = 0;
                autotest_done_at = -1;
#endif
            } else {
                TCC_TASK_SLEEP(10);
            }
            continue;
        }

        hold_screen();
        int stay = poll_keys();
#ifdef PAGE_AUTOTEST
        ++autotest_frames;
        if (autotest_stage == 0 && link_up) {
            static const char hello[] = "autotest hello from the page";
            submit(hello, sizeof(hello) - 1);
            autotest_stage = 1;
        }
        if (autotest_frames > 90 * 33) stay = 0;
        if (autotest_done_at >= 0 && autotest_frames - autotest_done_at > 100) stay = 0;
#endif
        if (!stay) {
            hide_page();
            visible = 0;
            shared_state.show_request = 0;
            continue;
        }
        if (dirty) {
            dirty = 0;
            render();
            present();
        }
        TCC_TASK_SLEEP(TICKS_PER_FRAME);
    }
}

/* main() runs in the OS UI task, where file access is safe; the log shows
 * how far the launch got (the task itself must not touch files). */
static void main_log(const char *text, int value) {
    char line[64];
    int n = 0;
    while (*text && n < 40) line[n++] = *text++;
    line[n++] = ' ';
    unsigned u = value < 0 ? (unsigned)-value : (unsigned)value;
    char digits[12];
    int d = 0;
    do { digits[d++] = '0' + u % 10; u /= 10; } while (u);
    if (value < 0) line[n++] = '-';
    while (d) line[n++] = digits[--d];
    line[n++] = '\n';
    line[n] = '\0';
    FILE *f = fopen("/documents/nspire_ai_log.tns", "ab");
    if (!f) return;
    fputs(line, f);
    fclose(f);
}

int main(void) {
    if (nl_osid() != CX2_CAS_6_2_0_333_OSID) { main_log("wrong os", (int)nl_osid()); return 1; }
    struct shared *existing = registry_lookup();
    if (existing) {
        /* The page is already resident: bring it back, load nothing new.
         * A build deployed later in the same boot only takes effect after a
         * reset.  Retiring the old copy in place (TI_NN_StopService and task
         * termination while its NavNet callback was still inside
         * TI_NN_Read) coincided with the handheld dropping off USB on
         * 2026-09-29, so it is not attempted. */
        existing->show_request = 1;
        main_log(existing->build == BUILD_ID ? "show existing page"
                                             : "show older resident build",
                 (int)existing->build);
        return 0;
    }
    /* The framebuffer comes from the OS heap at run time; the Ndless loader
     * rejects images whose Zehn allocation exceeds ~60 KB. */
    saved_screen = malloc(W * H * 2);
    if (!saved_screen) { main_log("out of memory", 0); return 1; }
    int status = TCC_CREATE_TASK(task_control, (char *)"NspireAI", task_main,
                                 0, NULL, task_stack, sizeof(task_stack),
                                 TASK_PRIORITY, 0, NU_PREEMPT, NU_START);
    main_log("create task", status);
    if (status != 0) { free(saved_screen); return 1; }
    registry_publish();
    nl_set_resident();
    return 0;
}
