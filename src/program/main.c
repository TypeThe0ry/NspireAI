/*
 * NspireAI: standalone Ndless program.
 *
 * This is deliberately not a TI document/Lua page.  The .tns produced by
 * this directory is an Ndless executable package.  At runtime it keeps all
 * chat state in memory and uses the calculator's NavNet client syscalls for
 * the Mac bridge; it never opens, reads, writes, or replaces a TI document.
 */
#include <os.h>
#include "nav_os_call.h"
#include "nav_fragment.h"
#ifdef NSPIRE_UI_NGC
#define NSPIRE_NAV_CLOCK_RTC
#else
#include <SDL/SDL.h>
#endif
#include "nav_clock.h"
#ifndef _WIN32
#ifndef __cdecl
#define __cdecl
#endif
#ifndef __declspec
#define __declspec(x)
#endif
#endif

#include <stdarg.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>

#define SCREEN_WIDTH 320
#define SCREEN_HEIGHT 240

#define MAGIC0 'N'
#define MAGIC1 'S'
#define MAGIC2 'A'
#define MAGIC3 'I'
#define PROTOCOL_VERSION 1

#define OP_PING 1
#define OP_PONG 2
#define OP_REQUEST 3
#define OP_RESPONSE 4
#define OP_ERROR 5
#define OP_NEW 7
#define OP_FRAGMENT 8

#define HEADER_SIZE 16
#define FRAGMENT_HEADER_SIZE 10
#define MAX_FRAME_PAYLOAD 224 /* NavNet service payload <= 254 bytes */
#define MAX_INPUT 768
#define MAX_RESPONSE 16384
#define HISTORY_LINES 96
#define LINE_CAP 192

#define SERVICE_ID 0x5001 /* project-private NavNet application service */

/* NavNet node enumeration is a calculator/host transport operation, not a
 * local socket lookup.  Give the USB stack time to settle after the page is
 * opened and avoid hammering a missing or half-attached host on every frame.
 * These delays are deliberately conservative: they change retry cadence only,
 * not the service or NSAI wire format. */
#define NAV_INITIAL_DELAY_MS 2000
#define NAV_RETRY_DELAY_MS 3000
#define NAV_DISCONNECT_RETRY_MS 2000
#define NAV_PING_DELAY_MS 250
#define NAV_HANDSHAKE_TIMEOUT_MS 2500
/* Timeout units and scheduler progress on CX II 6.2 are unverified. A value
 * of 1 is only a requested timeout, NOT a bound on this synchronous call or
 * a guarantee that the UI cannot freeze. */
#define NAV_READ_TIMEOUT 1

/* Newlib's fini object is linked by nspire-ld, while a standalone Ndless
 * process intentionally has no host-style process teardown. */
void _fini(void) __attribute__((weak));
void _fini(void) {}

#ifndef NSPIRE_UI_NGC
static SDL_Surface *screen;
static nSDL_Font *font;
#endif
static int done;

static char input_text[MAX_INPUT];
static size_t input_len;
static char history[HISTORY_LINES][LINE_CAP];
static int history_count;
static char status_text[LINE_CAP];

static nn_ch_t nav_channel;
static int nav_local_service_started;
static int nav_connected;
static int nav_ping_sent;
static int nav_handshake_pending;
static uint32_t nav_retry_at;
static uint32_t nav_ping_at;
static uint32_t nav_connected_at;
static uint32_t next_request_id = 1;
static uint16_t conversation_id = 1;
static uint32_t pending_request_id;
static int pending;

#ifdef NSPIRE_UI_NGC
/* The NGC page must be usable before the experimental NavNet path is armed.
 * Keep this flag in the shared translation unit so status/input/history
 * changes can mark the native framebuffer dirty without introducing another
 * callback or scheduler dependency. */
static int ngc_ui_dirty = 1;
#endif

static unsigned char response_data[MAX_RESPONSE];
static size_t response_len;
static size_t response_total;
static size_t response_next_offset;
static uint8_t response_opcode;
static uint32_t response_request_id;
static uint16_t response_conversation_id;

static void set_status(const char *format, ...);

static int nav_write_frame(uint8_t opcode, uint32_t request_id,
                           uint16_t conversation, const unsigned char *payload,
                           size_t payload_length);

