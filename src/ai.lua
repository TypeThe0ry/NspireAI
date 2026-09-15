-- AI page with the same native controls as ai-ui-demo.lua.
-- The optional nspire_ai extension only performs short local file operations;
-- all USB/API waiting happens on the Mac bridge, while this page polls from a
-- timer so the native editor remains interactive.

local input_editor
local answer_editor
local initialized = false
local pending_id = nil
local next_id = 1
local conversation_id = 0
local session_id = "s"
do
    local ok, ticks = pcall(function() return timer.getMilliSecCounter() end)
    if ok and ticks then session_id = session_id .. tostring(ticks) end
end
local canceled_ids = {}
local bridge = nil

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
    answer_editor:setText("AI\n\nBridge: not connected")
    answer_editor:setVisible(true)

    local ok, ext = pcall(function()
        nrequire "nspire_ai"
        return nspire_ai
    end)
    if ok then bridge = ext end
    initialized = true
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
        set_answer("AI\n\nBridge extension unavailable. Build/install nspire_ai.luax.tns first.")
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
    local request_id = session_id .. "-" .. tostring(conversation_id) .. "-" .. tostring(next_id)
    next_id = next_id + 1
    local ok, err = pcall(function() bridge.submit(request_id, question) end)
    if not ok then
        set_answer("AI\n\nSubmit failed: " .. tostring(err))
        return
    end
    pending_id = request_id
    set_answer("AI\n\nWaiting for Mac…\nRequest " .. request_id)
    timer.start(0.25)
end

local function cancel_request()
    if pending_id then
        canceled_ids[pending_id] = true
        pending_id = nil
        timer.stop()
        set_answer("AI\n\nCanceled. A late response will be ignored.")
    end
end

local function new_conversation()
    cancel_request()
    conversation_id = conversation_id + 1
    next_id = 1
    input_editor:setText("")
    set_answer("AI\n\nNew conversation")
end

local function menu_action(_, item)
    if item == "Send" then send_request()
    elseif item == "Cancel" then cancel_request()
    elseif item == "New conversation" then new_conversation()
    elseif item == "Connection status" then
        set_answer("AI\n\nMac bridge transport: file exchange\nUI remains on this page; polling is timer-driven.")
    end
end

toolpalette.register({
    {"AI", {
        {"Send", menu_action},
        {"Cancel", menu_action},
        {"New conversation", menu_action},
        "-",
        {"Connection status", menu_action}
    }}
})

function on.construction() ensure_editors() end
function on.create() ensure_editors() end
function on.resize() layout() end
function on.paint(_) end

function on.timer()
    if not bridge or not pending_id then return end
    local ok, response_id, response = pcall(function() return bridge.poll() end)
    if not ok then
        pending_id = nil
        timer.stop()
        set_answer("AI\n\nPoll failed: " .. tostring(response_id))
        return
    end
    if response_id and response and pending_id == response_id then
        pending_id = nil
        timer.stop()
        if not canceled_ids[response_id] then
            set_answer("AI\n\n" .. response)
        end
    end
end
