-- ReceiptLink.lua — the receipt's public link (pure Lua, no Lightroom SDK).
--
-- The server's own receipt_url wins: behind a proxy or an internal endpoint
-- only the server knows its public address. It is taken only when it is a
-- plain http(s) link to /r/<this receipt id> (host, optional port, path
-- segments; no space, quote, "@", query or empty segment); anything else
-- falls back to the endpoint the plugin posted to. The same rule as the
-- Python clients' receipt_link.

local M = {}

local function served_ok(served, rid)
  if type(served) ~= "string" then return false end
  local suffix = "/r/" .. rid
  if #served <= #suffix or served:sub(-#suffix) ~= suffix then return false end
  local rest = served:match("^https?://(.+)$")
  if not rest then return false end
  if rest:find("//", 1, true) then return false end
  local host, port, path = rest:match("^([%w%.%-]+)(:?%d*)(/.*)$")
  if not host then return false end
  if port == ":" or #port > 6 then return false end
  if not path:match("^[%w%._~%%%-/]+$") then return false end
  return true
end

function M.link(base, rid, served)
  if type(rid) == "string" and rid:match("^[%w_%-]+$") and served_ok(served, rid) then
    return served
  end
  return (tostring(base or "https://orphograph.com"):gsub("/+$", "")) .. "/r/" .. tostring(rid)
end

return M
