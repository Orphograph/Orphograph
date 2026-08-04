#!/usr/bin/env python3
"""
zk_provenance.py — ZK AGENT-PROVENANCE (prototype)

Novelty thesis
--------------
Orphograph today anchors the *existence* of a file (a SHA-256 committed to
Bitcoin via OpenTimestamps). It does NOT prove *derivation*: that an AI output
O was produced by model M from some hidden prompt P and seed S.

This module bolts a zero-knowledge provenance layer on top of the EXISTING
anchor pipeline. The agent output's hash C_out = SHA-256(O) is submitted to
Orphograph exactly as it already accepts (engine.anchor_hash(hash_hex=...)).
The novelty is that the receipt also carries a Schnorr zero-knowledge proof of
knowledge of the hidden inputs (P, S) bound to (model_id, output_hash). A
verifier can confirm "this output existed, and the prover knew the prompt/seed
that produced it" WITHOUT the prompt or seed ever leaving the prover.

PRODUCTION GAP (stated honestly, not hidden):
  This prototype proves *knowledge of the committed inputs* (a real ZKPoK over
  a 2048-bit DH group). It does NOT, by itself, prove the *program executed* —
  i.e. that O is genuinely Program(M,P,S) rather than a value the prover chose
  and back-committed. Closing that gap is the SNARK step (EZKL / circom): the
  same wire format (commitment + proof + output_hash + model_id) is what the
  SNARK verifier would consume. Everything here is a drop-in scaffold for it.

Stdlib only. RFC 3526 2048-bit MODP group (nothing-up-my-sleeve).
"""
from __future__ import annotations
import hashlib
import json
import secrets
from dataclasses import dataclass, field, asdict

# RFC 3526, 2048-bit MODP group 14 (p = 2^2048 - 2^1984 + ... + 2^64 - 1)
_P = int(
    "FFFFFFFFFFFFFFFFC90FDAA22168C234C4C6628B80DC1CD129024E088A67CC74020BBEA6"
    "3B139B22514A08798E3404DDEF9519B3CD3A431B302B0A6DF25F14374FE1356D6D51C245"
    "E485B576625E7EC6F44C42E9A637ED6B0BFF5CB6F406B7EDEE386BFB5A899FA5AE9F2411"
    "7C4B1FE649286651ECE45B3DC2007CB8A163BF0598DA48361C55D39A69163FA8FD24CF5F"
    "83655D23DCA3AD961C62F356208552BB9ED529077096966D670C354E4ABC9804F1746C08"
    "CA18217C32905E462E36CE3BE39E772C180E86039B2783A2EC07A28FB5C55DF06F4C52C9"
    "DE2BCBF6955817183995497CEA956AE515D2261898FA051015728E5A8AACAA68FFFFFFFF"
    "FFFFFFFF",
    16,
)
_G = 2  # RFC 3526 generator
_Q = (_P - 1) // 2  # safe prime -> subgroup order q


def _h_bytes(*parts: bytes) -> bytes:
    h = hashlib.sha256()
    for p in parts:
        h.update(p if isinstance(p, bytes) else str(p).encode())
    return h.digest()


def _reduce_scalar(*parts) -> int:
    return int.from_bytes(_h_bytes(*parts), "big") % _Q


def _modpow(base: int, exp: int) -> int:
    return pow(base % _P, exp % (_P - 1), _P)


def _h_second_generator() -> int:
    # Derive a second generator h of the same subgroup via a fixed salt.
    # g^H(salt) is a generator whenever g is. Nothing-up-my-sleeve via hash.
    return _modpow(_G, _reduce_scalar(b"orphograph-zk-salt-v1"))


_H = _h_second_generator()


def _group_check() -> dict:
    """Self-verify group structure at import (cheap, one-time)."""
    return {
        "p_bits": _P.bit_length(),
        "g_order_minus_one": _modpow(_G, _P - 1),  # should be 1
        "h_is_one": _H == 1,                        # should be False
        "q_prime": _Q > 2,                          # safe-prime structure assumed
    }


# The "agent program" under provenance. Deterministic & reproducible so the
# SNARK replacement has a fixed circuit to target.
def PROGRAM(model_id: str, prompt: str, seed: str) -> str:
    """Toy agent (v1, kept for compatibility): single-hash stand-in."""
    digest = _h_bytes(b"PROG", model_id.encode(), prompt.encode(), seed.encode())
    return "out:" + digest.hex()[:40]


