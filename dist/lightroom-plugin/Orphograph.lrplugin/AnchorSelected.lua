-- AnchorSelected.lua — anchor every currently selected photo in the Library
-- to Bitcoin via Orphograph. Reads each photo's master file path, computes
-- SHA-256 locally, posts only the hash, then writes the receipt ID into the
-- photo's metadata (Title field).

local LrApplication = import "LrApplication"
local LrDialogs = import "LrDialogs"
local LrTasks = import "LrTasks"
local LrFunctionContext = import "LrFunctionContext"
local LrProgressScope = import "LrProgressScope"

local API = require "OrphographAPI"

LrFunctionContext.callWithContext("anchorSelected", function(context)
  LrTasks.startAsyncTask(function()
    local catalog = LrApplication.activeCatalog()
    local photos = catalog:getTargetPhotos()
    if not photos or #photos == 0 then
      LrDialogs.message("Orphograph", "No photos selected.", "info")
      return
    end

    local confirm = LrDialogs.confirm(
      "Anchor " .. tostring(#photos) .. " photo(s) to Bitcoin?",
      "Each file will be hashed locally (SHA-256). Only the 32-byte hash " ..
      "is sent to orphograph.com — the photo itself never leaves your " ..
      "machine. Receipt IDs will be written back to each photo's Title field.",
      "Anchor",
      "Cancel"
    )
    if confirm ~= "ok" then return end

    local progress = LrProgressScope({
      title = "Anchoring photos via Orphograph",
      functionContext = context,
    })
    progress:setCancelable(true)

    local succeeded, failed = 0, 0
    for i, photo in ipairs(photos) do
      if progress:isCanceled() then break end
      progress:setPortionComplete(i - 1, #photos)
      local path = photo:getRawMetadata("path")
      if not path then
        failed = failed + 1
      else
        progress:setCaption("Hashing " .. (path:match("[^/\\]+$") or path))
        local hex, hashErr = API.sha256OfFile(path)
        if not hex then
          failed = failed + 1
        else
          local label = photo:getFormattedMetadata("fileName") or ""
          local rid, postErr = API.anchor(hex, label)
          if rid then
            succeeded = succeeded + 1
            catalog:withWriteAccessDo("orphographReceipt", function()
              photo:setRawMetadata("title", "orpho:" .. rid)
            end)
          else
            failed = failed + 1
          end
        end
      end
    end
    progress:done()

    LrDialogs.message(
      "Orphograph anchoring complete",
      string.format("%d anchored · %d failed. Receipt IDs saved to each " ..
                    "photo's Title metadata (prefixed orpho:).", succeeded, failed),
      "info"
    )
  end)
end)
