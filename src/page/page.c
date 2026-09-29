/* NspireAI chat page for TI-Nspire CX II CAS 6.2.0.333 (Ndless OS id 46).
 *
 * Architecture (each piece verified on the handheld, see docs/test-status.md
 * 2026-09-28/29):
 *
 * - The page runs in main(), in the OS UI task.  main() re-enables IRQs
 *   (the Ndless loader masks them) and paces itself with Nucleus
 *   TCC_Task_Sleep, which really blocks the UI task.  USB and NavNet keep
 *   running meanwhile (navmain probe, 2026-09-29 17:18), while the OS
 *   document browser is frozen: it neither repaints over the page (drawing
 *   from a resident task flickered) nor reacts to keys.
 * - The calculator registers NavNet service 0x5001 and the Mac helper
 *   connects to it (host-as-client) and sends a 1 s keepalive.  The service
 *   callback is the connection: the session runs inside it and talks to the
 *   page through the small shared state below.
 * - The LCD only scans out of on-chip SRAM (0xA8000000), so the page renders
 *   off screen and copies into the LCD buffer; on exit the Ndless loader
 *   restores the OS picture.
 * - Nothing stays resident: images must stay under ~60 KB of Zehn
 *   allocation, and resident images leak until reset.
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
typedef void (*tcc_task_sleep_t)(unsigned ticks);
extern unsigned int nl_osid(void); /* Ndless ext syscall, not in SDK headers */
#define CX2_CAS_6_2_0_333_OSID 46u
#define TCC_TASK_SLEEP ((tcc_task_sleep_t)(uintptr_t)0x1042A1C4u)
#define TICKS_PER_FRAME 3 /* 100 ticks/s -> ~30 ms */

/* PL190-style interrupt controller: +0 read/enable, +4 disable. */
#define IRQ_ENABLE (*(volatile uint32_t *)0xDC000010u)
#define IRQ_DISABLE (*(volatile uint32_t *)0xDC000014u)
#define IRQ_KEYPAD (1u << 16)
#define LCD_BASE (*(volatile uint32_t *)0xC0000010u)
/* PL111 hardware cursor overlay: the OS shows its hourglass through it and
 * keeps turning it back on while a document is "opening". */
#define LCD_CURSOR_CTRL (*(volatile uint32_t *)0xC0000C00u)
#define LCD_CURSOR_XY (*(volatile uint32_t *)0xC0000C10u)   /* x bits 0-9, y bits 16-25 */
#define LCD_CURSOR_CLIP (*(volatile uint32_t *)0xC0000C14u) /* clip x bits 0-5, y bits 8-13 */
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

static uint16_t *fb;    /* the OS's LCD buffer (LCD_BASE at start) */
static uint16_t *back;  /* off-screen 320x240 landscape render target */
static int hww;         /* 240x320 panel: pixel (x,y) at x*240+y */
/* Private scan-out.  The OS keeps repainting its LCD buffer (the document
 * "opening" hourglass animation alone repaints the whole screen several
 * times a second), so the page gives the LCD its own SDRAM buffers instead:
 * two of them, filled in the panel's native portrait order (measured with
 * src/probes/scanout: pixel (x,y) at x*240 + 239-y) and flipped by writing
 * the LCD base register, which the PL111 latches at the next frame. */
static uint16_t *scan[2];
static int scan_front;

/* ---- shared between the page loop and the NavNet callback ---- */
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
static volatile nn_ch_t active_ch;     /* lets the page end a session on exit */
/* Callbacks currently executing code in this image.  main() must not return
 * (freeing the image) while this is non-zero: the navmain probe returned
 * with a second callback running and the handheld dropped off USB. */
static volatile int callbacks_running;
/* Telemetry for the host: the page loop bumps loop_beat every frame and
 * records the step it is about to run, and the callback reports both in its
 * PONG payload.  If the UI task ever stalls, the bridge log shows where. */
static volatile uint32_t loop_beat;
static volatile int loop_step;
#define STEP(n) (loop_step = (n))
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

static void service_session(nn_ch_t ch) {
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
    active_ch = ch;
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
            char pong[40] = "PONG b=";
            int n = 7;
            uint32_t v = loop_beat;
            char d[12];
            int k = 0;
            do { d[k++] = '0' + v % 10; v /= 10; } while (v);
            while (k) pong[n++] = d[--k];
            pong[n++] = ' '; pong[n++] = 's'; pong[n++] = '=';
            v = (uint32_t)loop_step;
            k = 0;
            do { d[k++] = '0' + v % 10; v /= 10; } while (v);
            while (k) pong[n++] = d[--k];
            if (send_frame(ch, OP_PONG, request, pong, n) < 0) break;
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
    active_ch = NULL;
    session_active = 0;
    dirty = 1;
}

