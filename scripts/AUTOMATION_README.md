# Daily Compliance + Auto-Anchor Automation

Three scheduled jobs that protect the founder's two hard editorial
rules, keep a Bitcoin-anchored provenance trail of every meaningful
repo state, and watch the public package registries for newly-published
``orphograph`` releases.

| Job | Script | Schedule | Plist template |
|---|---|---|---|
| Brand-rule compliance sweep | `scripts/compliance_scan.py` | Daily 09:00 local | `scripts/com.orphograph.compliance.plist.template` |
| Repo folder-anchor | `scripts/auto_anchor_repo.py` | Daily 23:55 local | `scripts/com.orphograph.auto_anchor.plist.template` |
| Publish-cascade watcher | `scripts/publish_watcher.py` | Every 30 min | `scripts/com.orphograph.publish_watcher.plist.template` |

Both jobs are stdlib-only Python. Neither binds a port. Neither logs
PII, leaf paths, or full receipt bodies.

## 1. What each job does

### Compliance scan
Walks the repo and flags two kinds of forbidden content:

- Other commercial companies named anywhere in any text file (founder rule).
- Dollar / valuation language on any non-private surface (founder rule).

A JSON report lands in `outbox/compliance_scan_<UTC-date>.json`. The
script exits non-zero if any **high-severity** hit is found, so the
launchd run is "noisy" exactly when it should be.

The report has three lists:

- `high_severity_hits` — third-party brand names. Each entry has
  `path`, `line`, `match`, `context_50_chars`.
- `low_severity_hits` — protocol / file-format references that share a
  brand name with a major tech vendor. Flagged for awareness only.
- `dollar_hits` — anything matching `$<digits>[KMB]`, `valuation`,
  `acquired for`, `raised $`, or `series A`.

Read the report with any JSON tool, e.g.:

```bash
jq '.high_severity_hits | length' outbox/compliance_scan_2026-05-20.json
jq '.dollar_hits[] | "\(.path):\(.line)  \(.match)"' outbox/compliance_scan_2026-05-20.json
```

### Auto-anchor
Hashes the meaningful repo state into one folder-Merkle root via
`server/merkle.py` and submits it to `/api/anchor_folder` as a
**private** anchor. Every successful run appends one JSON line to
`outbox/AUTO_ANCHOR_HISTORY.jsonl`:

```json
{"receipt_id":"...","root_hex":"...","calendars_ok":5,"anchored_at_utc":"2026-05-20T23:55:00+00:00","git_sha":"abc1234"}
```

Without an API key the script anchors under the free tier (3/day/IP)
and self-throttles. With `ORPHO_AUTO_ANCHOR_KEY` set, the anchor lands
in the founder's subscription vault and there is no rate limit.

### Publish-cascade watcher

Polls `https://pypi.org/pypi/orphograph/json` and
`https://registry.npmjs.org/orphograph` once per launchd tick (every 30
minutes). The first time a new version is observed the watcher:

- Downloads the artefact files listed in the registry response (wheel
  + sdist for PyPI, tarball for npm).
- Hashes them, builds a folder-Merkle root via `server/merkle.py`, and
  POSTs the manifest to `/api/anchor_folder` with `private: true`.
- Appends one JSONL line to `outbox/PUBLISH_STATE_PYPI.json` or
  `outbox/PUBLISH_STATE_NPM.json` with the version, file names, file
  SHA-256s, manifest root, and anchor receipt id.
- Updates `outbox/HOMEPAGE_BADGES.json` with the version + install
  hint + receipt id so a downstream consumer (the homepage renderer)
  can pick up the badge without redeploying the script.

Idempotent: rerunning in the same minute is safe because both state
files dedupe by version. The watcher always exits 0 — a transient
network failure must not disable the launchd job. Errors are logged to
stderr (which launchd captures into the log file below).

Install:

```bash
cp scripts/com.orphograph.publish_watcher.plist.template \
   ~/Library/LaunchAgents/com.orphograph.publish_watcher.plist
# Edit the plist: replace every REPLACE_ME_HOMEDIR with your macOS
# short username; optionally set ORPHO_AUTO_ANCHOR_KEY.
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.orphograph.publish_watcher.plist
launchctl enable    gui/$(id -u)/com.orphograph.publish_watcher
```

Disable temporarily:

```bash
launchctl disable gui/$(id -u)/com.orphograph.publish_watcher
```

Uninstall:

