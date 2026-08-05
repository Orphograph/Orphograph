#!/usr/bin/env python3
"""test_renewal.py — Phase 1 batch renewal (docs/DESIGN_RENEWAL_PATH.md).

The invariants that matter most are the destructive ones, so they are tested
first-class rather than as afterthoughts:
  * an issued receipt is NEVER modified (byte-for-byte, including .ots);
  * no renewal .ots ever lands in the receipt root — the non-recursive
    `*.ots` globs elsewhere would sweep it up and make receipts that verify
    TODAY start failing;
  * the core digest survives the mutations upgrade_worker.py performs on
    receipt.json (this is the failure mode that would silently void the whole
    corpus);
  * absent vs present-but-null is pinned per field, because a strict
    canonical serializer produces different bytes for {"x": null} and {},
    and two verifiers disagreeing silently is worse than either failing.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SERVER_DIR = ROOT / "server"
VERIFIER = ROOT / "dist" / "orphograph-verify" / "verify_renewal.py"

_POLLUTED = ("engine", "merkle", "renewal", "app")
_ENV_KEYS = ("ORPHO_DATA_DIR", "ORPHO_RECEIPTS_DIR", "ORPHO_LEDGER")

ENGINE = None
RENEWAL = None
_TMP = None
_OLD_ENV: dict = {}
_OLD_MODULES: dict = {}
_ORIG_SUBMIT = None


def setUpModule() -> None:
    global _TMP, ENGINE, RENEWAL, _ORIG_SUBMIT
    _TMP = tempfile.TemporaryDirectory(prefix="orpho_renewal_")
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
    import renewal as renewal_mod  # noqa: PLC0415
    ENGINE = engine_mod
    RENEWAL = renewal_mod
    _ORIG_SUBMIT = engine_mod._submit
    engine_mod._submit = lambda cal, h: (True, b"\xf0stub-calendar-body")


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


def _snapshot(path: Path) -> dict[str, bytes]:
    """Every byte under a receipt dir, for exact before/after comparison."""
    return {str(p.relative_to(path)): p.read_bytes()
            for p in sorted(path.rglob("*")) if p.is_file()}


class TestRenewal(unittest.TestCase):
    def setUp(self):
        self.receipts = ENGINE.RECEIPTS_DIR

    def _anchor(self, n=3):
        return [ENGINE.anchor_hash(f"{i:02x}" * 32)["receipt_id"]
                for i in range(1, n + 1)]

    # ── invariant 1: receipts are never touched ─────────────────────────

    def test_renewal_never_modifies_the_receipt(self):
        rids = self._anchor(3)
        before = {r: _snapshot(self.receipts / r) for r in rids}
        RENEWAL.renew_corpus(self.receipts, ENGINE.anchor_hash,
                             receipt_ids=rids)
        for r in rids:
            after = _snapshot(self.receipts / r)
            for name, data in before[r].items():
                self.assertIn(name, after, f"{r}/{name} disappeared")
                self.assertEqual(after[name], data,
                                 f"{r}/{name} was modified by renewal")

    # ── invariant 2: no renewal .ots in the receipt root ────────────────

    def test_no_renewal_ots_in_receipt_root(self):
        rids = self._anchor(2)
        ots_before = {r: sorted(p.name for p in (self.receipts / r).glob("*.ots"))
                      for r in rids}
        RENEWAL.renew_corpus(self.receipts, ENGINE.anchor_hash, receipt_ids=rids)
        for r in rids:
            self.assertEqual(
                sorted(p.name for p in (self.receipts / r).glob("*.ots")),
                ots_before[r],
                "a renewal .ots reached the receipt root — the non-recursive "
                "*.ots globs would sweep it up and break verification")
            self.assertTrue((self.receipts / r / "renewal").is_dir())

    # ── invariant 3: the mutation that would silently void everything ───

    def test_core_digest_survives_upgrade_worker_mutations(self):
        rid = self._anchor(1)[0]
        rpath = self.receipts / rid / "receipt.json"
        record = json.loads(rpath.read_text())
        before = RENEWAL.core_digests(record)
        # Exactly what upgrade_worker.py:252-287 and the mailers write.
        mutated = copy.deepcopy(record)
        mutated.update({
            "status": "pinned", "btc_pinned_at": "2026-08-05T00:00:00+00:00",
            "pinned_count": 5, "pinned_total": 5, "upgrade_attempts": 3,
            "upgrade_stalls": 0, "upgrade_frozen": False,
            "pin_email_sent_at": "2026-08-05T00:01:00+00:00",
            "lineage": {"parent_receipt_id": "x", "committed": True},
        })
        self.assertEqual(RENEWAL.core_digests(mutated), before,
                         "a background worker rewriting receipt.json changed "
                         "the core digest — the renewal record would be "
                         "silently voided")

    # ── the spec's present/absent table ─────────────────────────────────

    def test_absent_vs_null_is_pinned_per_field(self):
        rid = self._anchor(1)[0]
        record = json.loads((self.receipts / rid / "receipt.json").read_text())
        core = RENEWAL.receipt_core(record)
        # sha512_hex is always written (may be null) — key must be present.
        self.assertIn("sha512_hex", core)
        # zk_provenance is omitted when absent — never null.
        self.assertNotIn("zk_provenance", core)
        with_zk = copy.deepcopy(record)
        with_zk["zk_provenance"] = {"proof_type": "schnorr-zk-pok-v1"}
        self.assertIn("zk_provenance", RENEWAL.receipt_core(with_zk))
        # And the two must produce different digests, or the distinction is
        # cosmetic and verifiers can disagree undetected.
        self.assertNotEqual(RENEWAL.core_digests(record),
                            RENEWAL.core_digests(with_zk))

    def test_missing_core_field_is_fatal_not_defaulted(self):
        rid = self._anchor(1)[0]
        record = json.loads((self.receipts / rid / "receipt.json").read_text())
        del record["created_at"]
        with self.assertRaises(RENEWAL.RenewalError):
            RENEWAL.receipt_core(record)

    # ── batch mechanics ─────────────────────────────────────────────────

    def test_one_anchor_covers_the_whole_corpus(self):
        rids = self._anchor(5)
        before = len(list(self.receipts.iterdir()))
        out = RENEWAL.renew_corpus(self.receipts, ENGINE.anchor_hash,
                                   receipt_ids=rids)
        self.assertEqual(out["renewed"], 5)
        # Exactly ONE new receipt dir (the batch anchor) for five renewals.
        self.assertEqual(len(list(self.receipts.iterdir())), before + 1)

    def test_inclusion_proof_re_derives_the_root(self):
        rids = self._anchor(4)
        RENEWAL.renew_corpus(self.receipts, ENGINE.anchor_hash, receipt_ids=rids)
        for r in rids:
            rr = json.loads((self.receipts / r / "renewal" / "001.json").read_text())
            self.assertTrue(RENEWAL.verify_inclusion(rr),
                            f"{r}: inclusion proof does not re-derive the root")

    def test_record_digest_excludes_batch_block(self):
        # Otherwise the proof would have to commit to itself.
        rid = self._anchor(1)[0]
        record = json.loads((self.receipts / rid / "receipt.json").read_text())
        rr = RENEWAL.build_record(record, 1, "2026-08-05T00:00:00+00:00")
        d1 = RENEWAL.record_digest(rr)
        rr["batch"] = {"root_hex": "ff" * 32, "proof": [], "leaf_path": "x"}
        self.assertEqual(RENEWAL.record_digest(rr), d1)

    def test_second_cycle_chains_to_the_first(self):
        rids = self._anchor(2)
        RENEWAL.renew_corpus(self.receipts, ENGINE.anchor_hash, receipt_ids=rids)
        RENEWAL.renew_corpus(self.receipts, ENGINE.anchor_hash, receipt_ids=rids)
        for r in rids:
            d = self.receipts / r / "renewal"
            first = json.loads((d / "001.json").read_text())
            second = json.loads((d / "002.json").read_text())
            self.assertIsNone(first["prev_renewal_sha256"])
            self.assertEqual(second["prev_renewal_sha256"],
                             RENEWAL.record_digest(first))

    def test_malformed_receipt_is_skipped_with_a_reason(self):
        rids = self._anchor(2)
        bad = self.receipts / rids[0] / "receipt.json"
        rec = json.loads(bad.read_text())
        del rec["hash_hex"]
        bad.write_text(json.dumps(rec))
        out = RENEWAL.renew_corpus(self.receipts, ENGINE.anchor_hash,
                                   receipt_ids=rids)
        self.assertEqual(out["renewed"], 1)
        self.assertEqual(len(out["skipped"]), 1)
        self.assertIn(rids[0], out["skipped"][0]["receipt_id"])
        self.assertTrue(out["skipped"][0]["reason"])

    def test_dry_run_writes_nothing(self):
        rids = self._anchor(2)
        out = RENEWAL.renew_corpus(self.receipts, ENGINE.anchor_hash,
                                   receipt_ids=rids, dry_run=True)
        self.assertEqual(out["renewed"], 0)
        self.assertEqual(out["would_renew"], 2)
        self.assertIsNotNone(out["root_hex"])
        for r in rids:
            self.assertFalse((self.receipts / r / "renewal").exists())


class TestVerifyRenewalCLI(unittest.TestCase):
    def _run(self, *args):
        return subprocess.run([sys.executable, str(VERIFIER), *args],
                              capture_output=True, text=True, timeout=60)

    def test_verifier_passes_on_a_real_renewal(self):
        rid = ENGINE.anchor_hash("ab" * 32)["receipt_id"]
        RENEWAL.renew_corpus(ENGINE.RECEIPTS_DIR, ENGINE.anchor_hash,
                             receipt_ids=[rid])
        r = self._run(str(ENGINE.RECEIPTS_DIR / rid / "receipt.json"))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("PASS", r.stdout)
        # The honesty scope must always print with the pass.
        self.assertIn("still SHA-256", r.stdout)
        self.assertIn("cannot repair a break that already happened", r.stdout)

    def test_verifier_fails_when_the_receipt_core_is_altered(self):
        rid = ENGINE.anchor_hash("cd" * 32)["receipt_id"]
        RENEWAL.renew_corpus(ENGINE.RECEIPTS_DIR, ENGINE.anchor_hash,
                             receipt_ids=[rid])
        rpath = ENGINE.RECEIPTS_DIR / rid / "receipt.json"
        rec = json.loads(rpath.read_text())
        rec["client_label"] = "tampered after renewal"
        rpath.write_text(json.dumps(rec, indent=2))
        r = self._run(str(rpath))
        self.assertEqual(r.returncode, 1)
        self.assertIn("mismatch", r.stdout)

    def test_verifier_detects_a_broken_chain(self):
        rid = ENGINE.anchor_hash("ef" * 32)["receipt_id"]
        RENEWAL.renew_corpus(ENGINE.RECEIPTS_DIR, ENGINE.anchor_hash,
                             receipt_ids=[rid])
        RENEWAL.renew_corpus(ENGINE.RECEIPTS_DIR, ENGINE.anchor_hash,
                             receipt_ids=[rid])
        d = ENGINE.RECEIPTS_DIR / rid / "renewal"
        second = json.loads((d / "002.json").read_text())
        second["prev_renewal_sha256"] = "00" * 32
        (d / "002.json").write_text(json.dumps(second, indent=2))
        r = self._run(str(ENGINE.RECEIPTS_DIR / rid / "receipt.json"))
        self.assertEqual(r.returncode, 1)
        self.assertIn("broken chain", r.stdout)

    def test_verifier_exits_2_when_never_renewed(self):
        rid = ENGINE.anchor_hash("12" * 32)["receipt_id"]
        r = self._run(str(ENGINE.RECEIPTS_DIR / rid / "receipt.json"))
        self.assertEqual(r.returncode, 2)
        self.assertIn("not a failure", r.stderr)


if __name__ == "__main__":
    unittest.main()
