#!/usr/bin/env bash
# scripts/init_volume.sh — runs at container start.
# Ensures the persistent data directory exists and is owned by the running user,
# then execs the server. This is the entry point referenced by Dockerfile.
set -euo pipefail

DATA_DIR="${ORPHO_DATA_DIR:-/app/data}"

umask 077
mkdir -p "${DATA_DIR}/receipts"
chmod 700 "${DATA_DIR}" "${DATA_DIR}/receipts" 2>/dev/null || true
# Append-only ledgers are touched lazily by their owning modules; nothing to do here.

# Drop a marker so first-boot is observable in logs.
if [ ! -f "${DATA_DIR}/.initialized" ]; then
  echo "init_volume: first boot, DATA_DIR=${DATA_DIR}" >&2
  date -u +"%Y-%m-%dT%H:%M:%SZ" > "${DATA_DIR}/.initialized"
fi

# In the Docker image WORKDIR=/app and this script lives at /app/scripts/.
# In dev, repo root is the script's parent. Either way: cd to repo root.
cd "$(dirname "$0")/.."
exec python3 server/app.py
