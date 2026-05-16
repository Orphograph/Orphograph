--[[
PluginInfoProvider.lua — the static info panel shown in Lightroom's
Plug-in Manager when the user selects "Orphograph". Doubles as the
place where the founder enters their API key persistently (vs. per-
export).
--]]
local LrView = import "LrView"
local LrPrefs = import "LrPrefs"
local prefs = LrPrefs.prefsForPlugin()

local function sectionsForTopOfDialog(viewFactory, propertyTable)
    return {
        {
            title = "Orphograph",
            synopsis = "Bitcoin-anchored file timestamping",

            viewFactory:row {
                viewFactory:static_text {
                    title = "Orphograph anchors each exported file's SHA-256 to Bitcoin via OpenTimestamps.\n" ..
                            "The file itself never uploads. Receipts live forever on the chain.\n\n" ..
                            "Get an API key (Creator tier) at https://orphograph.com/account.html",
                    height_in_lines = 5,
                    width = 480,
                },
            },

            viewFactory:row {
                viewFactory:static_text { title = "Endpoint:", width = 100 },
                viewFactory:edit_field {
                    value = LrView.bind { key = "endpoint", object = prefs },
                    width = 360,
                    placeholder_string = "https://orphograph.com",
                },
            },

            viewFactory:row {
                viewFactory:static_text { title = "API key:", width = 100 },
                viewFactory:password_field {
                    value = LrView.bind { key = "apiKey", object = prefs },
                    width = 360,
                    placeholder_string = "rk_live_...",
                },
            },

            viewFactory:row {
                viewFactory:checkbox {
                    title = "Include filename in receipt (default: off — privacy)",
                    value = LrView.bind { key = "includeFilename", object = prefs },
                },
            },
        },
    }
end

return {
    sectionsForTopOfDialog = sectionsForTopOfDialog,
}
