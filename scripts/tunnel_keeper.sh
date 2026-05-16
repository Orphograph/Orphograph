#!/usr/bin/env bash
# tunnel_keeper.sh — keeps a public tunnel alive for orphograph during the
# launch window. Free pinggy tunnels expire every 60 min, so we restart
# proactively at the 55-min mark.
#
# Writes the current public URL to ~/orphograph/data/tunnel_url.txt so the
# rest of the system (status pages, ops checks) can read it.
#
# Run via:
#   nohup ~/orphograph/scripts/tunnel_keeper.sh > ~/orphograph/logs/tunnel_keeper.out 2>&1 &
#
# Stop via:
#   pkill -f tunnel_keeper.sh
#   pkill -f 'ssh.*pinggy'
set -u

LOCAL_PORT="${ORPHO_LOCAL_PORT:-8989}"
LOG_DIR="$HOME/orphograph/logs"
DATA_DIR="$HOME/orphograph/data"
URL_FILE="$DATA_DIR/tunnel_url.txt"
TUNNEL_LOG="$LOG_DIR/pinggy_current.log"
KEEPER_LOG="$LOG_DIR/tunnel_keeper.log"

mkdir -p "$LOG_DIR" "$DATA_DIR"

log() {
  printf "%s %s\n" "$(date -u +'%Y-%m-%dT%H:%M:%SZ')" "$*" >> "$KEEPER_LOG"
}

start_tunnel() {
  : > "$TUNNEL_LOG"
  pkill -f 'ssh.*a.pinggy.io' 2>/dev/null
  sleep 1
  ssh -o StrictHostKeyChecking=accept-new \
      -o ServerAliveInterval=30 \
      -o ServerAliveCountMax=3 \
      -o ExitOnForwardFailure=yes \
      -p 443 \
      -R0:localhost:"$LOCAL_PORT" \
      a.pinggy.io \
      > "$TUNNEL_LOG" 2>&1 &
  SSH_PID=$!
  log "ssh started pid=$SSH_PID"

  # Wait up to 20s for the public URL line to appear in the log.
  for i in $(seq 1 40); do
    if grep -q "pinggy-free.link" "$TUNNEL_LOG" 2>/dev/null; then
      url=$(grep -o 'https://[^[:space:]]*pinggy-free.link' "$TUNNEL_LOG" | head -1)
      if [ -n "$url" ]; then
        printf "%s\n" "$url" > "$URL_FILE"
        log "URL=$url"
        echo "$url"
        return 0
      fi
    fi
    sleep 0.5
  done
  log "FAILED to detect URL within 20s"
  return 1
}

# Main loop.
log "tunnel_keeper starting; local port=$LOCAL_PORT"
trap 'log "exiting"; pkill -P $$ 2>/dev/null; pkill -f "ssh.*a.pinggy.io" 2>/dev/null; exit 0' INT TERM

while true; do
  if start_tunnel; then
    # Free tier expires at 60 min. Refresh at 55 min to be safe.
    sleep 3300
  else
    log "start_tunnel failed, retry in 30s"
    sleep 30
  fi
done