/* The calculator is the client; the Java Mac helper owns service 0x5001.
 *
 * Do not pull newlib's printf implementation into the handheld package just
 * to render a few diagnostic integers.  On Ndless that drags in dtoa and a
 * several-kilobyte formatter, pushing an otherwise valid NGC package past
 * the CX II document-size limit.  This deliberately supports only the
 * conversions used below: %s, %d, %u, %lu, %x and %04x. */
static size_t status_append(char *out, size_t used, const char *text) {
    while (*text && used + 1 < sizeof(status_text))
        out[used++] = *text++;
    out[used] = '\0';
    return used;
}

static size_t status_append_uint(char *out, size_t used, unsigned long value,
                                 unsigned base, unsigned width, char pad) {
    char digits[sizeof(unsigned long) * 8 + 1];
    unsigned count = 0;
    static const char alphabet[] = "0123456789abcdef";
    do {
        digits[count++] = alphabet[value % base];
        value /= base;
    } while (value && count < sizeof(digits));
    while (count < width && count < sizeof(digits)) digits[count++] = pad;
    while (count) {
        if (used + 1 >= sizeof(status_text)) break;
        out[used++] = digits[--count];
    }
    out[used] = '\0';
    return used;
}

static void set_status(const char *format, ...) {
    va_list args;
    size_t used = 0;
    va_start(args, format);
    status_text[0] = '\0';
    while (*format && used + 1 < sizeof(status_text)) {
        if (*format++ != '%') {
            status_text[used++] = format[-1];
            status_text[used] = '\0';
            continue;
        }
        if (*format == '%') {
            status_text[used++] = *format++;
            status_text[used] = '\0';
            continue;
        }
        {
            char pad = ' ';
            unsigned width = 0;
            if (*format == '0') { pad = '0'; format++; }
            while (*format >= '0' && *format <= '9')
                width = width * 10u + (unsigned)(*format++ - '0');
            if (*format == 'l') {
                format++;
                if (*format == 'u') {
                    used = status_append_uint(status_text, used,
                                              va_arg(args, unsigned long), 10, width, pad);
                    format++;
                    continue;
                }
            }
            if (*format == 's') {
                used = status_append(status_text, used, va_arg(args, const char *));
                format++;
            } else if (*format == 'd') {
                int value = va_arg(args, int);
                if (value < 0) {
                    used = status_append(status_text, used, "-");
                    value = -value;
                }
                used = status_append_uint(status_text, used, (unsigned long)(unsigned)value,
                                          10, width, pad);
                format++;
            } else if (*format == 'u') {
                used = status_append_uint(status_text, used, va_arg(args, unsigned int),
                                          10, width, pad);
                format++;
            } else if (*format == 'x') {
                used = status_append_uint(status_text, used, va_arg(args, unsigned int),
                                          16, width, pad);
                format++;
            } else {
                /* Keep an unknown conversion visible without consuming an
                 * argument; there are no such conversions in production. */
                if (used + 1 < sizeof(status_text)) status_text[used++] = '%';
                status_text[used] = '\0';
            }
        }
    }
    status_text[sizeof(status_text) - 1] = '\0';
    va_end(args);
#ifdef NSPIRE_UI_NGC
    ngc_ui_dirty = 1;
#endif
}

static void add_history_line(const char *text) {
    if (history_count == HISTORY_LINES) {
        memmove(history[0], history[1], sizeof(history[0]) * (HISTORY_LINES - 1));
        history_count = HISTORY_LINES - 1;
    }
    strncpy(history[history_count], text, LINE_CAP - 1);
    history[history_count][LINE_CAP - 1] = '\0';
    history_count++;
#ifdef NSPIRE_UI_NGC
    ngc_ui_dirty = 1;
#endif
}

/* The tiny Ndless font is intentionally handled as a conservative fixed
 * width text surface.  It keeps drawing deterministic on both CX and CX II;
 * UTF-8 bytes are preserved for the bridge, while unsupported glyphs simply
 * render according to the installed calculator font. */
