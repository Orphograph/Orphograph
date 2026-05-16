-- Configure.lua — credentials dialog for the Orphograph plugin.

local LrDialogs = import "LrDialogs"
local LrView = import "LrView"
local LrPrefs = import "LrPrefs"
local LrFunctionContext = import "LrFunctionContext"

local prefs = LrPrefs.prefsForPlugin()
local f = LrView.osFactory()

LrFunctionContext.callWithContext("configure", function(context)
  local props = LrView.bindingsForFunctionContext(context)
  props.packToken = prefs.packToken or ""
  props.apiKey = prefs.apiKey or ""

  local contents = f:column {
    spacing = f:control_spacing(),
    fill_horizontal = 1,
    f:row {
      f:static_text { title = "Pack token (optional):", width = 160 },
      f:edit_field {
        value = LrView.bind("packToken"),
        width_in_chars = 36,
        placeholder_string = "pk_…",
      },
    },
    f:row {
      f:static_text { title = "API key (subscribers):", width = 160 },
      f:edit_field {
        value = LrView.bind("apiKey"),
        width_in_chars = 36,
        placeholder_string = "ok_…",
      },
    },
    f:row {
      f:static_text {
        title = "Tokens are stored locally in Lightroom preferences and " ..
                "are only sent to orphograph.com when anchoring.",
        width = 480,
        height_in_lines = 2,
      },
    },
  }

  local result = LrDialogs.presentModalDialog {
    title = "Orphograph — credentials",
    contents = contents,
    actionVerb = "Save",
  }

  if result == "ok" then
    prefs.packToken = props.packToken
    prefs.apiKey = props.apiKey
    LrDialogs.message("Orphograph", "Credentials saved.", "info")
  end
end)
