/*
 * NspireAI resident Lua/NavNet module.
 *
 * This is a new page backend, not a copy of the removed file-exchange Lua
 * implementation.  The module is loaded by TI's Lua document runtime and
 * keeps all synchronous NavNet calls inside short timer/menu callbacks, where
 * the TI OS owns the scheduler.  It never enters the Ndless standalone Zehn
 * loader path.
 */
#include <os.h>
#include <nucleus.h>
#include <lauxlib.h>
#include <stdint.h>
#include <string.h>

/* Ndless keeps this Lua module resident; its crt still expects a fini hook. */
void _fini(void) __attribute__((weak));
void _fini(void) {}

#define SERVICE_ID 0x5001u
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
#define HEADER_SIZE 16u
#define FRAGMENT_HEADER_SIZE 10u
#define MAX_FRAME_PAYLOAD 224u
#define MAX_RESPONSE 4096u
#define MAX_REQUEST 65536u
#define READ_TIMEOUT 1u

static nn_ch_t channel;
static int connected;
static uint32_t next_request = 1;
static unsigned char response[MAX_RESPONSE];
static size_t response_len;
static size_t response_total;
static uint32_t response_id;
static uint16_t response_conversation;
static uint8_t response_opcode;
static uint16_t conversation = 1;

static void put_u16(unsigned char *p, uint16_t v) {
    p[0] = (unsigned char)(v >> 8);
    p[1] = (unsigned char)v;
}

static void put_u32(unsigned char *p, uint32_t v) {
    p[0] = (unsigned char)(v >> 24);
    p[1] = (unsigned char)(v >> 16);
    p[2] = (unsigned char)(v >> 8);
    p[3] = (unsigned char)v;
}

static uint16_t get_u16(const unsigned char *p) {
    return (uint16_t)(((uint16_t)p[0] << 8) | p[1]);
}

static uint32_t get_u32(const unsigned char *p) {
    return ((uint32_t)p[0] << 24) | ((uint32_t)p[1] << 16) |
           ((uint32_t)p[2] << 8) | p[3];
}

static void reset_response(void) {
    response_len = 0;
    response_total = 0;
    response_id = 0;
    response_conversation = 0;
    response_opcode = 0;
}

static int write_frame(uint8_t opcode, uint32_t id, uint16_t conv,
                       const unsigned char *payload, size_t length) {
    unsigned char frame[HEADER_SIZE + MAX_FRAME_PAYLOAD];
    int16_t status;
    if (!channel || !connected || length > MAX_FRAME_PAYLOAD) return 0;
    frame[0] = MAGIC0; frame[1] = MAGIC1; frame[2] = MAGIC2; frame[3] = MAGIC3;
    frame[4] = PROTOCOL_VERSION; frame[5] = opcode;
    put_u32(frame + 6, id); put_u16(frame + 10, conv); put_u32(frame + 12, (uint32_t)length);
    if (length) memcpy(frame + HEADER_SIZE, payload, length);
    status = (int16_t)TI_NN_Write(channel, frame, (uint32_t)(HEADER_SIZE + length));
    if (status < 0) {
        channel = NULL;
        connected = 0;
        return 0;
    }
    return 1;
}

static int connect_peer(lua_State *L) {
    nn_oh_t operation;
    nn_nh_t node = NULL;
    int16_t status;
    if (connected && channel) {
        lua_pushboolean(L, 1);
        lua_pushstring(L, "connected");
        return 2;
    }
    operation = TI_NN_CreateOperationHandle();
    if (!operation) {
        lua_pushboolean(L, 0); lua_pushstring(L, "operation unavailable"); return 2;
    }
    status = (int16_t)TI_NN_NodeEnumInit(operation);
    if (status < 0) {
        (void)TI_NN_DestroyOperationHandle(operation);
        lua_pushboolean(L, 0); lua_pushfstring(L, "enum init=%d", status); return 2;
    }
    status = (int16_t)TI_NN_NodeEnumNext(operation, &node);
    (void)TI_NN_NodeEnumDone(operation);
    (void)TI_NN_DestroyOperationHandle(operation);
    if (status < 0 || !node) {
        lua_pushboolean(L, 0); lua_pushfstring(L, "no peer (%d)", status); return 2;
    }
    status = (int16_t)TI_NN_Connect(node, SERVICE_ID, &channel);
    if (status < 0 || !channel) {
        channel = NULL;
        lua_pushboolean(L, 0); lua_pushfstring(L, "connect=%d", status); return 2;
    }
    connected = 1;
    if (!write_frame(OP_PING, 0, conversation, NULL, 0)) {
        lua_pushboolean(L, 0); lua_pushstring(L, "handshake write failed"); return 2;
    }
    lua_pushboolean(L, 1);
    lua_pushstring(L, "connected");
    return 2;
}

static int disconnect_peer(lua_State *L) {
    (void)L;
    if (channel) (void)TI_NN_Disconnect(channel);
    channel = NULL;
    connected = 0;
    reset_response();
    return 0;
}