static void add_wrapped(const char *prefix, const unsigned char *text, size_t length) {
    char line[LINE_CAP];
    size_t line_len = 0;
    size_t prefix_len = prefix ? strlen(prefix) : 0;
    size_t i;

    if (prefix_len >= sizeof(line)) prefix_len = sizeof(line) - 1;
    if (prefix_len) {
        memcpy(line, prefix, prefix_len);
        line_len = prefix_len;
    }

    for (i = 0; i < length; i++) {
        unsigned char ch = text[i];
        if (ch == '\r') continue;
        if (ch == '\n' || line_len >= LINE_CAP - 1) {
            line[line_len] = '\0';
            add_history_line(line);
            line_len = 0;
            prefix_len = 0;
        }
        if (ch == '\n') continue;
        line[line_len++] = (char)ch;
        /* Tinytype is approximately six pixels wide.  48 characters leave
         * room for the scrollbar/status margin on a 320px display. */
        if (line_len >= 48 && i + 1 < length && text[i + 1] != '\n') {
            line[line_len] = '\0';
            add_history_line(line);
            line_len = 0;
            prefix_len = 0;
        }
    }
    if (line_len || length == 0) {
        line[line_len] = '\0';
        add_history_line(line);
    }
}

static void add_prompt(const char *prompt) {
    add_wrapped("> ", (const unsigned char *)prompt, strlen(prompt));
}

static void add_answer(uint8_t opcode, const unsigned char *answer, size_t length) {
    if (opcode == OP_ERROR) {
        add_wrapped("Error: ", answer, length);
    } else {
        add_wrapped("AI: ", answer, length);
    }
}

static void put_u16_be(unsigned char *out, uint16_t value) {
    out[0] = (unsigned char)(value >> 8);
    out[1] = (unsigned char)value;
}

static void put_u32_be(unsigned char *out, uint32_t value) {
    out[0] = (unsigned char)(value >> 24);
    out[1] = (unsigned char)(value >> 16);
    out[2] = (unsigned char)(value >> 8);
    out[3] = (unsigned char)value;
}

static uint16_t get_u16_be(const unsigned char *in) {
    return (uint16_t)(((uint16_t)in[0] << 8) | in[1]);
}

static uint32_t get_u32_be(const unsigned char *in) {
    return ((uint32_t)in[0] << 24) |
           ((uint32_t)in[1] << 16) |
           ((uint32_t)in[2] << 8) |
           (uint32_t)in[3];
}

static void nav_disconnect(const char *reason) {
    if (nav_channel) {
        (void)NAV_OS_CALL(TI_NN_Disconnect(nav_channel));
    }
    nav_channel = NULL;
    nav_connected = 0;
    nav_ping_sent = 0;
    nav_handshake_pending = 0;
    pending = 0;
    response_len = 0;
    response_total = 0;
    response_next_offset = 0;
    nav_connected_at = 0;
    nav_ping_at = 0;
    nav_retry_at = nav_clock_ms() + NAV_DISCONNECT_RETRY_MS;
    set_status("bridge disconnected: %s", reason ? reason : "retrying");
}

/* Historical Ndless NavNet calculator tests start a local service before
 * enumerating the computer node.  This appears to be the calculator-side
 * NavNet bootstrap path; it is not a scheduler or interrupt workaround.
 * Keep it scoped to the experimental NGC transport candidate and never call
 * it from the default safe page. */
#ifdef NSPIRE_NGC_LOCAL_SERVICE
static void nav_bootstrap_service_callback(nn_ch_t channel, void *data) {
    (void)channel;
    (void)data;
}

static void nav_start_local_service(void) {
    int16_t status;
    if (nav_local_service_started) return;
    status = NAV_OS_CALL(TI_NN_StartService(SERVICE_ID, NULL,
                                            nav_bootstrap_service_callback));
    if (status >= 0) {
        nav_local_service_started = 1;
        set_status("NavNet local service ready; USB armed");
    } else {
        set_status("NavNet local service=%d; USB armed", status);
    }
}
#endif

static void nav_stop_local_service(void) {
    if (!nav_local_service_started) return;
    (void)NAV_OS_CALL(TI_NN_StopService(SERVICE_ID));
    nav_local_service_started = 0;
}

