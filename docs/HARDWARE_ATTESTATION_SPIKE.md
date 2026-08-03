# Hardware-Attested Capture — Research Spike

Status: research spike, doc-only. No code, no server changes, no hardware
purchased. Hardware BOM spend is FOUNDER-GATED; nothing in this document
authorizes ordering parts. INTERNAL until a demand signal green-lights
exposure; nothing here is marketing copy.

Language discipline (binding; same class of rule as
`docs/ZK_PROVENANCE_THREAT_MODEL.md` and the regulatory self-audit):
tamper-evident provenance only. Never "court-admissible", "notarized",
"legally binding", "guarantee". Never AI-detection or authorship claims.
Framing is PROVENANCE — what was committed when, and by which device key —
never detection of how content was made or proof of who made it.

---

## 1. Goal statement

Today an Orphograph receipt proves one thing about time:
**this hash existed no later than the Bitcoin attestation of its .ots
proofs** (the K3 property in the ZK threat model). It proves nothing about
*where* the hash came from — any machine anywhere could have POSTed it to
`/api/anchor`.

The goal of hardware-attested capture is one additive strengthening:

> Bind the claim "this hash was produced on this specific physical device,
> no later than this time" into the existing receipt, without changing
> anything about receipts that don't carry it.

Precisely, the new statement a verifier can check offline:

- **H1 — Device binding:** a signature over `(hash_hex, signed_at,
  device_id)` verifies against a public key held in (and non-exportable
  from) a hardware secure element. Whoever produced this receipt had live
  access to that physical device at signing time.
- **H2 — Continuity:** two receipts signed by the same pinned device key
  were signed by the same physical device (subject to the TOFU caveat in
  §4 — this is same-device continuity, not manufacturer identity, in v1).
- **H3 — Time bound, unchanged:** "no later than" still comes ONLY from
  the OTS→Bitcoin path. The `signed_at` inside the attestation is a
  claimed clock reading, corroborating at best (see threat table).

What is explicitly NOT claimed: that the device was uncompromised, that
the file content is authentic capture (analog hole), or that `signed_at`
is true wall-clock time. Same G-gap discipline as the ZK layer.

### Where it plugs in today

The two capture daemons are the natural signing points, because they
already run on the device that holds the file bytes:

- `capture/orphograph_capture.py` — `anchor_hash()` (line 84) POSTs
  `{"hash_hex", "sha512_hex", "client_label"}` to `/api/anchor` with an
  optional `X-Orpho-Api-Key` header. Stdlib-only by design.
- `capture/orphograph_usb.py` — same anchor contract (`anchor_hash()`,
  line 95), plus an on-drive `.orphograph/` index and offline proof
  bundles.

