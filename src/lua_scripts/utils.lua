-- utils.lua — Shared utility functions for Pokemon Agent Lua scripts
-- Provides JSON encoding/decoding, string helpers, and timestamp support

local utils = {}

-- JSON encoder: encodes Lua values to JSON strings
-- Handles nil, boolean, number, string, and table (array or object)
function utils.jsonEncode(val)
    local t = type(val)
    if t == "nil" then
        return "null"
    elseif t == "boolean" then
        return tostring(val)
    elseif t == "number" then
        -- Use integer representation when possible
        if val == math.floor(val) then
            return string.format("%d", val)
        end
        return tostring(val)
    elseif t == "string" then
        -- Escape special characters per JSON spec
        val = val:gsub('\\', '\\\\')
                  :gsub('"', '\\"')
                  :gsub('\n', '\\n')
                  :gsub('\r', '\\r')
                  :gsub('\t', '\\t')
        return '"' .. val .. '"'
    elseif t == "table" then
        -- Detect array vs object: array has consecutive integer keys starting at 1.
        -- Empty tables are treated as arrays (produces "[]" not "{}") because all
        -- our response arrays (party, badges, moves) should serialize as arrays.
        local isArray = (#val > 0) or (next(val) == nil)
        if isArray then
            local items = {}
            for _, v in ipairs(val) do
                table.insert(items, utils.jsonEncode(v))
            end
            return "[" .. table.concat(items, ",") .. "]"
        else
            local items = {}
            for k, v in pairs(val) do
                local key = utils.jsonEncode(tostring(k))
                local value = utils.jsonEncode(v)
                table.insert(items, key .. ":" .. value)
            end
            return "{" .. table.concat(items, ",") .. "}"
        end
    end
    return "null"
end

-- JSON decoder: parses incoming Python request messages
-- Only handles the limited set of fields Python sends us
-- (Full JSON parsing is not needed — we only decode known request formats)
function utils.jsonDecode(str)
    if not str then return {} end
    local result = {}

    -- Extract string fields: "key": "value"
    for key, value in str:gmatch('"([%w_]+)"%s*:%s*"([^"]*)"') do
        result[key] = value
    end

    -- Extract numeric fields: "key": 123
    for key, value in str:gmatch('"([%w_]+)"%s*:%s*(%d+)') do
        -- Only set if not already captured as string
        if result[key] == nil then
            result[key] = tonumber(value)
        end
    end

    -- Extract boolean fields: "key": true/false
    for key, value in str:gmatch('"([%w_]+)"%s*:%s*(true|false)') do
        if result[key] == nil then
            result[key] = (value == "true")
        end
    end

    -- Extract id specifically (numeric, possibly after type/timestamp fields)
    local id = str:match('"id"%s*:%s*(%d+)')
    if id then result.id = tonumber(id) end

    -- Extract nested buttons array: "buttons": ["A", "B"]
    local buttons_str = str:match('"buttons"%s*:%s*%[(.-)%]')
    if buttons_str then
        local buttons = {}
        for btn in buttons_str:gmatch('"([^"]+)"') do
            table.insert(buttons, btn)
        end
        result.buttons = buttons
    end

    return result
end

-- Returns an ISO-8601 UTC timestamp string
-- mGBA Lua doesn't have os.date by default; use a simple counter-based approach
-- Format: "2026-01-01T00:00:00Z" (approximate, based on script start time)
function utils.timestamp()
    -- Use os.time() if available, otherwise return a static placeholder
    local ok, t = pcall(os.time)
    if ok and t then
        local dt = os.date("!%Y-%m-%dT%H:%M:%SZ", t)
        if dt then return dt end
    end
    return "1970-01-01T00:00:00Z"
end

-- Trims leading and trailing whitespace from a string
function utils.trim(s)
    if not s then return "" end
    return s:match("^%s*(.-)%s*$")
end

-- Splits a string by a delimiter
function utils.split(str, sep)
    local result = {}
    local pattern = "([^" .. sep .. "]+)"
    for match in str:gmatch(pattern) do
        table.insert(result, match)
    end
    return result
end

return utils
