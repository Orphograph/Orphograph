"""test_btc_claims.py — pin the BTC claim submission + ledger semantics."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "server"))


class TestBtcClaims(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._old_data = os.environ.get("ORPHO_DATA_DIR")
        os.environ["ORPHO_DATA_DIR"] = self._tmp.name
        for m in ("btc_claims", "file_lock"):
            sys.modules.pop(m, None)
        import btc_claims
        self.bc = btc_claims

    def tearDown(self):
        sys.modules.pop("btc_claims", None)
        if self._old_data is None:
            os.environ.pop("ORPHO_DATA_DIR", None)
        else:
            os.environ["ORPHO_DATA_DIR"] = self._old_data
        self._tmp.cleanup()

    def _valid_txid(self) -> str:
        return "a" * 64

    def test_submit_rejects_bad_email(self):
        ok, err = self.bc.submit(email="notanemail", txid=self._valid_txid(), pack_size=10)
        self.assertFalse(ok)
        self.assertEqual(err, "invalid email")

    def test_submit_rejects_bad_txid(self):
        ok, err = self.bc.submit(email="ok@example.com", txid="too-short", pack_size=10)
        self.assertFalse(ok)
        self.assertEqual(err, "invalid txid")

    def test_submit_rejects_bad_pack_size(self):
        ok, err = self.bc.submit(email="ok@example.com", txid=self._valid_txid(), pack_size=99)
        self.assertFalse(ok)
        self.assertEqual(err, "invalid pack size")

    def test_submit_appends_to_ledger(self):
        ok, claim_id = self.bc.submit(
            email="buyer@example.com",
            txid=self._valid_txid(),
            pack_size=50,
            usd=29.0,
            btc_amount=0.0004,
            btc_address="bc1qclvjjmwmr294rydv4x0dc787nx9jd8j4ny4jaz",
            note="paid via Phantom",
        )
        self.assertTrue(ok)
        self.assertTrue(claim_id.startswith("btc_"))

        with open(self.bc.LEDGER, "r", encoding="utf-8") as f:
            lines = [l for l in f if l.strip()]
        self.assertEqual(len(lines), 1)
        rec = json.loads(lines[0])
        self.assertEqual(rec["claim_id"], claim_id)
        self.assertEqual(rec["pack_size"], 50)
        self.assertEqual(rec["txid"], "a" * 64)
        self.assertEqual(rec["status"], "pending")
        # Email must be hashed, NOT plaintext
        self.assertNotIn("buyer@example.com", lines[0])
        self.assertEqual(len(rec["email_hash"]), 64)  # sha256 hex

    def test_pending_list(self):
        for i in range(3):
            self.bc.submit(email=f"b{i}@example.com", txid=("b" * 64), pack_size=10)
        pending = self.bc.list_pending()
        self.assertEqual(len(pending), 3)
        for r in pending:
            self.assertEqual(r["status"], "pending")

    def test_note_is_truncated(self):
        ok, _ = self.bc.submit(
            email="ok@example.com",
            txid=self._valid_txid(),
            pack_size=10,
            note="x" * 9999,
        )
        self.assertTrue(ok)
        with open(self.bc.LEDGER) as f:
            rec = json.loads(f.readline())
        self.assertEqual(len(rec["note"]), self.bc.MAX_NOTE_LEN)


if __name__ == "__main__":
    unittest.main()
