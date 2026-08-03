# Running the USB recorder as a daemon

`orphograph_usb.py` usage, flags, and the on-drive sidecar are documented in
[USB_RECORDER.md](USB_RECORDER.md) — this page only covers keeping it running
unattended. Ship-ready template: [`com.orphograph.usb.plist.template`](com.orphograph.usb.plist.template).

## `--once` vs daemon mode

The script has two shapes, and the daemon builds on the first:

- **`--once`** — one recursive pass, print counts as JSON, exit. If the volume
  isn't mounted it exits immediately (exit 2). Idempotent: the on-drive
  `index.jsonl` dedups by content hash, so re-running is always safe.
- **no `--once`** — a resident watch loop that polls every `--interval` seconds
  and waits across unmount/re-insert.

The packaged daemon is **scheduled `--once`, not the resident loop**: launchd
(or cron) runs a one-shot every 5 minutes. Each run checks for the drive, does
one pass if it's there, exits cleanly if it's not. This is deliberate — under
launchd `KeepAlive`, a `--volume` run with no stick inserted exits instantly
and becomes a crash-loop restarting forever. `StartInterval` is the correct
shape for plug-in-when-you-want hardware.

## macOS: launchd (recommended)

```bash
# 1. Fill in the template ($HOME is a placeholder — launchd does NOT expand
#    shell variables in plists, so substitute your real home directory):
sed "s|\$HOME|$HOME|g" capture/com.orphograph.usb.plist.template \
  > ~/Library/LaunchAgents/com.orphograph.usb.plist

# 2. Load it into your user session:
launchctl bootstrap gui/$UID ~/Library/LaunchAgents/com.orphograph.usb.plist

# 3. Verify it's loaded and see last exit status:
launchctl print gui/$UID/com.orphograph.usb | grep -E 'state|last exit'
```

To stop/remove: `launchctl bootout gui/$UID/com.orphograph.usb`. To reload
after editing the plist, bootout then bootstrap again.

Edit the template before installing if your paths differ: the script location
(`ProgramArguments`), the volume label (`ORPHOGRAPH`), and — for high-volume
drives — uncomment `ORPHOGRAPH_API_KEY`. Note the env var name: the recorder
reads `ORPHOGRAPH_API_KEY`, **not** `ORPHO_API_KEY`.

## macOS Sequoia fallback: IO error 5

On some Sequoia machines `launchctl bootstrap` fails with
`Bootstrap failed: 5: Input/output error` (a known launchd quirk, often after
OS updates; retrying rarely helps). Fall back to a plain `nohup` loop that
reproduces the same every-5-minutes one-shot:

```bash
nohup /bin/sh -c 'while :; do
  /usr/bin/python3 "$HOME/orphograph/capture/orphograph_usb.py" --volume ORPHOGRAPH --once \
    >> "$HOME/Library/Logs/orphograph-usb.out" 2>> "$HOME/Library/Logs/orphograph-usb.err"
  sleep 300
done' >/dev/null 2>&1 &
```

This survives terminal close but not reboot — add it to a login item, or retry
`launchctl bootstrap` after the next OS update. To stop it:
`pkill -f orphograph_usb.py`.

## Linux: cron

```bash
crontab -e   # add:
*/5 * * * * /usr/bin/python3 $HOME/orphograph/capture/orphograph_usb.py --volume ORPHOGRAPH --once >> $HOME/.local/state/orphograph-usb.log 2>&1
```

Same one-shot shape; cron expands `$HOME` in most crons, but if yours doesn't,
spell the paths out. Volume auto-detection looks under `/media/$USER`,
`/run/media/$USER`, `/media`, then `/mnt` — if your distro mounts elsewhere,
swap `--volume ORPHOGRAPH` for an explicit `--mount /path/to/usb`.

## Logs

- macOS (both launchd and the nohup fallback):
  `~/Library/Logs/orphograph-usb.out` (scan output, anchored/rate-limit lines)
  and `~/Library/Logs/orphograph-usb.err` (tracebacks, if any).
- Linux: wherever your crontab redirects (`~/.local/state/orphograph-usb.log`
  above).
- The drive itself is the durable record: `.orphograph/index.jsonl` on the
  stick shows exactly what's anchored/pending — check it any time with
  `python3 capture/orphograph_usb.py --mount /Volumes/MYUSB --status`, and
  audit the proofs offline with `python3 capture/verify_usb_bundle.py /Volumes/MYUSB`.

## Notes

- Free-tier anchoring is rate-limited; the recorder pauses cleanly on 429 and
  the next scheduled run retries `pending` rows — a big drive just takes more
  cycles (or an API key).
- Nothing here changes the privacy model: hashes leave the machine, bytes and
  (by default) filenames don't. See [USB_RECORDER.md](USB_RECORDER.md).
