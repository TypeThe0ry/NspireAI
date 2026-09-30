/* NspireAI chat page for TI-Nspire CX II CAS 6.2.0.333 (Ndless OS id 46).
 *
 * The calculator is a thin terminal: the bridge host renders answers
 * (Markdown, LaTeX, CJK) and menus into grayscale images and keeps the
 * sessions; this page shows them, edits one input line and sends requests.
 *
 * Architecture (each piece verified on the handheld, see docs/test-status.md
 * 2026-09-28/29):
 *
 * - The page runs in main(), in the OS UI task.  main() re-enables IRQs
 *   (the Ndless loader masks them) and paces itself with Nucleus
 *   TCC_Task_Sleep, which really blocks the UI task.  USB and NavNet keep
 *   running meanwhile, while the OS document browser is frozen: it neither
 *   handles keys nor finishes "opening" the document.
 * - The calculator registers a NavNet service and the bridge host connects
 *   to it (host-as-client) and sends a keepalive.  The service callback is
 *   the connection: the session runs inside it and exchanges whole messages
 *   with the page loop through the rx buffer and the tx slots below.
 * - The OS keeps repainting its own LCD buffer, so the page scans out of
 *   its own double-buffered SDRAM framebuffer in the panel's portrait order
 *   and hides the hardware cursor.
 * - Nothing stays resident, and the page never frees memory a callback
 *   could still be using.
 */
#include <os.h>
#include <libndls.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include "charmap.h" /* nspire-io 6x8 font, from the Ndless SDK */

#ifndef SERVICE_ID
#define SERVICE_ID 0x5001
#endif
#define PROTOCOL_VERSION 2
#define HEADER_SIZE 16
#define MAX_FRAME_PAYLOAD 224
#define FRAGMENT_HEADER_SIZE 10
#define OP_PING 1
#define OP_PONG 2
#define OP_REQUEST 3
#define OP_RESPONSE 4
#define OP_ERROR 5
#define OP_FRAGMENT 8
#define OP_HELLO 9     /* page -> host: capabilities, what the page already has */
#define OP_BLOCK 10    /* host -> page: rendered image block */
#define OP_SCREEN 11   /* host -> page: overlay screen (menu) with a key table */
#define OP_ACTION 12   /* page -> host: action string chosen on a screen */
#define OP_STATE 13    /* host -> page: "k=v;k=v" status for the title bar */
#define OP_DUMP_REQ 14 /* host -> page: send the framebuffer */
#define OP_DUMP 15     /* page -> host: framebuffer, RLE RGB565 */
#define OP_INJECT 16   /* host -> page: key events, for unattended tests */
#define OP_CLEAR 17    /* host -> page: drop all history blocks */
#define OP_PREVIEW_REQ 18 /* page -> host: input text to typeset (id = revision) */
#define OP_PREVIEW 19  /* host -> page: typeset input (BLOCK layout) or empty */
#define OP_IME_REQ 20  /* page -> host: u8 candidate page, then pinyin letters */
#define OP_IME 21      /* host -> page: candidates and their bar image */
#define OP_IME_PICK 22 /* page -> host: "letters<TAB>text<TAB>letters left" (learning) */
#define OP_BYE 23      /* page -> host: the page is closing; send nothing for a while */

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
 * re-enables it on every animation tick, so the page clips it away and
 * parks it off screen instead of toggling the enable bit (which flickers). */
#define LCD_CURSOR_XY (*(volatile uint32_t *)0xC0000C10u)   /* x bits 0-9, y bits 16-25 */
#define LCD_CURSOR_CLIP (*(volatile uint32_t *)0xC0000C14u) /* clip x bits 0-5, y bits 8-13 */
#define RTC_SECONDS (*(volatile uint32_t *)0x90090000u) /* as Ndless gettimeofday */

/* Session liveness (seconds).  TI_NN_Read only ever reports -257 when the
 * host really closed the channel; a host that died without closing leaves a
 * half-open channel, so the page pings when idle and gives the session up
 * when nothing has come back for a while. */
#define IDLE_PING_SECONDS 5
#define SESSION_DEAD_SECONDS 20
#define START_DELAY_FRAMES 50 /* ~1.5 s before the NavNet service is registered */
#define ANSWER_TIMEOUT_SECONDS 240 /* > the bridge's API timeout, thinking included */

/* ---- display ---- */
#define W 320
#define H 240
#define CW 6
#define CH 9
#define COLS (W / CW) /* 53 */
#define TITLE_H 13
#define VIEW_TOP 14
#define INPUT_TOP 204
#define LEGEND_ROWS 4
#define SCROLL_STEP 60

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

static uint16_t *fb;   /* the OS's LCD buffer (LCD_BASE at start) */
static uint16_t *back; /* off-screen 320x240 landscape render target */
static int hww;        /* 240x320 panel: pixel (x,y) at x*240+y */
/* Private scan-out buffers in the panel's native portrait order (measured
 * with src/probes/scanout: pixel (x,y) at x*240 + 239-y), flipped by
 * writing the LCD base register, which the PL111 latches per frame. */
static uint16_t *scan[2];
static int scan_front;

/* ------------------------------------------------------------------ */
/* State shared between the page loop and the NavNet callback          */
/* ------------------------------------------------------------------ */

static volatile int link_up;
static volatile int quitting;
static volatile int session_active;
static volatile uint32_t session_gen; /* newest callback wins */
static volatile nn_ch_t active_ch;    /* lets the page end a session on exit */
/* RTC second of the last frame received; read and written by the callback
 * task only. */
static volatile uint32_t session_last_rx;
/* Callbacks currently executing code in this image.  main() must not return
 * (freeing the image) while this is non-zero. */
static volatile int callbacks_running;
/* Telemetry: the page loop bumps loop_beat every frame and records the step
 * it is about to run; the callback reports both in its PONG payload. */
static volatile uint32_t loop_beat;
static volatile int loop_step;
#define STEP(n) (loop_step = (n))
static uint16_t conversation_id = 1; /* RTC-seeded per launch */
/* Largest frame payload the page accepts.  (TI_NN_GetConnMaxPktSize is not
 * in the 6.2.0.333 syscall map; calling it crashed the NavNet task.) */
static const uint32_t max_packet = MAX_FRAME_PAYLOAD;

/* Host -> page: one complete message at a time.  The callback reassembles
 * into rx_buf, sets rx_ready and waits until the page loop has consumed it,
 * so the page never sees a buffer that is being written. */
#define RX_CAP (96u * 1024u)
static uint8_t *rx_buf;
/* Frame buffer of the (single) active session; statics count against the
 * loader's image size limit, so the large buffers come from the heap. */
#define RX_FRAME_CAP (HEADER_SIZE + 2048u)
static uint8_t *rx_frame;
static volatile uint32_t rx_len, rx_id;
static volatile int rx_opcode;
static volatile int rx_ready;

/* Page -> host: one slot per kind of message.  The page fills a free slot
 * and sets pending; the callback sends it (fragmented when long). */
/* Sent in this order: a pick goes out before the request for what remains. */
enum { TX_REQUEST, TX_ACTION, TX_DUMP, TX_PREVIEW, TX_PICK, TX_IME, TX_SLOTS };
/* The callback task outranks the page loop and reads a slot as soon as
 * `pending` is set: the other fields must be written first. */
#define PUBLISH_BARRIER() __asm volatile("" ::: "memory")
struct tx_slot {
    volatile int pending;
    int opcode;
    uint32_t id;
    uint32_t len;
    const uint8_t *data;
};
static struct tx_slot tx[TX_SLOTS];

/* What the page already has, reported in HELLO so the host only resends
 * history and menus when needed. */
static volatile int have_blocks;
static volatile int have_session;
static volatile int have_menu_version;

static void put_u32(unsigned char *p, uint32_t v) {
    p[0] = v >> 24; p[1] = v >> 16; p[2] = v >> 8; p[3] = v;
}

static uint32_t get_u32(const unsigned char *p) {
    return ((uint32_t)p[0] << 24) | ((uint32_t)p[1] << 16) |
           ((uint32_t)p[2] << 8) | p[3];
}

static uint32_t get_u16(const unsigned char *p) {
    return ((uint32_t)p[0] << 8) | p[1];
}

static int fmt_uint(char *out, uint32_t v) {
    char d[12];
    int k = 0, n = 0;
    do { d[k++] = '0' + v % 10; v /= 10; } while (v);
    while (k) out[n++] = d[--k];
    out[n] = '\0';
    return n;
}

