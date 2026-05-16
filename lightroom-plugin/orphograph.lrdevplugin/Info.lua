--[[
Info.lua — Lightroom plugin manifest for Orphograph.

Anchors exported photos to Bitcoin via OpenTimestamps. The file's pixels never
upload — only the SHA-256 of the exported file leaves the user's machine.

Install:
  1. Copy this folder (orphograph.lrdevplugin) to:
     macOS: ~/Library/Application Support/Adobe/Lightroom/Modules/
     Win:   %APPDATA%\Adobe\Lightroom\Modules\
  2. Lightroom → File → Plug-in Manager → Add → select this folder
  3. Lightroom → File → Plug-in Manager → Orphograph → enter API key
  4. On export: enable "Orphograph: Bitcoin anchor" under Post-Process Actions
--]]

return {
    LrSdkVersion = 13.0,
    LrSdkMinimumVersion = 6.0,
    LrToolkitIdentifier = "com.orphograph.lightroom",
    LrPluginName = LOC "$$$/Orphograph/PluginName=Orphograph",
    LrPluginInfoUrl = "https://orphograph.com",
    VERSION = { major = 0, minor = 1, revision = 0, build = 1 },

    LrExportFilterProvider = {
        title = LOC "$$$/Orphograph/Anchor=Orphograph: Bitcoin anchor",
        file = "ExportFilterProvider.lua",
        id = "com.orphograph.lightroom.anchor",
    },

    LrPluginInfoProvider = "PluginInfoProvider.lua",
}
