# Lightroom Plugin Spec — distribution wedge

**Why this matters:** Lightroom is the photographer's daily tool. Reaching photographers AT Lightroom Export removes every friction step between their workflow and Orphograph. The plugin folder at `~/orphograph/lightroom-plugin/orphograph.lrdevplugin/` is a working first-pass skeleton.

---

## Design

The plugin attaches to Lightroom's **Post-Process Actions** export pipeline. When a user exports a photo with "Orphograph: Bitcoin anchor" enabled, Lightroom invokes our `postProcessRenderedPhotos` hook, we:

1. Receive the rendered file path (the JPEG/PNG/RAW that just landed on disk)
2. SHA-256 it locally via embedded pure-Lua implementation
3. POST `{hash_hex, sha512_hex (empty), client_label}` to `/api/anchor`
4. Receive a receipt URL
5. Write the receipt URL into the source photo's IPTC **Instructions** field so it stays with the catalog forever

No pixel data leaves the machine. No third-party Lua libraries required. Plugin loads inside Lightroom's sandbox.

---

## File-by-file

| File | Lines | Role |
|---|---|---|
| `Info.lua` | ~25 | Plugin manifest — declares LrSdkVersion, the export filter, and the info provider |
| `PluginInfoProvider.lua` | ~55 | Settings panel inside Plug-in Manager (API key, endpoint, include-filename toggle) |
| `ExportFilterProvider.lua` | ~140 | The actual hook — runs on every export with the filter enabled |
| `sha256.lua` | ~100 | Pure-Lua SHA-256, no LuaRocks dependency |
| `README.md` | ~110 | Install + usage + testing notes |

Total: ~430 lines, all in the plugin bundle.

---

## Why pure-Lua SHA-256 (not a system call)

Lightroom plugins can't shell out to `shasum` without raising privilege prompts on every export. They CAN bundle pure-Lua. Pure-Lua SHA-256 is well-trodden territory — the implementation is straight from the FIPS 180-4 spec, 100 lines, MIT-licensable. Performance is ~50 MB/s on modern Apple Silicon; a 50MB RAW hashes in <1s.

For 4K video (multi-GB), this will be slow. The TODO list in `lightroom-plugin/README.md` calls out streaming SHA-256 as the next iteration — it's a 30-line patch.

---

## Why IPTC Instructions (not sidecar XMP)

IPTC Instructions is:

- Already supported by every photo viewer / catalog tool
- Editable inside Lightroom's Library module
- Travels with the photo when copied between catalogs
- Visible in metadata panels in Photos.app, Bridge, capture-one, etc.

Sidecar `.xmp` files would be more discoverable to some workflows but invisible to others. IPTC is more universal. The TODO calls out adding optional `.xmp` sidecar as a follow-up.

---

## Distribution paths

### Phase 1 — Founder-curated installs (now)

Drop the bundle in GitHub, link from `/blog/`, photographers in the launch posts install it manually. ~5-min install. No Adobe approval needed.

### Phase 2 — Adobe Add-Ons store submission (after first 50 installs)

Adobe Add-Ons (formerly Adobe Exchange) is the official discovery surface. Submission requires:

- Adobe developer account (free)
- Bundle renamed `.lrplugin` and signed
- Description, screenshots, support email
- Adobe review (typically 1-2 weeks)

Once published, photographers find it via Lightroom's built-in plugin search.

### Phase 3 — Bundled with Capture-time desktop app

The $19 Creator tier desktop app (`~/orphograph/capture/`) and this Lightroom plugin are complementary, not competing:

- **Capture-time daemon** anchors at the moment a file lands on disk (shutter press, screenshot, screen recording)
- **Lightroom plugin** anchors at the moment a file is exported (after editing)

A photographer running both gets: a receipt for the original RAW (proof of capture-time existence) AND a receipt for the final JPEG (proof of edit completion). Two receipts, one client conversation.

The Creator-tier signup flow should auto-recommend installing both.

---

## Marketing copy for plugin store

```
Title:        Orphograph — Bitcoin-Anchored Export
Subtitle:     Prove your photos predate AI training. Without lawyers.
Categories:   Workflow, Output / Export
Price:        Free (plugin) + orphograph.com subscription for high-volume use

Tagline:
  One checkbox at export. Every photo gets a Bitcoin-anchored proof-of-
  existence receipt. Pixels never upload — only the SHA-256 hash does.

Use cases:
  • Photographers building pre-AI-era portfolio evidence
  • Journalists timestamping source documents before publication
  • Wedding/event photographers proving delivery dates to clients
  • Stock photo originators establishing priority over their work

Privacy:
  • The image bytes never leave your computer
  • Filename inclusion is opt-in only
  • All your data is yours; receipts work even if Orphograph disappears
  • Open-source verifier at orphograph.com/verify/
```

---

## Lightroom version compatibility

| Lightroom version | Compatible? | Notes |
|---|---|---|
| Lightroom Classic 13.0+ | ✓ | Primary target (LrSdkVersion = 13.0) |
| Lightroom Classic 12.0-12.5 | ✓ | LrSdkMinimumVersion = 6.0 — broad fallback |
| Lightroom CC (cloud) | ✗ | No plugin support |
| Lightroom Mobile | ✗ | No plugin support; iOS Shortcuts integration is a separate effort |
| Lightroom 6 (perpetual) | ✓ | Last non-subscription version; many photographers still on it |

This skeleton targets the broadest possible install base (Lightroom 6 → Lightroom Classic 13+).

---

## Testing checklist (before Adobe submission)

- [ ] Install bundle via `~/Library/Application Support/Adobe/Lightroom/Modules/`
- [ ] Open Plug-in Manager, verify it appears + settings panel renders
- [ ] Paste local-server endpoint (`http://127.0.0.1:8989`) for dev testing
- [ ] Export a single JPEG with the post-process action enabled
- [ ] Verify receipt URL written to IPTC Instructions in Library module
- [ ] Verify the receipt resolves at the URL (open in browser)
- [ ] Verify 5/5 OTS calendars confirmed on the live receipt page
- [ ] Repeat with a 50MB RAW file — verify completion in <2s
- [ ] Repeat with a 200MB RAW + a 4K mp4 — verify graceful handling (may be slow; that's the streaming-SHA-256 TODO)
- [ ] Verify nothing leaks if the API key is invalid (auth failure should not stop the export)
- [ ] Stress test: export 50 photos in one batch; verify rate-limit handling

---

## What this skeleton is NOT

- **NOT a finished plugin.** It's a working starting point that needs Lightroom-side smoke testing. The founder doesn't have Lightroom installed for me to test against in this session.
- **NOT signed for Adobe Add-Ons.** That's a Phase 2 task.
- **NOT supporting Lightroom CC / Mobile.** Those don't have plugin APIs.
- **NOT a substitute for the Claude Code plugin or the capture-time desktop daemon.** Different surfaces, different distribution channels, complementary.

---

## Estimated effort to ship

| Phase | Effort | Owner |
|---|---|---|
| Lightroom-side smoke testing of this skeleton | ~2 hours | Founder (needs Lightroom Classic) |
| Bug fixes from smoke testing | ~2-4 hours | Lightroom-savvy contractor or me in a follow-up session |
| Adobe Add-Ons submission package | ~3 hours | Founder + me |
| Adobe review + iteration | ~1-2 weeks | Adobe |

Total wall-clock: ~3 weeks from skeleton to public availability. Plugin distribution begins informally via README install on day-1.
