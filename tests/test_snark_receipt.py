#!/usr/bin/env python3
"""test_snark_receipt.py — honesty-ladder rung 4: snark-exec-v1 receipts.

Fixtures are the REAL committed 8-round proof evidence
(zk-provenance/snark/evidence_8round_2026_08_04/), so every acceptance
test exercises a genuine groth16 proof, and every forgery test perturbs
genuine material rather than synthetic strawmen.

The REQUIRED forgery coverage (snark/README.md rung 4):
  A. same proof, different anchored hash        → engine rejects (binding 1)
  B. claimed output not derived from stN        → engine rejects (binding 2)
  C. model swap (st0 mismatch)                  → engine rejects (binding 3)
  D. tampered signals made hash-consistent      → engine structural checks
     pass BY DESIGN (no pairing on the server) but `snarkjs groth16 verify`
     REJECTS — proven live when npx is available, else skipped with reason.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SERVER_DIR = REPO_ROOT / "server"
ZK_DIR = REPO_ROOT / "zk-provenance"
EVIDENCE = REPO_ROOT / "zk-provenance" / "snark" / "evidence_8round_2026_08_04"

_POLLUTED = ("engine", "merkle", "app")
_ENV_KEYS = ("ORPHO_DATA_DIR", "ORPHO_RECEIPTS_DIR", "ORPHO_LEDGER")

ENGINE = None
_TMP = None
_OLD_ENV: dict = {}
_OLD_MODULES: dict = {}
_ORIG_SUBMIT = None


def setUpModule() -> None:
    global _TMP, ENGINE, _ORIG_SUBMIT
    _TMP = tempfile.TemporaryDirectory(prefix="orpho_snark_receipt_")
    for k in _ENV_KEYS:
        _OLD_ENV[k] = os.environ.get(k)
        os.environ.pop(k, None)
    os.environ["ORPHO_DATA_DIR"] = _TMP.name
    for m in _POLLUTED:
        if m in sys.modules:
            _OLD_MODULES[m] = sys.modules.pop(m)
    if str(SERVER_DIR) not in sys.path:
        sys.path.insert(0, str(SERVER_DIR))
    import engine as engine_mod  # noqa: PLC0415 — after env
    ENGINE = engine_mod
    _ORIG_SUBMIT = engine_mod._submit
    engine_mod._submit = lambda cal, h: (False, "stubbed: snark test mode")


def tearDownModule() -> None:
    if ENGINE is not None and _ORIG_SUBMIT is not None:
        ENGINE._submit = _ORIG_SUBMIT
    for m in _POLLUTED:
        sys.modules.pop(m, None)
    for m, mod in _OLD_MODULES.items():
        sys.modules[m] = mod
    for k, v in _OLD_ENV.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    _TMP.cleanup()


def _payload() -> dict:
    """Genuine payload from the committed evidence via the real builder."""
    if str(ZK_DIR) not in sys.path:
        sys.path.insert(0, str(ZK_DIR))
    import zk_provenance  # noqa: PLC0415
    return zk_provenance.build_snark_anchor_payload(
        "gpt-class-v3",
        EVIDENCE / "proof.json",
        EVIDENCE / "public.json",
        EVIDENCE / "verification_key.json",
    )


class TestSnarkExecReceipt(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not (EVIDENCE / "proof.json").exists():
            raise unittest.SkipTest("committed 8-round evidence not present")
        cls.payload = _payload()

    # ── acceptance ──────────────────────────────────────────────────────

    def test_genuine_proof_anchors_and_persists(self):
        p = copy.deepcopy(self.payload)
        record = ENGINE.anchor_hash(p["hash_hex"], zk_proof=p["zk_proof"])
        zk = record.get("zk_provenance")
        self.assertIsNotNone(zk, "genuine snark-exec-v1 must survive the sanitizer")
        self.assertEqual(zk["proof_type"], "snark-exec-v1")
        self.assertEqual(zk["output_hash"], p["hash_hex"])
        # Derived identities match the committed expected transcript.
        expected = json.loads((EVIDENCE / "expected.json").read_text())
        self.assertEqual(zk["stN_hex"], expected["stN_hex"])
        self.assertEqual(zk["commitment_hex"], expected["commitment_hex"])
        self.assertEqual(zk["st0_hex"], expected["st0_hex"])
        # Round-trips through verify_receipt from disk.
        out = ENGINE.verify_receipt(record["receipt_id"])
        self.assertEqual(out["zk_provenance"]["proof_type"], "snark-exec-v1")

    def test_output_hash_binds_to_program_output(self):
        # The builder's anchored hash IS SHA-256("out2:" + stN) — the exact
        # PROGRAM_V2 output identity, recomputed here independently.
        expected = json.loads((EVIDENCE / "expected.json").read_text())
        want = hashlib.sha256(("out2:" + expected["stN_hex"]).encode()).hexdigest()
        self.assertEqual(self.payload["hash_hex"], want)

    # ── forgeries (server-side bindings) ────────────────────────────────

    def test_forgery_a_same_proof_different_anchor_rejected(self):
        p = copy.deepcopy(self.payload)
        other = "11" * 32
        record = ENGINE.anchor_hash(other, zk_proof=p["zk_proof"])
        self.assertNotIn("zk_provenance", record)

    def test_forgery_b_claimed_output_not_from_stn_rejected(self):
        # Attacker anchors a hash consistent with output_hash but whose value
        # was NOT derived from the proof's stN: rebind output_hash to a
        # chosen O' and anchor sha256(O').
        p = copy.deepcopy(self.payload)
        forged_output_hash = hashlib.sha256(b"out2:" + b"f" * 64).hexdigest()
        p["zk_proof"]["output_hash"] = forged_output_hash
        record = ENGINE.anchor_hash(forged_output_hash, zk_proof=p["zk_proof"])
        self.assertNotIn("zk_provenance", record)

    def test_forgery_c_model_swap_rejected(self):
        p = copy.deepcopy(self.payload)
        p["zk_proof"]["model_id"] = "some-other-model"
        record = ENGINE.anchor_hash(p["hash_hex"], zk_proof=p["zk_proof"])
        self.assertNotIn("zk_provenance", record)

    # ── structural rejection matrix ─────────────────────────────────────

    def test_rejects_malformed_signal_identities(self):
        for field, bad in (("stN_hex", "zz" * 32), ("commitment_hex", "ab" * 31),
                           ("st0_hex", None), ("stN_hex", ("AB" * 32))):
            p = copy.deepcopy(self.payload)
            p["zk_proof"][field] = bad
            record = ENGINE.anchor_hash(p["hash_hex"], zk_proof=p["zk_proof"])
            self.assertNotIn("zk_provenance", record,
                             f"{field}={bad!r} must reject the whole proof")

    def test_payload_fits_api_anchor_body_cap(self):
        # The whole reason for the compact wire format: the 768-bit array
        # blew /api/anchor's 4KB cap in prod (found by live verification).
        body = json.dumps({"hash_hex": self.payload["hash_hex"],
                           "zk_proof": self.payload["zk_proof"]}).encode()
        self.assertLessEqual(len(body), 4096,
                             f"snark anchor body is {len(body)}B — exceeds MAX_BODY_BYTES")

    def test_rejects_bad_vk_hash_and_missing_fields(self):
        for mutate in (
            lambda z: z.update(vk_sha256="zz" * 32),
            lambda z: z.update(program="orpho-prog-v2/2"),
            lambda z: z.update(protocol="plonk"),
            lambda z: z.pop("proof"),
            lambda z: z["proof"].update(pi_a=["1", "2"]),          # arity
            lambda z: z["proof"].update(pi_a=["1", "x", "3"]),     # non-digit
            lambda z: z["proof"].update(pi_b=[["1"], ["2"], ["3"]]),
        ):
            p = copy.deepcopy(self.payload)
            mutate(p["zk_proof"])
            record = ENGINE.anchor_hash(p["hash_hex"], zk_proof=p["zk_proof"])
            self.assertNotIn("zk_provenance", record,
                             f"mutation {mutate} must reject the whole proof")

    # ── forgery D: pairing-level rejection (the cryptographic gate) ─────

    SNARKJS_CLI = REPO_ROOT / "zk-provenance" / "snark" / "node_modules" / "snarkjs" / "build" / "cli.cjs"

    @unittest.skipUnless(
        shutil.which("node") and SNARKJS_CLI.exists(),
        "repo-local snarkjs unavailable (zk-provenance/snark/node_modules)")
    def test_forgery_d_tampered_signals_fail_groth16_verify(self):
        # Flip one stN bit and rebind output_hash so ALL server-side hash
        # checks pass — the pairing check must be what kills it. The bit
        # array is reconstructed from the receipt's compact hex identities
        # exactly the way verify_snark.py does.
        def hex_to_bits(h):
            v = int(h, 16)
            return [str((v >> (255 - i)) & 1) for i in range(256)]
        zkp = self.payload["zk_proof"]
        signals = (hex_to_bits(zkp["stN_hex"]) + hex_to_bits(zkp["commitment_hex"])
                   + hex_to_bits(zkp["st0_hex"]))
        genuine_signals = json.loads((EVIDENCE / "public.json").read_text())
        self.assertEqual(signals, genuine_signals,
                         "hex reconstruction must be lossless vs snarkjs output")
        signals = list(signals)
        signals[0] = "1" if signals[0] == "0" else "0"
        with tempfile.TemporaryDirectory() as td:
            tampered_pub = Path(td) / "public.json"
            tampered_pub.write_text(json.dumps(signals))
            def run(pub_path):
                return subprocess.run(
                    ["node", str(self.SNARKJS_CLI), "groth16", "verify",
                     str(EVIDENCE / "verification_key.json"), str(pub_path),
                     str(EVIDENCE / "proof.json")],
                    capture_output=True, text=True, timeout=300,
                )
            genuine = run(EVIDENCE / "public.json")
            forged = run(tampered_pub)
        self.assertIn("OK", genuine.stdout + genuine.stderr)
        self.assertEqual(genuine.returncode, 0)
        self.assertNotEqual(forged.returncode, 0,
                            "tampered public signals MUST fail groth16 verify")


if __name__ == "__main__":
    unittest.main()
