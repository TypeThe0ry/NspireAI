-- AI page with the same native controls as ai-ui-demo.lua.
-- Live transport is the Ndless NavNet channel exposed by nspire_ai_nav.luax.tns.
-- No request/response document is created.  The page only performs short
-- writes and zero-timeout reads; the native editor stays responsive.

local input_editor
local answer_editor
local initialized = false
local pending_id = nil
local next_id = 1
local conversation_id = 0
local request_seed = 1
do
    local ok, ticks = pcall(function() return timer.getMilliSecCounter() end)
    if ok and ticks then request_seed = math.floor(ticks) % 2147483647 end
end
local canceled_ids = {}
local bridge = nil
local channel_open = false
local BUILD_LABEL = "Ndless NavNet 0x4051"
local last_open_attempt = 0
local last_open_error = "waiting for Mac"

local OP_PING, OP_PONG = 1, 2
local OP_REQUEST, OP_RESPONSE, OP_ERROR = 3, 4, 5
local OP_CANCEL, OP_NEW = 6, 7
local OP_FRAGMENT = 8
local FRAME_PAYLOAD_MAX = 1200
local FRAGMENT_HEADER_SIZE = 10
local fragment_buffers = {}

local function be16(value)
    return string.char(math.floor(value / 256) % 256, value % 256)
end

local function be32(value)
    return string.char(
        math.floor(value / 16777216) % 256,
        math.floor(value / 65536) % 256,
        math.floor(value / 256) % 256,
        value % 256)
end

local function read16(value, offset)
    local a, b = string.byte(value, offset, offset + 1)
    if not a or not b then return nil end
    return a * 256 + b
end

local function read32(value, offset)
    local a, b, c, d = string.byte(value, offset, offset + 3)
    if not a or not b or not c or not d then return nil end
    return ((a * 256 + b) * 256 + c) * 256 + d
end

