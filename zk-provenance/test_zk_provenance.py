#!/usr/bin/env python3
"""Regression tests for the ZK agent-provenance prototype.

Run:  python3 -m pytest zk-provenance/test_zk_provenance.py -q
Or:    python3 zk-provenance/test_zk_provenance.py   (self-contained)
"""
import sys, os
from pathlib import Path
import hashlib

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "zk-provenance"))

import zk_provenance as zk


def test_group_self_check():
    g = zk._group_check()
    assert g["p_bits"] == 2048
    assert g["g_order_minus_one"] == 1   # g^(p-1) == 1
    assert g["h_is_one"] is False


def test_prove_verify_roundtrip():
    out, proof = zk.prove("m1", "hidden prompt", "seed-1")
    assert out.startswith("out2:")   # PROGRAM_V2 is the default
    assert zk.verify(proof)["valid"] is True


def test_program_v1_still_available():
    out, proof = zk.prove("m1", "hidden prompt", "seed-1", program=zk.PROGRAM)
    assert out.startswith("out:") and not out.startswith("out2:")
    assert zk.verify(proof)["valid"] is True


def test_program_v2_matches_normative_spec():
    """Recompute PROGRAM_V2 from its documented spec, byte for byte.
    This test IS the spec lock: if it fails, the circuit target changed."""
    model_id, prompt, seed = "gpt-class-v3", "hidden P", "seed-9"
    p_digest = hashlib.sha256(prompt.encode()).digest()
    s_digest = hashlib.sha256(seed.encode()).digest()
    st = hashlib.sha256(b"orpho-prog-v2" + model_id.encode()).digest()
    for i in range(1, 9):
        st = hashlib.sha256(st + p_digest + s_digest + i.to_bytes(4, "big")).digest()
    assert zk.PROGRAM_V2(model_id, prompt, seed) == "out2:" + st.hex()
    assert zk.PROGRAM_V2_ROUNDS == 8


def test_verify_fails_with_wrong_output_hash():
    _, proof = zk.prove("m1", "p", "s")
    bad = zk.ProvenanceProof(**{**proof.__dict__,
                                "output_hash": "0" * 64})
    # challenge is bound to output_hash, so recomputed c won't match
    assert zk.verify(bad)["valid"] is False


def test_verify_fails_with_tampered_commitment():
    _, proof = zk.prove("m1", "p", "s")
    tampered = zk.ProvenanceProof(**{**proof.__dict__,
                                     "commitment": str(int(proof.commitment) + 1)})
    assert zk.verify(tampered)["valid"] is False


def test_output_hash_is_sha256_of_output():
    out, proof = zk.prove("m", "p", "s")
    assert proof.output_hash == hashlib.sha256(out.encode()).hexdigest()


def test_prompt_seed_never_appear_in_proof():
    out, proof = zk.prove("m", "TOPSECRET_PROMPT", "TOPSECRET_SEED")
    blob = repr(proof.to_attestation()).lower()
    assert "topsecret" not in blob


def test_anchor_payload_shape():
    _, proof = zk.prove("m", "p", "s")
    payload = zk.build_anchor_payload(proof, label="x")
    assert payload["hash_hex"] == proof.output_hash
    assert payload["zk_proof"]["proof_type"] == "schnorr-zk-pok-v1"


# ---------------------------------------------------------------------------
# Engine integration: the zk_provenance receipt field (offline — calendars
# stubbed, receipts in a temp dir set BEFORE engine import, since engine
# resolves ORPHO_DATA_DIR at import time).
# ---------------------------------------------------------------------------

def _engine_offline(tmp_dir: str):
    os.environ["ORPHO_DATA_DIR"] = tmp_dir
    os.environ.pop("ORPHO_RECEIPTS_DIR", None)
    for mod in ("server.engine", "server"):
        sys.modules.pop(mod, None)
    import server.engine as engine
    engine._submit = lambda cal, hb: (False, "offline-test")  # no network
    return engine


def test_engine_disk_roundtrip_zk_provenance():
    import tempfile, json as _json
    with tempfile.TemporaryDirectory() as td:
        engine = _engine_offline(td)
        out, proof = zk.prove("m-disk", "hidden P", "hidden S")
        payload = zk.build_anchor_payload(proof, label="roundtrip")
        receipt = engine.anchor_hash(
            hash_hex=payload["hash_hex"],
            client_label=payload["client_label"],
            zk_proof=payload["zk_proof"],
        )
        rid = receipt["receipt_id"]
        assert receipt["zk_provenance"]["proof_type"] == "schnorr-zk-pok-v1"
        # Cold path: read the persisted receipt via verify_receipt and
        # re-verify the ZK proof from disk alone.
        recheck = engine.verify_receipt(rid)
        stored = recheck["zk_provenance"]
        again = zk.verify(zk.ProvenanceProof(
            **{k: stored[k] for k in ("model_id", "output_hash", "commitment",
                                      "A", "s1", "s2", "challenge")}))
        assert again["valid"] is True
        # And the raw file on disk carries the field too.
        raw = _json.loads((Path(td) / "receipts" / rid / "receipt.json").read_text())
        assert raw["zk_provenance"]["output_hash"] == payload["hash_hex"]
        # Human attestation stays independent and empty here.
        assert raw["attestation"] is None


def test_engine_sanitizer_rejects_unbound_proof():
    """A proof whose output_hash differs from the anchored hash must NOT persist."""
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        engine = _engine_offline(td)
        _, proof = zk.prove("m", "p", "s")
        wrong = dict(proof.to_attestation(), output_hash="0" * 64)
        receipt = engine.anchor_hash(hash_hex=proof.output_hash, zk_proof=wrong)
        assert "zk_provenance" not in receipt


def test_engine_sanitizer_rejects_nondigit_fields():
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        engine = _engine_offline(td)
        _, proof = zk.prove("m", "p", "s")
        bad = dict(proof.to_attestation(), commitment="12345abc")
        receipt = engine.anchor_hash(hash_hex=proof.output_hash, zk_proof=bad)
        assert "zk_provenance" not in receipt


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"PASS {name}")
    print("ALL ZK PROVENANCE TESTS PASSED")
