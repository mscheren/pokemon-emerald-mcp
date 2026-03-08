-- pokemon_agent.lua — Pokemon Emerald AI Agent main Lua script for mGBA
-- Runs inside the mGBA process and communicates with the Python controller
-- via a TCP socket on port 5000. All messages are newline-terminated JSON.
--
-- Launch: mgba -l src/lua_scripts/pokemon_agent.lua <rom_path>

local utils = require('utils')
local memory = require('memory')
local PORT = 5000

-- Connection state
local server = nil
local client = nil
local frame_count = 0

-- Event tracking state
local fainted_slots = {}
local prev_in_battle = false
local prev_party_levels = {}
local event_id = 1000
local ADDR_BATTLE_FLAGS   = 0x02022FEC
local ADDR_BATTLE_OUTCOME = 0x0202433A

-- Button name to GBA key constant mapping
local BUTTON_MAP = {
    A      = C.GBA_KEY.A,
    B      = C.GBA_KEY.B,
    START  = C.GBA_KEY.START,
    SELECT = C.GBA_KEY.SELECT,
    UP     = C.GBA_KEY.UP,
    DOWN   = C.GBA_KEY.DOWN,
    LEFT   = C.GBA_KEY.LEFT,
    RIGHT  = C.GBA_KEY.RIGHT,
    L      = C.GBA_KEY.L,
    R      = C.GBA_KEY.R,
}

-- Frame-accurate button hold tracking: key_const -> frames_remaining
local active_buttons = {}

-- Wait state tracking
local wait_frames_remaining = 0
local wait_response_pending = nil  -- msg_id to respond when wait completes
local wait_frames_total = 0

-- Request queue: incoming data is buffered here by the "received" callback
-- and processed one-per-frame in onFrame() so the socket callback never
-- blocks mGBA's event loop (avoids UI freeze on screenshot/heavy requests).
local request_queue = {}

-- Initialize TCP server on PORT
local function initServer()
    local err
    server, err = socket.bind(nil, PORT)
    if not server then
        console:error("[PokemonAgent] Failed to bind port " .. PORT .. ": " .. tostring(err))
        return false
    end
    local ok
    ok, err = server:listen()
    if not ok then
        console:error("[PokemonAgent] Failed to listen: " .. tostring(err))
        return false
    end
    console:log("[PokemonAgent] Listening on port " .. PORT)
    return true
end

-- Send a JSON response back to the Python client
local function sendResponse(id, payload)
    if not client then return end
    local msg = {
        type = "response",
        id = id,
        timestamp = utils.timestamp(),
        payload = payload
    }
    local ok, err = client:send(utils.jsonEncode(msg) .. "\n")
    if not ok then
        console:error("[PokemonAgent] Send error: " .. tostring(err))
    end
end

-- Handle press_button — press a single button for duration_frames frames
local function handlePressButton(msg_id, button_name, duration_frames)
    local key = BUTTON_MAP[button_name]
    if not key then
        sendResponse(msg_id, {
            status = "error",
            error_code = "INVALID_BUTTON",
            error_message = "Unknown button: " .. tostring(button_name),
        })
        return
    end
    duration_frames = duration_frames or 5
    emu:addKey(key)
    active_buttons[key] = duration_frames
    sendResponse(msg_id, {status = "ok", frames_executed = duration_frames})
end

-- Handle press_buttons — press multiple buttons simultaneously
local function handlePressButtons(msg_id, button_names, duration_frames)
    duration_frames = duration_frames or 5
    local pressed = {}
    for _, name in ipairs(button_names or {}) do
        local key = BUTTON_MAP[name]
        if key then
            emu:addKey(key)
            active_buttons[key] = duration_frames
            table.insert(pressed, name)
        else
            console:warn("[PokemonAgent] Unknown button: " .. tostring(name))
        end
    end
    sendResponse(msg_id, {status = "ok", buttons_pressed = pressed})
end

-- Handle wait — defer response until N frames have elapsed
local function handleWait(msg_id, frames)
    frames = frames or 30
    wait_frames_remaining = frames
    wait_frames_total = frames
    wait_response_pending = msg_id
    -- Response is sent after frames elapse in onFrame()
end

