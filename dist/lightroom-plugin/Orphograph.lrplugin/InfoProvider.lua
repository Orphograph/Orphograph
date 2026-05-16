-- InfoProvider.lua — populates the plugin's Plugin Manager section.

local LrView = import "LrView"

return {
  sectionsForTopOfDialog = function(_, _)
    local f = LrView.osFactory()
    return {
      {
        title = "Orphograph",
        f:row {
          f:static_text {
            title = "Anchor your photo hashes to Bitcoin. Files never " ..
                    "leave your machine — only the SHA-256 digest is sent.",
            height_in_lines = 2,
            width = 500,
          },
        },
        f:row {
          f:static_text { title = "Site:" },
          f:static_text { title = "https://orphograph.com" },
        },
        f:row {
          f:static_text { title = "Docs:" },
          f:static_text { title = "https://orphograph.com/docs/api.html" },
        },
      },
    }
  end,
}
