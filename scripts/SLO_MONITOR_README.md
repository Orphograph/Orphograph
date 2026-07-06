# Orphograph SLO Monitor

`scripts/slo_monitor.py` produces dated operational evidence without using
GitHub, Fly, Stripe, Resend, or any deploy credential. It is meant to move the
monitoring/TRL gap forward while external accounts are unavailable.

It checks:

- public `/`, `/api/health`, `/api/config`, and `/api/stats`
- local `data/receipts` for pending Bitcoin pins older than the SLA
- local `data/upgrade_log.jsonl` freshness when pending receipts exist

It writes:

- JSONL evidence: `outbox/SLO_MONITOR.jsonl`
- optional markdown reports: `deploy/SLO_REPORTS/<timestamp>.md`

Run by hand:

```bash
python3 scripts/slo_monitor.py --write-report
```

Run without network access:

```bash
python3 scripts/slo_monitor.py --no-network --write-report
```

The report intentionally avoids customer emails, filenames, full receipt IDs,
and secret values. Public checks are skipped in `--no-network` mode so GitHub or
Fly suspension does not block local TRL evidence.

## Exit Codes

- `0`: no FAIL findings
- `1`: one or more FAIL findings
- `2`: monitor crashed before writing a usable run

Use `--fail-on-warn` if a scheduled runner should treat warning conditions as
hard failures.

## Suggested Cadence

During suspended GitHub/Fly access:

```bash
python3 scripts/slo_monitor.py --no-network --write-report
```

After public access is stable again:

```bash
python3 scripts/slo_monitor.py --write-report
```

Daily evidence is enough for TRL movement. A five-minute liveness loop already
belongs to `scripts/orphograph_watchdog.py`; this monitor is for slower,
auditable service-promise evidence.

## Optional launchd Install

The launchd template is `scripts/com.orphograph.slo_monitor.plist.template`.
Copy it manually, replace `REPLACE_ME_HOMEDIR`, and bootstrap it only when you
want the job scheduled:

```bash
cp scripts/com.orphograph.slo_monitor.plist.template \
   ~/Library/LaunchAgents/com.orphograph.slo_monitor.plist
launchctl bootstrap gui/$UID ~/Library/LaunchAgents/com.orphograph.slo_monitor.plist
launchctl enable        gui/$UID/com.orphograph.slo_monitor
launchctl kickstart -k  gui/$UID/com.orphograph.slo_monitor
```

No agent or script installs this automatically.
