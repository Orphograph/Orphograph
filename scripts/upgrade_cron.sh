#!/usr/bin/env bash
# scripts/upgrade_cron.sh — periodic OTS upgrade pass.
# Run every 30 minutes via launchd / systemd / fly machines cron.
set -euo pipefail

cd "$(dirname "$0")/.."
exec python3 server/upgrade_worker.py