static int send_request(lua_State *L) {
    size_t length;
    const char *text = luaL_checklstring(L, 1, &length);
    uint32_t id;
    size_t offset;
    if (!connected || !channel) return luaL_error(L, "bridge is not connected");
    if (length > MAX_REQUEST) return luaL_error(L, "request is too large");
    id = next_request++;
    if (next_request == 0) next_request = 1;
    if (length <= MAX_FRAME_PAYLOAD) {
        if (!write_frame(OP_REQUEST, id, conversation, (const unsigned char *)text, length))
            return luaL_error(L, "request write failed");
    } else {
        const size_t chunk = MAX_FRAME_PAYLOAD - FRAGMENT_HEADER_SIZE;
        for (offset = 0; offset < length; offset += chunk) {
            unsigned char payload[MAX_FRAME_PAYLOAD];
            size_t n = length - offset;
            if (n > chunk) n = chunk;
            payload[0] = OP_REQUEST; payload[1] = 0;
            put_u32(payload + 2, (uint32_t)length);
            put_u32(payload + 6, (uint32_t)offset);
            memcpy(payload + FRAGMENT_HEADER_SIZE, text + offset, n);
            if (!write_frame(OP_FRAGMENT, id, conversation, payload, FRAGMENT_HEADER_SIZE + n))
                return luaL_error(L, "fragment write failed");
        }
    }
    lua_pushinteger(L, (lua_Integer)id);
    return 1;
}

static int new_conversation(lua_State *L) {
    conversation++;
    if (conversation == 0) conversation = 1;
    reset_response();
    if (connected && !write_frame(OP_NEW, 0, conversation, NULL, 0)) {
        lua_pushboolean(L, 0); return 1;
    }
    lua_pushboolean(L, 1);
    return 1;
}

static int poll_response(lua_State *L) {
    unsigned char frame[HEADER_SIZE + MAX_FRAME_PAYLOAD];
    uint32_t received = 0;
    int16_t status;
    uint32_t id;
    uint16_t conv;
    uint32_t length;
    uint8_t opcode;
    if (!connected || !channel) { lua_pushnil(L); return 1; }
    status = (int16_t)TI_NN_Read(channel, READ_TIMEOUT, frame, sizeof(frame), (uint32_t)&received);
    if (status < 0) {
        channel = NULL;
        connected = 0;
        lua_pushnil(L);
        return 1;
    }
    if (received < HEADER_SIZE || received > sizeof(frame) ||
        frame[0] != MAGIC0 || frame[1] != MAGIC1 || frame[2] != MAGIC2 ||
        frame[3] != MAGIC3 || frame[4] != PROTOCOL_VERSION) {
        lua_pushnil(L); return 1;
    }
    opcode = frame[5]; id = get_u32(frame + 6); conv = get_u16(frame + 10);
    length = get_u32(frame + 12);
    if (length != received - HEADER_SIZE || length > MAX_FRAME_PAYLOAD) {
        lua_pushnil(L); return 1;
    }
    if (opcode == OP_PING) {
        (void)write_frame(OP_PONG, id, conv, (const unsigned char *)"PONG", 4);
        lua_pushstring(L, "ping");
        return 1;
    }
    if (opcode == OP_PONG) {
        lua_pushstring(L, "pong");
        return 1;
    }
    if (opcode == OP_RESPONSE || opcode == OP_ERROR) {
        if (id != response_id || conv != response_conversation) reset_response();
        if (response_len + length > MAX_RESPONSE) { reset_response(); lua_pushnil(L); return 1; }
        response_opcode = opcode;
        response_id = id;
        response_conversation = conv;
        memcpy(response + response_len, frame + HEADER_SIZE, length);
        response_len += length;
        lua_pushinteger(L, (lua_Integer)id);
        lua_pushboolean(L, opcode == OP_ERROR);
        lua_pushlstring(L, (const char *)response, response_len);
        reset_response();
        return 3;
    }
    if (opcode == OP_FRAGMENT && length >= FRAGMENT_HEADER_SIZE) {
        uint8_t original = frame[HEADER_SIZE];
        uint32_t total = get_u32(frame + HEADER_SIZE + 2);
        uint32_t offset = get_u32(frame + HEADER_SIZE + 6);
        size_t chunk = length - FRAGMENT_HEADER_SIZE;
        if ((original != OP_RESPONSE && original != OP_ERROR) || !total ||
            total > MAX_RESPONSE || offset != response_len ||
            chunk > total - offset || (offset && total != response_total) ||
            response_len + chunk > MAX_RESPONSE) {
            reset_response(); lua_pushnil(L); return 1;
        }
        if (!offset) { response_total = total; response_id = id; response_conversation = conv; response_opcode = original; }
        if (id != response_id || conv != response_conversation || original != response_opcode) {
            reset_response(); lua_pushnil(L); return 1;
        }
        memcpy(response + response_len, frame + HEADER_SIZE + FRAGMENT_HEADER_SIZE, chunk);
        response_len += chunk;
        if (response_len == response_total) {
            lua_pushinteger(L, (lua_Integer)response_id);
            lua_pushboolean(L, response_opcode == OP_ERROR);
            lua_pushlstring(L, (const char *)response, response_len);
            reset_response();
            return 3;
        }
    }
    lua_pushnil(L);
    return 1;
}

static int status(lua_State *L) {
    lua_pushstring(L, connected && channel ? "connected" : "disconnected");
    return 1;
}

static const luaL_Reg functions[] = {
    {"connect", connect_peer}, {"disconnect", disconnect_peer},
    {"send", send_request}, {"poll", poll_response},
    {"newConversation", new_conversation}, {"status", status},
    {NULL, NULL}
};

int main(void) {
    lua_State *L = nl_lua_getstate();
    if (!L) return 0;
    luaL_register(L, "nspire_ai_nav", functions);
    _exit(0);
}
