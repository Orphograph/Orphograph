#!/bin/bash
# ceremony_contribute.sh — one-command phase-2 ceremony steps (CEREMONY.md).
# Wraps the repo-local snarkjs so contributors need only node + this repo.
set -euo pipefail
cd "$(dirname "$0")"

SNARKJS="node node_modules/snarkjs/build/cli.cjs"
command -v node >/dev/null || { echo "node is required"; exit 1; }
[ -f node_modules/snarkjs/build/cli.cjs ] || { echo "run: npm install (in $(pwd))"; exit 1; }

PTAU=build_full/ppot20.ptau
R1CS=build_full/program_v2.r1cs
mkdir -p ceremony

case "${1:-}" in
  init)
    [ -f "$R1CS" ] || { echo "missing $R1CS — run the compile step first (build.sh / run_full_overnight.sh [1/6])"; exit 1; }
    [ -f "$PTAU" ] || { echo "missing $PTAU — fetch + blake2b-verify per run_full_overnight.sh [2/6]"; exit 1; }
    $SNARKJS groth16 setup "$R1CS" "$PTAU" ceremony/ckt_0000.zkey
    echo "init done → ceremony/ckt_0000.zkey (send to contributor 1)"
    ;;
  contribute)
    IN=${2:?usage: contribute <in.zkey> <out.zkey> "<name>"}
    OUT=${3:?usage: contribute <in.zkey> <out.zkey> "<name>"}
    NAME=${4:?usage: contribute <in.zkey> <out.zkey> "<name>"}
    $SNARKJS zkey contribute "$IN" "$OUT" --name="$NAME" -v
    echo
    echo "PUBLISH the contribution hash printed above, then destroy your"
    echo "entropy and publicly attest that you did."
    ;;
  finalize)
    BEACON=${2:?usage: finalize <beacon_hex> <n_iterations_exp>}
    ITER=${3:-10}
    LAST=$(ls ceremony/ckt_*.zkey | sort | tail -1)
    $SNARKJS zkey beacon "$LAST" ceremony/ckt_final.zkey "$BEACON" "$ITER" -n="final beacon"
    $SNARKJS zkey export verificationkey ceremony/ckt_final.zkey ceremony/verification_key.json
    shasum -a 256 ceremony/ckt_final.zkey ceremony/verification_key.json
    ;;
  verify)
    [ -f ceremony/ckt_final.zkey ] || { echo "no ceremony/ckt_final.zkey yet"; exit 1; }
    $SNARKJS zkey verify "$R1CS" "$PTAU" ceremony/ckt_final.zkey
    ;;
  *)
    echo "usage: $0 init | contribute <in> <out> <name> | finalize <beacon_hex> [iter] | verify"
    exit 1
    ;;
esac
