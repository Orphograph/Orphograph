# Orphograph for Adobe Lightroom

Anchor exported photos to the Bitcoin blockchain via OpenTimestamps — directly from Lightroom's export dialog. The pixel data never uploads.

## How it fits the workflow

1. Edit your photo in Lightroom as normal.
2. **File → Export** (or Cmd-Shift-E).
3. Under **Post-Process Actions**, enable **"Orphograph: Bitcoin anchor"**.
4. Export.
5. The receipt URL is written into the source photo's **IPTC Instructions** field — it stays with your catalog forever, viewable in the Library module's metadata panel.

## What gets transmitted

- ✓ SHA-256 hex (64 chars)
- ✗ Pixel data — never. Lightroom renders the export locally, the plugin hashes it locally, only the hash is POSTed to `/api/anchor`.
- ✓ Filename — only if you tick "Include filename in receipt" (default: off)
- ✓ Your API key — `X-Orpho-Api-Key` header, for the Creator-tier rate limit

## Install (3 minutes)

1. **Locate the plugin folder** in this directory: `orphograph.lrdevplugin/`
2. **Copy it** to:
   - macOS: `~/Library/Application Support/Adobe/Lightroom/Modules/`
   - Windows: `%APPDATA%\Adobe\Lightroom\Modules\`
3. **In Lightroom:** File → Plug-in Manager → Add → select the `orphograph.lrdevplugin` folder.
4. **In Plug-in Manager:** select **Orphograph** in the left sidebar, paste your API key (get one at https://orphograph.com/account.html).
5. Done. The export hook is available next time you export.

## Files in the bundle

| File | Purpose |
|---|---|
| `Info.lua` | Plugin manifest read by Lightroom on load |
| `PluginInfoProvider.lua` | Settings panel in Plug-in Manager (API key, endpoint) |
| `ExportFilterProvider.lua` | The export-time hook that hashes + anchors |
| `sha256.lua` | Pure-Lua SHA-256 (no LuaRocks; bundled inline) |

## Architecture

- The plugin runs **inside Lightroom's Lua sandbox**. No subprocess, no shell, no native binaries.
- HTTP is via Lightroom's `LrHttp` (Adobe's vetted HTTP client).
- File I/O is via Lightroom's `LrFileUtils` / `LrPathUtils`.
- No state is stored outside the catalog: receipts are written into the IPTC Instructions field of the source photo, so a Lightroom catalog backup IS an orphograph backup.

## Privacy posture (same as the website + Claude plugin)

| Data | Where it lives | Who sees it |
|---|---|---|
| Pixel data | Your computer only | Just you |
| SHA-256 hash | POSTed to orphograph.com over HTTPS | Orphograph server + the 5 OTS calendars |
| Filename | Only if you tick the box | Same as hash if included |
| Receipt URL | In the source photo's IPTC Instructions | Anyone you share the photo with (intended) |

## Testing locally

Before installing in production Lightroom:

```bash
# Run the orphograph server locally
nohup python3 ~/orphograph/server/app.py > ~/orphograph/logs/server.out 2>&1 &

# In the plugin's Plug-in Manager settings, set:
#   Endpoint: http://127.0.0.1:8989
#   API key: (any string for dev — server doesn't enforce in dev mode)

# Export one photo and check the IPTC Instructions field updates.
```

## Submitting to Adobe Add-Ons

This bundle is in `.lrdevplugin` (developer) format. To publish on the Adobe Add-Ons store:

1. Rename folder to `.lrplugin`
2. Sign with an Adobe developer cert
3. Submit at https://exchange.adobe.com/

For solo/manual install via the README above, the `.lrdevplugin` format works fine.

## Status

This is a **first-pass skeleton.** Tested for code structure. Lightroom-side testing requires Lightroom Classic 12+ on the founder's machine. Known TODOs:

- [ ] Add streaming SHA-256 (sha256.lua reads full file into memory; fine for ≤200MB photos, ouch for 4K video)
- [ ] Add visual progress indicator in the export progress bar
- [ ] Sidecar `.xmp` file with receipt URL (for users who don't want IPTC mutation)
- [ ] Sign the bundle for Adobe Add-Ons submission

## License

MIT. Plugin code is open-source. The Creator-tier subscription pays for orphograph.com API access, not the plugin itself.
