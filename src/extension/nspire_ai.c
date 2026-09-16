/*
 * Small Ndless Lua extension, based on Ndless's luaext sample.
 *
 * It never touches AI.tns and does not use the TI document-transfer path for
 * the live AI channel.  The live path is a persistent Ndless NavNet channel;
 * the old file helpers remain below only for compatibility with old documents.
 */
#include <os.h>
#include <lauxlib.h>
#include <nucleus.h>
#ifndef _WIN32
#define __cdecl
#define __declspec(x)
#endif
#define TI_NN_SERVICE_ECHO 0x4002
#define TI_NN_SERVICE_EXTECHO 0x5000
/* 0x4051 is TI's built-in Message service.  It is not an application
 * transport and using it causes the OS to own the packet stream.  0x5001 is
 * a private service in the valid Mac NavNet range; the older nsocket example
 * used 0x8001, but the Mac NavNet implementation treats IDs with bit 15 set
 * as invalid signed service numbers.
 * after the USB address handshake.  This avoids TI_NN_NodeEnumInit(), which
 * is for calculator-to-calculator/host-stack enumeration and returns 1 when
 * the lightweight libnspire helper is not running a full NavNet stack. */
/* Use TI's supported message-service slot.  The Mac NavNet library rejects
 * arbitrary private service IDs on this Student Software build; 0x4051 is
 * the documented bidirectional message service and still gives us a single
 * in-memory connection while AI.tns stays open. */
#define TI_NN_SERVICE_AI 0x4051
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

#define AI_DIR "/documents/nspireai"
#define REQUEST_ID AI_DIR "/request.id.tns"
#define REQUEST_BODY AI_DIR "/request.tns"
#define RESPONSE_ID AI_DIR "/response.id.tns"
#define RESPONSE_BODY AI_DIR "/response.tns"
#define TEMP_ID AI_DIR "/.exchange.id.tmp"
#define TEMP_BODY AI_DIR "/.exchange.body.tmp"
#define MAX_ID 96
#define MAX_BODY (256 * 1024)

/* Newlib's static fini object expects this hook; Ndless's crt0 does not
 * provide a process-exit sequence for a resident Lua extension. */
void _fini(void) __attribute__((weak));
void _fini(void) {}

static void ensure_dir(void) {
    (void)mkdir(AI_DIR, 0777);
}

static int write_file(const char *path, const char *data, size_t len) {
    FILE *f = fopen(path, "wb");
    size_t written;
    int close_result;
    if (!f) return 0;
    written = fwrite(data, 1, len, f);
    close_result = fclose(f);
    if (written != len || close_result != 0) {
        unlink(path);
        return 0;
    }
    return 1;
}

static int read_alloc(const char *path, char **out, size_t *out_len, size_t max_len) {
    FILE *f = fopen(path, "rb");
    long size;
    char *buf;
    if (!f) return 0;
    if (fseek(f, 0, SEEK_END) != 0) { fclose(f); return 0; }
    size = ftell(f);
    if (size < 0 || (size_t)size > max_len) { fclose(f); return 0; }
    if (fseek(f, 0, SEEK_SET) != 0) { fclose(f); return 0; }
    buf = (char *)malloc((size_t)size + 1);
    if (!buf) { fclose(f); return 0; }
    if (size && fread(buf, 1, (size_t)size, f) != (size_t)size) {
        free(buf); fclose(f); return 0;
    }
    fclose(f);
    buf[size] = '\0';
    *out = buf;
    *out_len = (size_t)size;
    return 1;
}

static int submit(lua_State *L) {
    size_t id_len, body_len;
    const char *id = luaL_checklstring(L, 1, &id_len);
    const char *body = luaL_checklstring(L, 2, &body_len);
    if (id_len == 0 || id_len >= MAX_ID || body_len > MAX_BODY) {
        return luaL_error(L, "invalid request size");
    }
    ensure_dir();
    /* Body first, ID last: ID is the ready marker. */
    if (!write_file(REQUEST_BODY, body, body_len) ||
        !write_file(REQUEST_ID, id, id_len)) {
        return luaL_error(L, "cannot write request files");
    }
    lua_pushboolean(L, 1);
    return 1;
}

static int poll_response(lua_State *L) {
    char *id = NULL, *body = NULL;
    size_t id_len = 0, body_len = 0;
    if (!read_alloc(RESPONSE_ID, &id, &id_len, MAX_ID - 1)) return 0;
    if (!read_alloc(RESPONSE_BODY, &body, &body_len, MAX_BODY)) {
        free(id);
        return 0;
    }
    lua_pushlstring(L, id, id_len);
    lua_pushlstring(L, body, body_len);
    free(id);
    free(body);
    return 2;
}

static int clear_response(lua_State *L) {
    (void)L;
    unlink(RESPONSE_ID);
    unlink(RESPONSE_BODY);
    return 0;
}

