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

PTAU_URL="https://storage.googleapis.com/zkevm/ptau/powersOfTau28_hex_final_20.ptau"
PTAU=build_full/ppot20.ptau
export NODE_OPTIONS="--max-old-space-size=6144"
SNARKJS="npx --yes snarkjs"

ts() { date -u +"%H:%M:%SZ"; }

# --- disk watchdog: kill our process group if free space gets dangerous ---
(
  while true; do
    FREE_KB=$(df -k "$HOME" | tail -1 | awk '{print $4}')
    if [ "$FREE_KB" -lt 1572864 ]; then   # 1.5 GB
      echo "$(ts) WATCHDOG: free disk < 1.5GB — aborting run to protect the machine"
      kill -TERM -- -$$ 2>/dev/null
      exit 1
    fi
    sleep 60
  done
) &
WATCHDOG=$!
trap 'kill $WATCHDOG 2>/dev/null || true' EXIT

echo "$(ts) == [1/6] compile program_v2.circom (8 rounds, no --sym)"
CIRCOM="circom"; command -v circom >/dev/null || CIRCOM="npx --yes circom2"
[ -f build_full/program_v2.r1cs ] || $CIRCOM program_v2.circom --r1cs --wasm -o build_full
$SNARKJS r1cs info build_full/program_v2.r1cs

echo "$(ts) == [2/6] fetch public 2^20 powers-of-tau (resumable)"
if [ ! -f "$PTAU" ] || [ "$(stat -f%z "$PTAU")" -lt 2000000000 ]; then
  curl -L -C - --retry 8 --retry-delay 20 -o "$PTAU" "$PTAU_URL"
fi
echo "$(ts) ptau size: $(stat -f%z "$PTAU") bytes"

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
