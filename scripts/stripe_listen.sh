#!/usr/bin/env bash
# stripe_listen.sh — forward Stripe webhooks to the local orphograph server.
#
# Why this exists:
#   Stripe's webhook endpoint must be a publicly-reachable URL. Until
#   orphograph.com is on Fly + DNS pointed, that URL doesn't exist.
#   The Stripe CLI fixes this by opening a WebSocket from Stripe → your
#   machine and forwarding every webhook to localhost. No tunnel needed,
#   no rotating subdomain, no Stripe error "URL couldn't be reached".
#
# Usage:
#   bash ~/orphograph/scripts/stripe_listen.sh
#
# On first run, it'll open a browser to authorize the Stripe CLI against
# your account. After that, it streams events forever (Ctrl-C to stop).
#
# Webhooks landed via this CLI use a special LOCAL signing secret that
# differs from your production webhook secret. The CLI prints it on
# startup like:
#   > Ready! You are using Stripe API Version [2024-12-18.acacia]. Your webhook signing secret is whsec_...
# Copy that whsec_... value into .env.local as STRIPE_WEBHOOK_SECRET while
# you're developing. Switch to the real prod secret when you deploy.

set -u
cd "$(dirname "$0")/.."

AMBER=$'\033[38;2;192;138;62m'
SAGE=$'\033[38;2;74;154;115m'
INK=$'\033[38;2;31;29;26m'
MUTED=$'\033[38;2;131;126;117m'
ERR=$'\033[38;2;178;80;80m'
RESET=$'\033[0m'

LOCAL_PORT="${ORPHO_LOCAL_PORT:-8989}"

if ! command -v stripe >/dev/null 2>&1; then
    echo "${ERR}error: stripe CLI not installed${RESET}"
    echo "  brew install stripe/stripe-cli/stripe"
    echo "  or: https://docs.stripe.com/stripe-cli"
    exit 1
fi

echo
echo "${AMBER}orphograph — Stripe webhook forwarder${RESET}"
echo "${MUTED}────────────────────────────────────────────${RESET}"
echo "${INK}Forwarding Stripe webhooks → http://localhost:${LOCAL_PORT}/api/stripe/webhook${RESET}"
echo "${INK}Events: all (filter at the server side via the webhook signature check)${RESET}"
echo
echo "${MUTED}Watch the line that starts with 'Your webhook signing secret is whsec_...'${RESET}"
echo "${MUTED}Copy that whsec_... value into .env.local as STRIPE_WEBHOOK_SECRET while developing.${RESET}"
echo "${MUTED}Ctrl-C to stop.${RESET}"
echo

exec stripe listen --forward-to "localhost:${LOCAL_PORT}/api/stripe/webhook" \
  --events checkout.session.completed,customer.subscription.created,customer.subscription.updated,customer.subscription.deleted,invoice.payment_succeeded,invoice.payment_failed