static int nav_try_connect(void) {
    static int last_enum_error;
    nn_oh_t operation;
    nn_nh_t node = NULL;
    int16_t status;
    int node_count = 0;

    if (nav_connected) return 1;
    if (!nav_deadline_reached(nav_clock_ms(), nav_retry_at)) return 0;

    operation = NAV_OS_CALL(TI_NN_CreateOperationHandle());
    if (!operation) {
        set_status("NavNet operation unavailable");
        nav_retry_at = nav_clock_ms() + NAV_RETRY_DELAY_MS;
        return 0;
    }
    status = NAV_OS_CALL(TI_NN_NodeEnumInit(operation));
    if (status < 0) {
        char detail[LINE_CAP];
        set_status("NavNet enum init=%d", status);
        strncpy(detail, status_text, sizeof(detail) - 1);
        detail[sizeof(detail) - 1] = '\0';
        /* Keep an unchanged failure from evicting the user's conversation. */
        if (status != last_enum_error) add_history_line(detail);
        last_enum_error = status;
        (void)NAV_OS_CALL(TI_NN_DestroyOperationHandle(operation));
        if (status == -274) {
            /* TI's Mac NavNet returns -274 for an empty node list. Keep the
             * numeric code visible: handheld equivalence is not yet verified. */
            set_status("No NavNet peer? (enum -274)");
        } else {
            set_status("NavNet enumeration init: %d", status);
        }
        nav_retry_at = nav_clock_ms() + NAV_RETRY_DELAY_MS;
        return 0;
    }
    last_enum_error = 0;

    /* Try every discovered node; a stale host entry may precede live USB. */
    while ((status = NAV_OS_CALL(TI_NN_NodeEnumNext(operation, &node))) >= 0 && node) {
        node_count++;
        status = NAV_OS_CALL(TI_NN_Connect(node, SERVICE_ID, &nav_channel));
        if (status >= 0 && nav_channel) break;
        {
            char detail[LINE_CAP];
            set_status("NavNet node %d service 0x%04x=%d",
                       node_count, SERVICE_ID, status);
            strncpy(detail, status_text, sizeof(detail) - 1);
            detail[sizeof(detail) - 1] = '\0';
            add_history_line(detail);
        }
        nav_channel = NULL;
        node = NULL;
    }
    (void)NAV_OS_CALL(TI_NN_NodeEnumDone(operation));
    (void)NAV_OS_CALL(TI_NN_DestroyOperationHandle(operation));
    if (status < 0 || !nav_channel) {
        char detail[LINE_CAP];
        set_status("NavNet enum nodes=%d last=%d", node_count, status);
        strncpy(detail, status_text, sizeof(detail) - 1);
        detail[sizeof(detail) - 1] = '\0';
        add_history_line(detail);
        nav_channel = NULL;
        set_status("NavNet service 0x%04x unavailable (%d nodes)", SERVICE_ID, node_count);
        nav_retry_at = nav_clock_ms() + NAV_RETRY_DELAY_MS;
        return 0;
    }

    nav_connected = 1;
    nav_ping_sent = 0;
    nav_handshake_pending = 1;
    nav_connected_at = nav_clock_ms();
    nav_ping_at = nav_connected_at + NAV_PING_DELAY_MS;
    /* Do not write in the same tick as TI_NN_Connect.  On CX II builds the
     * connect call can return before the host-side service handle is usable;
     * an immediate PING then creates a callback followed by an invalid
     * connection.  The short delayed probe also gives the reconnect watchdog
     * a concrete liveness gate instead of trusting CONNECT alone. */
    set_status("connecting to Mac bridge");
    return 1;
}


static int nav_write_frame(uint8_t opcode, uint32_t request_id,
                           uint16_t conversation, const unsigned char *payload,
                           size_t payload_length) {
    unsigned char frame[HEADER_SIZE + MAX_FRAME_PAYLOAD];
    int16_t status;

    if (!nav_connected || !nav_channel || payload_length > MAX_FRAME_PAYLOAD) return 0;
    frame[0] = MAGIC0;
    frame[1] = MAGIC1;
    frame[2] = MAGIC2;
    frame[3] = MAGIC3;
    frame[4] = PROTOCOL_VERSION;
    frame[5] = opcode;
    put_u32_be(frame + 6, request_id);
    put_u16_be(frame + 10, conversation);
    put_u32_be(frame + 12, (uint32_t)payload_length);
    if (payload_length) memcpy(frame + HEADER_SIZE, payload, payload_length);
    status = NAV_OS_CALL(TI_NN_Write(nav_channel, frame, (uint32_t)(HEADER_SIZE + payload_length)));
    if (status < 0) {
        nav_disconnect("write failed");
        return 0;
    }
    return 1;
}

