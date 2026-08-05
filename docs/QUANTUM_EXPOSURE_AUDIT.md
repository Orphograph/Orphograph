# Quantum Exposure Audit — every cryptographic primitive in the repo

Status: internal engineering audit. Read-only analysis; no code was changed.
Date of sweep: 2026-08-04. Scope: `server/`, `mcp/`, `capture/`, `dist/`,
`zk-provenance/`, `tools/`, `scripts/`, `integrations/`, `sdk-*/`,
`verifier-js/`, `web/`, `.github/`, `lightroom-plugin/`.

Language discipline (binding, same rule as every other doc here):
**tamper-evident, not tamper-proof.** No court-admissibility framing, no
AI-detection or authorship claims, no "guarantee". This document describes
what breaks and what does not; it does not describe legal consequences.

---

## 0. The one-paragraph answer

The core product claim — *content with this fingerprint existed no later
than a specific Bitcoin block* — rests on **SHA-256 and proof-of-work
alone**. There is no signature anywhere in that path. A cryptographically
relevant quantum computer (CRQC) does not falsify it, retroactively or
prospectively. Every Shor-vulnerable primitive in this repo sits in an
**optional layer wrapped around** that claim (authorship signature, device
attestation, knowledge proof, execution proof) or in **payment/transport
plumbing** (BIP-32 address derivation, TLS). The honest exposure is
therefore narrow but real: the optional layers go to zero, and the public
copy in two places promises a hedge (`SHA-512 sibling on every receipt`)
that the code does not actually deliver on every receipt.

---

## 1. Classification key

| Class | Meaning |
|---|---|
| **PQ-SAFE** | Hash-based or symmetric with adequate margin after Grover. |
| **DEGRADED** | Survives, reduced margin. Post-Grover effective bits stated. |
| **BROKEN** | Shor-vulnerable: any discrete-log, factoring, or pairing scheme. |

Grover framing used throughout, stated conservatively:

* Preimage on an *n*-bit hash: ~2^(n/2) quantum queries, and realistic
  circuit-depth limits push the practical cost higher than that bound.
* Collision: classical birthday ~2^(n/2) is already the binding number.
  The BHT quantum collision attack at ~2^(n/3) requires quantum memory on
  the same order and is not considered competitive with classical machines.
  We therefore do **not** claim SHA-256 collision resistance degrades.
* Grover needs an oracle it can evaluate in superposition. It applies to
  **offline** search (inverting a stored digest) and **not** to guessing a
  bearer token against a live HTTP endpoint. This distinction matters for
  the token findings in §5 and is stated there rather than glossed.

---

## 2. The anchor path — PQ-SAFE

This is the product. Everything in this section is hash-only.

| Primitive | file:line | Protects (what breaks if it breaks) | Algorithm / size | Class |
|---|---|---|---|---|
| File digest, streamed | `server/verify_cli.py:26-32`; `mcp/orphograph_mcp.py:113`; `capture/orphograph_capture.py:75-82`; `dist/orphograph-verify/verify.py:54-62` | The file→receipt binding. Break it and a different file claims the anchored timestamp. | SHA-256, 256-bit, 1 MiB chunks | **PQ-SAFE** |
| SHA-512 sibling | `server/engine.py:283,307-310,386`; `verifier-js/orphograph_verify.js:126-131` | Second, independent witness of the same bytes. See §2.1 — weaker than the copy implies. | SHA-512, 512-bit | **PQ-SAFE** |
| Anchored-digest shape gate | `server/engine.py:96-97,304-306` | Rejects non-canonical hex before it reaches a calendar. | n/a (validator) | n/a |
| OTS submission envelope | `server/engine.py:33-35,52-75,373` | Exactly 32 bytes of SHA-256 leave the process; the digest is what the calendar aggregates. | OTS v1, `OTS_TAG_SHA256 = 0x08` | **PQ-SAFE** |
| OTS commitment ops | `server/upgrade_worker.py:100-124` | Walks `OP_SHA256`/`OP_APPEND`/`OP_PREPEND` from the anchored digest to the calendar's pending commitment. Pure hashing. | SHA-256 | **PQ-SAFE** |
| Bitcoin time attestation | delegated — `dist/orphograph-verify/verify.py:65-96`, `verify_lineage.py:184-215` shell out to the external `ots` client | The "no later than" property itself. | Proof-of-work; see §2.2 | **inherited, not ours** |

### 2.1 The SHA-512 sibling is weaker than the public copy states

Three facts, all verifiable in the tree:

1. **It is optional at every layer.** `server/app.py:2236,2265-2266` accepts
   `sha512_hex` and silently drops it if it is not a string.
   `server/engine.py:283` defaults it to `None`.
   `verifier-js/orphograph_verify.js:114` computes it only when the caller
   asks. `dist/orphograph-verify/verify.py` and `server/verify_cli.py:68-77`
   check it **only when present**.
2. **It is client-supplied and never independently derived.** The office
   never sees the bytes (by design), so it cannot compute SHA-512 itself.
   The sibling is an assertion by the anchoring client.
3. **It is not anchored.** The OTS submission carries the SHA-256 only
   (`server/engine.py:52-56` rejects anything that is not exactly 32 bytes).
   `sha512_hex` lives in `receipt.json`, whose integrity depends on the
   receipt store — not on Bitcoin.

Shipped receipts confirm the gap: of the first three under `receipts/`,
`4WJmffxdXvpK6RL5` and `99DPOhO1_BKawuae` carry **no** `sha512_hex`;
`8lDnKzQe4pmH8XuL` does. Folder anchors never carry one at all — the
anchored value is a Merkle root, not a file digest.

This is a **present-tense factual error in public copy**, independent of
quantum computing. See §7 R1.

