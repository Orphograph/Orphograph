# Orphograph Capture — capture-time provenance ($19 Creator tier)

**What it does:** watches your photo + document folders, computes SHA-256 of every new file locally (the file never uploads), anchors that hash to Bitcoin via Orphograph, and writes a receipt sidecar next to each file. Your portfolio becomes cryptographically timestamped as you make it.

**Tier:** $19/mo Creator (after free + Pack + Personal tiers ship). Get an API key from `https://orphograph.com/account.html`.

## What gets anchored, what doesn't

✓ Hash: SHA-256 + SHA-512 (computed locally)
✗ Filename: opt-in only (`--include-filename`)
✗ File bytes: **never uploaded** — only the hash leaves your machine
✓ Receipt: saved as `<your_photo>.orpho.json` next to the original

## Install (5 minutes)

```bash
# 1. Get an API key
open https://orphograph.com/account.html

# 2. Edit the launchd plist
cp ~/orphograph/capture/com.orphograph.capture.plist ~/Library/LaunchAgents/
sed -i '' "s/CHANGEME/$USER/g" ~/Library/LaunchAgents/com.orphograph.capture.plist
# Open it and paste your API key into ORPHO_API_KEY:
open -a TextEdit ~/Library/LaunchAgents/com.orphograph.capture.plist

# 3. Load it
launchctl load ~/Library/LaunchAgents/com.orphograph.capture.plist

# 4. Verify it's running
launchctl list | grep orphograph
tail -f ~/Library/Logs/orphograph-capture.out
```

## Default watch folders

- `~/Pictures` — your photo library
- `~/Desktop` — quick screen captures

Edit the `--watch` flags in the plist to add more (e.g., `~/Documents`, `~/Movies`).

## Default file types

Photos, videos, audio, and documents. To anchor everything regardless of extension, add `--all-extensions` to the plist `ProgramArguments`.

| Category | Extensions |
|---|---|
| Photos | jpg, jpeg, png, heic, heif, raw, nef, cr2, cr3, arw, dng, tiff, webp, gif |
| Video | mp4, mov, m4v, avi, mkv |
| Audio | mp3, m4a, wav, flac, aac |
| Documents | pdf, doc, docx, txt, md |

## CLI usage (foreground / testing)

```bash
# One scan pass, no daemon
python3 ~/orphograph/capture/orphograph_capture.py --watch ~/Pictures --once

# Foreground watch (Ctrl-C to stop)
python3 ~/orphograph/capture/orphograph_capture.py --watch ~/Pictures

# Status
python3 ~/orphograph/capture/orphograph_capture.py --status
```

## Stopping the daemon

```bash
launchctl unload ~/Library/LaunchAgents/com.orphograph.capture.plist
```

## Privacy + security model

Identical to the website's anchor flow:

1. **Files never leave your computer.** WebCrypto / hashlib only sees them.
2. **Only the 32-byte SHA-256 + 64-byte SHA-512 hashes leave.** Reconstructing the file from those is mathematically impossible.
3. **Filenames are off by default.** Pass `--include-filename` if you want them embedded in receipts (useful for portfolio matching; bad if filenames are sensitive).
4. **API key is a token, not a wallet.** Lose it, revoke it from `/account.html`, get a new one.

## State files

| File | Purpose |
|---|---|
| `~/Library/Application Support/Orphograph/seen.jsonl` | Append-only log of every file we've anchored (so we never anchor twice) |
| `~/Library/Application Support/Orphograph/capture.log` | Operational log |
| `~/Library/Logs/orphograph-capture.{out,err}` | launchd stdout/stderr |
| `<your_file>.orpho.json` | Receipt sidecar next to each anchored file |

## Verifying a receipt later

The `.orpho.json` sidecar contains the receipt URL. To verify offline:

```bash
# Pull the full receipt JSON (with .ots proof files):
curl https://orphograph.com/api/receipt/<receipt-id> > receipt.json

# Use the standalone verifier:
python3 ~/orphograph/marketplace/orphograph-plugin/skills/orphograph-verify/verify.py \
  receipt.json /path/to/your/original/file
```

Or use the in-app /verify path: drop the original file + the receipt JSON into the website. Receipt URL is in the sidecar.

## Architecture

This daemon is an independent clean rewrite. It uses only Orphograph's own `/api/anchor` endpoint — the same engine that powers the website's drop-zone flow.

## License

MIT. The capture daemon is shipped open-source so customers can audit what it does. The Creator-tier subscription is for the API access (rate limit bypass + verifier badge + custom branding), not the daemon code.
