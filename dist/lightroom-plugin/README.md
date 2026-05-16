# Orphograph — Adobe Lightroom Classic Plugin

Anchor your photo hashes to Bitcoin from inside Lightroom. Files never leave
your machine — only the 32-byte SHA-256 digest is sent.

## Install

1. Download or clone the `Orphograph.lrplugin` directory from this folder.
2. Open Lightroom Classic.
3. **File → Plug-in Manager… → Add**
4. Select the `Orphograph.lrplugin` directory.
5. Click **Done**.

The plugin appears under the **Library → Plug-in Extras** menu.

## Configure

**Library → Plug-in Extras → Configure Orphograph credentials…**

Set either a Pack token (`pk_…`) or an API key (`ok_…`) so anchors bypass
rate limits. Tokens are stored only in Lightroom's local preferences.

## Usage

### Anchor selected photos

1. Select one or more photos in the Library grid.
2. **Library → Plug-in Extras → Anchor selected photo(s) to Bitcoin (Orphograph)…**
3. Confirm. Each photo is hashed locally and the hash is posted to
   `https://orphograph.com/api/anchor`.
4. Receipt IDs are saved to each photo's **Title** field, prefixed
   `orpho:` (e.g., `orpho:r_abc123`).

### Anchor on export

Use the **Orphograph — anchor on export** post-process filter on any export
preset:

1. **File → Export…**
2. In the Post-Process Actions panel, double-click **Orphograph — anchor on export**.
3. Run your export. Each exported file is anchored and a sidecar
   `<name>.orpho.txt` is written next to it with the receipt ID + verify URL.

## Privacy invariants

- The plugin shells out to `shasum -a 256` (macOS/Linux) or PowerShell
  `Get-FileHash` (Windows) to compute the digest. **No file bytes are read
  into the plugin's network code.**
- The POST body contains only the 64-char hex hash + an optional
  filename label.
- Tokens are stored in `~/Library/Preferences/com.orphograph.lightroom/` (macOS)
  or the equivalent Windows path. Lightroom encrypts them at rest if the OS
  supports it.

## Source

Source is in the main Orphograph repo at
`dist/lightroom-plugin/Orphograph.lrplugin/`. Bug reports and PRs welcome at
the GitHub issue tracker.

## License

MIT.
