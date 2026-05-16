-- Info.lua — plugin metadata for Adobe Lightroom Classic.
-- Required by the Lightroom SDK. Tells Lightroom what menus / export hooks
-- this plugin registers.

return {
  LrSdkVersion = 11.0,
  LrSdkMinimumVersion = 6.0,

  LrToolkitIdentifier = "com.orphograph.lightroom",
  LrPluginName = "Orphograph",

  LrPluginInfoUrl = "https://orphograph.com",
  LrPluginInfoProvider = "InfoProvider.lua",

  -- Add an "Anchor selected photos to Bitcoin" item under the Library menu.
  LrLibraryMenuItems = {
    {
      title = "Anchor selected photo(s) to Bitcoin (Orphograph)…",
      file = "AnchorSelected.lua",
    },
    {
      title = "Configure Orphograph credentials…",
      file = "Configure.lua",
    },
  },

  -- Optional: surface the "Anchor on export" filter so users can hook
  -- the action into their export presets.
  LrExportFilterProvider = {
    title = "Orphograph — anchor on export",
    file = "ExportFilter.lua",
    id = "com.orphograph.lightroom.exportFilter",
  },

  VERSION = { major = 0, minor = 1, revision = 0, build = 1 },
}