local function encode_frame(opcode, request_id, conv_id, payload)
    payload = payload or ""
    return "NSAI" .. string.char(1, opcode) .. be32(request_id) ..
        be16(conv_id) .. be32(#payload) .. payload
end

local function decode_frame(value)
    if not value or #value < 16 or string.sub(value, 1, 4) ~= "NSAI" then return nil end
    local version, opcode = string.byte(value, 5, 6)
    local request_id = read32(value, 7)
    local conv_id = read16(value, 11)
    local length = read32(value, 13)
    if version ~= 1 or not length or length ~= #value - 16 then return nil end
    return opcode, request_id, conv_id, string.sub(value, 17)
end

local function send_message(opcode, request_id, conv_id, payload)
    payload = payload or ""
    if #payload <= FRAME_PAYLOAD_MAX then
        bridge.nav_send(encode_frame(opcode, request_id, conv_id, payload))
        return
    end
    local chunk_size = FRAME_PAYLOAD_MAX - FRAGMENT_HEADER_SIZE
    local offset = 1
    while offset <= #payload do
        local chunk = string.sub(payload, offset, offset + chunk_size - 1)
        local fragment_payload = string.char(opcode, 0) .. be32(#payload) ..
            be32(offset - 1) .. chunk
        bridge.nav_send(encode_frame(OP_FRAGMENT, request_id, conv_id, fragment_payload))
        offset = offset + #chunk
    end
end

local function consume_frame(opcode, request_id, conv_id, payload)
    if opcode ~= OP_FRAGMENT then return opcode, payload end
    if #payload < FRAGMENT_HEADER_SIZE then return nil end
    local original_opcode = string.byte(payload, 1)
    local reserved = string.byte(payload, 2)
    local total = read32(payload, 3)
    local offset = read32(payload, 7)
    if not original_opcode or reserved ~= 0 or not total or not offset then return nil end
    local key = tostring(conv_id) .. ":" .. tostring(request_id)
    local state = fragment_buffers[key]
    if offset == 0 then
        state = {opcode = original_opcode, total = total, data = ""}
        fragment_buffers[key] = state
    end
    if not state or state.opcode ~= original_opcode or state.total ~= total or
       offset ~= #state.data then
        fragment_buffers[key] = nil
        return nil
    end
    state.data = state.data .. string.sub(payload, FRAGMENT_HEADER_SIZE + 1)
    if #state.data > state.total then
        fragment_buffers[key] = nil
        return nil
    end
    if #state.data == state.total then
        fragment_buffers[key] = nil
        return state.opcode, state.data
    end
    return nil
end

local function open_channel()
    if channel_open then return true end
    if not bridge or not bridge.nav_open then return false, "Ndless NavNet extension unavailable" end
    local ok, result, detail = pcall(function() return bridge.nav_open() end)
    if not ok then return false, tostring(result) end
    channel_open = result and true or false
    if channel_open then
        last_open_error = nil
        return true
    end
    last_open_error = tostring(detail or "NavNet connection pending")
    return false, last_open_error
end

local function set_answer(text)
    answer_editor:setText(text or "")
    platform.window:invalidate()
end

local function ensure_editors()
    if initialized then return end
    input_editor = D2Editor.newRichText()
    input_editor:setBorder(1)
    input_editor:setSelectable(true)
    input_editor:setText("")
    input_editor:setVisible(true)

    answer_editor = D2Editor.newRichText()
    answer_editor:setBorder(1)
    answer_editor:setReadOnly(true)
    answer_editor:setSelectable(true)
    answer_editor:setText("AI\n\n" .. BUILD_LABEL .. "\nExtension: loading")
    answer_editor:setVisible(true)

    local ok, ext = pcall(function()
        nrequire "nspire_ai_nav"
        return nspire_ai_nav
    end)
    if ok then
        bridge = ext
        -- Try once immediately; the timer keeps retrying so the Mac bridge may
        -- be started before or after this page without reopening the document.
        local opened, open_error = open_channel()
        if not opened then
            set_answer("AI\n\n" .. BUILD_LABEL .. "\nExtension: loaded\nService: " .. tostring(open_error))
        else
            set_answer("AI\n\n" .. BUILD_LABEL .. "\nExtension: loaded\nService: waiting for Mac")
        end
    else
        set_answer("AI\n\n" .. BUILD_LABEL .. "\nExtension load failed:\n" .. tostring(ext))
    end
    initialized = true
    timer.start(0.10)
end

local function layout()
    ensure_editors()
    local w = platform.window:width()
    local h = platform.window:height()
    local margin, gap = 8, 6
    local input_h = math.max(48, math.floor(h * 0.30))
    local answer_h = math.max(48, h - input_h - gap - (2 * margin))
    input_editor:move(margin, margin)
    input_editor:resize(math.max(1, w - (2 * margin)), input_h)
    input_editor:setWordWrapWidth(math.max(1, w - (2 * margin)))
    answer_editor:move(margin, margin + input_h + gap)
    answer_editor:resize(math.max(1, w - (2 * margin)), answer_h)
    answer_editor:setWordWrapWidth(math.max(1, w - (2 * margin)))
end

local function send_request()
    ensure_editors()
    if not bridge then
        set_answer("AI\n\nNdless NavNet extension unavailable. Reinstall nspire_ai_nav.luax.tns.")
        return
    end
    if pending_id then
        set_answer("AI\n\nA request is already waiting. Use Cancel first.")
        return
    end
    local question = input_editor:getText() or ""
    if question == "" then
        set_answer("AI\n\nEnter a question first.")
        return
    end
    local request_id = (request_seed + next_id) % 4294967295
    next_id = next_id + 1
    local opened, open_error = open_channel()
    if not opened then
        set_answer("AI\n\nConnect failed: " .. tostring(open_error))
        return
    end
    local frame = encode_frame(OP_REQUEST, request_id, conversation_id, question)
    local ok, err = pcall(function() send_message(OP_REQUEST, request_id, conversation_id, question) end)
    if not ok then
        set_answer("AI\n\nSubmit failed: " .. tostring(err))
        return
    end
    pending_id = request_id
    set_answer("AI\n\nWaiting for Mac…\nRequest " .. request_id)
end

local function cancel_request()
    if pending_id then
        canceled_ids[pending_id] = true
        if channel_open then
            pcall(function() send_message(OP_CANCEL, pending_id, conversation_id, "") end)
        end
        pending_id = nil
        set_answer("AI\n\nCanceled. A late response will be ignored.")
    end
end

local function new_conversation()
    cancel_request()
    conversation_id = conversation_id + 1
    if conversation_id > 65535 then conversation_id = 0 end
    next_id = 1
    if channel_open then
        pcall(function() send_message(OP_NEW, 0, conversation_id, "") end)
    end
    input_editor:setText("")
    set_answer("AI\n\nNew conversation")
end

local function usb_probe()
    ensure_editors()
    if not bridge or not bridge.nav_send then
        set_answer("AI\n\nNdless NavNet probe unavailable. Reinstall nspire_ai_nav.luax.tns, then reopen AI.tns.")
        return
    end
    local opened, result = open_channel()
    if not opened then
        set_answer("AI\n\nNdless NavNet probe failed:\n" .. tostring(result))
        return
    end
    local probe_id = (request_seed + next_id) % 4294967295
    next_id = next_id + 1
    pending_id = probe_id
    local ok, result = pcall(function()
        return send_message(OP_PING, probe_id, conversation_id, "PING")
    end)
    if ok then
        set_answer("AI\n\nNdless NavNet probe sent. Waiting for PONG…")
    else
        set_answer("AI\n\nNdless NavNet probe failed:\n" .. tostring(result))
    end
end

local function menu_action(_, item)
    if item == "Send" then send_request()
    elseif item == "Cancel" then cancel_request()
    elseif item == "New conversation" then new_conversation()
    elseif item == "USB NavNet probe" then usb_probe()
    elseif item == "Connection status" then
        set_answer("AI\n\nTransport: Ndless persistent NavNet\nDocuments: not used\nChannel: " ..
            (channel_open and "connected" or "not connected"))
    end
end

toolpalette.register({
    {"AI",
        {"Send", menu_action},
        {"Cancel", menu_action},
        {"New conversation", menu_action},
        {"USB NavNet probe", menu_action},
        "-",
        {"Connection status", menu_action}
    }
})

function on.construction() ensure_editors() end
function on.create() ensure_editors() end
function on.resize() layout() end
function on.paint(_) end

function on.timer()
    if not bridge then return end
    if not channel_open then
        local ok, now = pcall(function() return timer.getMilliSecCounter() end)
        if not ok or not now then now = last_open_attempt + 1000 end
        if now < last_open_attempt or now - last_open_attempt >= 1000 then
            last_open_attempt = now
            local opened = open_channel()
            if opened then
                set_answer("AI\n\n" .. BUILD_LABEL ..
                    "\nExtension: loaded\nService: connected")
            end
        end
        return
    end
    local ok, raw = pcall(function() return bridge.nav_poll() end)
    if not ok then
        channel_open = false
        set_answer("AI\n\nNavNet poll failed: " .. tostring(raw))
        return
    end
    if not raw then return end
    local opcode, response_id, response_conversation, response = decode_frame(raw)
    if not opcode then return end
    opcode, response = consume_frame(opcode, response_id, response_conversation, response)
    if not opcode then return end
    -- The Mac helper sends a harmless bootstrap PING while the page is
    -- opening.  Always answer it, even when there is no user request yet;
    -- this confirms the service is alive and stops bootstrap retries.
    if opcode == OP_PING then
        pcall(function()
            send_message(OP_PONG, response_id or 0,
                response_conversation or conversation_id, "PONG")
        end)
        return
    end
    if response_id and response and pending_id == response_id and
       response_conversation == conversation_id then
        pending_id = nil
        if not canceled_ids[response_id] then
            if opcode == OP_PONG then
                set_answer("AI\n\nNdless NavNet connected: " .. response)
            elseif opcode == OP_RESPONSE then
                set_answer("AI\n\n" .. response)
            elseif opcode == OP_ERROR then
                set_answer("AI\n\nBridge error: " .. response)
            end
        end
    end
end

function on.destroy()
    if bridge and bridge.nav_close then pcall(function() bridge.nav_close() end) end
end

-- Treat Enter in the native input editor like the Send action.  The editor
-- remains a real TI control; this only routes its return-key event.
function on.returnKey()
    if input_editor and input_editor:hasFocus() then
        send_request()
    end
end

function on.enterKey()
    if input_editor and input_editor:hasFocus() then
        send_request()
    end
end
