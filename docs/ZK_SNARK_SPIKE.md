# SNARK Execution-Proof Spike (T4) — scoped feasibility, not implementation

Status: SPIKE. No SNARK code ships in this cycle. This document fixes the
statement, the toolchain choice, and the one honest correction to the
"drop-in scaffold" story, so the implementation cycle starts from ground
truth instead of optimism.

## 1. Target statement (closing gap G1 of the threat model)

Public inputs:  `model_id_hash` (SHA-256 of the model label),
                `output_hash`  (= receipt hash_hex),
                `input_commitment`
Private inputs: `p_digest = SHA256(prompt)`, `s_digest = SHA256(seed)`,
                commitment randomness `r`

Statement:
```
∃ (p_digest, s_digest, r):
      output_hash      == SHA256( PROGRAM_V2_core(model_id_hash, p_digest, s_digest) )
  AND input_commitment == Commit(p_digest || s_digest || model_id_hash ; r)
```
where `PROGRAM_V2_core` is the 8-round SHA-256 chain of
`docs/ZK_PROVENANCE_THREAT_MODEL.md` §5 (spec-locked by test). Because
prompt/seed enter only as digests, the witness is 3×32 bytes + r —
constant-size regardless of prompt length.

## 2. Toolchain assessment

- **circom + groth16 (snarkjs): RECOMMENDED.** PROGRAM_V2 is pure SHA-256
  over fixed-width inputs; circomlib's sha256 component is the most
  battle-tested hash circuit in the ecosystem. The whole statement is
  ~10 SHA-256 compressions (8 rounds + outer hash + commitment) — small
  by SNARK standards. Groth16 verification is cheap enough to re-implement
  in the stdlib verifier eventually (pairing check), though v1 should
  verify with snarkjs and record the verification transcript in the
  receipt workflow instead of claiming stdlib verification prematurely.
- **EZKL: NOT for PROGRAM_V2.** EZKL proves ONNX/NN inference. It becomes
  the right tool only if/when PROGRAM graduates from a hash-chain stand-in
  to an actual small model. Keep it named as the migration path, not the
  current tool.

## 3. The honest correction (wire format ≠ math compatibility)

The existing `schnorr-zk-pok-v1` commitment lives in a 2048-bit MODP
group. Proving statements about that group inside a SNARK circuit is
prohibitively expensive (non-native 2048-bit modular exponentiation).
Therefore the SNARK version does NOT reuse the Pedersen-over-MODP
commitment: `proof_type: "snark-exec-v1"` will use a circuit-friendly
commitment (SHA-256-based inside the same circuit; Poseidon if we accept
a second primitive). The receipt FIELD SHAPE carries over (proof_type,
output_hash, model_id, commitment, proof-blob, public-inputs); the
GROUP MATH does not. Any "drop-in scaffold" sentence must mean the JSON
interface — never the cryptography. The sanitizer gains one allowlisted
proof_type and per-type field validation; nothing else in the engine
changes.

## 4. Deliverables for the implementation cycle (in order)

1. circom circuit for PROGRAM_V2_core + SHA-256 commitment; witness
   builder from (prompt, seed, model_id).
2. Trusted setup story documented honestly (groth16 needs one; powers-of-
   tau + per-circuit phase 2 — or switch to PLONK/fflonk to reduce it).
3. `prove_snark.py` / `verify_snark.py` wrappers; `snark-exec-v1`
   sanitizer arm + tests (mirror the schnorr ones incl. reject-unbound).
4. Forgery test — REQUIRED acceptance: a (O, proof) pair where O was NOT
   produced by PROGRAM_V2 must fail verification. This is the test that
   distinguishes execution-proof from knowledge-proof.
5. Threat-model §2 update: G1 moves from "not proven" to "proven for
   PROGRAM_V2 under groth16 assumptions + setup ceremony caveat".

## 5. Non-goals (unchanged by the SNARK)

Model-weight identity (G2), input meaningfulness (G3), wall-clock
exactness (G4), and every deny-phrase rule. Proving execution of a
hash-chain stand-in is NOT proving LLM inference — external copy keeps
the distinction explicit.
