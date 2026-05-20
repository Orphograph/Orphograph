# Orphograph Production Watchdog

A local launchd job that probes orphograph.com and /api/health every 5 minutes. When both endpoints stop returning 200, the watchdog reads `fly status` and either starts a stopped/failed/unhealthy machine or restarts a stuck `started` machine using `fly machine restart --skip-health-checks`. Three consecutive unhealthy checks within a 10-minute window trigger a Telegram notification (via `~/.claude/notifier.py` if importable) and append an entry to `~/Hydroboro/logs/orphograph_watchdog_ALERT.txt`. The watchdog only sees HTTP status codes — no PII, no receipt content, no customer email.

## Install (founder, when ready)

```
cp scripts/com.orphograph.watchdog.plist.template ~/Library/LaunchAgents/com.orphograph.watchdog.plist
launchctl bootstrap gui/$UID ~/Library/LaunchAgents/com.orphograph.watchdog.plist
launchctl enable        gui/$UID/com.orphograph.watchdog
launchctl kickstart -k  gui/$UID/com.orphograph.watchdog
```

If `bootstrap` returns IO error 5 (a known Sequoia bug), use the nohup sleeper documented inside the template file instead.

## Uninstall

```
launchctl bootout gui/$UID/com.orphograph.watchdog
rm ~/Library/LaunchAgents/com.orphograph.watchdog.plist
```

## Logs

- Per-probe JSONL: `~/Hydroboro/logs/orphograph_watchdog.jsonl`
- launchd stdout/stderr: `~/Library/Logs/orphograph_watchdog.log`
- Alert overflow (when Telegram is unavailable): `~/Hydroboro/logs/orphograph_watchdog_ALERT.txt`

## Test the recovery path safely

Stop the prod machine and watch the watchdog bring it back at the next 5-minute tick:

```
fly machine list -a orphograph                 # note the machine id
fly machine stop <id> -a orphograph
launchctl kickstart -k gui/$UID/com.orphograph.watchdog   # fire immediately
tail -f ~/Hydroboro/logs/orphograph_watchdog.jsonl
```

You should see a line with `"status": "UNHEALTHY"` and `"action_taken": "machine_start:<id>:rc0"`, followed by HEALTHY on the next tick.
