#!/usr/bin/env bash
# scripts/expire_cron.sh — daily prune of free-tier receipts older than 30 days.
# NOT SCHEDULED: the ToS keeps free receipts (2026-09-11). Amend the Terms
# and Privacy pages before wiring this anywhere; see server/expire_worker.py.
# Schedule via fly machines cron (see deploy/README.md).
set -euo pipefail

cd "$(dirname "$0")/.."
exec python3 server/expire_worker.py
