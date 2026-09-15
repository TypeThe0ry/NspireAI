/*
 * Small Ndless Lua extension, based on Ndless's luaext sample.
 *
 * It never touches AI.tns. It only exchanges four ordinary files under
 * /documents/nspireai. The Mac side transfers those files with N-Link/libnspire.
 * The response ID is written last, making it the ready marker for the reader.
 */
#include <os.h>
#include <lauxlib.h>
#include <nucleus.h>
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

static int write_atomic(const char *tmp, const char *path, const char *data, size_t len) {
    FILE *f = fopen(tmp, "wb");
    size_t written;
    int close_result;
    if (!f) return 0;
    written = fwrite(data, 1, len, f);
    close_result = fclose(f);
    if (written != len || close_result != 0) {
        unlink(tmp);
        return 0;
    }
    unlink(path);
    return rename(tmp, path) == 0;
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
    if (!write_atomic(TEMP_BODY, REQUEST_BODY, body, body_len) ||
        !write_atomic(TEMP_ID, REQUEST_ID, id, id_len)) {
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

static const luaL_reg functions[] = {
    {"submit", submit},
    {"poll", poll_response},
    {"clear", clear_response},
    {NULL, NULL}
};

int main(void) {
    lua_State *L = nl_lua_getstate();
    if (!L) return 0;
    luaL_register(L, "nspire_ai", functions);
    _exit(0); /* resident Lua module; do not tear down the host state */
}
