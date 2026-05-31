# Orphograph USB provenance recorder

`orphograph_usb.py` — plug in a USB drive; every file on it is hashed locally and
anchored to Bitcoin, and the **proofs are written onto the drive itself** so they
travel with it.

## What it does

1. Watches a mounted USB volume (recursively).
2. For each file: computes SHA-256 + SHA-512 **locally** — the bytes never leave
   your machine, only the hashes do.
3. `POST`s the hash to `orphograph.com/api/anchor` (5 OpenTimestamps calendars → Bitcoin).
4. Writes the result into a `.orphograph/` folder **on the drive**:
   - `index.jsonl` — one line per file (sha256, relpath, receipt id + url, status)
   - `receipts/<id>.json` — the anchor response
   - `receipts/<id>/` — the **full verifiable bundle** (`receipt.json` + 5 `.ots`),
     so the proof verifies **offline** with `server/verify_cli.py` even if
     orphograph.com is gone.

Move the USB to another machine and its provenance moves with it.

## Usage

```bash
# auto-find a volume by label under /Volumes (macOS) or /media (Linux)
python3 capture/orphograph_usb.py --volume ORPHOGRAPH

# or point at the mount directly
python3 capture/orphograph_usb.py --mount /Volumes/MYUSB

# one pass (cron) / preview without anchoring
python3 capture/orphograph_usb.py --mount /Volumes/MYUSB --once
python3 capture/orphograph_usb.py --mount /Volumes/MYUSB --once --dry-run

# status of what's on the drive
python3 capture/orphograph_usb.py --mount /Volumes/MYUSB --status
```

Key flags: `--api-key` (paid pack/subscription, for high-volume drives — the free
tier is rate-limited), `--include-names` (send relative paths as the anchor label;
**off by default** for privacy), `--all-extensions` (anchor everything, not just the
media/doc set), `--no-proofs` (skip the on-drive `.ots` bundle download; the index
keeps the receipt URL for an on-demand fetch later).

## Verify a drive's proofs (offline, no service)

```bash
python3 server/verify_cli.py /Volumes/MYUSB/.orphograph/receipts/<id>/receipt.json
```

## Notes

- **No custom hardware required.** Works with any USB stick today; a branded drive
  is a packaging/fulfillment decision, not a dependency.
- Free-tier anchoring is rate-limited; a drive with many files needs a paid
  pack/subscription (`--api-key`). The daemon pauses cleanly on rate-limit and
  retries, and records `failed`/`pending` rows it can re-attempt.
- Stdlib only; self-contained so it can ship on the drive.