```bash
launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/com.orphograph.publish_watcher.plist
rm ~/Library/LaunchAgents/com.orphograph.publish_watcher.plist
```

State files (all under `outbox/`):

- `PUBLISH_STATE_PYPI.json` — JSONL, one record per PyPI release.
- `PUBLISH_STATE_NPM.json` — JSONL, one record per npm release.
- `HOMEPAGE_BADGES.json` — single JSON object, last-write-wins per registry.

Log: `~/Library/Logs/orphograph_publish_watcher.log`.

## 2. Install (manual; not done by any agent)

1. Copy the plist into LaunchAgents:
   ```bash
   cp scripts/com.orphograph.compliance.plist.template \
      ~/Library/LaunchAgents/com.orphograph.compliance.plist
   cp scripts/com.orphograph.auto_anchor.plist.template \
      ~/Library/LaunchAgents/com.orphograph.auto_anchor.plist
   ```
2. In each copied plist, replace:
   - `REPO_ROOT` with the absolute repo path
     (e.g. `/Users/<you>/orphograph`).
   - `USERNAME_HERE` with your macOS short username.
   - For `com.orphograph.auto_anchor.plist`: optionally set the
     `ORPHO_AUTO_ANCHOR_KEY` value to your account API key.
3. Load with launchd:
   ```bash
   launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.orphograph.compliance.plist
   launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.orphograph.auto_anchor.plist
   launchctl enable    gui/$(id -u)/com.orphograph.compliance
   launchctl enable    gui/$(id -u)/com.orphograph.auto_anchor
   ```
4. Verify:
   ```bash
   launchctl list | grep com.orphograph
   ```

**Sequoia caveat**: `launchctl bootstrap` sometimes returns `IO error 5`
on macOS 15. If that happens, fall back to a nohup Python sleeper loop:

```bash
nohup python3 -c "
import subprocess, time
while True:
    subprocess.call(['python3','/ABS/PATH/scripts/compliance_scan.py'])
    time.sleep(86400)
" > ~/Library/Logs/orphograph_compliance.log 2>&1 &
```

(Substitute the auto-anchor script for the equivalent loop.) The
scripts themselves do not depend on launchd.

## 3. Disable / uninstall

Disable temporarily without removing the plist:

```bash
launchctl disable gui/$(id -u)/com.orphograph.compliance
launchctl disable gui/$(id -u)/com.orphograph.auto_anchor
```

Or comment out the `<key>StartCalendarInterval</key>` block in the
plist and reload.

Fully uninstall:

```bash
launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/com.orphograph.compliance.plist
launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/com.orphograph.auto_anchor.plist
rm ~/Library/LaunchAgents/com.orphograph.compliance.plist
rm ~/Library/LaunchAgents/com.orphograph.auto_anchor.plist
```

## 4. Where logs live

- `~/Library/Logs/orphograph_compliance.log` — daily compliance run stdout/stderr.
- `~/Library/Logs/orphograph_auto_anchor.log` — daily anchor run stdout/stderr.
- `outbox/compliance_scan_<UTC-date>.json` — structured compliance report.
- `outbox/AUTO_ANCHOR_HISTORY.jsonl` — append-only anchor receipt log.

## 5. Verifying an auto-anchor receipt

Each row in `AUTO_ANCHOR_HISTORY.jsonl` carries the receipt id and the
folder root. Verify either against the office's open verifier:

```bash
# Public CLI verifier (ships in scripts/):
python3 scripts/all_endpoints_probe.py  # sanity-pings the API surface

# Or use the open verifier UI at https://orphograph.com/verify
# Paste the receipt_id; it shows the Bitcoin attestation block.
```

Because the anchor is private, the receipt body itself is only fetchable
when authenticated as the founder (session cookie or API key). The
`root_hex` printed in the JSONL is the public part — the Bitcoin
attestation binds it permanently, and the folder leaves can be rebuilt
locally any time with `python3 scripts/auto_anchor_repo.py` against the
same source tree to confirm the root matches.

## 6. Running by hand

```bash
# Dry run the compliance scanner against the working tree:
python3 scripts/compliance_scan.py

# Same, but write to a custom path:
python3 scripts/compliance_scan.py --out /tmp/scan.json

# Single auto-anchor without scheduling:
python3 scripts/auto_anchor_repo.py
```

Tests live at `tests/test_compliance_scan.py` and
`tests/test_auto_anchor.py`. Neither test reaches the network or
touches the live repo.
