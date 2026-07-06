# Orphograph Watch Folder

A single-file "sealed folder" daemon. Point it at a directory and every new or
changed file gets hashed locally (SHA-256 + SHA-512) and anchored on
[Orphograph](https://orphograph.com). **Your files never leave your machine —
only the hashes are sent.**

Stdlib-only Python 3. No dependencies to install.

## Usage

```sh
python3 orphograph_watch.py ~/Documents/sealed              # free tier, 30s scans
python3 orphograph_watch.py ~/contracts --api-key YOUR_KEY  # subscription
python3 orphograph_watch.py ~/contracts --pack-token TOKEN  # prepaid pack
python3 orphograph_watch.py ~/contracts --once              # single pass (cron)
python3 orphograph_watch.py ~/contracts --once --dry-run    # show what it would do
```

Options: `--base URL` (default `https://orphograph.com`), `--interval SECONDS`
(default 30), `--once`, `--dry-run`.

### How it behaves

- Scans recursively. A file is only hashed once it has been **stable for a
  full interval** (mtime + size unchanged), so files are never hashed mid-write.
- Skips the `.orphograph/` state dir, hidden files/dirs, and zero-byte files.
- Receipts append to `<dir>/.orphograph/receipts.jsonl`
  (`{ts, path, sha256, receipt_id, receipt_url}` per line). View any receipt at
  `https://orphograph.com/r/<receipt_id>`.
- State lives in `<dir>/.orphograph/state.json`, so restarts never re-anchor
  files that haven't changed. A file whose timestamp changes but whose content
  is byte-identical is also not re-anchored.
- On HTTP 429 it backs off exactly as long as the server asks; on network
  errors it backs off exponentially (capped at 15 minutes). It never hammers
  the API. Ctrl-C exits cleanly.

## What a receipt proves — and what it does not

A receipt proves that a file with **exactly these bytes existed at the time it
was anchored**, and lets anyone verify that later against independent
timestamp calendars.

It does **not** prove who created the file, who owns it, or that its contents
are true, original, or legally valid. It is proof of existence and integrity
in time — nothing more, and we won't pretend otherwise.

## Free tier and limits

Anonymous use is limited to **3 anchors per day per IP**. If your folder
produces more than that, use a prepaid pack (`--pack-token`) or a subscription
API key (`--api-key`) from [orphograph.com](https://orphograph.com). When the
daemon hits the limit it logs the wait and resumes automatically — pending
files are anchored on a later scan, not dropped.

## Run it on a schedule

### cron (any Unix)

One pass every 10 minutes:

```cron
*/10 * * * * /usr/bin/python3 /path/to/orphograph_watch.py /path/to/sealed --once >> /tmp/orphograph_watch.log 2>&1
```

Note: with `--once`, a freshly written file may be picked up on the *next* run
(the stability debounce), which is the intended behavior.

### launchd (macOS)

Save as `~/Library/LaunchAgents/com.orphograph.watchfolder.plist`, then
`launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.orphograph.watchfolder.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>com.orphograph.watchfolder</string>
  <key>ProgramArguments</key>
  <array>
    <string>/usr/bin/python3</string>
    <string>/path/to/orphograph_watch.py</string>
    <string>/path/to/sealed</string>
    <string>--interval</string><string>60</string>
  </array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>/tmp/orphograph_watch.log</string>
  <key>StandardErrorPath</key><string>/tmp/orphograph_watch.log</string>
</dict>
</plist>
```
