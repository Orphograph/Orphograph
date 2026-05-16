#!/usr/bin/env bash
# backup_to_b2.sh — daily off-Fly redundancy for the data volume.
#
# Why: Fly volumes are SSD-backed but not auto-snapshotted. A volume corruption
# or accidental delete loses every customer's receipt + ledger. We sync to a
# Backblaze B2 bucket nightly. B2 is $5/TB-month at rest + free first 1GB.
#
# What gets backed up:
#   • data/ledger.jsonl + every appended ledger (credit, balance, suppressions)
#   • data/receipts/<id>/{receipt.json, *.ots} — every customer's receipt
#   • data/btc_address_pool.txt, btc_address.txt, cold_wallet_address.txt
# What does NOT (intentionally):
#   • .env.local — secrets live in fly secrets, not in the backup
#   • logs/ — operational only, expendable
#   • __pycache__ — compiled artifacts
#
# Encryption:
#   • B2 supports server-side encryption (SSE-B2). Enabled at bucket creation.
#   • Optional client-side: pipe through `openssl enc -aes-256-cbc -pbkdf2`
#     using ORPHO_BACKUP_KEY env. Default OFF — server-side encryption is
#     enough for a non-PII dataset (only hashes + timestamps).
#
# Restore: see RESTORE section at the bottom of this file.

set -eu
cd "$(dirname "$0")/.."

AMBER=$'\033[38;2;192;138;62m'
SAGE=$'\033[38;2;74;154;115m'
INK=$'\033[38;2;31;29;26m'
MUTED=$'\033[38;2;131;126;117m'
ERR=$'\033[38;2;178;80;80m'
RESET=$'\033[0m'

DATA_DIR="${ORPHO_DATA_DIR:-./data}"
BUCKET="${ORPHO_B2_BUCKET:-orphograph-backups}"
PREFIX="${ORPHO_B2_PREFIX:-$(date -u +%Y-%m-%d)}"

# Load API keys from .env.local
if [ -f .env.local ]; then
  set -a
  # shellcheck disable=SC1091
  source .env.local
  set +a
fi

B2_KEY_ID="${B2_APPLICATION_KEY_ID:-}"
B2_KEY="${B2_APPLICATION_KEY:-}"

echo
echo "${AMBER}orphograph — daily B2 backup${RESET}"
echo "${MUTED}────────────────────────────────${RESET}"
echo "${INK}Source: ${DATA_DIR}${RESET}"
echo "${INK}Bucket: b2://${BUCKET}/${PREFIX}/${RESET}"
echo

if [ -z "$B2_KEY_ID" ] || [ -z "$B2_KEY" ]; then
  echo "${ERR}error: B2_APPLICATION_KEY_ID + B2_APPLICATION_KEY missing in .env.local${RESET}" >&2
  echo "" >&2
  echo "Setup (one-time, founder action):" >&2
  echo "  1. Sign up at https://www.backblaze.com/b2/sign-up.html (free)" >&2
  echo "  2. Create a bucket named '${BUCKET}' — region: us-west-002 (cheapest)" >&2
  echo "  3. Enable Server-Side Encryption (SSE-B2)" >&2
  echo "  4. Create an Application Key scoped to this bucket only" >&2
  echo "  5. Paste keys into .env.local:" >&2
  echo "       B2_APPLICATION_KEY_ID=\"...\"" >&2
  echo "       B2_APPLICATION_KEY=\"...\"" >&2
  exit 1
fi

# Prefer rclone (more robust than b2 CLI for incremental sync).
if ! command -v rclone >/dev/null 2>&1; then
  echo "${MUTED}rclone not installed; install with: brew install rclone${RESET}" >&2
  echo "${MUTED}then run: rclone config create orpho-b2 b2 account=\$B2_APPLICATION_KEY_ID key=\$B2_APPLICATION_KEY${RESET}" >&2
  exit 2
fi

# Sync data/ but exclude ephemera.
echo "${INK}Starting incremental sync…${RESET}"
rclone sync "$DATA_DIR" "orpho-b2:${BUCKET}/${PREFIX}/" \
  --exclude "logs/**" \
  --exclude "__pycache__/**" \
  --exclude "*.bak" \
  --exclude ".DS_Store" \
  --progress --transfers 4 --checkers 8 \
  --b2-versions

RC=$?
if [ $RC -eq 0 ]; then
  echo "${SAGE}✓${RESET} ${INK}backup complete${RESET}"
  echo "  ${MUTED}browse: rclone tree orpho-b2:${BUCKET}/${PREFIX}/${RESET}"
else
  echo "${ERR}✗ backup failed (rclone exit ${RC})${RESET}" >&2
  exit $RC
fi

# Retention: keep last 30 daily backups. Older than 30d: delete.
echo
echo "${INK}Pruning backups older than 30 days…${RESET}"
CUTOFF=$(date -u -v-30d +%Y-%m-%d 2>/dev/null || date -u -d "30 days ago" +%Y-%m-%d)
echo "  cutoff: anything older than ${CUTOFF} will be deleted"
# rclone purge any path matching YYYY-MM-DD prefix older than cutoff
rclone lsd "orpho-b2:${BUCKET}/" 2>/dev/null \
  | awk '{print $NF}' \
  | grep -E '^[0-9]{4}-[0-9]{2}-[0-9]{2}$' \
  | while read -r d; do
      if [[ "$d" < "$CUTOFF" ]]; then
        echo "  purging $d"
        rclone purge "orpho-b2:${BUCKET}/$d" 2>/dev/null || true
      fi
    done

echo
echo "${SAGE}backup done. next run: tomorrow at 03:00 UTC (via fly cron or launchd).${RESET}"

# ─── RESTORE ─────────────────────────────────────────────────────────────
# To restore from yesterday's snapshot:
#
#   YESTERDAY=$(date -u -v-1d +%Y-%m-%d)   # or whichever date you need
#   rclone sync "orpho-b2:${BUCKET}/${YESTERDAY}/" ~/orphograph/data/
#   ls -la ~/orphograph/data/receipts/ | head
#   # restart server: pkill -f 'orphograph/server/app.py'; nohup python3 ~/orphograph/server/app.py &
#
# To restore a single receipt:
#
#   rclone copy "orpho-b2:${BUCKET}/2026-05-14/receipts/<id>/" \
#               ~/orphograph/data/receipts/<id>/
#
# To verify a restored receipt:
#
#   python3 ~/orphograph/marketplace/orphograph-plugin/skills/orphograph-verify/verify.py \
#           <receipt-id> <original-file>
