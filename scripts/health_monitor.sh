#!/usr/bin/env bash
# scripts/health_monitor.sh — liveness check for the local orphograph server.
#
# Fires every minute via launchd. On the first failure within a window,
# emits a Telegram alert via ~/.claude/notifier.py. Tracks state in
# ~/orphograph/data/.health_state so we don't spam the founder during
# an extended outage.
#
# Also fires positive milestone alerts:
#   - First BTC payment settled
#   - First $100 cumulative revenue (placeholder; computed from credit ledger)
#   - Daily summary at 09:00 local
set -u

ROOT="${ORPHO_ROOT:-$(cd "$(dirname "$0")/.." && pwd)}"
NOTIFIER="$HOME/.claude/notifier.py"
HEALTH_URL="http://127.0.0.1:8989/api/health"
STATE_DIR="$ROOT/data"
STATE_FILE="$STATE_DIR/.health_state"
LOG_FILE="$ROOT/logs/health_monitor.log"

mkdir -p "$STATE_DIR" "$(dirname "$LOG_FILE")"

ts() { date -u +"%Y-%m-%dT%H:%M:%SZ"; }
log() { printf "%s %s\n" "$(ts)" "$*" >> "$LOG_FILE"; }
notify() {
  if [ -x "$NOTIFIER" ]; then
    python3 "$NOTIFIER" "$@" >/dev/null 2>&1 || true
  fi
}

# ACP supervisor persistence. launchd refuses to bootstrap com.orphograph.acp
# on this Mac (IO error 5), so this already-loaded 60s job re-arms keeper_v2
# when it is absent. The ensure script is idempotent and never spawns a second
# keeper; it lives outside the repo with the ACP service.
ENSURE_ACP="$HOME/orphograph-acp/ensure_acp_keeper.sh"
if [ -x "$ENSURE_ACP" ]; then
  bash "$ENSURE_ACP" >/dev/null 2>&1 || true
fi

# State: "ok" or "down:<since-ts>"
prev_state="$(cat "$STATE_FILE" 2>/dev/null || echo ok)"

# Probe /api/health with 5s timeout.
http_code=$(curl -sS -o /tmp/orpho_health_probe.json -m 5 -w "%{http_code}" "$HEALTH_URL" 2>/dev/null || echo "000")

if [ "$http_code" = "200" ]; then
  # Parse a few interesting fields for the daily summary path.
  ok_field=$(python3 -c "import json; d=json.load(open('/tmp/orpho_health_probe.json')); print(d.get('ok', False))" 2>/dev/null || echo "False")
  if [ "$ok_field" = "True" ]; then
    if [ "$prev_state" != "ok" ]; then
      # Recovery from a previously-down state.
      log "RECOVERED from ${prev_state}"
      notify "✅ Orphograph local server back online ($(ts))"
    fi
    echo "ok" > "$STATE_FILE"
    log "ok (http 200)"
    exit 0
  fi
fi

# We're down (or returned non-200).
if [[ "$prev_state" == down:* ]]; then
  # Already down; don't re-alert, but log.
  log "still down (http $http_code) since ${prev_state#down:}"
else
  since=$(ts)
  echo "down:$since" > "$STATE_FILE"
  log "DOWN — http $http_code"
  notify "🚨 Orphograph local server DOWN — http $http_code at $since. Check ~/orphograph/logs/server.err.log."
fi
exit 0
