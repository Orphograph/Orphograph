# PROGRAM_V2 phase-2 ceremony — runbook (honesty-ladder rung 5)

Rung 5 requires a REAL multi-party trusted setup before any public SNARK
claim. This runbook makes each contribution one command. It cannot be
completed solo — the security claim is precisely that no single
contributor (including the founder) knows the full toxic waste.

## Trust model, stated plainly
- Phase 1 (powers of tau) is inherited from the public Hermez 2^20 file
  (blake2b-verified; see README rung 3). Its ceremony had many
  independent contributors — we do not redo phase 1.
- Phase 2 is circuit-specific. TODAY the zkey came from `groth16 setup`
  alone: whoever ran setup could forge proofs. That is why rungs 1–4
  carry a dev-grade posture.
- Rung 5 = at least 3 phase-2 contributions from parties who do not
  share infrastructure, plus a public random beacon finalization. The
  setup is sound if AT LEAST ONE contributor was honest and destroyed
  their entropy.

## Coordinator (founder) flow
1. Start from the committed circuit (`program_v2.circom`, hash pinned in
   README rung 3) and the verified ptau:
   `./ceremony_contribute.sh init` → produces `ceremony/ckt_0000.zkey`.
2. Send the current zkey to contributor N (any channel; the file is not
   secret). They run one command (below) and send back `ckt_000N.zkey`
   plus the printed contribution hash — collect those hashes publicly.
3. After the final contribution, apply a public beacon (e.g. a stated
   future Bitcoin block hash, announced BEFORE the block exists):
   `./ceremony_contribute.sh finalize <beacon_hex> <n_iterations>`.
4. Verify the whole chain end-to-end:
   `./ceremony_contribute.sh verify` (checks every link from the r1cs +
   ptau through every contribution to the final key).
5. Publish: final zkey hash, verification_key.json, every contribution
   hash, and each contributor's public attestation of entropy
   destruction. Only then may external copy reference the SNARK — and
   still only with the PROGRAM_V2-not-LLM scope note.

## Contributor flow (one command)
    ./ceremony_contribute.sh contribute <in.zkey> <out.zkey> "<your name/handle>"
It prompts for random entropy (mash keys), prints the contribution hash
to publish, and you then delete your entropy and attest to it.

## What this still does not give (unchanged from README)
Proving PROGRAM_V2 executed proves a hash-chain stand-in executed — NOT
that an LLM produced the output. Gaps G2/G3 in the threat model remain
open at every rung.
