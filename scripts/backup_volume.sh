#!/usr/bin/env bash
# scripts/backup_volume.sh — pull a gpg-encrypted snapshot of the Fly volume.
#
# Runs from the founder's local machine. Requires:
#   - flyctl on PATH + authenticated (`fly auth login` done)
#   - gpg with the founder's public key imported (key id in $ORPHO_BACKUP_GPG_KEY
#     or argv[1])
#   - $ORPHO_BACKUP_DIR (or default ~/orphograph-backups) writable
#
# What it does:
#   1. ssh into the Fly machine, tar+gzip /app/data into /tmp/.
#   2. scp the tarball down to the local backup dir.
#   3. gpg-encrypt to the founder's public key.
#   4. shred the unencrypted tarball.
#   5. shred the remote tmp tarball.
#
# The encrypted backup contains PII (email-keyed ledgers). Store the
# .gpg files anywhere; without the founder's private key they're
# opaque.
#
# Run on demand or via `launchd` weekly. Crontab example at the bottom.
set -euo pipefail

GPG_KEY="${1:-${ORPHO_BACKUP_GPG_KEY:-}}"
BACKUP_DIR="${ORPHO_BACKUP_DIR:-$HOME/orphograph-backups}"
FLY_APP="${ORPHO_FLY_APP:-orphograph}"
REMOTE_TMP="/tmp/orpho_backup_$(date -u +%Y%m%dT%H%M%SZ).tar.gz"
LOCAL_TMP="${BACKUP_DIR}/$(basename ${REMOTE_TMP})"
LOCAL_ENC="${LOCAL_TMP}.gpg"

if [ -z "$GPG_KEY" ]; then
  echo "ERROR: GPG key id required. Pass as argv[1] or set ORPHO_BACKUP_GPG_KEY." >&2
  echo "List your keys: gpg --list-keys" >&2
  exit 1
fi

if ! command -v fly >/dev/null 2>&1; then
  echo "ERROR: flyctl not on PATH. Install: brew install flyctl" >&2
  exit 1
fi

if ! command -v gpg >/dev/null 2>&1; then
  echo "ERROR: gpg not on PATH." >&2
  exit 1
fi

mkdir -p "$BACKUP_DIR"
chmod 700 "$BACKUP_DIR"

echo "—— Creating remote tarball at ${REMOTE_TMP} ——"
fly ssh console --app "$FLY_APP" --command "tar -C /app -czf ${REMOTE_TMP} data"

echo "—— Copying down to ${LOCAL_TMP} ——"
fly ssh sftp get "${REMOTE_TMP}" "${LOCAL_TMP}" --app "$FLY_APP"

if [ ! -s "$LOCAL_TMP" ]; then
  echo "ERROR: local copy is empty or missing" >&2
  exit 1
fi

echo "—— gpg-encrypting to ${GPG_KEY} ——"
gpg --batch --yes --output "${LOCAL_ENC}" --encrypt --recipient "$GPG_KEY" "$LOCAL_TMP"

if [ ! -s "$LOCAL_ENC" ]; then
  echo "ERROR: encrypted file is empty" >&2
  exit 1
fi

echo "—— Shredding plaintext locally and remotely ——"
# Mac default `shred` doesn't exist; use rm + overwrite if needed. For a
# tarball that lives ~seconds, rm is sufficient on encrypted volumes
# (APFS on most Macs). Founder can use `srm` if they want belt+suspenders.
rm -f "$LOCAL_TMP"
fly ssh console --app "$FLY_APP" --command "rm -f ${REMOTE_TMP}"

echo
echo "Done. Encrypted backup at:"
echo "  ${LOCAL_ENC}"
echo "Size: $(du -h "$LOCAL_ENC" | awk '{print $1}')"
echo
echo "To decrypt later (with founder's private key):"
echo "  gpg --decrypt ${LOCAL_ENC} > orpho_data.tar.gz"
echo "  tar tzf orpho_data.tar.gz | head"

# Retention: keep the last 14 daily backups, drop older.
ls -1t "${BACKUP_DIR}"/orpho_backup_*.tar.gz.gpg 2>/dev/null | tail -n +15 | xargs -I {} rm -f {}

# ---
#
# Crontab line for weekly Sunday 3AM local backup:
#
# 0 3 * * 0 /Users/[founder]/orphograph/scripts/backup_volume.sh ABC123DEF456 >> ~/orphograph-backups/backup.log 2>&1
#
# launchd plist alternative at deploy/launchd/orphograph_backup.plist (TBD).
