# Orphograph — Right-click to Anchor (Browser Extension)

Right-click any image, link, or media element and anchor its SHA-256 hash to
Bitcoin via [Orphograph](https://orphograph.com). The file itself never
leaves your machine — only the 32-byte hash is sent.

## Install (developer mode, Chrome / Brave / Edge)

1. Open `chrome://extensions/`
2. Toggle **Developer mode** (top right)
3. Click **Load unpacked**
4. Select this `browser-extension/` directory

The icon will appear in your toolbar. Right-click any image to test.

## Install (Firefox, temporary)

1. Open `about:debugging#/runtime/this-firefox`
2. Click **Load Temporary Add-on**
3. Select the `manifest.json` file
4. Extension is active until Firefox restarts

For permanent install, submit to AMO (addons.mozilla.org).

## Permissions explained

- `contextMenus` — to add the "Anchor with Orphograph" right-click entry
- `notifications` — to show "Anchored: receipt r_xxx" toast
- `storage` — to remember your Pack token / API key locally (never sent unless anchoring)
- `downloads` — to read file metadata of downloaded files
- `https://orphograph.com/*` — to POST the hash to the anchor API

## Privacy invariants

- The file is fetched by your browser, hashed locally via WebCrypto, and only
  the SHA-256 + SHA-512 digests are sent to Orphograph.
- Credentials (Pack token / API key) live in `chrome.storage.local` and are
  only sent to `orphograph.com` when you trigger an anchor.
- No analytics, no telemetry, no third-party scripts.

## Submitting to stores

- **Chrome Web Store:** $5 one-time developer fee, review ~3 days. Use
  `dist/browser-extension/` zipped.
- **Firefox AMO:** free, review 1–14 days. Use signed XPI.
- **Edge Add-ons:** free, review ~7 days.

## Source

Source is checked into the main Orphograph repo at `dist/browser-extension/`.
Bug reports and pull requests welcome.

## License

MIT. See `LICENSE` in the repo root.
