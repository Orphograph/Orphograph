#!/bin/bash
# fetch_then_run.sh — patient front-end for run_full_overnight.sh when the
# ptau host is throttled (08-03: storage.googleapis.com at ~1-10KB/s while
# the rest of the network was fine). Resume-download with LONG backoff —
# no attempt cap, 5 min between failures — verify blake2b, then hand off to
# the normal runner (whose own fetch step is skipped by the size gate).
set -uo pipefail
cd "$(dirname "$0")"
mkdir -p build_full

PTAU_URL="https://storage.googleapis.com/zkevm/ptau/powersOfTau28_hez_final_20.ptau"
PTAU=build_full/ppot20.ptau
PTAU_B2B="89a66eb5590a1c94e3f1ee0e72acf49b1669e050bb5f93c73b066b564dca4e0c7556a52b323178269d64af325d8fdddb33da3a27c34409b821de82aa2bf1a27b"
PTAU_MIN_BYTES=1200000000

ts() { date -u +"%H:%M:%SZ"; }

while true; do
  SIZE=$(stat -f%z "$PTAU" 2>/dev/null || echo 0)
  if [ "$SIZE" -ge "$PTAU_MIN_BYTES" ]; then
    break
  fi
  # low-and-slow: generous stall tolerance (no speed floor), resume forever
  curl -fsSL -C - --connect-timeout 30 -o "$PTAU" "$PTAU_URL"
  RC=$?
  NEWSIZE=$(stat -f%z "$PTAU" 2>/dev/null || echo 0)
  if [ "$RC" -eq 0 ] && [ "$NEWSIZE" -ge "$PTAU_MIN_BYTES" ]; then
    break
  fi
  echo "$(ts) PTAU-FETCH: rc=$RC size=$NEWSIZE (+$((NEWSIZE - SIZE)) bytes this attempt) — backing off 300s"
  sleep 300
done

echo "$(ts) PTAU-FETCH: download complete ($(stat -f%z "$PTAU") bytes) — verifying blake2b"
python3 - "$PTAU" "$PTAU_B2B" <<'PYEOF'
import hashlib, sys
h = hashlib.blake2b()
with open(sys.argv[1], "rb") as f:
    for chunk in iter(lambda: f.read(1 << 22), b""):
        h.update(chunk)
ok = h.hexdigest() == sys.argv[2]
print("ptau blake2b:", "MATCHES published hash" if ok else "MISMATCH - refusing to use file")
sys.exit(0 if ok else 1)
PYEOF
if [ $? -ne 0 ]; then
  echo "$(ts) FULL 8-ROUND RUN: FAIL — ptau blake2b mismatch after download"
  rm -f "$PTAU"
  exit 1
fi

echo "$(ts) PTAU-FETCH: verified — handing off to run_full_overnight.sh"
exec ./run_full_overnight.sh
