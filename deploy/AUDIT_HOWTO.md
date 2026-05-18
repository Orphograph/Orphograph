# Biweekly safety audit — HOWTO

Re-audits the class of issues closed in the 2026-05-18 premortem
(`deploy/SAFETY_GAPS_2026_05_18.md`, `deploy/HMAC_SECRET_AUDIT_2026_05_18.md`).

- Script: `scripts/biweekly_safety_audit.py` — pure stdlib, read-only
- Launchd template: `scripts/com.orphograph.audit.plist.template`
- Reports land in: `deploy/AUDIT_REPORTS/YYYY-MM-DD.md`

No email, no Telegram, no push. Founder reads the file when they want.

---

## Manual run (ad-hoc)

From the repo root:

```sh
python3 scripts/biweekly_safety_audit.py
```

Exits `0` if all checks pass, `1` if any check fails. The path of the
generated report is printed on the last line.

Re-running is idempotent — same date overwrites yesterday's report only if
run twice on the same UTC day (file is named `YYYY-MM-DD.md`).

## What the 12 sections check

| # | Section | Pass when |
|---|---|---|
| 1 | Site reachability | `https://orphograph.com/` → 200, TLS cert > 30d to expiry |
| 2 | API health | `/api/health` → `ok:true`, `uptime_sec>0`, no `reachable:false` calendars |
| 3 | CSS cache key freshness | Production `index.css?v=N` not >1 version behind local `web/index.html` |
| 4 | Genesis receipt status | `/api/receipt/o3WGD22T4UwqfCrb` → status `pinned` or `partial` |
| 5 | OTS calendar reachability | ≤1 of 5 calendars unreachable via HEAD |
| 6 | Kill-switch state | `/api/config` toggles all default-off |
| 7 | HMAC secret git history | `git log --all` on 5 secret files returns zero commits |
| 8 | Receipts pending >24h | Local `data/receipts/*/receipt.json` — no `status=pending` older than 24h |
| 9 | Stripe ↔ ledger drift | `scripts/reconcile_stripe_ledger.py` exits 0 |
| 10 | Fly memory tier | Machine memory > 256MB |
| 11 | Test suite green | `pytest -p no:anchorpy -q --tb=no` shows ≥381 passed, 0 failed |
| 12 | Inline secrets in tree | No literal `sk_live_…`, `re_…`, NOWPayments, or BTC address values in `web/` or `server/` |

Sections 8, 9, 10 will report `SKIPPED` with a one-line reason when their
preconditions aren't met (no local `data/receipts/`, no `STRIPE_SECRET_KEY`,
no `fly` CLI). A `SKIPPED` section is **not** an audit failure.

## Install the biweekly launchd job

1. Copy & substitute placeholders:
   ```sh
   sed \
     -e "s|{{PYTHON}}|$(which python3)|g" \
     -e "s|{{SCRIPT_PATH}}|$(pwd)/scripts/biweekly_safety_audit.py|g" \
     -e "s|{{REPO_ROOT}}|$(pwd)|g" \
     scripts/com.orphograph.audit.plist.template \
     > ~/Library/LaunchAgents/com.orphograph.audit.plist
   ```
2. Load it:
   ```sh
   launchctl bootstrap gui/$UID ~/Library/LaunchAgents/com.orphograph.audit.plist
   launchctl enable    gui/$UID/com.orphograph.audit
   ```
3. Smoke-test (forces an immediate run):
   ```sh
   launchctl kickstart -k gui/$UID/com.orphograph.audit
   ls -lt deploy/AUDIT_REPORTS/ | head -3
   ```

The plist fires on the **1st and 15th** of every month at 09:00 local —
launchd has no native "every 14 days" interval, so this is the closest
calendar approximation (~14 days apart, twice per month).

## Sequoia (macOS 15+) pitfall: `bootstrap` IO error 5

`launchctl bootstrap` sometimes returns `IO error 5` on Sequoia in
user-session setups. If that happens, fall back to a nohup Python sleeper:

```sh
nohup python3 -c "
import subprocess, time
PY = '$(which python3)'
S  = '$(pwd)/scripts/biweekly_safety_audit.py'
while True:
    subprocess.run([PY, S])
    time.sleep(60 * 60 * 24 * 14)   # 14 days
" >> logs/audit.out.log 2>&1 &
disown
```

Capture the PID; `kill <pid>` to stop it. Survives logout only with
`caffeinate -i` or if the user session stays open.

## Exit codes

- `0` — every check is PASS or SKIPPED (with a documented reason)
- `1` — one or more checks are FAIL; see the markdown report

The exit code is captured in `logs/audit.out.log` when the launchd job
runs; the markdown report is the canonical surface.

## Hard guarantees

- Pure stdlib (`urllib`, `ssl`, `hmac`, `hashlib`, `json`, `subprocess`,
  `pathlib`, `datetime`, `os`, `sys`, `re`, `shutil`, `socket`)
- No real Stripe / NOWPayments / Resend API calls
- No secret values are written to the report — section 12 records only
  the file path and key name, never the captured value
- Idempotent — safe to re-run