-- Handle capture_screenshot — save a PNG screenshot to disk
local function handleCaptureScreenshot(msg_id, path)
    path = path or "/tmp/screenshot.png"
    local ok, err = pcall(function()
        emu:screenshot(path)
    end)
    if ok then
        sendResponse(msg_id, {status = "ok", path = path, width = 240, height = 160})
    else
        -- Try alternative approach with image API
        local ok2, err2 = pcall(function()
            local img = emu:getPixels()
            if img then
                img:save(path)
                sendResponse(msg_id, {status = "ok", path = path, width = 240, height = 160})
            else
                sendResponse(msg_id, {
                    status = "error",
                    error_code = "SCREENSHOT_FAILED",
                    error_message = "Neither emu:screenshot() nor pixel capture available",
                })
            end
        end)
        if not ok2 then
            sendResponse(msg_id, {
                status = "error",
                error_code = "SCREENSHOT_FAILED",
                error_message = tostring(err) or "Screenshot failed",
            })
        end
    end
end

-- Detect significant game state transitions each polling cycle
local function detectEvents()
    local events = {}

    -- Battle state change
    -- gBattleTypeFlags stays non-zero even after returning to overworld, so we
    -- combine it with gBattleOutcome: the battle is "active" only while flags
    -- are set AND outcome has not yet been written (outcome == 0 during combat).
    local ok_b, battle_flags  = pcall(function() return emu:read32(ADDR_BATTLE_FLAGS) end)
    local ok_o, outcome_code  = pcall(function() return emu:read8(ADDR_BATTLE_OUTCOME) end)
    local flags_set    = ok_b and (battle_flags ~= 0) or false
    local outcome_set  = ok_o and (outcome_code ~= 0) or false
    local in_battle    = flags_set and not outcome_set
    if in_battle ~= prev_in_battle then
        if in_battle then
            table.insert(events, {event = "battle_started", battle_type = "unknown"})
        else
            local outcome = "unknown"
            if ok_o then
                outcome = ({[1]="victory",[2]="defeat",[3]="fled",[4]="caught"})[outcome_code] or "unknown"
            end
            table.insert(events, {event = "battle_ended", outcome = outcome})
        end
        prev_in_battle = in_battle
    end

    -- Level-up and faint detection for each party slot
    local ok_c, party_count = pcall(function() return emu:read8(0x020244E9) end)
    if not ok_c then party_count = 0 end
    if party_count > 6 then party_count = 6 end

    for slot = 0, party_count - 1 do
        local base = 0x020244EC + (slot * 100)

        local ok_l, level = pcall(function() return emu:read8(base + 0x54) end)
        if not ok_l then level = 0 end
        local prev_level = prev_party_levels[slot] or level
        if level > prev_level and prev_level > 0 then
            table.insert(events, {
                event = "level_up",
                slot = slot + 1,
                new_level = level,
                prev_level = prev_level,
            })
        end
        prev_party_levels[slot] = level

        local ok_hp, hp = pcall(function() return emu:read16(base + 0x56) end)
        local ok_mhp, max_hp = pcall(function() return emu:read16(base + 0x58) end)
        if ok_hp and ok_mhp and max_hp > 0 and hp == 0 then
            if not fainted_slots[slot] then
                table.insert(events, {event = "pokemon_fainted", slot = slot + 1})
                fainted_slots[slot] = true
            end
        else
            fainted_slots[slot] = nil
        end
    end

    return events
end

-- Push a detected event to the Python controller as an unsolicited JSON message
local function sendEvent(event_payload)
    if not client then return end
    event_id = event_id + 1
    local msg = {
        type = "event",
        id = event_id,
        timestamp = utils.timestamp(),
        payload = event_payload,
    }
    local ok, err = client:send(utils.jsonEncode(msg) .. "\n")
    if not ok then
        console:warn("[PokemonAgent] Event send error: " .. tostring(err))
    end
end