static void reset_conversation(void) {
    conversation_id++;
    if (conversation_id == 0) conversation_id = 1;
    history_count = 0;
    input_len = 0;
    input_text[0] = '\0';
    pending = 0;
    if (nav_connected) (void)nav_write_frame(OP_NEW, 0, conversation_id, NULL, 0);
    set_status("new conversation %u", conversation_id);
}

static int send_prompt(void) {
    uint32_t request_id;
    if (!input_len) return 0;
    if (!nav_connected) {
        set_status("bridge not connected; keep this program open");
        return 0;
    }
    if (pending) {
        set_status("waiting for request %lu", (unsigned long)pending_request_id);
        return 0;
    }
    if (input_len > MAX_FRAME_PAYLOAD) {
        set_status("question is too long for the first build");
        return 0;
    }

    request_id = next_request_id++;
    if (next_request_id == 0) next_request_id = 1;
    if (!nav_write_frame(OP_REQUEST, request_id, conversation_id,
                         (const unsigned char *)input_text, input_len)) {
        set_status("send failed; retry after bridge reconnect");
        return 0;
    }
    add_prompt(input_text);
    input_len = 0;
    input_text[0] = '\0';
    pending_request_id = request_id;
    pending = 1;
    set_status("waiting for Mac response %lu", (unsigned long)request_id);
    return 1;
}

static void complete_response(uint8_t opcode, uint32_t request_id,
                              uint16_t conversation, const unsigned char *data,
                              size_t length) {
    if (!pending || request_id != pending_request_id || conversation != conversation_id) return;
    add_answer(opcode, data, length);
    pending = 0;
    set_status("connected; Enter sends, Esc exits");
}

static void handle_frame(const unsigned char *frame, size_t frame_length) {
    uint8_t opcode;
    uint32_t request_id;
    uint16_t conversation;
    uint32_t payload_length;
    const unsigned char *payload;

    if (frame_length < HEADER_SIZE || frame[0] != MAGIC0 || frame[1] != MAGIC1 ||
        frame[2] != MAGIC2 || frame[3] != MAGIC3 || frame[4] != PROTOCOL_VERSION) {
        set_status("ignored malformed NavNet frame");
        return;
    }
    opcode = frame[5];
    request_id = get_u32_be(frame + 6);
    conversation = get_u16_be(frame + 10);
    payload_length = get_u32_be(frame + 12);
    if (payload_length != frame_length - HEADER_SIZE || payload_length > MAX_FRAME_PAYLOAD) {
        set_status("ignored invalid frame length");
        return;
    }
    payload = frame + HEADER_SIZE;

    if (opcode == OP_PING) {
        (void)nav_write_frame(OP_PONG, request_id, conversation,
                              (const unsigned char *)"PONG", 4);
        set_status("connected; Mac bridge pinged");
        return;
    }
    if (opcode == OP_PONG) {
        if (!nav_ping_sent || request_id != 0 || conversation != conversation_id) return;
        nav_handshake_pending = 0;
        set_status("connected; Enter sends, Esc exits");
        return;
    }
    if (opcode == OP_RESPONSE || opcode == OP_ERROR) {
        complete_response(opcode, request_id, conversation, payload, payload_length);
        return;
    }
    if (opcode == OP_FRAGMENT) {
        uint8_t original_opcode;
        uint32_t total;
        uint32_t offset;
        size_t chunk_length;

        if (payload_length < FRAGMENT_HEADER_SIZE) return;
        original_opcode = payload[0];
        if (payload[1] != 0) return;
        total = get_u32_be(payload + 2);
        offset = get_u32_be(payload + 6);
        chunk_length = payload_length - FRAGMENT_HEADER_SIZE;
        if (!nav_fragment_valid(original_opcode, total, offset, chunk_length,
                MAX_RESPONSE, response_next_offset, response_len, response_total, pending) ||
            (offset == 0 && (request_id != pending_request_id || conversation != conversation_id))) {
            response_len = 0;
            response_total = 0;
            response_next_offset = 0;
            set_status("invalid or stale response fragment");
            return;
        }
        if (offset == 0) {
            response_opcode = original_opcode;
            response_request_id = request_id;
            response_conversation_id = conversation;
            response_total = total;
            response_len = 0;
        }
        if (request_id != response_request_id || conversation != response_conversation_id ||
            original_opcode != response_opcode) return;
        memcpy(response_data + response_len, payload + FRAGMENT_HEADER_SIZE, chunk_length);
        response_len += chunk_length;
        response_next_offset += chunk_length;
        if (response_next_offset == response_total) {
            complete_response(response_opcode, response_request_id,
                              response_conversation_id, response_data, response_len);
            response_len = 0;
            response_total = 0;
            response_next_offset = 0;
        }
    }
}