static int fmt_kv(char *out, const char *key, uint32_t v) {
    int n = 0;
    while (*key) out[n++] = *key++;
    n += fmt_uint(out + n, v);
    return n;
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

/* Sends one message, as OP_FRAGMENT frames when it exceeds a frame. */
static int send_message(nn_ch_t ch, int opcode, uint32_t id,
                        const uint8_t *data, uint32_t len) {
    if (len <= MAX_FRAME_PAYLOAD) return send_frame(ch, opcode, id, data, len);
    unsigned char part[MAX_FRAME_PAYLOAD];
    uint32_t chunk_max = MAX_FRAME_PAYLOAD - FRAGMENT_HEADER_SIZE;
    for (uint32_t offset = 0; offset < len;) {
        uint32_t chunk = len - offset < chunk_max ? len - offset : chunk_max;
        part[0] = (unsigned char)opcode;
        part[1] = 0;
        put_u32(part + 2, len);
        put_u32(part + 6, offset);
        memcpy(part + FRAGMENT_HEADER_SIZE, data + offset, chunk);
        int w = send_frame(ch, OP_FRAGMENT, id, part, FRAGMENT_HEADER_SIZE + chunk);
        if (w < 0) return w;
        offset += chunk;
    }
    return 1;
}

static int session_current(uint32_t gen) {
    return !quitting && gen == session_gen;
}

/* Blocks until the page loop has consumed the previous message. */
static int rx_wait_free(uint32_t gen) {
    while (rx_ready) {
        if (!session_current(gen)) return 0;
        TCC_TASK_SLEEP(1);
    }
    return 1;
}

static void rx_post(int opcode, uint32_t id, uint32_t len) {
    rx_opcode = opcode;
    rx_id = id;
    rx_len = len;
    rx_ready = 1;
}

static volatile int start_attempts, start_result;

static int send_hello(nn_ch_t ch) {
    char hello[128];
    int n = 0;
    n += fmt_kv(hello + n, "v=", PROTOCOL_VERSION);
    n += fmt_kv(hello + n, ";w=", W);
    n += fmt_kv(hello + n, ";h=", H);
    n += fmt_kv(hello + n, ";bpp=", 4);
    n += fmt_kv(hello + n, ";max=", max_packet);
    n += fmt_kv(hello + n, ";n=", (uint32_t)have_blocks);
    n += fmt_kv(hello + n, ";sid=", (uint32_t)have_session);
    n += fmt_kv(hello + n, ";mv=", (uint32_t)have_menu_version);
    /* How the service registration went: attempts, last result (+1000). */
    n += fmt_kv(hello + n, ";st=", (uint32_t)start_attempts);
    n += fmt_kv(hello + n, ";sr=", (uint32_t)(start_result + 1000));
    return send_frame(ch, OP_HELLO, 1, hello, (uint32_t)n);
}

static void service_session(nn_ch_t ch) {
    unsigned char *frame = rx_frame;
    uint32_t frag_total = 0, frag_next = 0, frag_id = 0;
    int frag_opcode = 0, frag_skip = 0;

    if (quitting) return;
    /* Newest connection wins: a new callback means the host reconnected,
     * so any older session is stale.  Ask it to leave, then take over. */
    uint32_t gen = ++session_gen;
    /* Only wait: closing a channel that another task is reading froze the
     * handheld.  When the host goes away its side closes and the old
     * session's read fails by itself. */
    for (int i = 0; i < 100 && session_active; ++i) TCC_TASK_SLEEP(5);
    if (session_active || gen != session_gen) return;
    session_active = 1;
    active_ch = ch;
    session_last_rx = RTC_SECONDS;
    link_up = 1;
    uint32_t last_ping = session_last_rx;
    int frag_open = 0;
    uint32_t beat_seen = loop_beat, pong_beat = loop_beat - 1;
    int frames_since_beat = 0;
    if (send_hello(ch) < 0) goto done;

    while (session_current(gen)) {
        for (int i = 0; i < TX_SLOTS; ++i) {
            if (!tx[i].pending) continue;
            int w = send_message(ch, tx[i].opcode, tx[i].id, tx[i].data, tx[i].len);
            tx[i].pending = 0;
            if (w < 0) goto done;
        }
        uint32_t now = RTC_SECONDS;
        if (now - session_last_rx >= SESSION_DEAD_SECONDS) break;
        if (now - session_last_rx >= IDLE_PING_SECONDS && now - last_ping >= IDLE_PING_SECONDS) {
            if (send_frame(ch, OP_PING, 1, "PING", 4) < 0) break;
            last_ping = now;
        }
        uint32_t received = 0;
        /* TI_NN_Read blocks until a frame arrives whatever timeout is
         * passed; the host's keepalive is what paces this loop. */
        int status = (int16_t)TI_NN_Read(ch, 1, frame, RX_FRAME_CAP,
                                         (uint32_t)&received);
        /* This task outranks the page loop: give the loop a turn whenever it
         * has not run for a while, so the screen and Esc never starve. */
        if (loop_beat == beat_seen) {
            if (++frames_since_beat >= 16) {
                TCC_TASK_SLEEP(1);
                frames_since_beat = 0;
            }
        } else {
            beat_seen = loop_beat;
            frames_since_beat = 0;
        }
        if (status < 0) {
            if (status == -257) break;
            TCC_TASK_SLEEP(2); /* never spin in the OS NavNet context */
            continue;
        }
        if (received < HEADER_SIZE || memcmp(frame, "NSAI", 4) != 0 ||
            frame[4] != 1) {
            TCC_TASK_SLEEP(1); /* an empty or foreign read: do not spin */
            continue;
        }
        session_last_rx = RTC_SECONDS;
        int opcode = frame[5];
        uint32_t request = get_u32(frame + 6);
        uint32_t length = get_u32(frame + 12);
        const unsigned char *payload = frame + HEADER_SIZE;
        if (length > received - HEADER_SIZE) length = received - HEADER_SIZE;

        if (opcode == OP_PING) {
            /* At most one answer per page frame: a burst of pings that
             * queued up before the page opened needs no answer each. */
            if (loop_beat == pong_beat) continue;
            pong_beat = loop_beat;
            char pong[48] = "PONG b=";
            int n = 7;
            n += fmt_uint(pong + n, loop_beat);
            n += fmt_kv(pong + n, " s=", (uint32_t)loop_step);
            if (send_frame(ch, OP_PONG, request, pong, (uint32_t)n) < 0) break;
        } else if (opcode == OP_PONG) {
            /* liveness only */
        } else if (opcode == OP_FRAGMENT) {
            if (length < FRAGMENT_HEADER_SIZE) continue;
            int orig = payload[0];
            uint32_t total = get_u32(payload + 2);
            uint32_t offset = get_u32(payload + 6);
            uint32_t chunk = length - FRAGMENT_HEADER_SIZE;
            if (offset == 0) {
                if (!rx_wait_free(gen)) break;
                frag_total = total;
                frag_next = 0;
                frag_id = request;
                frag_opcode = orig;
                frag_skip = total > RX_CAP; /* too large: drop it whole */
                frag_open = 1;
            }
            if (!frag_open || request != frag_id || offset != frag_next ||
                orig != frag_opcode)
                continue;
            if (!frag_skip && frag_next + chunk <= RX_CAP)
                memcpy(rx_buf + frag_next, payload + FRAGMENT_HEADER_SIZE, chunk);
            frag_next += chunk;
            if (frag_next >= frag_total) {
                if (!frag_skip) rx_post(frag_opcode, frag_id, frag_total);
                frag_total = frag_next = 0;
                frag_open = 0;
            }
        } else {
            /* A whole message in one frame.  It reuses rx_buf, so a
             * fragmented message that was still arriving is given up (the
             * host sends messages one at a time; this is only a guard). */
            frag_open = 0;
            if (!rx_wait_free(gen)) break;
            memcpy(rx_buf, payload, length);
            rx_post(opcode, request, length);
        }
    }
done:
    /* Closing: tell the host to go quiet.  A packet that arrives while this
     * image unregisters its service and is freed, or while the next copy
     * loads, can be dispatched to a callback that no longer exists; the
     * host's keepalive did exactly that and rebooted the handheld. */
    if (quitting) send_frame(ch, OP_BYE, 0, "BYE", 3);
    /* Returning closes this channel.  Unsent messages must not be replayed
     * by the next session. */
    for (int i = 0; i < TX_SLOTS; ++i) tx[i].pending = 0;
    /* Always: a session that takes over sets link_up again itself. */
    link_up = 0;
    active_ch = NULL;
    session_active = 0;
}

static void service_callback(nn_ch_t ch, void *data) {
    (void)data;
    /* The counter guards the image against being freed under a callback, so
     * its updates must not be lost to a concurrent callback. */
    int mask = TCT_Local_Control_Interrupts(-1);
    int others = callbacks_running++;
    TCT_Local_Control_Interrupts(mask);
    /* One session and at most one waiting to take over: connections that
     * piled up while no page was listening must not all queue up here. */
    if (others < 2) service_session(ch);
    mask = TCT_Local_Control_Interrupts(-1);
    callbacks_running--;
    TCT_Local_Control_Interrupts(mask);
}

/* ------------------------------------------------------------------ */
/* History blocks                                                      */
/* ------------------------------------------------------------------ */

enum { KIND_AI, KIND_USER, KIND_INFO, KIND_COUNT };
struct block {
    uint8_t kind;  /* KIND_* */
    uint8_t bpp;   /* 0 = local text line (data is a C string), else image */
    uint8_t gap;   /* blank pixels above */
    uint16_t w, h;
    uint32_t id;
    uint32_t bytes;
    uint8_t *data;
};
#define MAX_BLOCKS 320
#define BLOCK_BUDGET (3u * 1024u * 1024u)
static struct block *blocks; /* MAX_BLOCKS entries, allocated in main() */
static int nblocks;
static uint32_t block_bytes;
static int scroll_px; /* 0 = newest visible */
/* The first block of the newest answer: its top stays at the top of the
 * view, so a long answer is read from its beginning.  -1 = follow the end. */
static int anchor = -1;
static int dirty = 1;
static uint16_t ink_lut[KIND_COUNT][16];

static void blocks_drop_first(int count) {
    if (count > nblocks) count = nblocks;
    for (int i = 0; i < count; ++i) {
        block_bytes -= blocks[i].bytes;
        free(blocks[i].data);
    }
    memmove(blocks, blocks + count, sizeof(blocks[0]) * (size_t)(nblocks - count));
    nblocks -= count;
    have_blocks = nblocks;
    anchor = anchor >= count ? anchor - count : -1;
}

static void blocks_clear(void) {
    blocks_drop_first(nblocks);
    scroll_px = 0;
    dirty = 1;
}

static void blocks_remove_echo(uint32_t id) {
    int out = 0;
    for (int i = 0; i < nblocks; ++i) {
        if (blocks[i].bpp == 0 && blocks[i].kind == KIND_USER && blocks[i].id == id) {
            block_bytes -= blocks[i].bytes;
            free(blocks[i].data);
        } else {
            blocks[out++] = blocks[i];
        }
    }
    nblocks = out;
}

/* Takes ownership of `data`. */
static void blocks_add(int kind, int bpp, int gap, int w, int h, uint32_t id,
                       uint8_t *data, uint32_t bytes) {
    while (nblocks >= MAX_BLOCKS || (nblocks > 0 && block_bytes + bytes > BLOCK_BUDGET))
        blocks_drop_first(1);
    struct block *b = &blocks[nblocks++];
    b->kind = (uint8_t)kind;
    b->bpp = (uint8_t)bpp;
    b->gap = (uint8_t)gap;
    b->w = (uint16_t)w;
    b->h = (uint16_t)h;
    b->id = id;
    b->bytes = bytes;
    b->data = data;
    block_bytes += bytes;
    have_blocks = nblocks;
    scroll_px = 0;
    if (kind == KIND_USER) anchor = -1; /* a new question: follow the end */
    dirty = 1;
}

static void text_add_line(const char *text, int len, int kind, uint32_t id, int gap) {
    char *copy = malloc((size_t)len + 1);
    if (!copy) return;
    memcpy(copy, text, (size_t)len);
    copy[len] = '\0';
    blocks_add(kind, 0, gap, W, CH, id, (uint8_t *)copy, (uint32_t)len + 1);
}

/* Word-wraps ASCII `text` into local text lines (prefix on the first). */
static void text_add(const char *prefix, const char *text, int kind, uint32_t id) {
    char line[COLS + 1];
    int used = 0, gap = 3;
    for (const char *p = prefix; *p && used < COLS; ++p) line[used++] = *p;
    const char *s = text;
    while (*s) {
        if (*s == '\n') {
            text_add_line(line, used, kind, id, gap);
            gap = 0;
            used = 0;
            ++s;
            continue;
        }
        if (*s == '\r') { ++s; continue; }
        int wlen = 0;
        while (s[wlen] && s[wlen] != ' ' && s[wlen] != '\n') ++wlen;
        if (wlen == 0) { /* space */
            if (used < COLS && used > 0) line[used++] = ' ';
            ++s;
            continue;
        }
        if (used + wlen > COLS && used > 0) {
            text_add_line(line, used, kind, id, gap);
            gap = 0;
            used = 0;
        }
        while (wlen > 0) {
            int take = wlen < COLS - used ? wlen : COLS - used;
            for (int i = 0; i < take; ++i) {
                unsigned char c = (unsigned char)s[i];
                if ((c & 0xC0) == 0x80) continue; /* rest of a UTF-8 character */
                line[used++] = (c >= 32 && c < 127) ? (char)c : '?';
            }
            s += take;
            wlen -= take;
            if (wlen > 0) {
                text_add_line(line, used, kind, id, gap);
                gap = 0;
                used = 0;
            }
        }
    }
    if (used > 0 || !*text) text_add_line(line, used, kind, id, gap);
}

static void info(const char *text) {
    text_add("", text, KIND_INFO, 0);
}

static int packbits_decode(const uint8_t *src, uint32_t n, uint8_t *dst, uint32_t want) {
    uint32_t i = 0, o = 0;
    while (i < n && o < want) {
        uint8_t c = src[i++];
        if (c < 128) {
            uint32_t k = (uint32_t)c + 1;
            if (i + k > n || o + k > want) return 0;
            memcpy(dst + o, src + i, k);
            i += k;
            o += k;
        } else if (c > 128) {
            uint32_t k = 257u - c;
            if (i >= n || o + k > want) return 0;
            memset(dst + o, src[i++], k);
            o += k;
        }
    }
    return o == want;
}

/* Decodes the image part shared by BLOCK and SCREEN payloads.  Returns a
 * malloc'd buffer of packed rows, or NULL. */
static uint8_t *image_decode(int bpp, int w, int h, int encoding,
                             const uint8_t *data, uint32_t len, uint32_t *bytes) {
    if ((bpp != 1 && bpp != 2 && bpp != 4) || w < 1 || w > W || h < 1) return NULL;
    uint32_t row = ((uint32_t)w * (uint32_t)bpp + 7u) / 8u;
    uint32_t want = row * (uint32_t)h;
    if (want > BLOCK_BUDGET / 2) return NULL;
    uint8_t *out = malloc(want);
    if (!out) return NULL;
    int ok;
    if (encoding == 0) {
        ok = len == want;
        if (ok) memcpy(out, data, want);
    } else {
        ok = encoding == 1 && packbits_decode(data, len, out, want);
    }
    if (!ok) {
        free(out);
        return NULL;
    }
    *bytes = want;
    return out;
}

/* ------------------------------------------------------------------ */
/* Overlay screens (menus), defined by the host                        */
/* ------------------------------------------------------------------ */

enum { ACT_CLOSE, ACT_GOTO, ACT_INSERT, ACT_COMMAND, ACT_SEND, ACT_SEND_STAY };
struct screen {
    uint8_t id, bpp, nkeys;
    uint16_t w, h;
    uint8_t *image;
    uint8_t *keys; /* nkeys entries: key, action, arg_len, arg bytes */
    uint32_t keys_len;
};
#define MAX_SCREENS 40
static struct screen screens[MAX_SCREENS];
static int nscreens;
static int overlay; /* screen id shown, 0 = none */

static struct screen *screen_find(int id) {
    for (int i = 0; i < nscreens; ++i)
        if (screens[i].id == id) return &screens[i];
    return NULL;
}

static void screens_clear(void) {
    for (int i = 0; i < nscreens; ++i) {
        free(screens[i].image);
        free(screens[i].keys);
    }
    nscreens = 0;
    overlay = 0;
}

static void screen_store(const uint8_t *p, uint32_t len) {
    if (len < 10) return;
    int id = p[0], flags = p[1], nkeys = p[2], bpp = p[3];
    int w = (int)get_u16(p + 4), h = (int)get_u16(p + 6), encoding = p[8];
    uint32_t pos = 10;
    for (int i = 0; i < nkeys; ++i) {
        if (pos + 3 > len) return;
        pos += 3u + p[pos + 2];
        if (pos > len) return;
    }
    uint32_t keys_len = pos - 10, bytes = 0;
    if (id == 0 || h > H - VIEW_TOP) return;
    uint8_t *image = image_decode(bpp, w, h, encoding, p + pos, len - pos, &bytes);
    uint8_t *keys = malloc(keys_len ? keys_len : 1);
    if (!image || !keys) {
        free(image);
        free(keys);
        return;
    }
    memcpy(keys, p + 10, keys_len);
    struct screen *s = screen_find(id);
    if (s) {
        free(s->image);
        free(s->keys);
    } else if (nscreens < MAX_SCREENS) {
        s = &screens[nscreens++];
    } else {
        free(image);
        free(keys);
        return;
    }
    s->id = (uint8_t)id;
    s->bpp = (uint8_t)bpp;
    s->nkeys = (uint8_t)nkeys;
    s->w = (uint16_t)w;
    s->h = (uint16_t)h;
    s->image = image;
    s->keys = keys;
    s->keys_len = keys_len;
    if (flags & 1) overlay = id;
    dirty = 1;
}

/* ------------------------------------------------------------------ */
/* Input line and page state                                           */
/* ------------------------------------------------------------------ */

#define INPUT_CAP 900
static char input[INPUT_CAP + 1];
static int input_len;
static int cursor; /* insertion point, 0..input_len */
/* Live preview: the host typesets the input (LaTeX, CJK) while the user
 * types.  input_rev counts edits; a preview is only shown for the revision
 * it was rendered from. */
#define PREVIEW_MAX_H 72
static uint32_t input_rev, preview_sent_rev, edit_beat;
static struct {
    uint8_t bpp;
    uint16_t w, h;
    uint8_t *data;
} preview;
static uint8_t preview_req[INPUT_CAP];

/* Pinyin input.  The input buffer holds UTF-8; the letters being composed
 * live in comp[] until the user picks a candidate.  The host owns the
 * dictionary: it answers OP_IME_REQ with the candidates (text and how many
 * letters each consumes) and an image of the candidate bar. */
#define COMP_CAP 40
#define IME_MAX_CAND 9
#define IME_TEXT_CAP 48
static int ime_on;
static char comp[COMP_CAP + 1];
static int comp_len;
static uint32_t ime_rev, ime_sent_rev;
static int ime_page;
static struct {
    uint8_t consumed, len;
    char text[IME_TEXT_CAP];
} cand[IME_MAX_CAND];
static int ncand;
static int ime_flags;      /* bit0: previous page exists, bit1: next page */
static int pending_pick = -1; /* choice made before the candidates arrived */
static struct {
    uint8_t bpp;
    uint16_t w, h;
    uint8_t *data;
} ime_bar;
static uint8_t ime_req[COMP_CAP + 2];
static uint8_t pick_buf[COMP_CAP + IME_TEXT_CAP + 3];
static int waiting;
static uint32_t waiting_since;
static uint32_t awaited_id;
static uint32_t next_id = 2;
/* Thinking effort, cycled with the Var key and sent as a "#think:<level> "
 * tag that the bridge strips (off = no reasoning). */
static const char *const think_levels[] = {"off", "low", "high", "max"};
static int think_level;
/* Keyboard: 0 = qwerty with legend, 1 = qwerty, 2 = abc (key caps). */
static int layout_mode;
#define QWERTY (layout_mode != 2)
#define LEGEND (layout_mode == 0)
/* Pending quick command chosen from a menu, sent as "#cmd:<id> ". */
static char cmd_id[33], cmd_label[14]; /* ids up to 32 chars (bridge/commands.py) */
static char session_label[24] = "";
static int session_number;
/* Touchpad arrows: up/down scroll, left/right move the input cursor.  One
 * touchpad_scan per frame ran without disturbing the OS (2026-09-29). */
static int arrows_enabled = 1;
static uint8_t request_buf[INPUT_CAP + 64];
static uint8_t action_buf[200];
static uint8_t *dump_buf;

static void preview_clear(void) {
    free(preview.data);
    preview.data = NULL;
    preview.h = 0;
    dirty = 1;
}

static void input_changed(void) {
    input_rev++;
    edit_beat = loop_beat;
    dirty = 1;
}

static int is_continuation(char c) {
    return ((unsigned char)c & 0xC0) == 0x80;
}

/* Start of the character before byte index i / after the one at i. */
static int utf8_prev(int i) {
    while (i > 0 && is_continuation(input[--i])) { }
    return i;
}

static int utf8_next(int i) {
    if (i < input_len) ++i;
    while (i < input_len && is_continuation(input[i])) ++i;
    return i;
}

static void input_insert(const char *text, int n) {
    if (n <= 0 || input_len + n > INPUT_CAP) return;
    memmove(input + cursor + n, input + cursor, (size_t)(input_len - cursor));
    memcpy(input + cursor, text, (size_t)n);
    cursor += n;
    input_len += n;
    input[input_len] = '\0';
    input_changed();
}

static void input_append(char c) {
    input_insert(&c, 1);
}

static void input_append_str(const char *s) {
    input_insert(s, (int)strlen(s));
}

static void ime_bar_clear(void) {
    free(ime_bar.data);
    ime_bar.data = NULL;
    ime_bar.h = 0;
    ncand = 0;
    ime_flags = 0;
    pending_pick = -1;
    dirty = 1;
}

/* The composition changed: the candidates on screen no longer apply. */
static void ime_changed(void) {
    ime_rev++;
    ncand = 0;
    if (comp_len == 0) ime_bar_clear();
    dirty = 1;
}

/* Types the composed letters as they are (Enter, or leaving Chinese mode). */
static void ime_commit_raw(void) {
    if (comp_len == 0) return;
    input_insert(comp, comp_len);
    comp_len = 0;
    ime_page = 0;
    ime_changed();
}

static void ime_pick(int k) {
    if (comp_len == 0) return;
    if (k >= ncand) {
        if (ncand == 0 && ime_rev != 0) pending_pick = k; /* still on their way */
        return;
    }
    int used = cand[k].consumed <= comp_len ? cand[k].consumed : comp_len;
    if (!tx[TX_PICK].pending && link_up) {
        int n = 0;
        memcpy(pick_buf, comp, (size_t)used);
        n += used;
        pick_buf[n++] = '\t';
        memcpy(pick_buf + n, cand[k].text, cand[k].len);
        n += cand[k].len;
        pick_buf[n++] = '\t';
        memcpy(pick_buf + n, comp + used, (size_t)(comp_len - used));
        n += comp_len - used;
        tx[TX_PICK].opcode = OP_IME_PICK;
        tx[TX_PICK].id = ime_rev;
        tx[TX_PICK].data = pick_buf;
        tx[TX_PICK].len = (uint32_t)n;
        PUBLISH_BARRIER();
        tx[TX_PICK].pending = 1;
    }
    input_insert(cand[k].text, cand[k].len);
    memmove(comp, comp + used, (size_t)(comp_len - used));
    comp_len -= used;
    ime_page = 0;
    pending_pick = -1;
    ime_changed();
}

/* Full-width punctuation after Chinese text; ASCII elsewhere (3.5, f(x)). */
static const char *ime_punctuation(char c) {
    if (cursor == 0 || !is_continuation(input[cursor - 1])) return NULL;
    switch (c) {
    case ',': return "\xEF\xBC\x8C";
    case '.': return "\xE3\x80\x82";
    case '?': return "\xEF\xBC\x9F";
    case '!': return "\xEF\xBC\x81";
    case ':': return "\xEF\xBC\x9A";
    case ';': return "\xEF\xBC\x9B";
    }
    return NULL;
}

static void tx_post(int slot, int opcode, uint32_t id, const uint8_t *data, uint32_t len) {
    tx[slot].opcode = opcode;
    tx[slot].id = id;
    tx[slot].data = data;
    tx[slot].len = len;
    PUBLISH_BARRIER();
    tx[slot].pending = 1;
}

static void send_action(const char *action) {
    if (!link_up) {
        info("(not connected to the bridge)");
        return;
    }
    if (tx[TX_ACTION].pending) return;
    size_t n = strlen(action);
    if (n > sizeof(action_buf)) n = sizeof(action_buf);
    memcpy(action_buf, action, n);
    tx_post(TX_ACTION, OP_ACTION, next_id++, action_buf, (uint32_t)n);
}

static void submit(void) {
    if (input_len <= 0 || waiting) return;
    uint32_t id = next_id++;
    char prefix[20] = "> ";
    if (cmd_id[0]) {
        int k = 0;
        prefix[k++] = '[';
        for (const char *c = cmd_label; *c && k < 16; ++c) prefix[k++] = *c;
        prefix[k++] = ']';
        prefix[k++] = ' ';
        prefix[k] = '\0';
    }
    text_add(prefix, input, KIND_USER, id);
    if (!link_up) {
        info("(not connected to the bridge yet)");
        return;
    }
    int n = 0;
    const char *level = think_levels[think_level];
    memcpy(request_buf + n, "#think:", 7);
    n += 7;
    while (*level) request_buf[n++] = (uint8_t)*level++;
    request_buf[n++] = ' ';
    if (cmd_id[0]) {
        memcpy(request_buf + n, "#cmd:", 5);
        n += 5;
        for (const char *c = cmd_id; *c; ++c) request_buf[n++] = (uint8_t)*c;
        request_buf[n++] = ' ';
    }
    request_buf[n++] = 0x1F; /* end of tags: whatever follows is the user's text */
    memcpy(request_buf + n, input, (size_t)input_len);
    n += input_len;
    awaited_id = id;
    waiting = 1;
    waiting_since = RTC_SECONDS;
    tx_post(TX_REQUEST, OP_REQUEST, id, request_buf, (uint32_t)n);
    input_len = 0;
    cursor = 0;
    input[0] = '\0';
    cmd_id[0] = '\0';
    cmd_label[0] = '\0';
    preview_clear();
    input_changed();
}

/* ------------------------------------------------------------------ */
/* Rendering                                                           */
/* ------------------------------------------------------------------ */

static void fill_rect(int x, int y, int w, int h, uint16_t c) {
    if (x < 0) { w += x; x = 0; }
    if (y < 0) { h += y; y = 0; }
    for (int yy = y; yy < y + h && yy < H; ++yy)
        for (int xx = x; xx < x + w && xx < W; ++xx)
            back[yy * W + xx] = c;
}

static void draw_char_clip(int x, int y, unsigned char ch, uint16_t fg, int y0, int y1) {
    const char *glyph = MBCharSet8x6_definition[ch];
    for (int col = 0; col < 6; ++col) {
        unsigned bits = (unsigned char)glyph[col];
        if (x + col < 0 || x + col >= W) continue;
        for (int row = 0; row < 8; ++row)
            if ((bits & (1u << row)) && y + row >= y0 && y + row < y1)
                back[(y + row) * W + x + col] = fg;
    }
}

static void draw_text_clip(int x, int y, const char *s, uint16_t fg, int y0, int y1) {
    for (; *s && x <= W - CW; ++s, x += CW)
        draw_char_clip(x, y, (unsigned char)*s, fg, y0, y1);
}

static void draw_text(int x, int y, const char *s, uint16_t fg) {
    draw_text_clip(x, y, s, fg, 0, H);
}

/* Draws rows [r0, r1) of packed image rows at screen (sx, sy). */
static void draw_image(int sx, int sy, int w, int bpp, const uint8_t *data,
                       int r0, int r1, const uint16_t *lut) {
    uint32_t row_bytes = ((uint32_t)w * (uint32_t)bpp + 7u) / 8u;
    unsigned mask = (1u << bpp) - 1u;
    unsigned scale = bpp == 4 ? 1u : bpp == 2 ? 5u : 15u;
    for (int r = r0; r < r1; ++r) {
        int y = sy + r - r0;
        if (y < 0 || y >= H) continue;
        const uint8_t *row = data + (uint32_t)r * row_bytes;
        uint16_t *out = back + y * W + sx;
        for (int x = 0; x < w && sx + x < W; ++x) {
            unsigned bit = (unsigned)x * (unsigned)bpp;
            unsigned v = (row[bit >> 3] >> (8u - (unsigned)bpp - (bit & 7u))) & mask;
            out[x] = lut[v * scale];
        }
    }
}

/* Bottom-up layout: input box, typeset preview, key legend, history. */
static int preview_top(void) {
    return INPUT_TOP - 2 - (preview.data ? preview.h + 3 : 0);
}

static int bar_top(void) {
    return preview_top() - (ime_bar.data && comp_len ? ime_bar.h + 2 : 0);
}

static int legend_top(void) {
    return bar_top() - (LEGEND ? LEGEND_ROWS * CH + 1 : 0);
}

static int view_bottom(void) {
    return legend_top() - 1;
}

static int doc_height(void) {
    int total = 0;
    for (int i = 0; i < nblocks; ++i) total += blocks[i].gap + blocks[i].h;
    return total;
}

static void render_history(void) {
    int y0 = VIEW_TOP, y1 = view_bottom(), vh = y1 - y0;
    int doc = doc_height();
    int max_scroll = doc > vh ? doc - vh : 0;
    if (scroll_px > max_scroll) scroll_px = max_scroll;
    if (scroll_px < 0) scroll_px = 0;
    if (anchor >= 0 && anchor < nblocks && doc > vh) {
        int at = 0;
        for (int i = 0; i < anchor; ++i) at += blocks[i].gap + blocks[i].h;
        scroll_px = at < doc - vh ? doc - vh - at : 0;
    }
    int top = doc > vh ? doc - vh - scroll_px : 0;
    int y = 0;
    for (int i = 0; i < nblocks; ++i) {
        const struct block *b = &blocks[i];
        y += b->gap;
        if (y + b->h > top && y < top + vh) {
            int r0 = top > y ? top - y : 0;
            int r1 = y + b->h > top + vh ? top + vh - y : b->h;
            if (b->bpp == 0) {
                uint16_t c = b->kind == KIND_USER ? C_USER
                           : b->kind == KIND_INFO ? C_DIM : C_TEXT;
                draw_text_clip(1, y0 + y - top, (const char *)b->data, c, y0, y1);
            } else {
                draw_image(0, y0 + y + r0 - top, b->w, b->bpp, b->data, r0, r1,
                           ink_lut[b->kind < KIND_COUNT ? b->kind : KIND_AI]);
            }
        }
        y += b->h;
    }
    if (max_scroll > 0) { /* scroll indicator */
        int bar = vh * vh / doc;
        if (bar < 8) bar = 8;
        int pos = (vh - bar) * (max_scroll - scroll_px) / max_scroll;
        fill_rect(W - 2, y0 + pos, 2, bar, C_DIM);
    }
}

static void render(void) {
    fill_rect(0, 0, W, H, C_BG);
    render_history();

    fill_rect(0, 0, W, TITLE_H, C_BAR);
    {
        char left[32];
        int n = 0;
        if (session_number) {
            left[n++] = 'S';
            n += fmt_uint(left + n, (uint32_t)session_number);
            left[n++] = ' ';
        }
        for (const char *t = session_label[0] ? session_label : "NspireAI";
             *t && n < 20; ++t)
            left[n++] = *t;
        left[n] = '\0';
        draw_text(4, 3, left, C_BAR_TEXT);
        char tag[16] = "th:";
        n = 3;
        for (const char *l = think_levels[think_level]; *l; ++l) tag[n++] = *l;
        tag[n] = '\0';
        draw_text(158, 3, tag, think_level ? C_OK : C_DIM);
        draw_text(206, 3, QWERTY ? "qw" : "abc", C_BAR_TEXT);
        draw_text(230, 3, ime_on ? "zh" : "en", ime_on ? C_OK : C_DIM);
    }
    const char *state = link_up ? (waiting ? "thinking" : "linked") : "offline";
    uint16_t dot = link_up ? C_OK : C_WARN;
    fill_rect(W - 6 - CW * (int)strlen(state) - 10, 4, 6, 6, dot);
    draw_text(W - 4 - CW * (int)strlen(state), 3, state, C_BAR_TEXT);

    if (LEGEND) {
        /* Key legend, laid out like the physical alpha block. */
        static const char *const legend[LEGEND_ROWS] = {
            "q  w  e  r  t  y  u  i  o",
            "a  s  d  f  g  h  j  k  l",
            "z  x  c  v  b  n  m  p  enter",
            "   ,  ?  !  '  -  space",
        };
        int top = legend_top();
        fill_rect(0, top, W, LEGEND_ROWS * CH + 1, C_INPUT_BG);
        for (int r = 0; r < LEGEND_ROWS; ++r)
            draw_text(70, top + 1 + r * CH, legend[r], C_BAR);
    }
    if (ime_bar.data && comp_len) {
        int top = bar_top();
        fill_rect(0, top, W, ime_bar.h + 2, C_BG);
        fill_rect(0, top, W, 1, C_OK);
        draw_image(0, top + 1, ime_bar.w, ime_bar.bpp, ime_bar.data, 0, ime_bar.h,
                   ink_lut[KIND_AI]);
    }
    if (preview.data) {
        int top = preview_top();
        fill_rect(0, top, W, preview.h + 3, C_BG);
        fill_rect(0, top, W, 1, C_USER);
        draw_image(0, top + 2, preview.w, preview.bpp, preview.data, 0, preview.h,
                   ink_lut[KIND_USER]);
    }
    fill_rect(0, INPUT_TOP - 2, W, 1, C_DIM);
    fill_rect(0, INPUT_TOP, W, 2 * CH + 4, C_INPUT_BG);
    {
        /* Prompt (or the pending command), then a window of the input
         * that keeps the cursor visible.  One cell per ASCII character, two
         * per non-ASCII character (drawn as a box: the local font is ASCII;
         * the preview above shows the real glyphs).  The letters being
         * composed appear at the cursor. */
        enum { CELL_TEXT, CELL_COMP, CELL_BOX_L, CELL_BOX_R };
        char shown[2 * COLS + 1];
        uint8_t kind[2 * COLS + 1];
        int n = 0;
        if (cmd_id[0]) {
            shown[n++] = '[';
            for (const char *c = cmd_label; *c; ++c) shown[n++] = *c;
            shown[n++] = ']';
        } else {
            shown[n++] = '>';
        }
        shown[n++] = ' ';
        int prefix = n;
        for (int k = 0; k < prefix; ++k) kind[k] = CELL_TEXT;
        int cap = 2 * COLS - prefix - 1;
        int before = comp_len, total = comp_len;
        for (int b = 0; b < input_len; b = utf8_next(b)) {
            int cells = (unsigned char)input[b] < 0x80 ? 1 : 2;
            if (b < cursor) before += cells;
            total += cells;
        }
        int start = before > cap - 8 ? before - (cap - 8) : 0;
        if (total - start < cap) start = total > cap ? total - cap : 0;
        int cursor_at = -1, pos = 0;
        for (int b = 0; b <= input_len && n < 2 * COLS; b = b < input_len ? utf8_next(b) : b + 1) {
            if (b == cursor) {
                for (int k = 0; k < comp_len && n < 2 * COLS; ++k, ++pos)
                    if (pos >= start) {
                        shown[n] = comp[k];
                        kind[n++] = CELL_COMP;
                    }
                if (pos >= start && n <= 2 * COLS) cursor_at = n;
            }
            if (b >= input_len) break;
            if ((unsigned char)input[b] < 0x80) {
                if (pos >= start) {
                    shown[n] = input[b];
                    kind[n++] = CELL_TEXT;
                }
                ++pos;
            } else {
                for (int half = 0; half < 2 && n < 2 * COLS; ++half, ++pos)
                    if (pos >= start) {
                        shown[n] = ' ';
                        kind[n++] = half ? CELL_BOX_R : CELL_BOX_L;
                    }
            }
        }
        shown[n] = '\0';
        for (int k = 0; k < n; ++k) {
            int cx = 1 + (k % COLS) * CW, cy = INPUT_TOP + 2 + (k / COLS) * CH;
            uint16_t colour = k < prefix && cmd_id[0] ? C_USER
                            : kind[k] == CELL_COMP ? C_USER : C_TEXT;
            if (kind[k] == CELL_BOX_L) {
                fill_rect(cx + 1, cy, 2 * CW - 3, 1, C_DIM);
                fill_rect(cx + 1, cy + 7, 2 * CW - 3, 1, C_DIM);
                fill_rect(cx + 1, cy, 1, 8, C_DIM);
                fill_rect(cx + 2 * CW - 3, cy, 1, 8, C_DIM);
            } else if (kind[k] != CELL_BOX_R) {
                draw_char_clip(cx, cy, (unsigned char)shown[k], colour, 0, H);
            }
            if (kind[k] == CELL_COMP) fill_rect(cx, cy + 8, CW, 1, C_OK);
        }
        if (cursor_at >= 0 && cursor_at < 2 * COLS) { /* underline cursor */
            int cx = 1 + (cursor_at % COLS) * CW;
            int cy = INPUT_TOP + 2 + (cursor_at / COLS) * CH + 8;
            fill_rect(cx, cy, CW, 1, C_USER);
        }
    }
    draw_text(1, H - CH, "menu cmds  cat chats  var think  doc kbd  ctrl+spc zh", C_DIM);

    if (overlay) {
        const struct screen *s = screen_find(overlay);
        if (!s) {
            overlay = 0;
        } else {
            int x = (W - s->w) / 2, y = VIEW_TOP + 1;
            fill_rect(x - 2, y - 1, s->w + 4, s->h + 3, C_BAR);
            draw_image(x, y, s->w, s->bpp, s->image, 0, s->h, ink_lut[KIND_AI]);
        }
    }
}

/* The LCD DMA reads RAM, not the data cache.  libndls clear_cache() also
 * invalidates the whole D-cache, which from a preemptible task can throw
 * away another task's writes; cleaning (write back) is all the LCD needs. */
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

/* Framebuffer dump for the host: u16 width, u16 height, u8 format (1),
 * then runs of { u8 count (1..255), u16 RGB565 big-endian }. */
static uint32_t dump_encode(uint8_t *out) {
    uint32_t n = 0;
    out[n++] = W >> 8; out[n++] = W & 0xFF;
    out[n++] = H >> 8; out[n++] = H & 0xFF;
    out[n++] = 1;
    const uint16_t *p = back, *end = back + W * H;
    while (p < end) {
        uint16_t v = *p;
        unsigned run = 1;
        while (p + run < end && run < 255 && p[run] == v) ++run;
        out[n++] = (uint8_t)run;
        out[n++] = (uint8_t)(v >> 8);
        out[n++] = (uint8_t)v;
        p += run;
    }
    return n;
}

/* ------------------------------------------------------------------ */
/* Keys                                                                */
/* ------------------------------------------------------------------ */

enum {
    EV_ENTER = 0x100, EV_DEL, EV_ESC, EV_UP, EV_DOWN, EV_THINK, EV_LAYOUT,
    EV_MENU, EV_CHATS, EV_ARROWS, EV_LEFT, EV_RIGHT, EV_IME,
};

/* Two layouts.  "abc" is what the key caps say.  "qwerty" (enhanced typing)
 * maps the alpha block by physical position, 9 keys per row:
 *   EE A B C D E F G ?!   ->  q w e r t y u i o
 *   pi H I J K L M N flag ->  a s d f g h j k l
 *   ,  O P Q R S T U      ->  z x c v b n m p      (return stays Enter)
 *      V W X Y Z          ->  , ? ! ' -
 * `ctrl` is the character typed with Ctrl held (LaTeX and other symbols);
 * a 0 means the key types nothing in that layout. */
struct keymap { const t_key *key; char lower, upper, qlower, qupper, ctrl; };
static const struct keymap chars[] = {
    {&KEY_NSPIRE_EE, 0, 0, 'q', 'Q', 0},
    {&KEY_NSPIRE_A, 'a', 'A', 'w', 'W', 0}, {&KEY_NSPIRE_B, 'b', 'B', 'e', 'E', 0},
    {&KEY_NSPIRE_C, 'c', 'C', 'r', 'R', 0}, {&KEY_NSPIRE_D, 'd', 'D', 't', 'T', 0},
    {&KEY_NSPIRE_E, 'e', 'E', 'y', 'Y', 0}, {&KEY_NSPIRE_F, 'f', 'F', 'u', 'U', 0},
    {&KEY_NSPIRE_G, 'g', 'G', 'i', 'I', 0}, {&KEY_NSPIRE_QUESEXCL, '?', '!', 'o', 'O', 0},
    {&KEY_NSPIRE_PI, 0, 0, 'a', 'A', 0},
    {&KEY_NSPIRE_H, 'h', 'H', 's', 'S', 0}, {&KEY_NSPIRE_I, 'i', 'I', 'd', 'D', 0},
    {&KEY_NSPIRE_J, 'j', 'J', 'f', 'F', 0}, {&KEY_NSPIRE_K, 'k', 'K', 'g', 'G', 0},
    {&KEY_NSPIRE_L, 'l', 'L', 'h', 'H', 0}, {&KEY_NSPIRE_M, 'm', 'M', 'j', 'J', 0},
    {&KEY_NSPIRE_N, 'n', 'N', 'k', 'K', 0}, {&KEY_NSPIRE_FLAG, 0, 0, 'l', 'L', 0},
    {&KEY_NSPIRE_COMMA, ',', ',', 'z', 'Z', 0},
    {&KEY_NSPIRE_O, 'o', 'O', 'x', 'X', 0}, {&KEY_NSPIRE_P, 'p', 'P', 'c', 'C', 0},
    {&KEY_NSPIRE_Q, 'q', 'Q', 'v', 'V', 0}, {&KEY_NSPIRE_R, 'r', 'R', 'b', 'B', 0},
    {&KEY_NSPIRE_S, 's', 'S', 'n', 'N', 0}, {&KEY_NSPIRE_T, 't', 'T', 'm', 'M', 0},
    {&KEY_NSPIRE_U, 'u', 'U', 'p', 'P', 0},
    {&KEY_NSPIRE_V, 'v', 'V', ',', ',', 0}, {&KEY_NSPIRE_W, 'w', 'W', '?', '?', 0},
    {&KEY_NSPIRE_X, 'x', 'X', '!', '!', 0}, {&KEY_NSPIRE_Y, 'y', 'Y', '\'', '"', 0},
    {&KEY_NSPIRE_Z, 'z', 'Z', '-', '_', 0},
    {&KEY_NSPIRE_0, '0', '0', '0', '0', '%'}, {&KEY_NSPIRE_1, '1', '1', '1', '1', '!'},
    {&KEY_NSPIRE_2, '2', '2', '2', '2', '@'}, {&KEY_NSPIRE_3, '3', '3', '3', '3', '#'},
    {&KEY_NSPIRE_4, '4', '4', '4', '4', '<'}, {&KEY_NSPIRE_5, '5', '5', '5', '5', '>'},
    {&KEY_NSPIRE_6, '6', '6', '6', '6', '\''}, {&KEY_NSPIRE_7, '7', '7', '7', '7', '['},
    {&KEY_NSPIRE_8, '8', '8', '8', '8', ']'}, {&KEY_NSPIRE_9, '9', '9', '9', '9', '"'},
    {&KEY_NSPIRE_SPACE, ' ', ' ', ' ', ' ', 0}, {&KEY_NSPIRE_PERIOD, '.', '.', '.', '.', ';'},
    {&KEY_NSPIRE_PLUS, '+', '+', '+', '+', '|'}, {&KEY_NSPIRE_MINUS, '-', '-', '-', '-', '_'},
    {&KEY_NSPIRE_NEGATIVE, '-', '-', '-', '-', '~'},
    {&KEY_NSPIRE_MULTIPLY, '*', '*', '*', '*', '&'},
    {&KEY_NSPIRE_DIVIDE, '/', '/', '/', '/', '\\'}, {&KEY_NSPIRE_EXP, '^', '^', '^', '^', '`'},
    {&KEY_NSPIRE_LP, '(', '(', '(', '(', '{'}, {&KEY_NSPIRE_RP, ')', ')', ')', ')', '}'},
    {&KEY_NSPIRE_EQU, '=', '=', '=', '=', '$'}, {&KEY_NSPIRE_COLON, ':', ':', ':', ':', ';'},
    {&KEY_NSPIRE_APOSTROPHE, '\'', '"', '\'', '"', 0}, {&KEY_NSPIRE_QUOTE, '"', '"', '"', '"', 0},
    {&KEY_NSPIRE_LTHAN, '<', '<', '<', '<', 0}, {&KEY_NSPIRE_GTHAN, '>', '>', '>', '>', 0},
};
#define NCHARS (int)(sizeof(chars) / sizeof(chars[0]))

/* Math keys type text; with Ctrl they type the LaTeX form. */
struct strkey { const t_key *key; const char *text, *ctrl; int abc_only; };
static const struct strkey strings[] = {
    {&KEY_NSPIRE_SQU, "^2", "\\sqrt{", 0},
    {&KEY_NSPIRE_eEXP, "e^", "\\ln(", 0},
    {&KEY_NSPIRE_TENX, "10^", "\\log(", 0},
    {&KEY_NSPIRE_FRAC, "\\frac{", "}{", 0},
    {&KEY_NSPIRE_TRIG, "\\sin(", "\\cos(", 0},
    {&KEY_NSPIRE_PI, "\\pi", "\\pi", 1},
    {&KEY_NSPIRE_EE, "E", "E", 1},
};
#define NSTRINGS (int)(sizeof(strings) / sizeof(strings[0]))

enum { K_ENTER, K_DEL, K_ESC, K_TAB, K_THINK, K_LAYOUT, K_MENU, K_CHATS, K_IME, K_COUNT };
static uint8_t prev_char[NCHARS];
static uint8_t prev_string[NSTRINGS];
static uint8_t prev_ctl[K_COUNT];
static uint8_t prev_arrow;
static int arrow_hold;
static int del_hold;

/* Injected key events from the host (OP_INJECT), consumed like real keys. */
static uint8_t inject_q[512];
static int inject_head, inject_tail;

static int matrix_any_pressed(void) {
    for (volatile uint32_t *r = (volatile uint32_t *)0x900E0010u;
         r < (volatile uint32_t *)0x900E0020u; ++r)
        if (*r) return 1;
    return 0;
}

static int ctl_down(int k) {
    switch (k) {
    case K_ENTER: return isKeyPressed(KEY_NSPIRE_ENTER) || isKeyPressed(KEY_NSPIRE_RET);
    case K_DEL: return isKeyPressed(KEY_NSPIRE_DEL);
    case K_ESC: return isKeyPressed(KEY_NSPIRE_ESC);
    case K_TAB: return isKeyPressed(KEY_NSPIRE_TAB);
    case K_THINK: return isKeyPressed(KEY_NSPIRE_VAR);
    case K_LAYOUT: return isKeyPressed(KEY_NSPIRE_DOC);
    case K_MENU: return isKeyPressed(KEY_NSPIRE_MENU);
    case K_CHATS: return isKeyPressed(KEY_NSPIRE_CAT);
    case K_IME: /* Chinese / English: ctrl+space, or the scratchpad key */
        return isKeyPressed(KEY_NSPIRE_SCRATCHPAD) ||
               (isKeyPressed(KEY_NSPIRE_CTRL) && isKeyPressed(KEY_NSPIRE_SPACE));
    }
    return 0;
}

static int handle_event(int ev);

static void overlay_key(int ch) {
    const struct screen *s = screen_find(overlay);
    if (!s) {
        overlay = 0;
        return;
    }
    const uint8_t *k = s->keys;
    for (int i = 0; i < s->nkeys; ++i, k += 3 + k[2]) {
        if (k[0] != (uint8_t)ch) continue;
        char arg[200];
        int n = k[2] < sizeof(arg) - 1 ? k[2] : (int)sizeof(arg) - 1;
        memcpy(arg, k + 3, (size_t)n);
        arg[n] = '\0';
        switch (k[1]) {
        case ACT_CLOSE:
            overlay = 0;
            break;
        case ACT_GOTO:
            if (n >= 1 && screen_find((uint8_t)arg[0])) overlay = (uint8_t)arg[0];
            break;
        case ACT_INSERT:
            overlay = 0;
            if (strcmp(arg, "<ime>") == 0) { /* the menu's Chinese switch */
                handle_event(EV_IME);
                break;
            }
            input_append_str(arg);
            break;
        case ACT_COMMAND: {
            char *bar = strchr(arg, '|');
            if (bar) *bar = '\0';
            strncpy(cmd_id, arg, sizeof(cmd_id) - 1);
            cmd_id[sizeof(cmd_id) - 1] = '\0';
            strncpy(cmd_label, bar ? bar + 1 : arg, sizeof(cmd_label) - 1);
            cmd_label[sizeof(cmd_label) - 1] = '\0';
            overlay = 0;
            break;
        }
        case ACT_SEND:
            send_action(arg);
            overlay = 0;
            break;
        case ACT_SEND_STAY:
            send_action(arg);
            break;
        }
        dirty = 1;
        return;
    }
}

/* Returns 0 when the page should close. */
static int handle_event(int ev) {
    if (overlay) {
        if (ev == EV_ESC || ev == EV_MENU) {
            overlay = 0;
            dirty = 1;
        } else if (ev > 0 && ev < 0x100) {
            overlay_key(ev >= 'A' && ev <= 'Z' ? ev + 32 : ev);
        }
        return 1;
    }
    if (ev > 0 && ev < 0x100) {
        char c = (char)ev;
        if (ime_on) {
            if (((c >= 'a' && c <= 'z') || (c == '\'' && comp_len > 0)) &&
                comp_len < COMP_CAP) {
                comp[comp_len++] = c;
                ime_page = 0;
                ime_changed();
                return 1;
            }
            if (comp_len > 0) {
                if (c >= '1' && c <= '9') {
                    ime_pick(c - '1');
                    return 1;
                }
                if (c == ' ') {
                    ime_pick(0);
                    return 1;
                }
                /* Anything else ends the composition first. */
                if (ncand > 0) ime_pick(0);
                ime_commit_raw();
            }
            const char *wide = ime_punctuation(c);
            if (wide) {
                input_append_str(wide);
                return 1;
            }
        }
        input_append(c);
        return 1;
    }
    if (comp_len > 0) { /* keys act on the composition while it exists */
        switch (ev) {
        case EV_ENTER:
            ime_commit_raw();
            return 1;
        case EV_DEL:
            --comp_len;
            ime_page = 0;
            ime_changed();
            return 1;
        case EV_ESC:
            comp_len = 0;
            ime_page = 0;
            ime_changed();
            return 1;
        case EV_LEFT:
        case EV_UP:
            if (ime_flags & 1) {
                --ime_page;
                ime_rev++;
                ncand = 0;
            }
            return 1;
        case EV_RIGHT:
        case EV_DOWN:
            if (ime_flags & 2) {
                ++ime_page;
                ime_rev++;
                ncand = 0;
            }
            return 1;
        }
    }
    switch (ev) {
    case EV_IME:
        ime_commit_raw();
        ime_on = !ime_on;
        dirty = 1;
        break;
    case EV_ENTER:
        submit();
        break;
    case EV_DEL:
        if (cursor > 0) {
            int start = utf8_prev(cursor);
            memmove(input + start, input + cursor, (size_t)(input_len - cursor));
            input_len -= cursor - start;
            cursor = start;
            input[input_len] = '\0';
            input_changed();
        } else if (input_len == 0) {
            cmd_id[0] = '\0';
            cmd_label[0] = '\0';
        }
        dirty = 1;
        break;
    case EV_LEFT:
        cursor = utf8_prev(cursor);
        dirty = 1;
        break;
    case EV_RIGHT:
        cursor = utf8_next(cursor);
        dirty = 1;
        break;
    case EV_ESC:
        return 0;
    case EV_UP:
        anchor = -1; /* the user scrolls from where the view is */
        scroll_px += SCROLL_STEP;
        dirty = 1;
        break;
    case EV_DOWN:
        anchor = -1;
        scroll_px -= SCROLL_STEP;
        dirty = 1;
        break;
    case EV_THINK:
        think_level = (think_level + 1) % 4;
        dirty = 1;
        break;
    case EV_LAYOUT:
        layout_mode = (layout_mode + 1) % 3;
        dirty = 1;
        break;
    case EV_MENU:
        if (screen_find(1)) overlay = 1;
        else info(link_up ? "(menu not loaded yet)" : "(menu needs the bridge)");
        dirty = 1;
        break;
    case EV_CHATS:
        send_action("session.list");
        break;
    case EV_ARROWS:
        arrows_enabled = !arrows_enabled;
        info(arrows_enabled ? "(touchpad arrows on)" : "(touchpad arrows off)");
        break;
    }
    return 1;
}

static int inject_event(uint8_t c) {
    switch (c) {
    case '\n': return EV_ENTER;
    case 0x08: return EV_DEL;
    case 0x1B: return EV_ESC;
    case 0x0B: return EV_UP;
    case 0x0C: return EV_DOWN;
    case 0x0E: return EV_THINK;
    case 0x0F: return EV_LAYOUT;
    case 0x10: return EV_MENU;
    case 0x11: return EV_CHATS;
    case 0x12: return EV_ARROWS;
    case 0x13: return EV_LEFT;
    case 0x14: return EV_RIGHT;
    case 0x15: return EV_IME;
    }
    return c >= 32 && c < 127 ? c : 0;
}

/* Returns 0 when the user asked to quit. */
static int poll_keys(void) {
    int stay = 1;
    while (inject_tail != inject_head) {
        int ev = inject_event(inject_q[inject_tail]);
        inject_tail = (inject_tail + 1) % (int)sizeof(inject_q);
        if (ev && !handle_event(ev)) stay = 0;
    }

    int shift = isKeyPressed(KEY_NSPIRE_SHIFT);
    int ctrl = isKeyPressed(KEY_NSPIRE_CTRL);
    for (int i = 0; i < NSTRINGS; ++i) {
        int down = isKeyPressed(*strings[i].key) ? 1 : 0;
        if (down && !prev_string[i] && !overlay && !(strings[i].abc_only && QWERTY)) {
            ime_commit_raw();
            input_append_str(ctrl ? strings[i].ctrl : strings[i].text);
        }
        prev_string[i] = (uint8_t)down;
    }
    for (int i = 0; i < NCHARS; ++i) {
        int down = isKeyPressed(*chars[i].key) ? 1 : 0;
        /* ctrl+space switches Chinese / English (K_IME); it types nothing. */
        if (ctrl && chars[i].key == &KEY_NSPIRE_SPACE) down = 0;
        char c = ctrl ? chars[i].ctrl
               : QWERTY ? (shift ? chars[i].qupper : chars[i].qlower)
                        : (shift ? chars[i].upper : chars[i].lower);
        if (down && !prev_char[i] && c && !handle_event((unsigned char)c)) stay = 0;
        prev_char[i] = (uint8_t)down;
    }
    for (int k = 0; k < K_COUNT; ++k) {
        int down = ctl_down(k);
        int pressed = down && !prev_ctl[k];
        int ev = 0;
        switch (k) {
        case K_ENTER: ev = EV_ENTER; break;
        case K_ESC: ev = EV_ESC; break;
        case K_TAB: ev = shift ? EV_DOWN : EV_UP; break;
        case K_THINK: ev = EV_THINK; break;
        case K_LAYOUT: ev = EV_LAYOUT; break;
        case K_MENU: ev = EV_MENU; break;
        case K_CHATS: ev = EV_CHATS; break;
        case K_IME: ev = EV_IME; break;
        case K_DEL:
            ev = EV_DEL;
            del_hold = down ? del_hold + 1 : 0;
            if (del_hold > 15) pressed = 1; /* auto-repeat */
            break;
        }
        if (pressed && !handle_event(ev)) stay = 0;
        prev_ctl[k] = (uint8_t)down;
    }
    if (arrows_enabled) {
        touchpad_report_t report;
        int arrow = 0;
        if (touchpad_scan(&report) == 0 && report.pressed) arrow = (int)report.arrow;
        arrow_hold = arrow && arrow == prev_arrow ? arrow_hold + 1 : 0;
        /* Act on the press, then auto-repeat while held. */
        if (arrow && (arrow != prev_arrow || (arrow_hold > 12 && arrow_hold % 3 == 0))) {
            if (arrow == TPAD_ARROW_UP) handle_event(EV_UP);
            if (arrow == TPAD_ARROW_DOWN) handle_event(EV_DOWN);
            if (arrow == TPAD_ARROW_LEFT) handle_event(EV_LEFT);
            if (arrow == TPAD_ARROW_RIGHT) handle_event(EV_RIGHT);
        }
        prev_arrow = (uint8_t)arrow;
    }
    return stay;
}

/* ------------------------------------------------------------------ */
/* Messages from the host                                              */
/* ------------------------------------------------------------------ */

static void state_apply(const char *text, uint32_t len) {
    uint32_t i = 0;
    while (i < len) {
        uint32_t k0 = i;
        while (i < len && text[i] != '=' && text[i] != ';') ++i;
        uint32_t klen = i - k0;
        uint32_t v0 = i, vlen = 0;
        if (i < len && text[i] == '=') {
            v0 = ++i;
            while (i < len && text[i] != ';') ++i;
            vlen = i - v0;
        }
        if (i < len) ++i; /* ';' */
        uint32_t number = 0;
        for (uint32_t j = 0; j < vlen && text[v0 + j] >= '0' && text[v0 + j] <= '9'; ++j)
            number = number * 10u + (uint32_t)(text[v0 + j] - '0');
        if (klen == 1 && text[k0] == 's') {
            session_number = (int)number;
            have_session = (int)number;
        } else if (klen == 1 && text[k0] == 't') {
            uint32_t n = vlen < sizeof(session_label) - 1 ? vlen : sizeof(session_label) - 1;
            for (uint32_t j = 0; j < n; ++j) {
                unsigned char c = (unsigned char)text[v0 + j];
                session_label[j] = (c >= 32 && c < 127) ? (char)c : '?';
            }
            session_label[n] = '\0';
        } else if (klen == 2 && text[k0] == 'm' && text[k0 + 1] == 'v') {
            if ((int)number != have_menu_version) screens_clear();
            have_menu_version = (int)number;
        }
    }
    dirty = 1;
}

static void handle_message(int opcode, uint32_t id, const uint8_t *p, uint32_t len) {
    switch (opcode) {
    case OP_RESPONSE:
    case OP_ERROR:
        if (id != awaited_id) break;
        if (len) {
            char *text = malloc(len + 1);
            if (text) {
                memcpy(text, p, len);
                text[len] = '\0';
                text_add(opcode == OP_ERROR ? "! " : "", text,
                         opcode == OP_ERROR ? KIND_INFO : KIND_AI, 0);
                free(text);
            }
        }
        waiting = 0;
        awaited_id = 0;
        dirty = 1;
        break;
    case OP_BLOCK: {
        if (len < 12) break;
        int kind = p[0], bpp = p[1], encoding = p[6], flags = p[7];
        int w = (int)get_u16(p + 2), h = (int)get_u16(p + 4);
        uint32_t block_id = get_u32(p + 8), bytes = 0;
        uint8_t *data = image_decode(bpp, w, h, encoding, p + 12, len - 12, &bytes);
        if (!data) break;
        if (kind >= KIND_COUNT) kind = KIND_AI;
        if (kind == KIND_USER) blocks_remove_echo(block_id);
        blocks_add(kind, bpp, (flags & 1) ? 0 : 5, w, h, block_id, data, bytes);
        if (kind == KIND_AI && !(flags & 1)) anchor = nblocks - 1;
        else if (kind == KIND_USER) anchor = -1;
        break;
    }
    case OP_SCREEN:
        screen_store(p, len);
        break;
    case OP_STATE:
        state_apply((const char *)p, len);
        break;
    case OP_CLEAR:
        blocks_clear();
        break;
    case OP_IME: {
        if (id != ime_rev || comp_len == 0 || len < 4) break; /* stale */
        int count = p[0] <= IME_MAX_CAND ? p[0] : IME_MAX_CAND;
        uint32_t pos = 4;
        int parsed = 0;
        for (int k = 0; k < p[0]; ++k) {
            if (pos + 2 > len || pos + 2 + p[pos + 1] > len) break;
            if (k < count && p[pos + 1] < IME_TEXT_CAP) {
                cand[parsed].consumed = p[pos];
                cand[parsed].len = p[pos + 1];
                memcpy(cand[parsed].text, p + pos + 2, p[pos + 1]);
                ++parsed;
            }
            pos += 2u + p[pos + 1];
        }
        ncand = parsed;
        ime_flags = p[1];
        ime_page = p[2];
        if (len >= pos + 12) {
            const uint8_t *img = p + pos;
            int bpp = img[1], encoding = img[6];
            int w = (int)get_u16(img + 2), h = (int)get_u16(img + 4);
            uint32_t bytes = 0;
            uint8_t *data = h <= 40 ? image_decode(bpp, w, h, encoding, img + 12,
                                                   len - pos - 12, &bytes) : NULL;
            if (data) {
                free(ime_bar.data);
                ime_bar.data = data;
                ime_bar.bpp = (uint8_t)bpp;
                ime_bar.w = (uint16_t)w;
                ime_bar.h = (uint16_t)h;
            }
        }
        if (pending_pick >= 0) {
            int k = pending_pick;
            pending_pick = -1;
            if (k < ncand) ime_pick(k);
        }
        dirty = 1;
        break;
    }
    case OP_PREVIEW: {
        if (id != input_rev) break; /* rendered from text that has changed since */
        if (len < 12) {
            preview_clear();
            break;
        }
        int bpp = p[1], encoding = p[6];
        int w = (int)get_u16(p + 2), h = (int)get_u16(p + 4);
        uint32_t bytes = 0;
        if (h > PREVIEW_MAX_H) break;
        uint8_t *data = image_decode(bpp, w, h, encoding, p + 12, len - 12, &bytes);
        if (!data) break;
        free(preview.data);
        preview.data = data;
        preview.bpp = (uint8_t)bpp;
        preview.w = (uint16_t)w;
        preview.h = (uint16_t)h;
        dirty = 1;
        break;
    }
    case OP_INJECT:
        for (uint32_t i = 0; i < len; ++i) {
            int next = (inject_head + 1) % (int)sizeof(inject_q);
            if (next == inject_tail) break;
            inject_q[inject_head] = p[i];
            inject_head = next;
        }
        break;
    case OP_DUMP_REQ:
        if (!dump_buf) dump_buf = malloc(3u * W * H + 8u);
        if (dump_buf && !tx[TX_DUMP].pending)
            tx_post(TX_DUMP, OP_DUMP, id, dump_buf, dump_encode(dump_buf));
        break;
    }
}

/* ------------------------------------------------------------------ */
/* Page loop (main, OS UI task)                                        */
/* ------------------------------------------------------------------ */

static void wait_keys_released(void) {
    for (int i = 0; i < 200 && matrix_any_pressed(); ++i) TCC_TASK_SLEEP(TICKS_PER_FRAME);
}

/* Start-up failures are shown in an OS dialog; main() runs in the UI task
 * before the page takes over, so that is safe.  (A log file would pull in
 * stdio, about 5 KB of the 60 KB image budget.) */
static int fail(const char *message) {
    show_msgbox("NspireAI", message);
    return 1;
}

/* The image is freed when main() returns, so no NavNet callback may be
 * running by then.  Order matters: end the session, stop the service so the
 * host's reconnects no longer start callbacks, then wait until every
 * callback has left this image.  Returns 1 when that is guaranteed. */
static int end_session(int started) {
    quitting = 1;
    session_gen++;
    rx_ready = 0; /* release a callback waiting for the page */
    /* The host's keepalive makes the callback's blocking read return; the
     * callback then says BYE and leaves. */
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

static void lut_init(void) {
    static const uint8_t ink[KIND_COUNT][3] = {
        {20, 20, 20}, {20, 70, 170}, {110, 110, 110},
    };
    for (int k = 0; k < KIND_COUNT; ++k)
        for (int v = 0; v < 16; ++v) {
            int r = ink[k][0] + (250 - ink[k][0]) * v / 15;
            int g = ink[k][1] + (250 - ink[k][1]) * v / 15;
            int b = ink[k][2] + (250 - ink[k][2]) * v / 15;
            ink_lut[k][v] = RGB(r, g, b);
        }
}

int main(void) {
    if (nl_osid() != CX2_CAS_6_2_0_333_OSID)
        return fail("This build needs a TI-Nspire CX II CAS with OS 6.2.0.333.");
    back = malloc(W * H * 2);
    rx_buf = malloc(RX_CAP);
    rx_frame = malloc(RX_FRAME_CAP);
    blocks = malloc(sizeof(struct block) * MAX_BLOCKS);
    if (!back || !rx_buf || !rx_frame || !blocks) {
        free(back);
        free(rx_buf);
        free(rx_frame);
        free(blocks);
        return fail("Not enough memory. Close other documents or reset.");
    }
    hww = lcd_type() == SCR_240x320_565;
    fb = (uint16_t *)(uintptr_t)LCD_BASE;
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
    lut_init();
    uint32_t boot = RTC_SECONDS;
    conversation_id = (uint16_t)(boot | 1u);
    next_id = (boot & 0xFFFFu) << 12 | 2u;

    /* The loader runs main() with IRQs masked; TCC_Task_Sleep and the USB
     * stack need them.  The keypad IRQ stays masked so no key presses are
     * queued for the frozen OS browser to replay after the page closes. */
    int saved_irq = TCT_Local_Control_Interrupts(0);
    int keypad_irq_was_enabled = (IRQ_ENABLE & IRQ_KEYPAD) != 0;
    if (keypad_irq_was_enabled) IRQ_DISABLE = IRQ_KEYPAD;

    info("NspireAI - type a question and press enter.");
    int started = -1;
    uint32_t start_tried = 0;
    int was_linked = 0;
    wait_keys_released(); /* the Enter that opened the document */
    for (int i = 0; i < NCHARS; ++i) prev_char[i] = 1;
    for (int i = 0; i < NSTRINGS; ++i) prev_string[i] = 1;
    for (int i = 0; i < K_COUNT; ++i) prev_ctl[i] = 1;

    for (;;) {
        loop_beat++;
        STEP(1);
        /* Register the service only once the page has run for a moment:
         * while the OS is still busy opening the document, a burst of host
         * traffic froze the handheld. */
        if (started < 0 && loop_beat >= START_DELAY_FRAMES &&
            RTC_SECONDS - start_tried >= 2) {
            started = (int16_t)TI_NN_StartService(SERVICE_ID, NULL, service_callback);
            start_attempts++;
            start_result = started;
            if (start_tried == 0 || started >= 0)
                info(started < 0 ? "NavNet service busy; retrying..."
                                 : "Waiting for the bridge...");
            start_tried = RTC_SECONDS;
        }
        STEP(2);
        if (link_up != was_linked) {
            was_linked = link_up;
            info(was_linked ? "Connected to the bridge."
                            : "Bridge disconnected; waiting...");
            if (!was_linked) waiting = 0;
            dirty = 1;
        }
        /* No watchdog here: the page loop must never close a channel the
         * callback task is using.  (One did, when a second boundary fell
         * between its two reads -- RTC_SECONDS, then the callback's newer
         * session_last_rx -- so the unsigned difference wrapped and a busy
         * link looked dead.  NavNet then froze the handheld.) */
        if (waiting && RTC_SECONDS - waiting_since >= ANSWER_TIMEOUT_SECONDS) {
            waiting = 0;
            awaited_id = 0;
            text_add("! ", "no answer from the bridge; try again", KIND_INFO, 0);
        }
        STEP(3);
        if (rx_ready) {
            handle_message(rx_opcode, rx_id, rx_buf, rx_len);
            rx_ready = 0;
        }
        STEP(4);
        int stay = poll_keys();
        /* Candidates for the letters being composed, after every change. */
        if (link_up && comp_len > 0 && ime_rev != ime_sent_rev && !tx[TX_IME].pending) {
            ime_sent_rev = ime_rev;
            ime_req[0] = (uint8_t)ime_page;
            memcpy(ime_req + 1, comp, (size_t)comp_len);
            tx_post(TX_IME, OP_IME_REQ, ime_rev, ime_req, (uint32_t)comp_len + 1);
        }
        /* Ask for a typeset preview once typing has paused (~250 ms). */
        if (link_up && input_rev != preview_sent_rev && loop_beat - edit_beat >= 8 &&
            !tx[TX_PREVIEW].pending) {
            preview_sent_rev = input_rev;
            if (input_len == 0) {
                preview_clear();
            } else {
                memcpy(preview_req, input, (size_t)input_len);
                tx_post(TX_PREVIEW, OP_PREVIEW_REQ, input_rev, preview_req,
                        (uint32_t)input_len);
            }
        }
        STEP(5);
        if (!stay) break;
        if (dirty) {
            dirty = 0;
            STEP(6);
            render();
        }
        /* Present every frame and keep the LCD on the page's buffer: the OS
         * still touches the LCD registers now and then. */
        STEP(7);
        present();
        if (scan[0] && LCD_BASE != (uint32_t)(uintptr_t)scan[scan_front])
            LCD_BASE = (uint32_t)(uintptr_t)scan[scan_front];
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
    if (keypad_irq_was_enabled) IRQ_ENABLE = IRQ_KEYPAD;
    TCT_Local_Control_Interrupts(saved_irq);
    if (!session_gone) {
        /* A callback is still inside this image: keep the image and its
         * buffers alive rather than free memory that is in use.  Leaks
         * once, never crashes. */
        nl_set_resident();
        return 0;
    }
    blocks_clear();
    screens_clear();
    preview_clear();
    ime_bar_clear();
    free(dump_buf);
    free(blocks);
    free(rx_frame);
    free(rx_buf);
    free(back);
    free(scan_raw);
    return 0;
}
