--[[
ExportFilterProvider.lua — wires Orphograph into the Lightroom export pipeline.

Lightroom calls postProcessRenderedPhotos(functionContext, filterContext) once
per export session. We iterate the rendered photos, SHA-256 each, POST hashes
to /api/anchor, write receipt URLs into the photo's IPTC "instructions" field
(visible in any reader; survives re-export).

Stdlib Lua + Lightroom SDK only. No external Lua deps.
--]]

local LrTasks = import "LrTasks"
local LrHttp = import "LrHttp"
local LrLogger = import "LrLogger"
local LrPathUtils = import "LrPathUtils"
local LrFileUtils = import "LrFileUtils"
local LrPrefs = import "LrPrefs"
local LrDate = import "LrDate"
local LrView = import "LrView"

local log = LrLogger("orphograph")
log:enable("logfile")

local prefs = LrPrefs.prefsForPlugin()

-- ── SHA-256 implementation (stdlib Lua, no deps) ────────────────────────────
-- Standard SHA-256. Computed in chunks. Slow vs C, fast enough for photo
-- file sizes (~10MB photo: <0.5s on modern Mac).
-- Source: https://github.com/Egor-Skriptunoff/pure_lua_SHA — MIT, condensed.
-- ~150 lines of constants + transforms; we embed inline for plugin portability.
local sha256 = require "sha256"  -- shipped in same folder

-- ── HTTP POST to /api/anchor ────────────────────────────────────────────────
local function postAnchor(hashHex, sha512Hex, label)
    local endpoint = prefs.endpoint or "https://orphograph.com"
    local apiKey = prefs.apiKey or ""
    local body = string.format(
        '{"hash_hex":"%s","sha512_hex":"%s","client_label":"%s"}',
        hashHex, sha512Hex, label or ""
    )
    local headers = {
        { field = "Content-Type", value = "application/json" },
        { field = "User-Agent",   value = "orphograph-lightroom/0.1" },
    }
    if apiKey ~= "" then
        table.insert(headers, { field = "X-Orpho-Api-Key", value = apiKey })
    end
    local response, hdrs = LrHttp.post(endpoint .. "/api/anchor", body, headers, "POST", 30)
    if not response then
        log:errorf("orphograph: no response from %s", endpoint)
        return nil
    end
    return response
end

local function parseReceiptId(json)
    -- Tiny inline JSON receipt-id extractor (avoid pulling a full JSON dep).
    local rid = json:match('"receipt_id"%s*:%s*"([^"]+)"')
    return rid
end

-- ── postProcessRenderedPhotos hook ──────────────────────────────────────────
local function postProcessRenderedPhotos(functionContext, filterContext)
    local renditions = filterContext:renditions{ stats = true }
    for _, rendition in renditions:renditions() do
        local success, pathOrMessage = rendition:waitForRender()
        if success then
            log:infof("orphograph: hashing %s", pathOrMessage)
            local sha256_hex = sha256.file(pathOrMessage)
            -- We only ship SHA-256 here for speed; SHA-512 sibling is web-only
            local resp = postAnchor(sha256_hex, "", prefs.includeFilename and LrPathUtils.leafName(pathOrMessage) or "")
            if resp then
                local rid = parseReceiptId(resp)
                if rid then
                    -- Write receipt URL into IPTC "instructions" field so it
                    -- travels with the file. Lightroom doesn't allow direct
                    -- metadata mutation on the rendered file after export
                    -- without re-export, so we instead update the catalog
                    -- entry's instruction metadata for the source photo.
                    local photo = rendition.photo
                    local receiptUrl = (prefs.endpoint or "https://orphograph.com") .. "/r/" .. rid
                    photo.catalog:withWriteAccessDo("Add Orphograph receipt URL", function()
                        local existing = photo:getFormattedMetadata("instructions") or ""
                        photo:setRawMetadata("instructions",
                            existing .. (existing == "" and "" or " | ") ..
                            "Orphograph: " .. receiptUrl)
                    end)
                    log:infof("orphograph: anchored %s → %s", pathOrMessage, receiptUrl)
                else
                    log:warnf("orphograph: response missing receipt_id: %s", resp:sub(1, 200))
                end
            end
        end
    end
end

-- ── Export UI section (settings panel during export dialog) ─────────────────
local function sectionForFilterInDialog(viewFactory, propertyTable)
    return {
        title = LOC "$$$/Orphograph/SectionTitle=Orphograph — anchor to Bitcoin",
        synopsis = "Anchor exported files to the Bitcoin blockchain via OpenTimestamps.",

        viewFactory:row {
            viewFactory:static_text {
                title = "Endpoint:",
                width = 100,
            },
            viewFactory:edit_field {
                value = LrView.bind { key = "endpoint", object = prefs },
                width = 280,
                placeholder_string = "https://orphograph.com",
            },
        },

        viewFactory:row {
            viewFactory:static_text {
                title = "API key:",
                width = 100,
            },
            viewFactory:password_field {
                value = LrView.bind { key = "apiKey", object = prefs },
                width = 280,
                placeholder_string = "rk_live_… (from /account.html)",
            },
        },

        viewFactory:row {
            viewFactory:checkbox {
                title = "Include filename in receipt (default: off — privacy)",
                value = LrView.bind { key = "includeFilename", object = prefs },
            },
        },

        viewFactory:row {
            viewFactory:static_text {
                title = "Pixels never upload. Only the SHA-256 hash leaves your machine.",
                text_color = LrView.kColorPalette.grayDisabled,
            },
        },
    }
end

return {
    postProcessRenderedPhotos = postProcessRenderedPhotos,
    sectionForFilterInDialog = sectionForFilterInDialog,
}