Neither sends `attestation` or `metadata` today, but the HTTP surface
already accepts both (`server/app.py` ~line 2107: "any caller can submit
these"), and `server/engine.py::anchor_hash` already takes `attestation`,
`metadata`, `c2pa_manifest_hash`, and `zk_proof` as optional parameters.
The hardware field would be one more optional parameter in exactly that
pattern.

---

## 2. Candidate secure elements — honest comparison for a solo bootstrapped founder

| | (a) Apple Secure Enclave | (b) ATECC608 | (c) FIDO2 / SoloKey-class | (d) TPM 2.0 |
|---|---|---|---|---|
| Hardware cost | **$0** (founder's existing Mac) | ~$1 part, but realistically $10–30 (breakout board + USB-I2C bridge or a Pi) | $20–50 per key (founder may already own one) | $0 if a Linux box with TPM exists; founder is on macOS daily |
| Algorithm | ECDSA P-256 (only curve the SE supports) | ECDSA P-256 | ECDSA P-256 (typ.), Ed25519 on some | ECDSA/RSA, rich but complex |
| Key non-exportability | Yes — key generated in and never leaves the SE (`kSecAttrTokenIDSecureEnclave`) | Yes — locked data zone | Yes | Yes |
| Manufacturer-rooted cert for the key | **Effectively no for a CLI** — App Attest / DeviceCheck is Apple's attestation service and is app-oriented; a plain `SecKeyCreateRandomKey` SE key comes with no Apple-signed certificate (VERIFY-BEFORE-BUILD #1) | Yes on pre-provisioned SKUs (Trust&GO-class parts ship with a device cert chained to a Microchip CA — exact SKU/chain: VERIFY-BEFORE-BUILD #3) | Yes — attestation cert at credential *creation* (packed attestation, vendor CA) | Yes — EK certificate from the TPM manufacturer, in NVRAM |
| Monotonic counter in hardware | No user-visible one (software counter only, marked as such) | Yes (VERIFY exact semantics) | Yes — signature counter in every assertion | Yes — NV counters; quotes also carry TPM clock |
| Integration effort from the Python daemons | Small compiled Swift/ObjC helper CLI wrapping the Security framework; Python `subprocess`s it. ctypes-to-Security is possible in principle but fragile (VERIFY-BEFORE-BUILD #2 covers entitlement/codesigning requirements) | CryptoAuthLib / `pip cryptoauthlib` over I2C; plus one-way, brick-if-wrong config-zone provisioning | The awkward one — see below | `tpm2-tools` CLI, well documented, Linux only in practice |
| Fits which Orphograph story | Dev spike; "Creator tier on a Mac" | The hardware/USB ingress product (`hash→OTS→BTC across software + hardware ingress` vision) | Opportunistic, if a key is already in a drawer | Server-side signing / Linux users |

### The FIDO2 awkwardness, stated honestly

FIDO2 authenticators sign *assertions*, and assertions are origin-bound by
design: the signature covers `authenticatorData` (which embeds the RP ID
hash and the signature counter) concatenated with a `clientDataHash`. Two
consequences:

1. Going through WebAuthn/a browser, you cannot make the device sign an
   arbitrary file hash — the client data is constructed by the browser.
   Talking raw CTAP2 (e.g. python-fido2), you *do* supply `clientDataHash`
   yourself and could set it to a digest derived from `(hash_hex,
   signed_at, device_id)` — but the verifier must then parse CTAP
   framing, know the RP ID you invented, and accept that you are
   repurposing an authentication protocol as a signing oracle
   (VERIFY-BEFORE-BUILD #4).
2. The attestation certificate exists only at credential creation, and
   consumer keys often use batch attestation certs (shared across ≥100k
   units for privacy) — so "manufacturer identity" is really "batch
   identity".

Workable, upstanding hardware — wrong-shaped API for this job. Rank it
last unless a customer specifically shows up holding one.

### Recommended dev-spike order

1. **Apple Secure Enclave** — zero hardware cost, on the founder's daily
   machine, same P-256 signature shape the other elements produce, so the
   receipt field and offline verifier built here transfer unchanged to
   ATECC/TPM later. This is the spike.
2. **TPM 2.0** — only if/when a Linux deployment target appears; free
   where the hardware already exists, and it's the path that upgrades v1
   TOFU to a manufacturer-rooted chain most cheaply.
3. **ATECC608** — only if a hardware product (the USB ingress vision)
   actually ships. It is the right part for that product, but it is a
   *product* decision with provisioning burden, not a spike. FOUNDER-GATED
   on cost, per the standing rule.
4. **FIDO2** — opportunistic experiment at most.

---

## 3. Attestation payload spec (proposed `hardware_attestation` receipt field)

### 3.1 Field placement: dedicated field, not the `attestation` dict

Two candidate homes exist in `server/engine.py::anchor_hash`:

- The existing `attestation` field —
  `_sanitize_attestation` allowlists five keys
  (`claim`, `author`, `license`, `url`, `signed_at`), strings only,
  500-char caps. It is documented as "a brief human authorship claim, not
  a generic data dump."
- A new dedicated field mirroring `zk_provenance` —
  `_sanitize_zk_provenance` has its own strict shape validator, rejects
  the WHOLE proof on any violation, requires the proof's `output_hash` to
  equal the receipt's `hash_hex`, and the record only carries the field
  when present so existing receipts stay shape-stable.
  `verify_receipt` surfaces it only when set (same rule as the folder
  fields and the Ed25519 `signature_verified`/`signer_kid` pair).

**Recommendation: new dedicated `hardware_attestation` field, mirroring
the `zk_provenance` pattern.** Reasons:

1. Semantics — this is a machine-verifiable cryptographic artifact, not a
   human claim. The repo already draws exactly this line between
   `attestation` and `zk_provenance`; a hardware signature is on the
   machine side of it.
2. Shape — a base64 SPKI public key (~120 chars) fits the 500-char cap,
   but a cert chain never will, and stuffing binary-ish data through the
   human-claim allowlist is the "blob smuggler" adversary the existing
   sanitizers exist to stop. A dedicated sanitizer can enforce the RIGHT
   caps (base64 alphabet, per-field lengths, whole-reject on violation).
3. Binding — the `zk_provenance` sanitizer's output-hash-must-match rule
   is exactly the rule this field needs (`_sanitize_hardware_attestation`
   must refuse any payload whose `hash_hex` differs from the receipt's),
   preventing the proof-swapper adversary at write time.
4. Shape stability — only written when present; every existing receipt,
   sidecar, and verifier is untouched. Additive by construction.

### 3.2 Proposed shape (v1, Secure Enclave / generic P-256)

```json
"hardware_attestation": {
  "attestation_type": "p256-device-sig-v1",
  "hash_hex":     "<64 hex — MUST equal receipt hash_hex; else whole field rejected>",
  "device_id":    "<64 hex — SHA-256 of the device public key (SPKI DER)>",
  "device_pubkey":"<base64 DER SubjectPublicKeyInfo, P-256 (~120 chars)>",
  "signed_at":    "<ISO-8601 UTC — CLAIMED client clock, corroborating only>",
  "counter":      123,
  "counter_kind": "software",
  "signature":    "<base64 DER ECDSA-SHA256 signature (~96 chars)>",
  "element":      "apple-secure-enclave",
  "cert_chain":   ["<base64 DER cert>", "..."]
}
```

Signed message (fixed order, deterministic, domain-separated — same
discipline as PROGRAM_V2's `"orpho-prog-v2"` tag):

```
msg = "orpho-hw-v1" || 0x00 || hash_hex(ascii) || 0x00 || signed_at(ascii)
      || 0x00 || device_id(ascii) || 0x00 || uint64_be(counter)
signature = ECDSA_P256_SHA256(device_private_key, msg)
```

Notes:

- `device_id` is derived from the pubkey, not asserted, so it cannot
  disagree with `device_pubkey`.
- `counter` with `counter_kind: "software"` is honest labeling: the SE has
  no user-visible hardware counter, so v1's counter is a local
  monotonically-incremented file — useful ordering hint, not a hardware
  guarantee. ATECC/TPM/FIDO2 variants would set `"hardware"`.
- `cert_chain` is OPTIONAL and expected ABSENT in v1 (TOFU, §4). Present
  only for elements that ship manufacturer certs.
- `element` is a label the client asserts (like `model_id` in the ZK
  layer — G2-class caveat: no cryptographic tie to the actual silicon in
  v1).

Sanitizer sketch (`_sanitize_hardware_attestation`): `attestation_type`
allowlist; `hash_hex` must equal the receipt hash; hex/base64 alphabet
checks; per-field caps (`device_pubkey` ≤ 200, `signature` ≤ 200,
`signed_at` ≤ 40, `element` ≤ 60, `cert_chain` ≤ 4 entries × ≤ 4000 chars
each, list absent otherwise); reject the whole field on ANY violation —
never persist a partial attestation that can't re-verify.

### 3.3 Size estimates

| Variant | Approx. serialized size |
|---|---|
| v1, no cert chain (SE / TOFU) | ~600 bytes |
| ATECC with 2-cert Microchip chain | ~2–3 KB |
| TPM quote + EK/AK chain | ~4–8 KB (VERIFY-BEFORE-BUILD #5) |

All well under receipt-JSON comfort; the ZK field already carries ~3.5 KB
of decimal group elements, so the storage precedent exists.

### 3.4 Transport

The daemons add one optional key to the existing POST body —
`{"hash_hex", "sha512_hex", "client_label", "hardware_attestation": {…}}`
— behind an opt-in flag (e.g. `--hw-attest`). Daemons without the flag,
old daemons, and the website drop-zone are entirely unaffected. The
sidecar writers (`_write_receipt_sidecar` in the capture daemon,
`.orphograph/receipts/` on the USB) carry the field automatically once the
server echoes it in the receipt.

---

## 4. Verification story

Offline verifier (`dist/orphograph-verify/verify_hw.py`, following
`verify_zk.py`'s conventions: stdlib only, no server, exit 0/1/2, honesty
line printed inside the PASS text):

1. Recompute `device_id` = SHA-256(pubkey DER); must match the field.
2. Rebuild `msg` from the receipt's own `hash_hex` + `signed_at` +
   `device_id` + `counter`.
3. Verify the ECDSA P-256 signature against `device_pubkey`.
   (Python stdlib has no ECDSA; a P-256 verify is ~100 lines of affine
   EC arithmetic with `pow(…, …, p)` — slow, fine for one signature, and
   keeps the no-dependency rule the ZK verifier established.)
4. Check `hash_hex` equals the receipt's anchored hash (already enforced
   at write time; re-check at read time, same belt-and-suspenders as the
   proof-swapper defense in the ZK layer).
5. Report the trust root honestly (next paragraph).

### What roots the public key — honest v1 answer: TOFU pinning

- **v1 (Secure Enclave): Trust-On-First-Use.** There is no practical
  Apple-signed certificate for a CLI-created SE key (App Attest is an
  app-service flow; VERIFY-BEFORE-BUILD #1). So v1 verification proves:
  *the same non-exportable device key that signed receipt A also signed
  receipt B*. That is same-device **continuity**, not manufacturer
  identity. The verifier's PASS text must say so verbatim — "device-key
  continuity under first-use pinning; the key's residence in a secure
  element is a client-side claim in v1" — the honesty line ships inside
  the tool, exactly like `verify_zk.py`'s K1–K3/G1 text.
- The pin itself can be strengthened cheaply: anchor a tiny "device
  enrollment" receipt (hash of the device pubkey + human `attestation`
  claim) once, so the pinning event itself is Bitcoin-timestamped and
  every later receipt's key can be checked against the enrolled one.
- **v2 paths to manufacturer-rooted chains** (in cost order): TPM EK
  certificate → manufacturer CA; ATECC Trust&GO device cert → Microchip
  CA; FIDO2 packed-attestation cert → vendor CA (batch-level only). Each
  arrives with its element; none blocks v1.

The existing `signature_verified` / `signer_kid` receipt fields (Ed25519
folder-manifest signatures, `server/app.py` ~line 3446) are the software
precedent: a signature checked at anchor time, surfaced only when present.
`hardware_attestation` is the same idea one level down the stack, and
deliberately does NOT reuse those field names — that pair means "manifest
signature checked by the server", this field means "device signature
verifiable by anyone offline".

---

## 5. Threat table

| Adversary / scenario | Outcome with `hardware_attestation` v1 |
|---|---|
| Post-hoc fabricator: computes a hash on another machine later and wants a receipt that looks like it came from the pinned device | **DEFENDED** — cannot produce a signature verifying against the pinned pubkey; the private key never leaves the secure element |
| Attestation-swapper: lifts a valid attestation onto a different receipt | **DEFENDED** — signature covers `hash_hex`; sanitizer refuses mismatched payloads at write time; verifier re-checks at read time |
| Receipt tamperer: edits attestation fields on disk | **DEFENDED** — signature breaks; and the receipt hash itself is OTS-anchored |
| Key exfiltrator: steals the laptop's disk image | **DEFENDED** (for the key) — SE/ATECC/TPM keys are non-exportable; disk contents don't contain the private key |
| **Compromised device**: malware on the host asks the element to sign an arbitrary hash | **NOT DEFENDED** — the element signs what the host hands it. H1 says *which device*, never *that the device was honest*. Do not claim otherwise |
| **Time spoofer**: sets the clock back and signs an old-looking `signed_at` | **NOT DEFENDED by the field** — `signed_at` is a claim. The only load-bearing time bound remains OTS→Bitcoin "no later than" (H3). A hardware counter (v2 elements) adds ordering, not wall-clock truth |
| **Analog hole**: camera pointed at a screen, re-photographed print, re-encoded file | **NOT DEFENDED** — the device honestly attests a hash of whatever bytes it was given. Provenance, not authenticity-of-scene. No copy may imply otherwise |
| Device borrower/thief: physically uses the real device | **NOT DEFENDED** — device binding is not person binding. (Biometric-gated SE access control raises the bar; still not an identity claim) |
| TOFU first-contact attacker: substitutes their own key before first pinning | **NOT DEFENDED in pure v1** — inherent TOFU limit; mitigated by the anchored enrollment receipt (§4), eliminated only by v2 manufacturer chains |
| Operator (Orphograph itself) | Same as the ZK layer: can DROP the field (availability), cannot forge a signature under the device key (integrity) |

---

## 6. Spike plan

**PoC — "Secure Enclave key, sign a hash, verify offline."** Zero
hardware spend, no server or daemon changes required to prove the loop:

1. Swift helper CLI (~150 lines): `orpho-se keygen` (P-256,
   `kSecAttrTokenIDSecureEnclave`, prints SPKI DER b64), `orpho-se sign
   <msg-hex>` (`SecKeyCreateSignature`, ECDSA-SHA256, prints DER sig b64).
   Compile with `swiftc`; resolve VERIFY-BEFORE-BUILD #2 (entitlements/
   codesigning for SE access from a bare CLI) as step one — it is the
   single biggest schedule risk. — ~0.5–1 day.
2. Python side: build the `orpho-hw-v1` message for a real file's SHA-256,
   subprocess the helper, emit a `hardware_attestation` JSON blob. — ~0.5 day.
3. Offline verifier: pure-Python P-256 ECDSA verify (stdlib only, verify
   against NIST CAVP test vectors first), then verify the blob end-to-end
   with the helper machine offline. — ~1 day.
4. Write-up + go/no-go: does the round trip work unattended from the
   capture daemon's context (launchd, no UI prompt)? If the SE demands an
   interactive presence check per signature, that's a finding, not a
   failure — it changes the product shape (per-session key unlock).
   — ~0.5 day.

**Total PoC estimate: 2–3 focused days, $0.** No engine or daemon edits
until the PoC passes; then the additive server field + `--hw-attest`
daemon flag is a separate, small change behind the normal branch+PR flow.

**ATECC path: gated.** Only opens if a hardware product (USB ingress
device) actually ships, because it carries real product burdens the spike
must not absorb: BOM spend (FOUNDER-GATED), one-way provisioning of
config/data zones (a wrong lock bricks the part), and per-unit cert
handling. The receipt field, message format, and verifier built in the
PoC are element-agnostic on purpose so this path inherits them unchanged.

---

## 7. AVOID-list check

- **No detection or authorship claims.** The field attests a device key
  signed a hash. It never says who took the photo, whether content is
  "real", or how it was made. All copy stays in provenance framing.
- **No new chains, no tokens.** Time still comes exclusively from the
  existing OTS→Bitcoin path; the hardware layer adds a signature, not a
  ledger. Nothing here touches the do-not-build backward-economy class.
- **Additive receipt format only.** Optional field, written only when
  present, mirroring `zk_provenance`'s shape-stability rule; every
  existing receipt, sidecar, `.orphograph/` index, and verifier behaves
  identically when the field is absent.
- **No deny-phrases.** No "court-admissible / notarized / legally binding
  / guarantee" anywhere in this doc or in any future copy derived from it;
  any external sentence goes through ip-redactor + founder first.
- **No spend.** The recommended spike costs $0; every hardware purchase
  remains founder-gated.

---

## VERIFY-BEFORE-BUILD register

1. **Apple-rooted attestation for CLI-created SE keys** — assumed
   unavailable outside the App Attest app-service flow; confirm before
   promising anything beyond TOFU on macOS.
2. **SE access from an unsigned/ad-hoc-signed CLI** — whether
   `kSecAttrTokenIDSecureEnclave` key creation and signing work from a
   `swiftc`-built helper without special entitlements or a paid developer
   certificate, and whether launchd (non-interactive) signing triggers UI
   prompts. Single biggest PoC risk.
3. **ATECC608 pre-provisioned SKUs** — exact Trust&GO-class part numbers,
   cert-chain structure, Microchip CA validation flow, and current
   availability/pricing before any BOM line is written.
4. **FIDO2 raw-CTAP2 `clientDataHash` control** — degree of control over
   the signed payload via python-fido2, per-vendor batch-attestation cert
   details, and whether counters survive across resets.
5. **TPM2 quote/counter semantics** — NV counter monotonicity across
   power loss, quote clock meaning, and EK→AK certification ceremony size
   before quoting the 4–8 KB estimate as fact.
6. **SE hardware counter absence** — v1 labels its counter `"software"`
   on the assumption the SE exposes no user-visible monotonic counter;
   confirm no supported API changes this before upgrading the label.
