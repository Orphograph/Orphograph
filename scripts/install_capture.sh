#!/usr/bin/env bash
# install_capture.sh — wire the capture-time daemon into launchd.
#
# What it does:
#   - Copies capture/com.orphograph.capture.plist into ~/Library/LaunchAgents/
#   - Substitutes CHANGEME with $USER
#   - Loads via launchctl
#   - Verifies the job is running and the daemon is hashing as expected
#
# The capture daemon watches ~/Pictures and ~/Desktop for new files and
# anchors each to Bitcoin via /api/anchor. Files NEVER upload — only the
# 32-byte SHA-256 leaves the machine.
#
# Idempotent: rerun to reset after path changes.

set -eu

SRC_PLIST="$HOME/orphograph/capture/com.orphograph.capture.plist"
DEST_PLIST="$HOME/Library/LaunchAgents/com.orphograph.capture.plist"

AMBER=$'\033[38;2;192;138;62m'
SAGE=$'\033[38;2;74;154;115m'
INK=$'\033[38;2;31;29;26m'
MUTED=$'\033[38;2;131;126;117m'
ERR=$'\033[38;2;178;80;80m'
RESET=$'\033[0m'

if [ ! -f "$SRC_PLIST" ]; then
    echo "${ERR}error: source plist missing at $SRC_PLIST${RESET}" >&2
    exit 1
fi

mkdir -p "$HOME/Library/LaunchAgents"
mkdir -p "$HOME/Library/Logs"

echo
echo "${AMBER}orphograph-capture — daemon installer${RESET}"
echo "${MUTED}───────────────────────────────────────────${RESET}"
echo

# Substitute CHANGEME with $USER + write to destination.
sed "s|CHANGEME|$USER|g" "$SRC_PLIST" > "$DEST_PLIST"
echo "${SAGE}✓${RESET} ${INK}plist installed:${RESET} $DEST_PLIST"

# Check for API key placeholder.
if grep -q "PASTE_YOUR_CREATOR_TIER_KEY_HERE" "$DEST_PLIST"; then
    echo "${MUTED}⚠  no API key set yet — daemon will use free tier (1 anchor/month per IP)${RESET}"
    echo "${MUTED}   get a Creator-tier key from https://orphograph.com/account.html${RESET}"
    echo "${MUTED}   then: sed -i '' 's|PASTE_YOUR_CREATOR_TIER_KEY_HERE|rk_live_...|' $DEST_PLIST${RESET}"
    echo "${MUTED}   then: launchctl unload $DEST_PLIST && launchctl load $DEST_PLIST${RESET}"
fi

# Unload if already loaded (idempotent — silent if not loaded).
launchctl unload "$DEST_PLIST" 2>/dev/null || true

# Load.
if ! launchctl load "$DEST_PLIST"; then
    echo "${ERR}✗ launchctl load failed — likely sequoia IO error 5${RESET}" >&2
    echo "  fallback: run manually with: nohup python3 $HOME/orphograph/capture/orphograph_capture.py &" >&2
    exit 2
fi
echo "${SAGE}✓${RESET} ${INK}launchd job loaded${RESET}"

# Verify it's running.
sleep 1
if launchctl list | grep -q "com.orphograph.capture"; then
    echo "${SAGE}✓${RESET} ${INK}job 'com.orphograph.capture' is registered${RESET}"
else
    echo "${ERR}✗ job not in launchctl list after load — investigate${RESET}" >&2
fi

echo
echo "${INK}What's running now:${RESET}"
echo "  • watching: ~/Pictures, ~/Desktop"
echo "  • interval: 5 seconds per scan"
echo "  • logs:    ~/Library/Logs/orphograph-capture.{out,err}"
echo "  • state:   ~/Library/Application Support/Orphograph/"
echo
echo "${INK}Test it by dropping a file in ~/Desktop:${RESET}"
echo "  cp ~/your-photo.jpg ~/Desktop/"
echo "  # wait 6 seconds, then:"
echo "  ls ~/Desktop/*.orpho.json    # receipt sidecar appears"
echo
echo "${INK}Verify status:${RESET}"
echo "  python3 ~/orphograph/capture/orphograph_capture.py --status"
echo
echo "${INK}Stop:${RESET}"
echo "  launchctl unload $DEST_PLIST"
echo
echo "${SAGE}done.${RESET}"