# PROGRAM_V2 — the fixed, deterministic transform a SNARK circuit targets.
#
# Spec (normative — the circuit statement in docs/ZK_PROVENANCE_THREAT_MODEL.md
# references these exact steps; change requires a version bump, never an edit):
#
#   st_0 = SHA256( b"orpho-prog-v2" || UTF8(model_id) )
#   st_i = SHA256( st_{i-1} || SHA256(UTF8(prompt)) || SHA256(UTF8(seed))
#                  || uint32_be(i) )          for i = 1..8
#   O    = "out2:" + hex(st_8)
#
# Rationale: every operation is a SHA-256 over fixed-width inputs — the one
# primitive with mature, audited circuit components in both circom
# (circomlib sha256) and EZKL. Prompt/seed enter only via their own digests,
# so the circuit's private inputs are two 32-byte values regardless of
# prompt length (bounded witness size). 8 rounds keeps the circuit small
# while making the transcript non-trivially sequential.
PROGRAM_V2_ROUNDS = 8
PROGRAM_V2_DOMAIN = b"orpho-prog-v2"


def PROGRAM_V2(model_id: str, prompt: str, seed: str) -> str:
    """Deterministic 8-round SHA-256 chain — the SNARK circuit target."""
    p_digest = hashlib.sha256(prompt.encode()).digest()
    s_digest = hashlib.sha256(seed.encode()).digest()
    st = hashlib.sha256(PROGRAM_V2_DOMAIN + model_id.encode()).digest()
    for i in range(1, PROGRAM_V2_ROUNDS + 1):
        st = hashlib.sha256(
            st + p_digest + s_digest + i.to_bytes(4, "big")
        ).digest()
    return "out2:" + st.hex()


def commit_output(output: str) -> str:
    """The value Orphograph anchors: SHA-256 of the output, hex."""
    return hashlib.sha256(output.encode()).hexdigest()


@dataclass
class ProvenanceProof:
    model_id: str
    output_hash: str          # C_out = SHA-256(O), hex — this is what's anchored
    commitment: str           # C = g^a * h^r  (a = H(P,S,M)), decimal string
    A: str                    # g^u * h^v  (Fiat-Shamir commitment), decimal
    s1: str                   # response 1
    s2: str                   # response 2
    challenge: str            # c = H(g,h,C,A,output_hash,model_id)
    proof_type: str = "schnorr-zk-pok-v1"

    def to_attestation(self) -> dict:
        """Shape Orphograph's engine.anchor_hash() `attestation` field expects
        once _sanitize_attestation is extended to allow these keys (see demo)."""
        return asdict(self)


def prove(model_id: str, prompt: str, seed: str,
          program=PROGRAM_V2) -> tuple[str, ProvenanceProof]:
    """Return (output, proof). Hidden inputs P,S never leave this function.

    program defaults to PROGRAM_V2 (the SNARK-targetable transform); pass
    PROGRAM for the legacy v1 toy transform."""
    output = program(model_id, prompt, seed)
    c_out = commit_output(output)

    a = _reduce_scalar(b"a", prompt.encode(), seed.encode(), model_id.encode())
    r = secrets.randbelow(_Q)
    C = (_modpow(_G, a) * _modpow(_H, r)) % _P

    u = secrets.randbelow(_Q)
    v = secrets.randbelow(_Q)
    A = (_modpow(_G, u) * _modpow(_H, v)) % _P

    # Fiat-Shamir challenge binds the proof to the output + model.
    c = _reduce_scalar(
        b"chal", str(_G).encode(), str(_H).encode(), str(C).encode(),
        str(A).encode(), c_out.encode(), model_id.encode(),
    )
    s1 = (u + c * a) % _Q
    s2 = (v + c * r) % _Q
    return output, ProvenanceProof(
        model_id=model_id, output_hash=c_out,
        commitment=str(C), A=str(A), s1=str(s1), s2=str(s2), challenge=str(c),
    )