/* Persistent NavNet state.  The Lua page opens this once and then only does
 * short Write/Read calls from menu/timer callbacks; no file is created.
 *
 * The calculator is the NavNet client.  TI's own NavNet examples expose the
 * custom service on the computer and call TI_NN_Connect() here; calling
 * TI_NN_StartService() on a CX II is not the client direction and returns
 * -281.  Keep the operation/node handles only for the short enumeration and
 * then retain the connection handle while AI.tns remains open. */
static volatile nn_ch_t nav_channel = NULL;
static volatile int nav_connected = 0;

static int nav_connect_host(char *detail, size_t detail_size) {
    nn_oh_t operation = NULL;
    nn_nh_t node = NULL;
    int16_t status;

    operation = TI_NN_CreateOperationHandle();
    if (!operation) {
        snprintf(detail, detail_size, "cannot create NavNet operation");
        return 0;
    }
    status = TI_NN_NodeEnumInit(operation);
    if (status < 0) {
        TI_NN_DestroyOperationHandle(operation);
        snprintf(detail, detail_size, "node enumeration init failed: %d", status);
        return 0;
    }
    status = TI_NN_NodeEnumNext(operation, &node);
    (void)TI_NN_NodeEnumDone(operation);
    (void)TI_NN_DestroyOperationHandle(operation);
    if (status < 0) {
        snprintf(detail, detail_size, "node enumeration failed: %d", status);
        return 0;
    }
    /* NavNet uses positive values for success/enumeration state.  In
     * particular, status 1 with a null node means that the host stack has not
     * advertised a node yet; it is a transient wait state, not a fatal USB
     * error. */
    if (!node) {
        snprintf(detail, detail_size, "waiting for Mac NavNet node (enum %d)", status);
        return 0;
    }

    status = TI_NN_Connect(node, TI_NN_SERVICE_AI, (nn_ch_t *)&nav_channel);
    if (status < 0 || !nav_channel) {
        snprintf(detail, detail_size, "service 0x4051 connect failed: %d", status);
        nav_channel = NULL;
        return 0;
    }
    nav_connected = 1;
    detail[0] = '\0';
    return 1;
}

static int nav_open(lua_State *L) {
    char detail[96];
    if (nav_connected) {
        lua_pushboolean(L, 1);
        return 1;
    }
    if (nav_connect_host(detail, sizeof(detail))) {
        lua_pushboolean(L, 1);
        return 1;
    }
    lua_pushboolean(L, 0);
    lua_pushstring(L, detail);
    return 2;
}

static int nav_send(lua_State *L) {
    size_t len;
    const char *payload = luaL_checklstring(L, 1, &len);
    int16_t status;
    if (!nav_connected) return luaL_error(L, "NavNet host is not connected");
    if (len > 4096) return luaL_error(L, "NavNet payload too large");
    status = TI_NN_Write(nav_channel, (void *)payload, (uint32_t)len);
    if (status < 0) return luaL_error(L, "NavNet write failed: %d", status);
    lua_pushboolean(L, 1);
    return 1;
}

static int nav_poll(lua_State *L) {
    unsigned char rx[4096];
    uint32_t received = 0;
    int16_t status;
    if (!nav_connected || !nav_channel) return 0;
    status = TI_NN_Read(nav_channel, 0, rx, sizeof(rx), (uint32_t)&received);
    if (status < 0 || received == 0) return 0;
    lua_pushlstring(L, (const char *)rx, received);
    return 1;
}

static int nav_close(lua_State *L) {
    (void)L;
    if (nav_channel) {
        TI_NN_Disconnect(nav_channel);
        nav_channel = NULL;
    }
    nav_connected = 0;
    return 0;
}

/* First native-transport probe. This does not touch the TI document store:
 * enumerate the Mac NavNet host and connect to the private service. */
static int nav_probe(lua_State *L) {
    char detail[96];
    if (nav_connected) {
        lua_pushliteral(L, "NavNet AI host connected");
        return 1;
    }
    if (!nav_connect_host(detail, sizeof(detail)))
        return luaL_error(L, "%s", detail);
    lua_pushliteral(L, "NavNet AI host connected");
    return 1;
}

static const luaL_reg functions[] = {
    {"submit", submit},
    {"poll", poll_response},
    {"clear", clear_response},
    {"nav_open", nav_open},
    {"nav_send", nav_send},
    {"nav_poll", nav_poll},
    {"nav_close", nav_close},
    {"nav_probe", nav_probe},
    {NULL, NULL}
};

int main(void) {
    lua_State *L = nl_lua_getstate();
    if (!L) return 0;
    luaL_register(L, "nspire_ai_nav", functions);
    _exit(0); /* resident Lua module; do not tear down the host state */
}