### 2.2 What the repo does *not* verify

There is no Bitcoin block-header parsing anywhere in this codebase. The
chain half of the proof is delegated to the external `ots` client. Two
consequences worth stating plainly:

* Bitcoin's own post-quantum posture (spending-key signatures, mining
  economics) is **outside this repo** and is not classified here. Guessing
  at it would be dishonest.
* `dist/orphograph-verify/verify.py:90-96` substring-matches `root_hex` in
  the `ots` client's combined output and **never gates on `returncode`**.
  This is a classical correctness bug, not a quantum one — logged in §8.

---

## 3. The Merkle layer and edit lineage — PQ-SAFE

| Primitive | file:line | Protects | Algorithm | Class |
|---|---|---|---|---|
| RFC 6962 leaf | `server/merkle.py:98-104`; `dist/orphograph-verify/merkle.py:108-114`; `mcp/orphograph_mcp.py:283`; `sdk-python/orphograph/_merkle.py:109` | Which file, at which relative path, with which contents, is in the set. Path is bound in, so a rename changes the root. | `SHA-256(0x00 ‖ rel_path_utf8 ‖ 0x00 ‖ file_sha256)` | **PQ-SAFE** |
| RFC 6962 internal node | `server/merkle.py:107-111`; `dist/orphograph-verify/merkle.py:117-121` | Tree structure. | `SHA-256(0x01 ‖ left ‖ right)` | **PQ-SAFE** |
| Odd-node promotion | `dist/orphograph-verify/merkle.py:174-176` | CVE-2012-2459 second-preimage ambiguity. The tree promotes the lone node; it never duplicates. Correct, and correctly documented. | structural | **PQ-SAFE** |
| Inclusion proof verify | `dist/orphograph-verify/merkle.py:338-376` | Selective disclosure of one file without revealing the rest. | SHA-256 fold | **PQ-SAFE** |
| Manifest re-fold | `server/merkle.py` `MerkleTree.from_manifest`; `dist/.../merkle.py:234-280` | A manifest whose stored root does not derive from its own leaves is rejected. | SHA-256 | **PQ-SAFE** |
| **Edit-lineage reserved leaf** | `server/engine.py:106,123-132`; `dist/orphograph-verify/verify_lineage.py:94-99` | Order of commitment: this version's anchored root commits to the parent's anchored root. | `SHA-256(0x00 ‖ ".orphograph/parent" ‖ 0x00 ‖ parent_root)` | **PQ-SAFE** |

The lineage commitment deserves an explicit note because it is the one
optional-looking feature that is **inside** the anchored bytes. The reserved
leaf rides the ordinary manifest, so the parent root is folded into the same
32 bytes that go to the calendars (`server/engine.py:100-106`). The server
re-derives it rather than trusting the hint block
(`server/engine.py:196-200`) and re-folds the whole tree
(`server/engine.py:213-221`). A CRQC does nothing to this. Lineage chains
survive intact.

Scope discipline already in the code and worth preserving: lineage proves
order of commitment only — never what changed, when the edit happened, or
who made it.

---

## 4. BROKEN — every Shor-vulnerable primitive in the repo

Seven items. Ordered by how close they sit to the product claim.

### B1. Ed25519 manifest authorship signature

* `server/manifest_signature.py:52-62` (backend), `:145-173` (`did:key`
  encode/decode), `:201-220` (sign/verify), `:181-196` (canonical bytes).
* Enforcement: `server/app.py:3591-3606` — a manifest **with** a signature
  block MUST verify (400 on failure, 503 if the backend is missing); a
  manifest **without** one anchors exactly as before.
* Protects: the claim that a specific key asserted authorship of a manifest.
* Algorithm: Ed25519 (Curve25519, 255-bit group, 128-bit classical security),
  64-byte signature, 32-byte public key.
* **BROKEN.** Shor recovers the private key from the published `did:key`
  public key. Any content can then be signed as that author.
