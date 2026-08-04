# program_v2 SNARK circuits — status: TOOLCHAIN-VALIDATED SCAFFOLD

What this directory is: the circom implementation of the PROGRAM_V2
execution statement from `docs/ZK_SNARK_SPIKE.md`, plus the scripts to
compile, prove, verify, and — the part that matters — cross-check the
circuit's public outputs against the normative Python transcript in
`zk-provenance/zk_provenance.py` (spec-locked by
`test_program_v2_matches_normative_spec`).

## Files
- `program_v2_lib.circom` — templates: `ProgramV2Round(i)`, `ProgramV2(n)`.
  Statement: given PUBLIC st0, there exist PRIVATE (pDigest, sDigest, r)
  with stN = the n-round chain and commitment = SHA256(p‖s‖st0‖r).
  The `output_hash = SHA256("out2:"+hex(stN))` step happens OUTSIDE the
  circuit (public, cheap) so ASCII-hex never enters the constraint system.
- `program_v2.circom` — normative 8-round profile (~575k constraints
  estimated; needs a 2^20 powers-of-tau).
- `program_v2_dev.circom` — 2-round DEV profile (219,872 constraints
  measured; 2^18 ptau). Toolchain validation ONLY — never ship its proofs.
- `make_input.py` / `check_public.py` — Python-side witness builder and
  the circuit↔transcript equality check.
- `build.sh dev|full` — end-to-end pipeline.

## Toolchain
`npm install` in this directory provides circomlib, snarkjs, and circom2
(the WASM build of the circom 2 compiler — used because no native circom
ships via Homebrew and this machine has no Rust toolchain; a native binary
is ~2-10x faster and drop-in if installed later).

## Honest status ladder (do not skip rungs in any claim)
1. ✅ Circuit compiles; constraint counts known.
2. ✅ Dev-profile end-to-end PASSED 2026-08-03: local 2^18 ceremony →
   groth16 prove → verify OK → `check_public.py` MATCH on stN, commitment,
   AND st0 against the Python transcript (circuit hash 489c0fc1…9de2099f).
   ~2h wall-clock on an M-series laptop with the WASM/pure-JS toolchain,
   dominated by the powers-of-tau prepare step.
3. ✅ Full 8-round profile PASSED 2026-08-04: public Hermez 2^20 ceremony
   file (`powersOfTau28_hez_final_20.ptau`, blake2b verified against the
   published hash — twice, wrapper + runner) → groth16 setup (595,040
   constraints) → prove → verify OK → `check_public.py` MATCH on stN,
   commitment, AND st0 vs the Python transcript (circuit hash
   4842082b…6ffc359c; stN f462ad35…5caf4986, commitment 4c4b8ec6…ebe1c2d3
   for model gpt-class-v3 / prompt "hidden P" / seed "seed-1").
   Evidence: `evidence_8round_2026_08_04/` (proof, public signals,
   verification key, expected transcript — the private input.json is
   deliberately NOT committed). Ops note: the 1.2GB fetch needed a
   resume loop with long backoff (ISP throttling + connection resets)
   and `caffeinate`; setup itself was fast because the hez "final" file
   is already phase-2 prepared.
4. ✅ `snark-exec-v1` receipt integration PASSED 2026-08-04: engine
   sanitizer arm (`_sanitize_snark_exec_v1`) recomputes every pure-hash
   binding server-side (anchored hash == SHA-256("out2:"+hex(stN)) from
   the proof's own public signals; st0 == model commitment), whole-record
   rejection; `build_snark_anchor_payload` in zk_provenance.py; 9 tests
   in tests/test_snark_receipt.py over the COMMITTED evidence proof,
   including the four forgery classes — A: same proof/different anchor,
   B: output not derived from stN, C: model swap (all engine-rejected),
   and D: tampered-but-hash-consistent signals REJECTED by live
   `groth16 verify` while the genuine proof passes. The pairing check is
   deliberately client-side; the server's checks are hashing only.
5. ☐ Production trusted-setup story (a single-contributor local ceremony
   is fine for development and WORTHLESS as a public trust claim — the
   contributor can forge proofs; needs a real MPC or a universal-setup
   scheme like PLONK before any external claim).

## What even rung 5 would NOT give
Proving PROGRAM_V2 executed is proving a hash-chain stand-in executed —
NOT that an LLM produced the output (gaps G2/G3 in the threat model stay
open). External copy keeps that distinction explicit, always.
