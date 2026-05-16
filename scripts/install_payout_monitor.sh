#!/usr/bin/env bash
# install_payout_monitor.sh — wire the daily BTC payout monitor into launchd.
#
# Daily at 9 AM local, polls mempool.space for the hot wallet's address pool,
# computes the total, and Telegram-pings if it crosses the sweep threshold.
#
# Idempotent: rerun to refresh after path changes.

set -eu

SRC_PLIST="$HOME/orphograph/capture/com.orphograph.payout.plist"
DEST_PLIST="$HOME/Library/LaunchAgents/com.orphograph.payout.plist"

if [ ! -f "$SRC_PLIST" ]; then
    echo "error: source plist missing at $SRC_PLIST" >&2
    exit 1
fi

mkdir -p "$HOME/Library/LaunchAgents"
mkdir -p "$HOME/Library/Logs"

# Substitute CHANGEME with actual $USER + write to destination.
sed "s|CHANGEME|$USER|g" "$SRC_PLIST" > "$DEST_PLIST"

# Unload if already loaded (idempotent).
launchctl unload "$DEST_PLIST" 2>/dev/null || true

# Load.
launchctl load "$DEST_PLIST"

echo "✓ installed: $DEST_PLIST"
echo "  next run: tomorrow at 9:00 AM local"
echo
echo "Run once manually to test:"
echo "  python3 $HOME/orphograph/server/payout_monitor.py"
echo
echo "Force a ping right now (regardless of threshold):"
echo "  python3 $HOME/orphograph/server/payout_monitor.py --force"
echo
echo "View status:"
echo "  python3 $HOME/orphograph/server/payout_monitor.py --status"
echo
echo "Stop:"
echo "  launchctl unload $DEST_PLIST"
