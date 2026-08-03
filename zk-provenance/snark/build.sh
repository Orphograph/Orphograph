#!/bin/bash
# build.sh — compile + prove + verify the program_v2 circuits.
#
#   ./build.sh dev    — 2-round DEV profile end-to-end (small local ptau;
#                       validates toolchain + that the circuit matches the
#                       Python transcript). Minutes on a laptop.
#   ./build.sh full   — normative 8-round profile. Needs a 2^20 powers-of-tau
#                       (local generation is CPU/RAM-heavy; run overnight or
#                       fetch a ceremony file and verify its hash first).
#
# groth16 REQUIRES a trusted setup. The dev flow below runs a single-
# contributor local ceremony — fine for development, NOT for production
# claims. See docs/ZK_SNARK_SPIKE.md §4.2 before shipping anything.
set -euo pipefail
cd "$(dirname "$0")"

MODE="${1:-dev}"
SNARKJS="npx --yes snarkjs"

if [ "$MODE" = "dev" ]; then
  CIRCUIT=program_v2_dev
  POWER=18            # 2-round profile measured at 219,872 constraints < 2^18
elif [ "$MODE" = "full" ]; then
  CIRCUIT=program_v2
  POWER=20
else
  echo "usage: $0 dev|full"; exit 2
fi

echo "== [1/6] compile $CIRCUIT.circom"
mkdir -p build_"$MODE"
# circom2 = WASM build of the circom 2 compiler (no Rust toolchain needed);
# swap in a native `circom` binary if one is installed.
CIRCOM="circom"; command -v circom >/dev/null || CIRCOM="npx --yes circom2"
$CIRCOM "$CIRCUIT.circom" --r1cs --wasm --sym -o build_"$MODE"
$SNARKJS r1cs info build_"$MODE"/"$CIRCUIT".r1cs

echo "== [2/6] powers of tau (local, single-contributor — dev only)"
PTAU=build_"$MODE"/pot"$POWER".ptau
if [ ! -f "$PTAU" ]; then
  $SNARKJS powersoftau new bn128 "$POWER" build_"$MODE"/pot_0000.ptau -v
  $SNARKJS powersoftau contribute build_"$MODE"/pot_0000.ptau \
      build_"$MODE"/pot_0001.ptau --name="orpho-dev" -v -e="$(head -c 32 /dev/urandom | xxd -p)"
  $SNARKJS powersoftau prepare phase2 build_"$MODE"/pot_0001.ptau "$PTAU" -v
  rm -f build_"$MODE"/pot_0000.ptau build_"$MODE"/pot_0001.ptau
fi

echo "== [3/6] groth16 setup + contribution"
$SNARKJS groth16 setup build_"$MODE"/"$CIRCUIT".r1cs "$PTAU" build_"$MODE"/ckt_0000.zkey
$SNARKJS zkey contribute build_"$MODE"/ckt_0000.zkey build_"$MODE"/ckt_final.zkey \
    --name="orpho-dev-2" -v -e="$(head -c 32 /dev/urandom | xxd -p)"
$SNARKJS zkey export verificationkey build_"$MODE"/ckt_final.zkey build_"$MODE"/verification_key.json

echo "== [4/6] witness input (Python transcript is the reference)"
ROUNDS=8; [ "$MODE" = "dev" ] && ROUNDS=2
python3 make_input.py --model gpt-class-v3 --prompt "hidden P" --seed "seed-1" \
    --rounds "$ROUNDS" --out build_"$MODE"/input.json --expected build_"$MODE"/expected.json
node build_"$MODE"/"$CIRCUIT"_js/generate_witness.js \
    build_"$MODE"/"$CIRCUIT"_js/"$CIRCUIT".wasm build_"$MODE"/input.json build_"$MODE"/witness.wtns

echo "== [5/6] prove + verify"
$SNARKJS groth16 prove build_"$MODE"/ckt_final.zkey build_"$MODE"/witness.wtns \
    build_"$MODE"/proof.json build_"$MODE"/public.json
$SNARKJS groth16 verify build_"$MODE"/verification_key.json \
    build_"$MODE"/public.json build_"$MODE"/proof.json

echo "== [6/6] cross-check circuit outputs against the Python transcript"
python3 check_public.py build_"$MODE"/public.json build_"$MODE"/expected.json
echo "DONE ($MODE profile)"
