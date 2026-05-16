-- ExportFilter.lua — post-export filter that anchors each exported file.
-- Activate it on any export preset (File → Export → "Post-Process Actions").
-- The filter hashes each finished file and posts the hash to Orphograph,
-- saving the receipt-ID next to the file as <name>.orpho.txt.

local LrTasks = import "LrTasks"
local LrFileUtils = import "LrFileUtils"
local LrPathUtils = import "LrPathUtils"

local API = require "OrphographAPI"

return {
  shouldRenderPhoto = function(_, _) return true end,

  postProcessRenderedPhotos = function(functionContext, filterContext)
    local renditions = filterContext:renditions()
    for _, rendition in renditions:renditions() do
      local success, pathOrMsg = rendition:waitForRender()
      if success then
        local path = pathOrMsg
        local hex, hashErr = API.sha256OfFile(path)
        if hex then
          local rid, postErr = API.anchor(hex, LrPathUtils.leafName(path))
          if rid then
            local sidecar = path .. ".orpho.txt"
            local fh = io.open(sidecar, "w")
            if fh then
              fh:write("receipt_id: " .. rid .. "\n")
              fh:write("hash_sha256: " .. hex .. "\n")
              fh:write("source_file: " .. LrPathUtils.leafName(path) .. "\n")
              fh:write("verify_url: https://orphograph.com/r/" .. rid .. "\n")
              fh:close()
            end
          end
        end
      end
    end
  end,
}
