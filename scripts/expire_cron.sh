#!/usr/bin/env bash
# scripts/expire_cron.sh — daily prune of free-tier receipts older than 30 days.
# Schedule via fly machines cron (see deploy/README.md).
set -euo pipefail

cd "$(dirname "$0")/.."
exec python3 server/expire_worker.py
