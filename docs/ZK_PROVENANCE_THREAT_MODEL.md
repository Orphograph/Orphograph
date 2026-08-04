# ZK Agent-Provenance — Threat Model and Honest Limits

Status: prototype layer (`zk-provenance/`, receipt field `zk_provenance`,
offline verifier `dist/orphograph-verify/verify_zk.py`). INTERNAL until a
demand signal green-lights exposure; nothing here is marketing copy.

Language discipline (binding; same class of rule the project's regulatory
self-audit enforces on deploy copy): tamper-evident provenance only. Never "court-admissible",
"notarized", "legally binding", "guarantee". Never AI-detection or
authorship claims. Framing is PROVENANCE — what was committed when — not
detection of how content was made.

## 1. The statement actually proven today (`schnorr-zk-pok-v1`)

Given a receipt whose `hash_hex` = SHA-256(O) for an agent output O, the
`zk_provenance` field carries a non-interactive (Fiat-Shamir) Schnorr
proof of knowledge over the RFC 3526 2048-bit MODP group binding a Pedersen
commitment `C = g^a · h^r` (where `a = H(prompt, seed, model_id)`) to the
public pair `(output_hash, model_id)`.

A verifier with only the receipt (plus the .ots files for the Bitcoin
path) can confirm:

- **K1 — Knowledge:** at anchor time, the prover knew opening values
  `(a, r)` of the commitment, where `a` is derived from hidden inputs
  (prompt, seed) and the named `model_id`. The hidden inputs never leave
  the prover; they appear in no field, log, or hash preimage the verifier
  sees (test: `test_prompt_seed_never_appear_in_proof`).
- **K2 — Binding:** the proof is cryptographically bound (via the
  Fiat-Shamir challenge) to this exact `output_hash` and `model_id`; a
  proof lifted onto a different output or model fails verification
  (tests: `test_verify_fails_with_wrong_output_hash`,
  `_sanitize_zk_provenance` output-hash match rule).
- **K3 — Existence-in-time:** the standard Orphograph property — the
  receipt's hash existed no later than the Bitcoin attestation of its
  .ots proofs. Unchanged by this layer.

## 2. What is NOT proven (the gap that must accompany every claim)

- **G1 — Execution.** The proof does NOT show that
  `O = PROGRAM(model_id, prompt, seed)`. A prover could pick an arbitrary
  O and back-commit inputs. Closing G1 is the SNARK step
  (`docs/ZK_SNARK_SPIKE.md`); until it ships, any external sentence about
  this layer must include the knowledge-not-execution qualifier.
- **G2 — Model identity.** `model_id` is a label the prover asserts. No
  cryptographic tie to actual model weights exists (weight attestation is
  a separate, much harder problem).
- **G3 — Input meaningfulness.** Nothing shows the hidden prompt was a
  "real" prompt rather than random bytes.
- **G4 — Wall-clock time.** As everywhere in Orphograph: Bitcoin gives
  "no later than"; it never gives "at exactly".

## 3. Adversary analysis

| Adversary | Goal | Outcome |
|---|---|---|
| Forger with a chosen O | pass verification with fabricated inputs | SUCCEEDS today (G1) — this is exactly the SNARK gap; do not claim otherwise |
| Proof-swapper | attach a valid proof to a different receipt | FAILS — challenge binds output_hash; sanitizer refuses unbound proofs at write time; verifier re-checks at read time |
| Receipt tamperer | edit proof fields on disk | FAILS — Schnorr equation breaks (verified: CLI case 4) |
| Prompt extractor | learn prompt/seed from receipt | FAILS under DL assumption in the 2048-bit group — commitment is hiding; only H(P),H(S)-derived scalar `a` is committed, never plaintext |
| Blob smuggler | stuff data into zk_provenance | FAILS — strict sanitizer: fixed keys, digits-only, ≤700 chars, whole-proof rejection on any violation |
| Operator (Orphograph itself) | forge or alter proofs post-anchor | Receipt content self-binds: altering zk fields breaks verification; the anchored hash pins O. Operator can DROP the field (availability), not forge it (integrity) |

## 4. Cryptographic assumptions

- Discrete log / DDH in the 2048-bit RFC 3526 group (commitment hiding &
  binding; `h = g^{H(salt)}` nothing-up-my-sleeve second generator).
- SHA-256 collision & preimage resistance (all Orphograph anchoring).
- Fiat-Shamir in the random-oracle model (non-interactive challenge).
- These are standard primitives, standardly composed. The productization
  is what is new here; the mathematics is textbook and is described as such.

## 5. PROGRAM_V2 — the normative circuit target

Defined in `zk-provenance/zk_provenance.py` (spec-locked by
`test_program_v2_matches_normative_spec`):

```
st_0 = SHA256( "orpho-prog-v2" || UTF8(model_id) )
st_i = SHA256( st_{i-1} || SHA256(UTF8(prompt)) || SHA256(UTF8(seed)) || uint32_be(i) )   i = 1..8
O    = "out2:" + hex(st_8)
```

Every operation is SHA-256 over fixed-width inputs; prompt/seed enter only
via their own 32-byte digests, so a proving circuit has bounded private
witnesses regardless of prompt length. This transform is a deterministic
stand-in with a fixed transcript — it is NOT model inference, and no copy
may imply it is.

## 6. Field & interface contract

- Receipt field `zk_provenance` (machine proof) is disjoint from
  `attestation` (brief human claim). Sanitizer:
  `server/engine.py::_sanitize_zk_provenance` — proof_type allowlist,
  output-hash binding check, digits-only ≤700-char numeric fields,
  reject-whole-proof-on-violation.
- Offline verification: `dist/orphograph-verify/verify_zk.py`, stdlib
  only, no server, exit 0/1/2. Its PASS text states the K1-K3 scope and
  the G1 qualifier verbatim — the honesty line ships inside the tool.

## 7. Review checklist for any future external copy

1. Contains the knowledge-not-execution qualifier (G1)? 
2. Zero deny-phrases (self-audit passes)?
3. Frames as provenance, never detection/authorship?
4. Claims novelty only for the productization (agent/MCP capture surface,
   receipt format, offline verifier) — never for Schnorr/Pedersen/OTS
   themselves?
5. Routed through ip-redactor + founder before leaving the machine?