static void nav_poll(void) {
    unsigned char frame[HEADER_SIZE + MAX_FRAME_PAYLOAD];
    uint32_t received = 0;
    int16_t status;
    uint32_t now;
    if (!nav_connected || !nav_channel) return;
    now = nav_clock_ms();
    if (!nav_ping_sent && !nav_deadline_reached(now, nav_ping_at)) return;
    if (!nav_ping_sent && nav_deadline_reached(now, nav_ping_at)) {
        nav_ping_sent = 1;
        if (!nav_write_frame(OP_PING, 0, conversation_id, NULL, 0)) return;
    }
    /* Ndless's syscall declaration passes the receive-size pointer through
     * the ABI as a uint32_t (the upstream NavNet header uses uint32_t *).
     * Match the SDK declaration used by os.h on the CX II. */
    /* Requested timeout is not a verified bound on this synchronous call. */
    status = NAV_OS_CALL(TI_NN_Read(nav_channel, NAV_READ_TIMEOUT, frame, sizeof(frame),
                        (uint32_t)&received));
    if (status < 0) {
        char detail[48];
        /* The public TI constants assign -1 to STORAGE_FULL, not timeout.
         * Never silently treat an undocumented negative result as no data. */
        set_status("read failed: %d", status);
        strncpy(detail, status_text, sizeof(detail) - 1);
        detail[sizeof(detail) - 1] = '\0';
        nav_disconnect(detail);
        return;
    }
    if (received > sizeof(frame)) {
        nav_disconnect("invalid receive size");
        return;
    }
    if (received) {
        handle_frame(frame, received);
    }
    now = nav_clock_ms();
    if (nav_handshake_pending && nav_ping_sent &&
        nav_deadline_reached(now, nav_connected_at + NAV_HANDSHAKE_TIMEOUT_MS)) {
        nav_disconnect("bridge handshake timeout");
    }
}

static void append_input(unsigned char ch) {
    if (ch < 32 || ch > 126 || input_len >= MAX_INPUT - 1) return;
    input_text[input_len++] = (char)ch;
    input_text[input_len] = '\0';
#ifdef NSPIRE_UI_NGC
    ngc_ui_dirty = 1;
#endif
}

#ifndef NSPIRE_UI_NGC
static void handle_key(SDLKey key, Uint16 unicode) {
    if (key == SDLK_ESCAPE) {
        done = SDL_TRUE;
        return;
    }
    if (key == SDLK_MENU) {
        set_status("Enter send | Ctrl+N new | Backspace erase | Esc exit");
        return;
    }
    if (key == SDLK_n && (SDL_GetModState() & KMOD_CTRL)) {
        reset_conversation();
        return;
    }
    if (key == SDLK_RETURN || key == SDLK_KP_ENTER) {
        (void)send_prompt();
        return;
    }
    if (key == SDLK_BACKSPACE || key == SDLK_DELETE) {
        if (input_len) input_text[--input_len] = '\0';
        return;
    }
    if (unicode >= 32 && unicode <= 126) {
        append_input((unsigned char)unicode);
    } else if (key >= SDLK_SPACE && key <= SDLK_z) {
        append_input((unsigned char)key);
    }
}

