#!/usr/bin/env bash
# scripts/smoke_test.sh — end-to-end test: spin up server, anchor a known hash,
# verify via API + standalone CLI. Exits non-zero on any failure.
set -euo pipefail

cd "$(dirname "$0")/.."

PORT="${PORT:-8989}"
URL="http://127.0.0.1:${PORT}"

# preflight: is something already on the port?
if lsof -nP -iTCP:"${PORT}" -sTCP:LISTEN >/dev/null 2>&1; then
  echo "ERROR: port ${PORT} already in use" >&2
  exit 1
fi

python3 -m py_compile server/engine.py server/app.py server/verify_cli.py

python3 server/app.py >/tmp/orphograph.log 2>&1 &
SERVER_PID=$!
trap "kill ${SERVER_PID} 2>/dev/null || true" EXIT

# wait for server
for _ in 1 2 3 4 5; do
  if curl -fs "${URL}/api/health" >/dev/null 2>&1; then break; fi
  sleep 0.5
done
if ! curl -fs "${URL}/api/health" >/dev/null 2>&1; then
  echo "ERROR: server did not start" >&2
  cat /tmp/orphograph.log >&2
  exit 1
fi

HASH=$(printf 'smoke-test-%s\n' "$(date +%s)" | shasum -a 256 | awk '{print $1}')
echo "smoke hash: ${HASH}"

RESP=$(curl -fs -X POST "${URL}/api/anchor" \
  -H "Content-Type: application/json" \
  -d "{\"hash_hex\":\"${HASH}\",\"client_label\":\"smoke-test\"}")
echo "${RESP}" | python3 -m json.tool

RID=$(echo "${RESP}" | python3 -c "import sys,json; print(json.load(sys.stdin)['receipt_id'])")
OK=$(echo "${RESP}" | python3 -c "import sys,json; print(json.load(sys.stdin)['calendars_ok'])")

if [ "${OK}" -lt 3 ]; then
  echo "ERROR: only ${OK}/5 calendars succeeded (need >=3)" >&2
  exit 2
fi

echo "--- standalone verify ---"
RECEIPTS_BASE="${ORPHO_DATA_DIR:-.}"
python3 server/verify_cli.py "${RECEIPTS_BASE}/receipts/${RID}/receipt.json"

echo ""
echo "PASS: anchored ${OK}/5 calendars, receipt ${RID} verified"