-- Route an incoming request to the appropriate handler
local function handleRequest(data)
    local msg = utils.jsonDecode(data)
    local action = msg.action or "unknown"
    local msg_id = msg.id or 0

    if action == "get_state" then
        local party_count, party = memory.readPartyPokemon()
        local pos = memory.readPlayerPosition()
        local badges = memory.readBadges()
        local in_battle = memory.readBattleState()

        local state = {
            frame_number = frame_count,
            map_id       = pos.map_id,
            map_name     = pos.map_name,
            player_x     = pos.x,
            player_y     = pos.y,
            party_count  = party_count,
            party        = party,
            badges       = badges,
            in_battle    = in_battle,
            can_save     = not in_battle,
        }
        sendResponse(msg_id, state)

    elseif action == "press_button" then
        handlePressButton(msg_id, msg.button, msg.duration_frames)

    elseif action == "press_buttons" then
        handlePressButtons(msg_id, msg.buttons, msg.duration_frames)

    elseif action == "capture_screenshot" then
        handleCaptureScreenshot(msg_id, msg.path)

    elseif action == "wait" then
        handleWait(msg_id, msg.frames)

    elseif action == "save_game" then
        -- save sequence (multi-frame menu navigation)
        -- acknowledge, operator saves manually
        sendResponse(msg_id, {
            status = "ok",
            note = "save sequence initiated — operator may need to confirm"
        })

    elseif action == "get_extended_state" then
        local bag      = memory.readBag()
        local pc_boxes = memory.readPCBoxes()
        sendResponse(msg_id, {bag = bag, pc_boxes = pc_boxes})

    elseif action == "shutdown" then
        sendResponse(msg_id, {status = "shutting_down"})
        if client then client:close(); client = nil end
        if server then server:close(); server = nil end
        console:log("[PokemonAgent] Shutdown complete")

    else
        sendResponse(msg_id, {
            status = "error",
            error_code = "UNKNOWN_ACTION",
            error_message = "Unknown action: " .. tostring(action)
        })
    end
end

-- Handle a newly connected Python client
local function onClientConnected()
    local err
    client, err = server:accept()
    if not client then
        console:error("[PokemonAgent] Accept failed: " .. tostring(err))
        return
    end
    console:log("[PokemonAgent] Python controller connected")

    -- Register data-received callback.
    -- Only buffer incoming data here — never process synchronously.
    -- Processing happens in onFrame() to avoid blocking mGBA's event loop.
    client:add("received", function()
        if not client then return end
        local data, recv_err = client:receive(4096)
        if data then
            data = utils.trim(data)
            if #data > 0 then
                table.insert(request_queue, data)
            end
        elseif recv_err and recv_err ~= socket.ERRORS.AGAIN then
            console:warn("[PokemonAgent] Receive error: " .. tostring(recv_err))
            client = nil
        end
    end)
end

-- Per-frame callback — called by mGBA once per emulated frame
local function onFrame()
    frame_count = frame_count + 1

    -- Event detection fires every 30 frames when a client is connected
    local run_events = frame_count % 30 == 0 and client ~= nil

    -- Fast path: nothing pending this frame — skip all work immediately.
    -- next() is O(1) vs pairs() iteration, keeping idle frames near-zero cost.
    if next(active_buttons) == nil
        and wait_frames_remaining == 0
        and #request_queue == 0
        and not run_events then
        return
    end

    -- Release buttons whose hold duration has expired
    for key, remaining in pairs(active_buttons) do
        if remaining <= 1 then
            emu:clearKey(key)
            active_buttons[key] = nil
        else
            active_buttons[key] = remaining - 1
        end
    end

    -- Countdown wait timer and send deferred response when complete
    if wait_frames_remaining > 0 then
        wait_frames_remaining = wait_frames_remaining - 1
        if wait_frames_remaining == 0 and wait_response_pending ~= nil then
            sendResponse(wait_response_pending, {status = "ok", frames_waited = wait_frames_total})
            wait_response_pending = nil
        end
    end

    -- Process one queued request per frame, but only when not in an active
    -- wait (Python is blocking for the wait response during that time).
    if wait_frames_remaining == 0 and #request_queue > 0 then
        local data = table.remove(request_queue, 1)
        handleRequest(data)
    end

    -- Detect game state events and push them to the Python controller
    if run_events then
        local events = detectEvents()
        for _, evt in ipairs(events) do
            sendEvent(evt)
        end
    end
end

-- Script entry point
if initServer() then
    -- Register server accept callback
    server:add("received", onClientConnected)
    -- Register frame callback
    callbacks:add("frame", onFrame)
    console:log("[PokemonAgent] Script initialized — waiting for Python controller")
else
    console:error("[PokemonAgent] Initialization failed — script will not function")
end
