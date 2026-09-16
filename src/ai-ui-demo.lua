-- AI-ui-demo: native TI-Nspire document UI proof of concept.
--
-- This script deliberately does not draw a chat application. The document
-- surface and both text areas are TI's D2Editor controls; the command menu is
-- registered with toolpalette. The first milestone is completely offline.

local input_editor
local answer_editor
local initialized = false
local demo_answer = "Mac received: (no question yet)"

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
    answer_editor:setText("AI demo\n\nEnter a question, then choose AI > Send.\n" .. demo_answer)
    answer_editor:setVisible(true)

    initialized = true
end

local function layout()
    ensure_editors()
    local w = platform.window:width()
    local h = platform.window:height()
    local margin = 8
    local gap = 6
    local input_h = math.max(48, math.floor(h * 0.30))
    local answer_h = math.max(48, h - input_h - gap - (2 * margin))

    input_editor:move(margin, margin)
    input_editor:resize(math.max(1, w - (2 * margin)), input_h)
    input_editor:setWordWrapWidth(math.max(1, w - (2 * margin)))

    answer_editor:move(margin, margin + input_h + gap)
    answer_editor:resize(math.max(1, w - (2 * margin)), answer_h)
    answer_editor:setWordWrapWidth(math.max(1, w - (2 * margin)))
end

local function send_demo()
    ensure_editors()
    local question = input_editor:getText() or ""
    if question == "" then
        answer_editor:setText("AI demo\n\nPlease enter a question first.")
        return
    end
    demo_answer = "Mac received: " .. question
    answer_editor:setText("AI demo\n\n" .. demo_answer)
    platform.window:invalidate()
end

local function new_conversation()
    ensure_editors()
    input_editor:setText("")
    answer_editor:setText("AI demo\n\nNew conversation")
    platform.window:invalidate()
end

local function menu_action(_, item)
    if item == "Send" then
        send_demo()
    elseif item == "New conversation" then
        new_conversation()
    end
end

-- Register once, outside paint. TI owns the actual menu rendering.
toolpalette.register({
    {"AI", {"Send", menu_action}, {"New conversation", menu_action}}
})

function on.construction()
    ensure_editors()
end

function on.create()
    ensure_editors()
end

function on.resize()
    layout()
end

function on.paint(_)
    -- D2Editor paints itself. No canvas or fake toolbar is used here.
end