def verify(proof: ProvenanceProof) -> dict:
    """Independent verification. Does NOT need prompt/seed. Returns result dict."""
    C = int(proof.commitment)
    A = int(proof.A)
    s1 = int(proof.s1)
    s2 = int(proof.s2)
    c = int(proof.challenge)

    # 1) Recompute challenge from public values; must match.
    c_recomputed = _reduce_scalar(
        b"chal", str(_G).encode(), str(_H).encode(), str(C).encode(),
        str(A).encode(), proof.output_hash.encode(), proof.model_id.encode(),
    )
    challenge_ok = c_recomputed == c

    # 2) Schnorr equation: g^s1 * h^s2 == A * C^c  (mod p)
    lhs = (_modpow(_G, s1) * _modpow(_H, s2)) % _P
    rhs = (A * _modpow(C, c)) % _P
    eq_ok = lhs == rhs

    return {
        "valid": bool(challenge_ok and eq_ok),
        "challenge_ok": challenge_ok,
        "equation_ok": eq_ok,
        "model_id": proof.model_id,
        "output_hash": proof.output_hash,
        "proof_type": proof.proof_type,
    }


def build_anchor_payload(proof: ProvenanceProof, label: str | None = None) -> dict:
    """Exact payload engine.anchor_hash() expects.

    The proof rides in the dedicated `zk_proof` kwarg (receipt field
    `zk_provenance`, sanitized by engine._sanitize_zk_provenance) — NOT in
    `attestation`, which stays reserved for brief human authorship claims.
    The output hash is submitted as hash_hex, unchanged."""
    return {
        "hash_hex": proof.output_hash,           # <- what gets Bitcoin-anchored
        "sha512_hex": None,
        "client_label": label,
        "zk_proof": proof.to_attestation(),      # <- the machine proof
    }


# ── snark-exec-v1 (honesty-ladder rung 4) ──────────────────────────────
# Packages a groth16 proof of PROGRAM_V2 execution (circuits in snark/)
# into the receipt payload. Unlike schnorr-zk-pok-v1 this DOES prove the
# 8-round transform ran — but with a dev/public-ceremony trust posture
# until rung 5, and it still does not prove an LLM produced the output.

def _bits_to_hex(bits: list) -> str:
    v = 0
    for b in bits:
        v = (v << 1) | int(b)
    return v.to_bytes(len(bits) // 8, "big").hex()


def build_snark_anchor_payload(model_id: str, proof_path, public_path,
                               vk_path, label: str | None = None) -> dict:
    """Build the engine.anchor_hash() payload for a snarkjs groth16 run.

    Reads snarkjs's proof.json/public.json/verification_key.json, derives
    the anchored hash from the circuit's OWN public signals
    (SHA-256("out2:" + hex(stN))), and pins the verification key by hash.
    The engine's sanitizer independently recomputes every binding.
    """
    import json as _json
    from pathlib import Path as _Path
    proof = _json.loads(_Path(proof_path).read_text())
    signals = _json.loads(_Path(public_path).read_text())
    if len(signals) != 768:
        raise ValueError(f"expected 768 public signals, got {len(signals)}")
    st_n = _bits_to_hex(signals[0:256])
    commitment = _bits_to_hex(signals[256:512])
    st0 = _bits_to_hex(signals[512:768])
    output = "out2:" + st_n
    output_hash = hashlib.sha256(output.encode()).hexdigest()
    vk_sha256 = hashlib.sha256(_Path(vk_path).read_bytes()).hexdigest()
    # Wire format carries the three 64-hex identities, not the 768-bit
    # array — reconstructible losslessly, and it keeps the whole payload
    # inside /api/anchor's 4KB body cap.
    return {
        "hash_hex": output_hash,
        "sha512_hex": None,
        "client_label": label,
        "zk_proof": {
            "proof_type": "snark-exec-v1",
            "output_hash": output_hash,
            "model_id": model_id,
            "program": "orpho-prog-v2/8",
            "protocol": "groth16",
            "curve": "bn128",
            "vk_sha256": vk_sha256,
            "stN_hex": st_n,
            "commitment_hex": commitment,
            "st0_hex": st0,
            "proof": {"pi_a": proof["pi_a"], "pi_b": proof["pi_b"],
                      "pi_c": proof["pi_c"]},
        },
    }


if __name__ == "__main__":
    print("group self-check:", _group_check())
    out, prf = prove("gpt-class-v3", "Summarize Q2 report", "seed-4417")
    print("output      :", out)
    print("anchored C  :", prf.output_hash)
    print("verify      :", verify(prf))
