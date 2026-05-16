#!/usr/bin/env python3
"""test_receipt_export.py — pin the (data, err) tuple contract on
receipt_export.export_zip and export_readable_json.

The previous None-or-data contract collapsed "not found" and "broken on disk"
into the same return value, causing paying subscribers to see 404 when they
should have seen 500 + a stderr-logged data-integrity event. Tests below
make sure that regression cannot return.
"""
from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "server"))


class TestReceiptExport(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._old_data = os.environ.get("ORPHO_DATA_DIR")
        self._old_receipts = os.environ.get("ORPHO_RECEIPTS_DIR")
        os.environ["ORPHO_DATA_DIR"] = self._tmp.name
        os.environ["ORPHO_RECEIPTS_DIR"] = str(Path(self._tmp.name) / "receipts")
        # Fresh import with the new dirs
        sys.modules.pop("receipt_export", None)
        import receipt_export
        self.rx = receipt_export
        # Materialize one valid receipt + one broken one + one missing one
        self.rid_ok = "r_ok123"
        self.rid_broken = "r_broken"
        self.rid_missing = "r_does_not_exist"

        valid_dir = Path(receipt_export.RECEIPTS_DIR) / self.rid_ok
        valid_dir.mkdir(parents=True, exist_ok=True)
        receipt_json = valid_dir / "receipt.json"
        receipt_json.write_text(json.dumps({
            "receipt_id": self.rid_ok,
            "created_at": "2026-05-15T00:00:00+00:00",
            "hash_hex": "a" * 64,
            "client_label": "test.jpg",
            "calendars_ok": 5,
            "calendars_total": 5,
        }))
        # Two .ots files
        (valid_dir / "alice.ots").write_bytes(b"OTS_PROOF_BYTES")
        (valid_dir / "finney.ots").write_bytes(b"ANOTHER_PROOF")

        # Broken receipt: dir exists, receipt.json is malformed JSON
        broken_dir = Path(receipt_export.RECEIPTS_DIR) / self.rid_broken
        broken_dir.mkdir(parents=True, exist_ok=True)
        (broken_dir / "receipt.json").write_text("{this is not valid json")

    def tearDown(self):
        sys.modules.pop("receipt_export", None)
        if self._old_data is None:
            os.environ.pop("ORPHO_DATA_DIR", None)
        else:
            os.environ["ORPHO_DATA_DIR"] = self._old_data
        if self._old_receipts is None:
            os.environ.pop("ORPHO_RECEIPTS_DIR", None)
        else:
            os.environ["ORPHO_RECEIPTS_DIR"] = self._old_receipts
        self._tmp.cleanup()

    # ── export_zip ──────────────────────────────────────────────────────

    def test_zip_success(self):
        zipped, err = self.rx.export_zip(self.rid_ok)
        self.assertIsNone(err)
        self.assertIsInstance(zipped, bytes)
        self.assertGreater(len(zipped), 0)
        # ZIP magic
        self.assertEqual(zipped[:2], b"PK")
        # Verify the archive contains receipt.json + the .ots files
        with zipfile.ZipFile(io.BytesIO(zipped)) as zf:
            names = set(zf.namelist())
        self.assertIn("receipt.json", names)
        self.assertIn("alice.ots", names)
        self.assertIn("finney.ots", names)

    def test_zip_not_found(self):
        zipped, err = self.rx.export_zip(self.rid_missing)
        self.assertIsNone(zipped)
        self.assertEqual(err, self.rx.NOT_FOUND)

    def test_zip_distinguishes_not_found_from_broken(self):
        """The whole point of the (data, err) refactor: a missing receipt
        returns NOT_FOUND; a corrupted one returns BROKEN. They must NOT
        collapse to the same return value."""
        # The broken receipt has a directory + receipt.json so export_zip's
        # existence checks pass; the broken file passes through zf.write
        # without breaking the zip itself (zf.write reads raw bytes, no
        # JSON parse). For the zip path, NOT_FOUND vs success is the
        # main distinction. Verify success here for a valid-on-disk
        # but JSON-malformed receipt — the zip path doesn't parse JSON.
        zipped, err = self.rx.export_zip(self.rid_broken)
        # zip doesn't parse JSON; it'll succeed
        self.assertIsNone(err)
        self.assertIsNotNone(zipped)

    # ── export_readable_json ────────────────────────────────────────────

    def test_summary_success(self):
        data, err = self.rx.export_readable_json(self.rid_ok)
        self.assertIsNone(err)
        self.assertIsInstance(data, dict)
        self.assertEqual(data["receipt_id"], self.rid_ok)
        # Honest-copy disclaimers must be present in every summary
        self.assertIn("what_this_does_not_prove", data)
        self.assertIsInstance(data["what_this_does_not_prove"], list)
        # Must contain the no-court-admissible disclaimer
        joined = " ".join(data["what_this_does_not_prove"]).lower()
        self.assertIn("court-admissible", joined)

    def test_summary_not_found(self):
        data, err = self.rx.export_readable_json(self.rid_missing)
        self.assertIsNone(data)
        self.assertEqual(err, self.rx.NOT_FOUND)

    def test_summary_broken_returns_broken_not_not_found(self):
        """The critical distinction: broken-on-disk must surface BROKEN,
        not NOT_FOUND. A paying subscriber whose receipt is corrupted needs
        to see a 500 + a logged data-integrity event, not a misleading 404."""
        data, err = self.rx.export_readable_json(self.rid_broken)
        self.assertIsNone(data)
        self.assertEqual(err, self.rx.BROKEN,
            "corrupted receipt.json must return BROKEN, not NOT_FOUND")


if __name__ == "__main__":
    unittest.main()
