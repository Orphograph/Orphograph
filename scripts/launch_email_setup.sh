#!/usr/bin/env bash
# launch_email_setup.sh — one-command door into the cozy email wizard.
#
# What it does:
#   1. Opens https://dash.cloudflare.com/profile/api-tokens in Brave
#      (you click "Create Token", copy it — leave the tab open)
#   2. Opens https://resend.com/api-keys in Brave
#      (sign up if needed, create a key, copy it)
#   3. Runs the interactive wizard, where you paste both keys
#   4. Reminds you to click the Cloudflare verification email when it lands
#
# Brave is the default per founder preference (feedback_brave_browser.md).
# Falls back to the system default browser if Brave isn't installed.
set -u

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
WIZARD="$SCRIPT_DIR/setup_email.py"

# ANSI for friendly preamble — matches the wizard's amber/sage palette
AMBER=$'\033[38;2;192;138;62m'
SAGE=$'\033[38;2;74;154;115m'
INK=$'\033[38;2;31;29;26m'
MUTED=$'\033[38;2;131;126;117m'
BOLD=$'\033[1m'
RESET=$'\033[0m'

echo
echo "${AMBER}╭─────────────────────────────────────────────────────────╮${RESET}"
echo "${AMBER}│${RESET} ${BOLD}${INK}Orphograph — one-shot email setup${RESET}                       ${AMBER}│${RESET}"
echo "${AMBER}╰─────────────────────────────────────────────────────────╯${RESET}"
echo
echo "${INK}Two tabs about to open in Brave. From each, grab one key:${RESET}"
echo
echo "  ${SAGE}1.${RESET} ${INK}Cloudflare API token${RESET}"
echo "     ${MUTED}https://dash.cloudflare.com/profile/api-tokens${RESET}"
echo "     ${MUTED}Click ${BOLD}Create Token${RESET}${MUTED} → ${BOLD}Edit zone DNS${RESET}${MUTED} template${RESET}"
echo "     ${MUTED}Add ${BOLD}Zone → Email Routing Rules → Edit${RESET}${MUTED} to permissions${RESET}"
echo "     ${MUTED}Zone resources: include ${BOLD}orphograph.com${RESET}${MUTED} only${RESET}"
echo
echo "  ${SAGE}2.${RESET} ${INK}Resend API key (free tier — no credit card)${RESET}"
echo "     ${MUTED}https://resend.com/api-keys${RESET}"
echo "     ${MUTED}Sign up if new (gmail OK), then ${BOLD}Create API Key${RESET}${MUTED} (full access){RESET}${MUTED}, copy ${BOLD}re_*${RESET}"
echo
echo "${INK}Paste each into the wizard when it asks. Both inputs are hidden${RESET}"
echo "${INK}(getpass — they don't show on screen, that's normal).${RESET}"
echo
echo "${MUTED}Cancel any time with Ctrl-C. Nothing is saved until you confirm.${RESET}"
echo

if command -v open >/dev/null 2>&1; then
  # macOS — try Brave first
  if open -Ra "Brave Browser" 2>/dev/null; then
    open -a "Brave Browser" "https://dash.cloudflare.com/profile/api-tokens" 2>/dev/null
    sleep 0.4
    open -a "Brave Browser" "https://resend.com/api-keys" 2>/dev/null
    echo "${SAGE}✓${RESET} ${INK}Opened both tabs in Brave.${RESET}"
  else
    open "https://dash.cloudflare.com/profile/api-tokens" 2>/dev/null
    sleep 0.4
    open "https://resend.com/api-keys" 2>/dev/null
    echo "${MUTED}(Brave not detected — opened in default browser.)${RESET}"
  fi
elif command -v xdg-open >/dev/null 2>&1; then
  xdg-open "https://dash.cloudflare.com/profile/api-tokens" 2>/dev/null
  sleep 0.4
  xdg-open "https://resend.com/api-keys" 2>/dev/null
fi

echo
echo "${MUTED}Press ENTER when you have both keys copied to clipboard.${RESET}"
read -r _ </dev/tty

exec python3 "$WIZARD"