* **Blast radius is narrow and this matters:** signatures are strictly
  additive. The anchor path never requires one. Public copy mentions this
  feature exactly once (`web/one-pager.html:115`, "Optional manifest
  signature"). Breaking it does not touch a single existence-and-time claim.

### B2. ECDSA P-256 hardware attestation

* Signing: `capture/orphograph_attest.py:110-124` (Secure Enclave keygen,
  `kSecAttrKeyTypeECSECPrimeRandom`, 256-bit), `:163-168`
  (`ecdsaSignatureMessageX962SHA256`), `:176-181` (signed message framing
  `orpho-hw-v1 ‖ 0x00 ‖ hash_hex ‖ 0x00 ‖ signed_at ‖ 0x00 ‖ device_id ‖ 0x00 ‖ counter`).
* Verifying: `dist/orphograph-verify/verify_hw.py:45-50` (secp256r1 domain
  parameters), `:71-108` (hand-rolled affine EC arithmetic), `:159-171`
  (ECDSA-SHA256 verify).
* Server side: `server/engine.py:637-704` **shape-validates only** — it
  never runs the signature check. `device_id` is *derived*, not asserted:
  `server/engine.py:677` and `verify_hw.py:197` both require
  `device_id == SHA-256(pubkey_der)`.
* Protects: "a device-held key signed this exact anchored hash."
* Algorithm: ECDSA over NIST P-256, SHA-256 message hash, DER signature.
* **BROKEN.** Shor on the 256-bit curve recovers the private key from the
  91-byte SPKI carried in the receipt itself. Attestations become forgeable
  by anyone.
* **Already weaker than it reads, pre-quantum:** `verify_hw.py:188-200`
  takes the public key *from the receipt* and derives `device_id` from that
  same key. There is no pin store — the TOFU state lives only on the capture
  host (`capture/orphograph_attest.py:59`) and is never transmitted. A
  single attestation is self-certifying today: any P-256 key passes, no
  enclave required. Four payload fields sit **outside** the signed message
  and are freely editable — `attestation_type`, `key_created_at`,
  `counter_kind`, `element` (`capture/orphograph_attest.py:317,322,324,326`).
  A CRQC removes the remaining signature value; the continuity property was
  never implemented.
* No public copy depends on this. There is no hardware-attestation, camera-
  signing, or device-signing claim anywhere in `web/`.

### B3. Schnorr/Okamoto proof of knowledge over RFC 3526 MODP Group 14

* `zk-provenance/zk_provenance.py:35-48` (2048-bit safe prime, `g = 2`,
  `q = (p-1)/2`), `:66-72` (second generator `h = g^(SHA256(salt) mod q)`),
  `:156-174` (prove), `:177-204` (verify:
  `g^s1 · h^s2 ≡ A · C^c (mod p)`).
* Verifier: `dist/orphograph-verify/verify_zk.py:41-53,73,76-101`.
* Server sanitizer: `server/engine.py:474-511` — binds `output_hash` to the
  anchored hash, caps fields, rejects the whole record on any violation.
* Protects: a signature-of-knowledge over `(output_hash, model_id)`. Per
  `docs/ZK_PROVENANCE_THREAT_MODEL.md` §2 G1, it does **not** prove
  execution, and no external sentence may say otherwise.
* Algorithm: 2048-bit finite-field discrete log; SHA-256 Fiat–Shamir
  challenge reduced mod q.
* **BROKEN.** Shor solves 2048-bit DL. Anyone can then compute a valid
  representation for any `C` and any `(output_hash, model_id)`. K1
  (knowledge) and K2 (binding) both go to zero.
* **Two honest nuances, in opposite directions:**
  * *Privacy survives.* `C = g^a · h^r` with `r ← secrets.randbelow(q)`
    (`zk_provenance.py:157`). Even given a solved DL, `a` remains hidden by
    the random blinder — one equation, two unknowns. A CRQC does **not**
    recover the prompt or seed from a published receipt.
  * *Binding was already weak, classically.* `h = g^(SHA256(salt) mod q)`
    with a public salt makes `log_g(h)` publicly recomputable
    (`zk_provenance.py:66-72`). Pedersen binding requires that log be
    unknown. The comment at `:67-68` conflates *unpredictable* with
    *unknown-dlog*. Correct construction is hash-to-group. Separately,
    `_h_bytes` (`:51-55`) concatenates without length delimiters, so the
    scalar derivation at `:156` is ambiguous across field boundaries. Both
    are classical defects, logged in §8.

### B4. groth16 over BN254 (`bn128`) — the execution-proof layer

* Circuit: `zk-provenance/snark/program_v2.circom:8`,
  `program_v2_lib.circom:31,40,52-91`. In-circuit hash is **SHA-256 only**
  (circomlib) — no Poseidon, no MiMC. 595,040 constraints at the 8-round
  profile.
* Curve confirmed empirically, not from comments:
  `zk-provenance/snark/full_run.log:51` (`Curve: bn-128`) and
  `evidence_8round_2026_08_04/verification_key.json`
  (`protocol=groth16, curve=bn128, nPublic=768`). `bn128` in snarkjs is
  **BN254 / alt_bn128** — ~254-bit prime, ~100-bit effective security after
  exTNFS. Not BLS12-381.
* Pinned by the server: `server/engine.py:542` hard-requires
  `protocol == "groth16"` and `curve == "bn128"`.
* The only place a pairing check actually runs:
  `dist/orphograph-verify/verify_snark.py:104-110`, shelling out to snarkjs.
* Protects: that the anchored hash is the image of the 8-round SHA-256 chain
  on inputs the prover knew (closing gap G1 of the ZK threat model).
* **BROKEN.** Shor breaks discrete log in the pairing groups; groth16
  soundness is gone.
* **What survives the break, and it is not nothing.** The server's three
  bindings in `_sanitize_snark_exec_v1` are **pure SHA-256** and remain
  sound:
  * `server/engine.py:533-535` — `output_hash == hash_hex`;
  * `server/engine.py:563-565` —
    `SHA-256("out2:" + stN_hex) == hash_hex`;
  * `server/engine.py:567-569` —
    `st0 == SHA-256(b"orpho-prog-v2" ‖ model_id)`.
  A post-CRQC forger can mint a passing pairing proof, but must still supply
  an `stN` whose `out2:` hash equals the anchored value, and an `st0` that is
  the correct model commitment. The layer therefore **degrades back to
  exactly the pre-SNARK, knowledge-only position** — it stops proving
  execution and stops nothing else.
* **Assurance is already near zero today, for a non-quantum reason.**
  `zk-provenance/snark/run_full_overnight.sh:93-103` runs `groth16 setup`
  and then proves against `ckt_0000.zkey` — **skipping** the
  `zkey contribute` phase-2 step. `zk-provenance/snark/CEREMONY.md:12-14`
  says so outright: whoever ran that setup can forge proofs. Phase 1 is
  inherited from a public ceremony and BLAKE2b-512-verified
  (`run_full_overnight.sh:81-90`) — that half is sound.
* **`vk_sha256` is self-referential.** `server/engine.py:544-549` checks
  64-hex shape and nothing else; there is no canonical VK hash constant
  anywhere in `server/`, `dist/`, or `zk-provenance/`.
  `verify_snark.py:87-90` compares the receipt's field against the hash of
  the VK file *the auditor supplies via `--vk`*. A submitter who runs their
  own setup produces a receipt that passes every server check and passes the
  offline verifier including the live pairing check. The word "pinned" at
  `verify_snark.py:90` overstates it. This is classical; logged in §8.
* **The MODP commitment does not enter the circuit.** Confirmed. The
  circuit's `commitment` is `SHA-256(pDigest ‖ sDigest ‖ st0 ‖ r)` over
  bn128 field bits (`program_v2_lib.circom:84-90`) with a fresh 256-bit `r`
  (`make_input.py:59`) unrelated to the Schnorr blinder. B3 and B4 are
  mutually exclusive proof types (`server/engine.py:487-490`) sharing only
  the `output_hash` / `model_id` labels. Any "drop-in scaffold" phrasing
  refers to the JSON wire format, never the mathematics — as
  `docs/ZK_SNARK_SPIKE.md` §3 already states.

### B5. secp256k1 / BIP-32 extended-public-key derivation

* `server/btc_hd.py:33-38` (curve parameters), `:41-90` (affine point
  arithmetic), `:177` (`HMAC-SHA512` child derivation), `:250-251`
  (`RIPEMD-160(SHA-256(pubkey))` → HASH160).
* Protects: **payment privacy**, not receipt integrity. A fresh address per
  order so on-chain observers cannot link customers. The docstring is
  correct that the xpub alone cannot spend.
* **BROKEN.** Shor on secp256k1 recovers the master private key from the
  xpub, which is present on the server. Exposure is **treasury funds**, not
  receipts. Nothing in the anchor, verification, or receipt path touches it.
* `hashlib.new("ripemd160", ...)` at `server/btc_hd.py:251` is separately
  **DEGRADED** — see §5.

### B6. TLS in transit (implicit, everywhere)

* Calendar submission: `server/engine.py:52-71` (`urllib` over the five
  HTTPS calendars at `:37-43`).
* Capture daemons: `capture/orphograph_capture.py:103`,
  `capture/orphograph_usb.py:107,142` — **no `ssl` module imported, no
  `SSLContext`, no `cafile`, no pinning.** System trust store only.
* Webhook/payment/price endpoints: `server/stripe_webhook.py`,
  `server/nowpayments_webhook.py`, `server/mempool_watcher.py:33-34`,
  `server/btc_price.py:39`.
* Browser/plugin surfaces: `dist/browser-extension/background.js:12,49`;
  `dist/lightroom-plugin/Orphograph.lrplugin/OrphographAPI.lua:22,72`.
* Protects: confidentiality and integrity of API keys, pack tokens, and the
  anchor request body in flight.
* **BROKEN** in the sense that mainstream TLS key exchange (ECDHE) and
  certificate signatures (ECDSA/RSA) are Shor-vulnerable. Two mitigating
  facts: (a) migration is a platform concern — hybrid PQ key exchange is
  already shipping in browsers and CDNs, and this repo inherits it without
  code changes; (b) harvest-now-decrypt-later is low-value here, because
  the only secrets in flight are API keys and pack tokens that can be
  rotated, and the payloads are digests that are published anyway.
* Adjacent classical finding: `--endpoint` accepts `http://` with no scheme
  validation (`capture/orphograph_capture.py:322-323`,
  `capture/orphograph_usb.py:349`) — a silent plaintext downgrade. §8.

### B7. Bitcoin's own signature and mining layer — named, not classified

Out of repo. The OTS proof path contains **no signature**, so a CRQC does
not falsify a commitment already in a block. Bitcoin's spending-key
signatures and mining economics are a separate question this codebase does
not control and this audit does not guess at. The public copy already draws
this line correctly (`web/faq.html:112`, `web/learn.html:292`).

---

## 5. DEGRADED — survives with reduced margin

| Primitive | file:line | Protects | Size → post-Grover | Notes |
|---|---|---|---|---|
| RIPEMD-160 (HASH160) | `server/btc_hd.py:250-251` | Bitcoin address derivation. | 160-bit → **~80-bit preimage**; ~80-bit collision classically | Weakest hash in the repo. Protocol-mandated, not a design choice. Payment path only. |
| Truncated HMAC-SHA256 email id | `server/auth.py:92` (`.hexdigest()[:16]`) | Non-reversible on-disk email identifier; blocks dictionary attack on the receipts→email map. | 64-bit tag → **~32-bit preimage**; ~32-bit birthday collision | The 256-bit HMAC key itself is PQ-SAFE (~2^128). The truncation is the weak part and is already the binding number classically. |
| API key entropy | `server/api_keys.py:87` (`token_urlsafe(24)`) | Bearer authentication for `/api`. | 144-bit → **~72-bit** | Offline-relevant: `api_keys.py:35` stores `SHA-256(key)`. An attacker holding the ledger could Grover-search the preimage at ~2^72. Still far out of reach. |
| Session / magic-link tokens | `server/auth.py:181,228` (`token_urlsafe(24)`) | Session and sign-in link. | 144-bit → **~72-bit** | Offline-relevant for the same reason: `auth.py:118` stores `SHA-256(token)` as `token_hash` / `session_hash`. Short TTLs (`LINK_TTL_SEC`, `SESSION_TTL_SEC`) bound the value of a recovered token. |
| Pack claim codes, team invites | `server/credits.py:42`, `server/teams.py:55,59` (`token_urlsafe(10..12)`) | Bearer capability for prepaid credit / team join. | 72–80-bit classical → **N/A (online only)** | **Stored in plaintext** in the ledgers (`credits.py:68` writes `claim_code`; `teams.py:142-151` reads `invite_code`), so there is no stored digest for Grover to invert — the classical margin is the binding number and Grover does not apply to guessing against a live endpoint. Flagged because these are the lowest-entropy bearer capabilities in the repo and widening them costs nothing. Plaintext-at-rest is a separate classical note, §8. |
| Order / receipt identifiers | `server/engine.py:93`, `server/btc_payments.py:233`, `server/app.py:4533` (`token_urlsafe(8..12)`) | Nothing — identifiers, not capabilities. | 48–72-bit classical → **N/A** | **Verified not a capability:** private receipts are gated on session→`owner_id`, not on knowing the id (`server/app.py:1117-1131`), and the non-owner response is an indistinguishable 404. Public receipts are public by design. No secret, so no Grover exposure. |

---

## 6. PQ-SAFE — the rest of the inventory

| Primitive | file:line | Protects | Algorithm | Note |
|---|---|---|---|---|
| Lightning macaroon (L402) | `server/lightning.py:65-76,84-93,100-119` | Single-purpose paid bearer token: mint and verify. | HMAC-SHA256, 256-bit secret from `secrets.token_bytes(32)`, constant-time compare | **PQ-SAFE.** No signature, no asymmetric key. |
| Lightning preimage / payment hash | `server/lightning.py:189-190,274` | The payment-settled proof: `payment_hash = SHA-256(preimage)`, 32-byte random preimage. | SHA-256 | **PQ-SAFE.** Grover preimage ~2^128 against a 256-bit random. |
| Spent-set single-use | `server/lightning.py:127-157` | Replay of a settled payment. | append-only file | Non-cryptographic. |
| Webhook signatures (outbound) | `server/webhooks.py:258,294` | Integrity of events we send to customers; the `X-Orpho-Signature` scheme documented at `web/account.html:86-88`. | HMAC-SHA256, 192-bit secret | **PQ-SAFE.** Symmetric, not a public-key signature. |
| Webhook signatures (inbound) | `server/stripe_webhook.py:119-120`; `server/nowpayments_webhook.py:96-97` (SHA-512); `server/resend_webhook.py:84-87` | Authenticity of payment/email provider callbacks. | HMAC-SHA256 / HMAC-SHA512, constant-time compare | **PQ-SAFE.** |
| Newsletter token | `server/newsletter.py:86,104-105` | Unsubscribe/confirm link integrity. | HMAC-SHA256 | **PQ-SAFE.** |
| BTC claim pepper | `server/btc_claims.py:63` | Email→claim mapping on disk. | HMAC-SHA256 (untruncated) | **PQ-SAFE.** |
| Admin token compare | `server/app.py:965` | Timing-safe admin auth compare. | `hmac.compare_digest` | **PQ-SAFE.** |
| BIP-32 child derivation | `server/btc_hd.py:177` | Deterministic child key derivation. | HMAC-SHA512 | Hash is PQ-SAFE; the **curve** it feeds is B5. |
| Base58Check checksum | `server/btc_hd.py:124` | xpub transcription errors. | double SHA-256, 4-byte | **PQ-SAFE** (error detection, not security). |
| PII envelope | `scripts/interim_pii_scrub.py:165-220` | At-rest confidentiality of stored email addresses. | Hand-rolled encrypt-then-MAC: HMAC-SHA256 keystream (CTR-style) + HMAC-SHA256 tag, 256-bit master, per-message nonce, domain-separated subkeys | **PQ-SAFE** (~2^128 key search). Construction is home-grown rather than a standard AEAD — classical review note, §8. |
| Powers-of-tau integrity | `zk-provenance/snark/run_full_overnight.sh:81-90`; `fetch_then_run.sh:35-43` | That the inherited phase-1 file is the published one. | BLAKE2b-512 | **PQ-SAFE.** |
| WebCrypto digests | `verifier-js/orphograph_verify.js:126-131`; `web/verify-js.js:295-296,381-382`; `dist/browser-extension/background.js:24,31` | Client-side file→receipt binding; file bytes never leave the device. | SHA-256 / SHA-512 via `crypto.subtle` | **PQ-SAFE.** |
| Pure-Lua SHA-256 | `lightroom-plugin/orphograph.lrdevplugin/sha256.lua` | In-process digest for the dev plugin. | SHA-256 | **PQ-SAFE** algorithm; has a classical bit-length wrap defect, §8. |
| Weekly / CI anchoring | `scripts/weekly_anchor.py:92,134`; `.github/actions/anchor/anchor_ci.py:35,45`; `tools/anchor_commit.py:73` | Repo-state and artifact anchoring. | SHA-256 (+ SHA-512) | **PQ-SAFE.** |
| Cache/etag digests | `server/app.py:471`; `capture/orphograph_attest.py:221-222` | Cache keys only. Not security. | truncated SHA-256 | n/a — **not** integrity checks, and `attest.py:221` must not be mistaken for one. |

**Absent by design, confirmed:** no MD5 in any executing path (the one
mention at `dist/lightroom-plugin/Orphograph.lrplugin/OrphographAPI.lua:4`
is a stale comment; `LrMD5` is never called). No SHA-1. No RSA. No
password-based KDF — there are no passwords. No AEAD or block cipher
outside `scripts/interim_pii_scrub.py`. No Diffie–Hellman key agreement
anywhere: the RFC 3526 modulus is used as a Schnorr/Pedersen group only.

---

## 7. The three questions

### Q1 — If a CRQC existed today

**(i) An already-issued receipt.**

*Stays sound.* Everything inside the anchored 32 bytes and everything the
OTS chain commits to:

* the SHA-256 file→receipt binding (`server/verify_cli.py:61`,
  `verifier-js/orphograph_verify.js:126`);
* for folder receipts, the RFC 6962 root including every relative path
  (`server/merkle.py:98-111`);
* the reserved-leaf parent commitment, i.e. the lineage link
  (`server/engine.py:123-132`);
* the OTS commitment operations from digest to calendar root
  (`server/upgrade_worker.py:100-124`);
* the fact that this commitment is *already in a block*. A CRQC cannot
  reach back and remove it. Rewriting settled history still means publicly
  out-working the accumulated chain.

*Breaks.* Only the optional, non-anchored layers stored beside the receipt:
a P-256 hardware attestation (B2), a Schnorr PoK (B3), a groth16 proof
(B4), and — if the manifest carried one — the Ed25519 authorship signature
(B1). An adversary can fabricate a receipt-shaped artifact carrying
convincing versions of all four. What they cannot do is get that artifact's
digest into a Bitcoin block that already happened.

The single most useful sentence in this audit: **a receipt whose OTS
attestation is confirmed in a Bitcoin block is retroactively sound after a
CRQC; the signature layers wrapped around it are not.** The existence-and-
time claim is the one that survives, and it is the one the product sells.

**(ii) A new receipt.**

Identical, plus one live-path concern: TLS (B6). An adversary who can break
TLS in real time could MITM the anchor request and substitute a digest.
This is detectable by the client, which holds the file and can re-verify
(`verifier-js/orphograph_verify.js:126`, `server/verify_cli.py:61`) — a
substituted digest simply fails to match. The window is a denial/confusion
attack, not a silent forgery. The signature-bearing options should be
treated as decorative from the CRQC date forward.

**(iii) An execution proof.**

Soundness of the pairing check is gone (B4). The claim degrades to exactly
what the pre-SNARK layer claimed: knowledge, not execution. The three
SHA-256 bindings the server actually runs
(`server/engine.py:533-535,563-565,567-569`) still hold, so a forged proof
must still hash-match the anchored output and the model commitment.

Worth stating without softening: **this layer's practical assurance is
already close to zero for a non-quantum reason** — phase 2 of the trusted
setup has no contributions (`zk-provenance/snark/CEREMONY.md:12-14`), and
`vk_sha256` binds to a key the verifier supplies rather than a published
canonical key. A CRQC changes the theoretical status of a layer that the
public copy already qualifies as "development-grade ... we do not yet make
this claim in any certified sense."

**(iv) A lineage chain.**

*Fully sound.* The commitment is SHA-256 inside the anchored root
(`server/engine.py:123-132`), re-derived and re-folded rather than trusted
(`server/engine.py:196-221`), and independently re-checked offline
(`dist/orphograph-verify/verify_lineage.py:94-99,242`). Order of commitment
survives a CRQC unchanged. Scope is unchanged too: order only, never what
changed or who changed it.

**(v) A Lightning payment credential.**

*The parts in this repo are sound.* The macaroon is HMAC-SHA256 under a
256-bit secret (`server/lightning.py:91,108-109`); the payment hash is
SHA-256 of a 256-bit random preimage (`server/lightning.py:189-190,274`).
Neither is Shor-vulnerable and both retain ~2^128 against Grover. What is
exposed sits **outside** this repo: Lightning channel and HTLC enforcement
ultimately rests on secp256k1 signatures on-chain, and the L402 token
travels over TLS (B6). A CRQC threatens the settlement rail, not our
credential format.

### Q2 — Which public claims become false, and which stay true

Checked against the actual wording in `web/*.html` and `web/llms.txt`.

**Stay TRUE under a CRQC — the large majority.**

| Claim | Where | Why it holds |
|---|---|---|
| "Shor's algorithm threatens signatures, not hash structures — and an OpenTimestamps proof is a chain of hash commitments with no signature in the proof path." | `web/faq.html:112`, `web/learn.html:292` | Accurate for the anchor path. Verified: `server/engine.py:33-75`, `server/upgrade_worker.py:100-124` contain no signature. |
| "Grover's algorithm leaves SHA-256 preimage resistance at roughly 2^128 ... NIST treats SHA-2 as secure for hashing in a post-quantum setting." | `web/faq.html:112`, `web/learn.html:292` | Correct and conservatively stated. |
| "it cannot silently falsify past commitments; rewriting history would mean publicly out-working the accumulated chain." | `web/faq.html:112` | Correct. This is the load-bearing sentence and it is right. |
| "That a file with this fingerprint existed by the time of the recorded Bitcoin block." | `web/faq.html:77` | The core claim. Unaffected. |
| "Records are tamper-evident, not tamper-proof." | `web/llms.txt:7,89` | Correct framing, and correct post-CRQC. |
| "Two different files cannot, in practice, produce the same fingerprint." | `web/learn.html:259` | Holds — we do not claim quantum collision degradation (§1). |
| "We cannot recover your file from the digest; nobody can." | `web/privacy.html:29-30`, `web/v2/index.html:357` | Holds at ~2^128 Grover. Also holds for the ZK layer: the random blinder keeps prompt/seed hidden even given a solved discrete log (§B3). |
| "the date can't be forged or revoked" / "can't be backdated, edited, or quietly removed" | `web/v2/index.html:109,176,369` | Holds — hash + proof-of-work, no signature. |
| Lineage scope: "proves the order of commitment — never what changed, when the edit happened, or who made it." | `web/index.html:258`, `web/llms.txt:51-56`, `web/certificate.html:80` | Holds exactly. |
| Merkle inclusion / selective disclosure | `web/about-the-office.html:73`, `web/dataset-provenance.html:63` | Holds. |
| "verifiable forever ... even if orphograph.com disappears" | `web/press-kit.html:71`, `web/v2/index.html:365`, `README.md:256-258` | Holds for the hash-and-chain path, which is what the offline verifier checks. |
| Webhook `X-Orpho-Signature` HMAC-SHA256 | `web/account.html:86-88` | Symmetric. Holds. |

**Become FALSE or require revision.**

| Claim | Where | Problem |
|---|---|---|
| "Every receipt also carries a SHA-512 sibling fingerprint for cryptanalytic diversity on a scale of decades." | `web/faq.html:112` (and `:34` JSON-LD twin), `web/learn.html:292` | **False today, for a non-quantum reason.** The sibling is optional, client-supplied, unanchored, and absent from shipped receipts (§2.1). Highest-priority correction in this document. |
| "We also embed a SHA-512 sibling witness in every receipt as a quantum-era hedge." / "Is this quantum-safe?" answer | `web/index-legacy.html:250,459-464` | Same defect, looser wording. This page is orphaned but **reachable** via the static wildcard in `server/app.py`, and is excluded from the compliance scan (`tests/test_compliance.py:36`). |
| "SHA-512 sibling (64 bytes) ... Quantum hedge; stored in receipt only" | `README.md:137` | The "stored in receipt only" half is accurate and honest; the surrounding table implies it is always present. |
| "Optional manifest signature" — as an *authorship* feature | `web/one-pager.html:115` | The feature listing stays true; the authorship property it implies becomes forgeable (B1). Needs a scope line if it is ever promoted. |
| "zero-knowledge execution proof — evidence that a fixed, published hash-chain procedure produced the output" | `web/anchor-output.html:90-96`, `web/mcp.html:111`, `web/lp/agent-receipts.html:119-120`, `web/llms.txt:58-67` | Soundness goes to zero under a CRQC (B4). The existing caveats — "proves a fixed hash-chain procedure ran, not that a specific AI model ran" and "the proving key ceremony is development-grade; we do not yet make this claim in any certified sense" — already do most of the work. They do not mention that the proof system is pairing-based and therefore not post-quantum. |
| "you would need to either find a SHA-256 collision (cryptographically infeasible) or re-mine the Bitcoin chain from the anchored block onward" | the credentials-standard comparison landing page under `web/lp/`, forgery-cost section | First half holds. Second half inherits Bitcoin's own PQ posture, which is outside this repo. Not false; under-scoped. |

**Notable: the public copy's overall shape is unusually defensible.** Almost
every claim reduces to hash preimage, hash collision, or proof-of-work. The
site repeatedly and deliberately states there is no signature in the proof
path, and that turns out to be true of the code. There is **no** public
claim that depends on P-256, Ed25519, the MODP group, or BN254 — the two
signature-adjacent mentions are the optional manifest signature and the
symmetric webhook HMAC.

### Q3 — Minimum change set to preserve the core claim under quantum attack

The finding that should drive the ranking: **the core claim already needs no
change.** Nothing in the file→digest→OTS→Bitcoin path is Shor-vulnerable.
The work below is about (a) removing a false statement, (b) making the
existing hedge real, and (c) not letting the optional layers be mistaken
for the core.

Ranked by risk-reduction per unit of effort.

**R1 — Correct the SHA-512 "every receipt" copy. Effort: hours. Risk
reduction: high (removes a false claim shipping today).**
Either narrow the wording in `web/faq.html:112` (+ the `:34` JSON-LD twin),
`web/learn.html:292`, `README.md:137` to "receipts anchored through clients
that submit it", or ship R2 and make it true. Also decide the fate of
`web/index-legacy.html` — it is reachable, carries the loosest version of
this claim plus a bare "We never see your file" at `:500`, and is excluded
from every compliance scan. Delete it or bring it into scope.

**R2 — Make the sibling real: require it, verify it, and anchor it.
Effort: days. Risk reduction: high.**
Three sub-steps, increasing in cost:
* *R2a (cheap):* make a present-but-mismatched `sha512_hex` fatal in every
  verifier. `server/verify_cli.py:68-77` already returns 3; confirm the
  same in `dist/orphograph-verify/verify.py` and
  `verifier-js/orphograph_verify.js:191-200`.
* *R2b:* require `sha512_hex` on new single-file anchors
  (`server/app.py:2236,2265-2266` currently drops it silently). Keep
  existing receipts valid — this is forward-only.
* *R2c (the real fix):* a v2 anchor whose submitted 32 bytes are
  `SHA-256(0x02 ‖ sha256 ‖ sha512)`, so the sibling is inside the Bitcoin
  commitment rather than beside it in the receipt store. Requires a new
  algorithm tag, a verifier arm, and a documented migration. Note honestly
  that this hardens mainly against a *classical* SHA-256 collision; it is
  not required for post-quantum safety of the existing claim.

**R3 — Label PQ class in the receipt itself. Effort: hours. Risk
reduction: medium-high (structural honesty, prevents future overclaim).**
Add a `pq_class` field to each optional proof block — `"hash-only"` for
lineage and the anchor, `"shor-vulnerable"` for `zk_provenance`,
`hardware_attestation`, and a manifest `signature`. Emit it from the
sanitizers (`server/engine.py:474-604,637-704`) and print it in the offline
verifiers. This makes the split machine-readable and survives copy drift.

**R4 — Publish a canonical verification-key hash. Effort: hours. Risk
reduction: medium (classical, but it is what makes B4 mean anything at
all).** `vk_sha256` is currently self-referential (§B4). Pin the canonical
value as a constant in `server/engine.py` and `verify_snark.py`, and
publish it. Without this, the SNARK layer's PQ status is academic — a
submitter can already substitute their own key.

**R5 — Complete or retire the phase-2 ceremony. Effort: days-weeks.
Risk reduction: medium, but only for a layer that is optional and
internal.** `zk-provenance/snark/ceremony_contribute.sh` is a written,
unexecuted runbook needing ≥3 independent contributors and a public random
beacon. Until it runs, the honest position is the one already in
`CEREMONY.md:12-14`. Under a CRQC this layer is broken regardless, so this
is a pre-quantum-credibility item, not a PQ mitigation.

**R6 — Add a post-quantum signature option for manifest authorship.
Effort: weeks. Risk reduction: low relative to cost.** ML-DSA (FIPS 204)
alongside Ed25519 in `server/manifest_signature.py`, selected by an
`alg` field, with `did:key` multicodec support. This protects an optional
feature used by no public claim. It is the right long-term answer for B1
and the wrong place to spend the next sprint.

**Explicitly not recommended:** rewriting the SNARK layer onto a
hash-based proof system (STARK/FRI) for post-quantum reasons. It is the
largest possible effort, and it protects a claim the product deliberately
does not make. If that layer is ever rebuilt, PQ soundness is a reason to
prefer a hash-based system — not a reason to rebuild it now.

---

## 8. Classical findings surfaced during this sweep

Not quantum. Recorded because omitting them while publishing a security
audit would be dishonest. These belong in a separate remediation track.

1. `dist/orphograph-verify/verify_snark.py` prints **PASS and exits 0 with
   no proof verification** when `--vk` is omitted or snarkjs is missing:
   `ok = True` at `:73`, SKIP branches at `:92-95` and `:111-112` leave it
   untouched, falling through to `:114-118`.
2. `dist/orphograph-verify/verify_hw.py:188-200` has no pin store. A single
   attestation is self-certifying — any P-256 key passes. The
   "device-key continuity under first-use pinning" text at `:60-67,320` is
   not implemented by the shipped code.
3. `dist/lightroom-plugin/Orphograph.lrplugin/OrphographAPI.lua:30-40`
   interpolates the file path into a **shell string** and reads the digest
   back from a predictable shared-temp filename derived from `os.time()`.
   The anchored hash is substitutable there.
4. `dist/orphograph-verify/verify.py:90-96` ignores the `ots` client's
   return code; `verify_hw.py:315` and `verify_zk.py:195` use
   `if checks and ok == 0`, so an **empty** `--ots-dir` silently passes.
5. `zk-provenance/zk_provenance.py:66-72` — `h = g^(SHA256(salt))` makes
   `log_g(h)` public, so the Pedersen commitment is not binding. Correct
   construction is hash-to-group. Also `_h_bytes` (`:51-55`) has no length
   delimiters, and `_group_check` (`:79`) asserts `g^(p-1) == 1`, which is
   vacuously true.
6. `zk-provenance/snark/build.sh:43,51` pass 256 bits of ceremony entropy
   on the **command line**, visible in `ps` to any local user.
   `ceremony_contribute.sh:26` correctly prompts instead.
7. `capture/orphograph_attest.py:104-109` — the Secure Enclave key has no
   user-presence gate, so any process in the unlocked session can sign
   arbitrary hashes. Deliberate (unattended launchd signing), but it is the
   honest scope of a "device signed this" statement.
8. `capture/orphograph_capture.py:322-323`, `capture/orphograph_usb.py:349`
   — `--endpoint` accepts `http://` unvalidated. Silent plaintext downgrade.
9. `lightroom-plugin/orphograph.lrdevplugin/sha256.lua:39-46` — the
   bit-length encoding wraps at ~512 MB under 32-bit bitop semantics,
   silently producing a wrong digest. The comment claims a 4 GB cap.
10. Pack claim codes (`server/credits.py:68`) and team invite codes
    (`server/teams.py:142-151`) are stored **in plaintext** in their
    ledgers, unlike API keys and session tokens, which are stored as
    SHA-256 digests (`server/api_keys.py:35`, `server/auth.py:118`). Anyone
    who can read the ledger file holds live bearer capabilities. Hashing
    them at rest would match the pattern the rest of the codebase already
    follows.
11. `scripts/interim_pii_scrub.py:165-220` is a hand-rolled encrypt-then-MAC
    construction. The composition is done correctly (separate subkeys,
    domain separation, constant-time tag compare, encrypt-then-MAC order),
    but a standard AEAD would be preferable to a bespoke one.

---

## 9. Summary table — every BROKEN primitive

| # | Primitive | Location | What it protects | Reaches the core claim? |
|---|---|---|---|---|
| B1 | Ed25519 (Curve25519) | `server/manifest_signature.py:52-62,201-220`; enforced `server/app.py:3591-3606` | Manifest authorship | No — strictly optional |
| B2 | ECDSA P-256 | `capture/orphograph_attest.py:110-168`; `dist/orphograph-verify/verify_hw.py:159-171` | Device attestation | No — optional, no public claim |
| B3 | Schnorr/Okamoto over RFC 3526 MODP-14 (2048-bit) | `zk-provenance/zk_provenance.py:156-204`; `dist/orphograph-verify/verify_zk.py:76-101` | Knowledge proof (not execution) | No |
| B4 | groth16 over BN254 (`bn128`) | `zk-provenance/snark/`; `dist/orphograph-verify/verify_snark.py:104-110`; pinned `server/engine.py:542` | Execution proof for PROGRAM_V2 | No — and its SHA-256 bindings survive |
| B5 | secp256k1 / BIP-32 | `server/btc_hd.py:33-90` | Payment-address privacy; treasury | No — payment path only |
| B6 | TLS (ECDHE + X.509) | `server/engine.py:52-71`; `capture/*.py`; all webhook and price clients | Credentials and request bodies in flight | No — detectable, rotatable |
| B7 | Bitcoin spending signatures / mining | outside this repo | The chain the timestamp rests on | Named, not classified — no signature exists in the OTS proof path |

**Single highest-value mitigation: R1 + R2a — correct the "every receipt
carries a SHA-512 sibling" copy and make a present-but-mismatched sibling
fatal in every verifier.** It removes a claim that is false today, costs
hours, requires no protocol change, and is the only item on this list where
the gap between what the product says and what the code does is currently
load-bearing. Every genuinely Shor-vulnerable primitive in this repo sits
in an optional layer that no public claim depends on.
