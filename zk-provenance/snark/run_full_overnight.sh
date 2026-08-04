#!/bin/bash
# run_full_overnight.sh — normative 8-round PROGRAM_V2 proof, sized for a
# small machine (8GB RAM / ~7GB free disk):
#   - uses the PUBLIC Hermez/iden3 2^20 powers-of-tau instead of local
#     generation (standard file the snarkjs docs point at; dev-grade trust
#     posture is unchanged — see README honesty ladder rung 5)
#   - skips --sym and the optional second zkey contribution (disk + hours;
#     adds no trust to a dev-grade ceremony either way)
#   - DISK WATCHDOG: aborts the whole run if free space < 1.5 GB so an
#     overnight run can never wedge the machine (ledger backups etc. live
#     on this disk)
# Progress: tail -f full_run.log   Artifacts: build_full/
set -euo pipefail
cd "$(dirname "$0")"
mkdir -p build_full

# --- single-instance lock: two concurrent runs both resume-download the same
# ptau file and corrupt it (happened 08-03 when a watchdog "abort" failed to
# actually kill attempt 3 and attempt 4 was launched over it) ---
LOCK=build_full/.run.lock
if ! mkdir "$LOCK" 2>/dev/null; then
  echo "FULL 8-ROUND RUN: FAIL — lock $LOCK exists (another instance running, or stale: rmdir it)"
  exit 1
fi

# NOTE: "hez" (Hermez ceremony), not "hex" — the hex spelling 404s.
PTAU_URL="https://storage.googleapis.com/zkevm/ptau/powersOfTau28_hez_final_20.ptau"
PTAU=build_full/ppot20.ptau
# Official blake2b from the snarkjs README ptau table — verified after download.
PTAU_B2B="89a66eb5590a1c94e3f1ee0e72acf49b1669e050bb5f93c73b066b564dca4e0c7556a52b323178269d64af325d8fdddb33da3a27c34409b821de82aa2bf1a27b"
export NODE_OPTIONS="--max-old-space-size=6144"
SNARKJS="npx --yes snarkjs"

ts() { date -u +"%H:%M:%SZ"; }

# --- disk watchdog: kill our process group if free space gets dangerous ---
(
  while true; do
    FREE_KB=$(df -k "$HOME" | tail -1 | awk '{print $4}')
    if [ "$FREE_KB" -lt 1572864 ]; then   # 1.5 GB
      echo "$(ts) WATCHDOG: free disk < 1.5GB — aborting run to protect the machine"
      # $$ is NOT the group leader under nohup/caffeinate — kill the real
      # process group, and fall back to killing the script's children + the
      # script itself so an "abort" can never leave the run half-alive.
      PGID=$(ps -o pgid= -p $$ | tr -d ' ')
      kill -TERM -- "-$PGID" 2>/dev/null || { pkill -TERM -P $$ 2>/dev/null; kill -TERM $$ 2>/dev/null; }
      exit 1
    fi
    sleep 60
  done
) &
WATCHDOG=$!
trap 'rmdir "$LOCK" 2>/dev/null; kill $WATCHDOG 2>/dev/null || true' EXIT
trap 'exit 1' TERM INT   # make TERM run the EXIT trap so the lock is released

echo "$(ts) == [1/6] compile program_v2.circom (8 rounds, no --sym)"
CIRCOM="circom"; command -v circom >/dev/null || CIRCOM="npx --yes circom2"
[ -f build_full/program_v2.r1cs ] || $CIRCOM program_v2.circom --r1cs --wasm -o build_full
$SNARKJS r1cs info build_full/program_v2.r1cs

echo "$(ts) == [2/6] fetch public 2^20 powers-of-tau (resumable)"
# The file is ~1.208 GB (curl shows 1152M = MiB). Loop until complete:
# plain --retry does NOT cover exit 56 (recv reset), which is exactly how
# the 08-02 overnight attempt died 220MB in — so resume with -C - until
# the size gate passes, then let the blake2b check be the real arbiter.
PTAU_MIN_BYTES=1200000000
if [ ! -f "$PTAU" ] || [ "$(stat -f%z "$PTAU")" -lt "$PTAU_MIN_BYTES" ]; then
  for attempt in $(seq 1 60); do
    curl -fsSL -C - --retry-all-errors --retry 5 --retry-delay 15 \
         -o "$PTAU" "$PTAU_URL" && break
    echo "$(ts) fetch attempt $attempt interrupted (size now: $(stat -f%z "$PTAU" 2>/dev/null || echo 0)); resuming in 30s…"
    sleep 30
  done
fi
if [ "$(stat -f%z "$PTAU")" -lt "$PTAU_MIN_BYTES" ]; then
  echo "$(ts) FULL 8-ROUND RUN: FAIL — ptau incomplete after 60 fetch attempts"
  exit 1
fi
echo "$(ts) ptau size: $(stat -f%z "$PTAU") bytes"
echo "$(ts) verifying blake2b against the published ceremony hash…"
python3 - "$PTAU" "$PTAU_B2B" <<'PYEOF'
import hashlib, sys
h = hashlib.blake2b()
with open(sys.argv[1], "rb") as f:
    for chunk in iter(lambda: f.read(1 << 22), b""):
        h.update(chunk)
ok = h.hexdigest() == sys.argv[2]
print("ptau blake2b:", "MATCHES published hash" if ok else "MISMATCH — refusing to use file")
sys.exit(0 if ok else 1)
PYEOF

echo "$(ts) == [3/6] groth16 setup (the RAM-heavy step)"
[ -f build_full/ckt_0000.zkey ] || $SNARKJS groth16 setup build_full/program_v2.r1cs "$PTAU" build_full/ckt_0000.zkey
$SNARKJS zkey export verificationkey build_full/ckt_0000.zkey build_full/verification_key.json

echo "$(ts) == [4/6] witness (8-round transcript, Python is the reference)"
python3 make_input.py --model gpt-class-v3 --prompt "hidden P" --seed "seed-1" \
    --rounds 8 --out build_full/input.json --expected build_full/expected.json
node build_full/program_v2_js/generate_witness.js \
    build_full/program_v2_js/program_v2.wasm build_full/input.json build_full/witness.wtns

echo "$(ts) == [5/6] prove + verify"
$SNARKJS groth16 prove build_full/ckt_0000.zkey build_full/witness.wtns \
    build_full/proof.json build_full/public.json
$SNARKJS groth16 verify build_full/verification_key.json \
    build_full/public.json build_full/proof.json

echo "$(ts) == [6/6] cross-check vs Python transcript"
python3 check_public.py build_full/public.json build_full/expected.json

# free the big regenerables, keep proof + vk + public as evidence
rm -f build_full/witness.wtns
echo "$(ts) FULL 8-ROUND RUN: DONE"