static void draw_ui(void) {
    SDL_Rect top = {0, 0, SCREEN_WIDTH, 28};
    SDL_Rect input_box = {0, SCREEN_HEIGHT - 25, SCREEN_WIDTH, 25};
    SDL_Rect divider = {0, 27, SCREEN_WIDTH, 1};
    SDL_Rect input_divider = {0, SCREEN_HEIGHT - 26, SCREEN_WIDTH, 1};
    Uint32 bg = SDL_MapRGB(screen->format, 245, 245, 245);
    Uint32 bar = SDL_MapRGB(screen->format, 35, 45, 60);
    Uint32 input_bg = SDL_MapRGB(screen->format, 225, 232, 240);
    Uint32 line = SDL_MapRGB(screen->format, 150, 160, 175);
    int first;
    int visible_lines = 16;
    int i;
    const char *visible_input = input_text;

    SDL_FillRect(screen, NULL, bg);
    SDL_FillRect(screen, &top, bar);
    SDL_FillRect(screen, &input_box, input_bg);
    SDL_FillRect(screen, &divider, line);
    SDL_FillRect(screen, &input_divider, line);
    nSDL_DrawString(screen, font, 6, 5, "NspireAI  [Ndless/NavNet]");
    nSDL_DrawString(screen, font, 6, 16, "%s", status_text);

    first = history_count - visible_lines;
    if (first < 0) first = 0;
    for (i = first; i < history_count; i++) {
        nSDL_DrawString(screen, font, 6, 34 + (i - first) * 10, "%s", history[i]);
    }
    if (input_len > 48) visible_input = input_text + input_len - 48;
    nSDL_DrawString(screen, font, 6, SCREEN_HEIGHT - 18, "> %s_", visible_input);
    SDL_Flip(screen);
}

static int init_ui(void) {
    /* nSDL on TI-Nspire ships with SDL_THREADS_DISABLED.  Requesting the
     * timer subsystem makes nSDL try to create its timer thread and abort
     * before the program reaches the UI ("SDL not configured with thread
     * support"). Video-only avoids that thread failure, but is NOT timer-
     * neutral: the linked SDL_InitSubSystem still calls SDL_StartTicks, which
     * reprograms timer 0x900C0000. USB coexistence remains unresolved. */
    if (SDL_Init(SDL_INIT_VIDEO) < 0) return 0;
    screen = SDL_SetVideoMode(SCREEN_WIDTH, SCREEN_HEIGHT, has_colors ? 16 : 8, SDL_SWSURFACE);
    if (!screen) return 0;
    font = nSDL_LoadFont(NSDL_FONT_TINYTYPE, 25, 35, 50);
    if (!font) return 0;
    SDL_ShowCursor(SDL_DISABLE);
    SDL_EnableUNICODE(1);
    SDL_EnableKeyRepeat(SDL_DEFAULT_REPEAT_DELAY, SDL_DEFAULT_REPEAT_INTERVAL);
    return 1;
}

int main(void) {
    if (!init_ui()) {
        SDL_Quit();
        return EXIT_FAILURE;
    }


    input_text[0] = '\0';
    set_status("USB unresolved; IRQ experiment reverted");
    nav_retry_at = nav_clock_ms() + NAV_INITIAL_DELAY_MS;

    while (!done) {
        SDL_Event event;
        while (SDL_PollEvent(&event)) {
            if (event.type == SDL_QUIT) {
                done = SDL_TRUE;
            } else if (event.type == SDL_KEYDOWN) {
                handle_key(event.key.keysym.sym, event.key.keysym.unicode);
            }
        }
        (void)nav_try_connect();
        nav_poll();
        draw_ui();
        SDL_Delay(20);
    }

    if (nav_channel) (void)NAV_OS_CALL(TI_NN_Disconnect(nav_channel));
    if (font) nSDL_FreeFont(font);
    SDL_Quit();
    return EXIT_SUCCESS;
}
#else
/* Uses the same protocol/session implementation above. Experimental backend,
 * not hardware approved; SDK abort still has an IRQ-enabled dialog path. */
#include "ui_ngc.h"
#endif