static void service_callback(nn_ch_t ch, void *data) {
    (void)data;
    callbacks_running++;
    service_session(ch);
    callbacks_running--;
}

/* ------------------------------------------------------------------ */
/* Rendering                                                           */
/* ------------------------------------------------------------------ */

static inline void px(int x, int y, uint16_t c) {
    back[y * W + x] = c;
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

    draw_text(1, H - CH, "enter send  del erase  tab/menu scroll  esc exit", C_DIM);
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
    if (!scan[0]) { /* fallback: draw into the OS buffer */
        if (hww) {
            for (int x = 0; x < W; ++x)
                for (int y = 0; y < H; ++y)
                    fb[x * 240 + y] = back[y * W + x];
        } else {
            memcpy(fb, back, W * H * 2);
        }
        clean_dcache();
        return;
    }
    uint16_t *dst = scan[scan_front ^ 1];
    for (int y = 0; y < H; ++y) {
        const uint16_t *row = back + y * W;
        uint16_t *col = dst + (239 - y);
        for (int x = 0; x < W; ++x, col += 240) *col = row[x];
    }
    clean_dcache();
    scan_front ^= 1;
    LCD_BASE = (uint32_t)(uintptr_t)dst;
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
/* Page loop (main, OS UI task)                                        */
/* ------------------------------------------------------------------ */

static void wait_keys_released(void) {
    for (int i = 0; i < 200 && matrix_any_pressed(); ++i) TCC_TASK_SLEEP(TICKS_PER_FRAME);
}

/* main() runs in the OS UI task, where file access is safe. */
static void main_log(const char *text, int value) {
    char line[80];
    int n = 0;
    while (*text && n < 60) line[n++] = *text++;
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

/* The image is freed when main() returns, so no NavNet callback may be
 * running by then.  Order matters: end the session, stop the service so the
 * host's reconnects no longer start callbacks, then wait until every
 * callback has left this image.  Returns 1 when that is guaranteed. */
static int end_session(int started) {
    quitting = 1;
    session_gen++;
    /* The Mac's 1 s keepalive makes the callback's blocking read return. */
    for (int i = 0; i < 100 && session_active; ++i) TCC_TASK_SLEEP(1);
    nn_ch_t ch = active_ch;
    if (session_active && ch) {
        TI_NN_Disconnect(ch); /* unblocks the read with -257 */
        for (int i = 0; i < 300 && session_active; ++i) TCC_TASK_SLEEP(1);
    }
    if (started >= 0) TI_NN_StopService(SERVICE_ID);
    /* A callback dispatched just before StopService sees quitting and
     * returns at once; give it time to leave. */
    for (int i = 0; i < 100; ++i) {
        TCC_TASK_SLEEP(1);
        if (i >= 30 && !callbacks_running && !session_active) return 1;
    }
    return !callbacks_running && !session_active;
}

int main(void) {
    if (nl_osid() != CX2_CAS_6_2_0_333_OSID) { main_log("wrong os", (int)nl_osid()); return 1; }
    back = malloc(W * H * 2);
    if (!back) { main_log("out of memory", 0); return 1; }
    hww = lcd_type() == SCR_240x320_565;
    fb = (uint16_t *)(uintptr_t)LCD_BASE;
    /* The OS re-enables the cursor overlay on every hourglass tick, so
     * toggling its enable bit flickers.  Clipping the whole image and
     * parking it off screen hides it whatever the OS does. */
    uint32_t saved_cursor_xy = LCD_CURSOR_XY, saved_cursor_clip = LCD_CURSOR_CLIP;
    uint8_t *scan_raw = NULL;
    if (!hww) { /* portrait mapping measured on a landscape-mode unit only */
        scan_raw = malloc(2 * W * H * 2 + 64);
        if (scan_raw) {
            scan[0] = (uint16_t *)(((uintptr_t)scan_raw + 31) & ~(uintptr_t)31);
            scan[1] = scan[0] + W * H;
            memset(scan[0], 0xFF, 2 * W * H * 2);
        }
    }
    uint32_t boot = RTC_SECONDS;
    conversation_id = (uint16_t)(boot | 1u);
    next_id = (boot & 0xFFFFu) << 12 | 2u;

    /* The loader runs main() with IRQs masked; TCC_Task_Sleep and the USB
     * stack need them.  The keypad IRQ stays masked so no key presses are
     * queued for the frozen OS browser to replay after the page closes. */
    int saved_irq = TCT_Local_Control_Interrupts(0);
#ifndef PAGE_KEEP_KEYPAD_IRQ
    int keypad_irq_was_enabled = (IRQ_ENABLE & IRQ_KEYPAD) != 0;
    if (keypad_irq_was_enabled) IRQ_DISABLE = IRQ_KEYPAD;
#endif

    hist_add("", "NspireAI - type a question and press enter.", 2);
    int started = -1;
    uint32_t start_tried = 0;
    int was_linked = 0;
    wait_keys_released(); /* the Enter that opened the document */
    for (int i = 0; i < NCHARS; ++i) prev_char[i] = 1;
    for (int i = 0; i < K_COUNT; ++i) prev_ctl[i] = 1;
#ifdef PAGE_AUTOTEST
    /* Sends a prompt by itself once linked, acknowledges the response
     * length (proof in the bridge log), then leaves. */
    int autotest_stage = 0, autotest_frames = 0, autotest_done_at = -1;
#endif

    for (;;) {
        loop_beat++;
        STEP(1);
        if (started < 0 && RTC_SECONDS - start_tried >= 2) {
            started = (int16_t)TI_NN_StartService(SERVICE_ID, NULL, service_callback);
            if (start_tried == 0 || started >= 0)
                hist_add("", started < 0 ? "NavNet service busy; retrying..."
                                         : "Waiting for the Mac bridge...", 2);
            start_tried = RTC_SECONDS;
        }
        STEP(2);
        if (link_up != was_linked) {
            was_linked = link_up;
            hist_add("", was_linked ? "Connected to the Mac bridge."
                                    : "Mac bridge disconnected; waiting...", 2);
            if (!was_linked) waiting = 0;
        }
        if (waiting && RTC_SECONDS - waiting_since >= ANSWER_TIMEOUT_SECONDS) {
            waiting = 0;
            awaited_id = 0;
            hist_add("! ", "no answer from the Mac; try again", 2);
        }
        STEP(3);
        if (in_ready) {
            int was_error = in_error;
            hist_add(was_error ? "! " : "", in_text, was_error ? 2 : 0);
            in_ready = 0;
            waiting = 0;
#ifdef PAGE_AUTOTEST
            if (autotest_stage == 1 && !was_error) {
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

        STEP(4);
        int stay = poll_keys();
        STEP(5);
#ifdef PAGE_AUTOTEST
        ++autotest_frames;
        if (autotest_stage == 0 && link_up) {
            static const char hello[] = "autotest hello from the page";
            submit(hello, sizeof(hello) - 1);
            autotest_stage = 1;
        }
        if (autotest_frames > 90 * 33) stay = 0;
        if (autotest_done_at >= 0 && autotest_frames - autotest_done_at > 60) stay = 0;
#endif
        if (!stay) break;
        if (dirty) {
            dirty = 0;
            STEP(6);
            render();
        }
        /* Copy every frame: the OS still repaints the LCD now and then (once
         * at open, at least), and a page that only presents on change stays
         * hidden after that. */
        STEP(7);
        present();
        if (scan[0] && LCD_BASE != (uint32_t)(uintptr_t)scan[scan_front])
            LCD_BASE = (uint32_t)(uintptr_t)scan[scan_front]; /* OS took it back */
        LCD_CURSOR_CLIP = 0x3F3Fu;
        LCD_CURSOR_XY = 0x03FF03FFu;
        STEP(8);
        TCC_TASK_SLEEP(TICKS_PER_FRAME);
    }
    STEP(9);

    if (scan[0]) LCD_BASE = (uint32_t)(uintptr_t)fb; /* give the LCD back */
    LCD_CURSOR_CLIP = saved_cursor_clip;
    LCD_CURSOR_XY = saved_cursor_xy;
    int session_gone = end_session(started);
    wait_keys_released(); /* do not leave Esc for the OS browser */
#ifndef PAGE_KEEP_KEYPAD_IRQ
    if (keypad_irq_was_enabled) IRQ_ENABLE = IRQ_KEYPAD;
#endif
    TCT_Local_Control_Interrupts(saved_irq);
    if (!session_gone) {
        /* A callback is still inside this image: keep the image alive rather
         * than free code that is running.  Leaks once, never crashes. */
        main_log("session still active at exit; staying resident", 0);
        nl_set_resident();
        return 0;
    }
    free(back);
    free(scan_raw);
    return 0;
}
