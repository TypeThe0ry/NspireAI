-- New TI-native document page for NspireAI.
--
-- The page uses TI's D2Editor and toolpalette controls. USB work is delegated
-- to nspire_ai_nav, a resident Lua extension, and is only called from the
-- document's timer/menu callbacks. This is deliberately a new backend; it
-- does not restore the removed file-exchange Lua page or its artifacts.

local input_editor
local answer_editor
local initialized = false
local pending_id
local conversation = 1
local nav
local poll_started = false

local function invalidate()
    platform.window:invalidate()
end

local function show(text)
    answer_editor:setText(text or "")
    invalidate()
end

local function ensure()
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
    answer_editor:setText("NspireAI\n\nBridge: not connected")
    answer_editor:setVisible(true)

    initialized = true
end

local function load_nav()
    if nav then return true end
    local ok, extension = pcall(function()
        nrequire "nspire_ai_nav"
        return nspire_ai_nav
    end)
    if ok and extension then
        nav = extension
        return true
    end
    return false
end

local function layout()
    ensure()
    local w, h = platform.window:width(), platform.window:height()
    local margin, gap = 8, 6
    local input_h = math.max(48, math.floor(h * 0.30))
    local answer_h = math.max(48, h - input_h - gap - 2 * margin)
    input_editor:move(margin, margin)
    input_editor:resize(math.max(1, w - 2 * margin), input_h)
    input_editor:setWordWrapWidth(math.max(1, w - 2 * margin))
    answer_editor:move(margin, margin + input_h + gap)
    answer_editor:resize(math.max(1, w - 2 * margin), answer_h)
    answer_editor:setWordWrapWidth(math.max(1, w - 2 * margin))
end

local function connect()
    ensure()
    if not load_nav() then show("NspireAI\n\nNavNet extension unavailable") return end
    local ok, connected, status = pcall(function() return nav.connect() end)
    if not ok then show("NspireAI\n\nConnect failed: " .. tostring(connected)); return end
    if connected then
        show("NspireAI\n\nCONNECTED\n" .. tostring(status))
        if not poll_started then timer.start(0.25); poll_started = true end
    else
        show("NspireAI\n\nBridge not connected\n" .. tostring(status))
    end
end

local function send()
    ensure()
    if not load_nav() then show("NspireAI\n\nNavNet extension unavailable"); return end
    if pending_id then show("NspireAI\n\nA request is already waiting"); return end
    local question = input_editor:getText() or ""
    if question == "" then show("NspireAI\n\nEnter a question first"); return end
    if nav.status() ~= "connected" then show("NspireAI\n\nBridge not connected; choose Connect"); return end
    local ok, id = pcall(function() return nav.send(question) end)
    if not ok then show("NspireAI\n\nSend failed: " .. tostring(id)); return end
    pending_id = id
    show("NspireAI\n\nWaiting for Mac\nRequest " .. tostring(id))
end

local function new_conversation()
    ensure()
    pending_id = nil
    conversation = conversation + 1
    if nav then pcall(function() nav.newConversation() end) end
    input_editor:setText("")
    show("NspireAI\n\nNew conversation")
end

local function disconnect()
    if nav then pcall(function() nav.disconnect() end) end
    pending_id = nil
    show("NspireAI\n\nBridge disconnected")
end

local function menu_action(_, item)
    if item == "Connect" then connect()
    elseif item == "Send" then send()
    elseif item == "New conversation" then new_conversation()
    elseif item == "Disconnect" then disconnect()
    elseif item == "Connection status" then
        show("NspireAI\n\nBridge: " .. (nav and nav.status() or "extension unavailable"))
    end
end

toolpalette.register({{"AI", {
    {"Connect", menu_action},
    {"Send", menu_action},
    {"New conversation", menu_action},
    {"Disconnect", menu_action},
    "-",
    {"Connection status", menu_action}
}}})

function on.construction() ensure() end
function on.create() ensure() end
function on.resize() layout() end
function on.paint(_) end
function on.destroy() disconnect() end

function on.timer()
    if not nav or not pending_id then return end
    local ok, id, is_error, response = pcall(function() return nav.poll() end)
    if not ok then pending_id = nil; show("NspireAI\n\nPoll failed: " .. tostring(id)); return end
    if id and id == pending_id and response then
        pending_id = nil
        show((is_error and "NspireAI\n\nError: " or "NspireAI\n\n") .. response)
    end
end
