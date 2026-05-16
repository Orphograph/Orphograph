-- OrphographAPI.lua — shared HTTP helper for anchoring file hashes via
-- the Orphograph public API. Used by AnchorSelected.lua + ExportFilter.lua.
--
-- Uses Lightroom SDK's LrHttp + LrMD5 (SDK doesn't ship SHA-256, so we
-- compute the digest by invoking the user's system `shasum` binary —
-- shipped on every macOS install and standard on Linux/Windows-WSL).
--
-- Privacy invariant: the file's bytes never leave the user's machine.
-- We hash locally and POST only the 64-char hex digest.

local LrHttp = import "LrHttp"
local LrTasks = import "LrTasks"
local LrFileUtils = import "LrFileUtils"
local LrPathUtils = import "LrPathUtils"
local LrPrefs = import "LrPrefs"
local LrLogger = import "LrLogger"

local prefs = LrPrefs.prefsForPlugin()
local log = LrLogger("Orphograph")
log:enable("logfile")

local API_BASE = "https://orphograph.com"

local OrphographAPI = {}

function OrphographAPI.sha256OfFile(path)
  -- Runs `shasum -a 256 <path>` via LrTasks.execute, captures stdout via
  -- a temp file (LrTasks.execute returns exit code only). On Windows we
  -- fall back to PowerShell Get-FileHash.
  local outFile = LrPathUtils.child(LrPathUtils.getStandardFilePath("temp"),
                                    "orpho_sha_" .. tostring(os.time()) .. ".txt")
  local cmd
  if WIN_ENV then
    cmd = string.format(
      'powershell -NoProfile -Command "Get-FileHash -Algorithm SHA256 \\"%s\\" | ' ..
      'Select-Object -ExpandProperty Hash" > "%s"', path, outFile)
  else
    cmd = string.format('shasum -a 256 "%s" > "%s"', path, outFile)
  end
  local code = LrTasks.execute(cmd)
  if code ~= 0 then
    log:errorf("sha256 command failed (exit %d) for %s", code, path)
    return nil, "sha256 command failed"
  end
  local content = LrFileUtils.readFile(outFile)
  LrFileUtils.delete(outFile)
  if not content then return nil, "could not read sha256 output" end
  -- shasum format: "<hash>  <filename>" — first 64 chars are the hex.
  -- PowerShell output is just the hash (uppercase).
  local hex = content:match("^%s*([%x]+)")
  if not hex or #hex < 64 then
    return nil, "unexpected hash format: " .. tostring(content):sub(1, 80)
  end
  return hex:sub(1, 64):lower()
end

function OrphographAPI.anchor(sha256_hex, clientLabel)
  local headers = {
    { field = "Content-Type", value = "application/json" },
  }
  if prefs.packToken and #prefs.packToken > 0 then
    table.insert(headers, { field = "X-Pack-Token", value = prefs.packToken })
  end
  if prefs.apiKey and #prefs.apiKey > 0 then
    table.insert(headers, { field = "X-Orpho-Api-Key", value = prefs.apiKey })
  end
  local body = string.format(
    '{"hash_hex":"%s","client_label":"%s"}',
    sha256_hex,
    (clientLabel or ""):gsub('"', '\\"'):sub(1, 200)
  )
  local response, respHeaders = LrHttp.post(API_BASE .. "/api/anchor", body, headers)
  if not response then
    return nil, "no response from server (offline?)"
  end
  -- Minimal JSON receipt-id extraction (no JSON parser in stock SDK).
  local receiptId = response:match('"receipt_id"%s*:%s*"([^"]+)"')
  if not receiptId then
    local err = response:match('"error"%s*:%s*"([^"]+)"') or "unknown error"
    return nil, err
  end
  return receiptId, nil
end

return OrphographAPI
