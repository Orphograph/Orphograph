#!/usr/bin/env python3
"""test_notify_and_folder_delivery.py — the notifications customers paid for.

Four defects from the 2026-08-06 Stage 3e sweep, all of the same shape: a
customer supplies something, is charged, and the thing they supplied it FOR
never happens — with no error anywhere.

1. notify_email was persisted only inside the subscriber branch of
   /api/anchor. docs/api.html documents the field as "Pack only — emails the
   receipt", so the documented audience is precisely the cohort whose address
   was thrown away after the immediate email. upgrade_worker reads
   notify_email off the receipt to send the BTC-pin notice, so a Pack buyer
   who asked to be told when their anchor reached Bitcoin never was.

2. /api/anchor_folder fired NO receipt email, NO webhook, and persisted NO
   notify_email. A subscriber anchoring a dataset got silence, and an
   integration watching the webhook stream saw folder anchors simply not
   occur.

3. The receipt email told folder customers to retain "the original file" —
   which for a folder anchor does not exist — and never mentioned the
   manifest, without which the root cannot be re-derived.

4. renewal: manifest_sha256 was structurally always null, and the batch
   manifest write was skipped silently when the anchor receipt's directory
   did not exist yet.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "server"))


class TestNotifyEmailPersistedForPackBuyers(unittest.TestCase):
    """Source-level guard: the persistence must not sit inside the
    subscriber-only branch. Driving the full HTTP path needs Stripe and a
    session; the structural property is what actually regressed."""

    def test_persistence_is_not_nested_under_the_subscriber_branch(self):
        src = (ROOT / "server" / "app.py").read_text()
        marker = 'on_disk["notify_email"] = candidate'
        self.assertIn(marker, src)
        idx = src.index(marker)
        # Walk back to the nearest enclosing `if` at a shallower indent and
        # confirm it is the paid-anchor gate, not the subscription gate.
        before = src[:idx].splitlines()
        own_indent = len(before[-1]) - len(before[-1].lstrip())
        guard = None
        for line in reversed(before):
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            indent = len(line) - len(line.lstrip())
            if indent < own_indent and stripped.startswith(("if ", "try", "for ")):
                if stripped.startswith("try"):
                    own_indent = indent
                    continue
                guard = stripped
                break
        self.assertIsNotNone(guard, "could not locate the enclosing guard")
        self.assertNotIn("subscription_active and subscriber_email", guard,
                         "notify_email is persisted only for subscribers "
                         "again — Pack buyers are the documented audience "
                         "for this field and get no BTC-pin notice.")
        self.assertIn("is_paid_anchor", guard)

    def test_folder_handler_sends_email_and_dispatches_webhook(self):
        src = (ROOT / "server" / "app.py").read_text()
        start = src.index("def _handle_anchor_folder")
        end = src.index("def _handle_verify_folder")
        body = src[start:end]
        self.assertIn("mailer.send_receipt_email", body,
                      "folder anchors send no receipt email")
        self.assertIn('webhooks.dispatch("anchor.created"', body,
                      "folder anchors dispatch no webhook")
        self.assertIn('on_disk2["notify_email"]', body,
                      "folder anchors never persist notify_email, so the "
                      "BTC-pin notice can never fire for them")


class TestFolderReceiptEmail(unittest.TestCase):
    def setUp(self):
        # Do NOT evict mailer from sys.modules and do NOT touch ORPHO_DATA_DIR.
        # An earlier draft of this file called
        # os.environ.setdefault("ORPHO_DATA_DIR", mkdtemp()), which leaked a
        # temp data dir into every test that ran afterwards and broke the
        # pack50 credit-count assertions in test_stripe_webhook.py — a failure
        # that only appeared in the full run, never in isolation. Patch the
        # one function and put it back.
        import mailer
        self.mailer = mailer
        self.sent = {}
        self._real = mailer._send

        def fake(to, subject, text, html=None, **kw):
            self.sent.update(to=to, subject=subject, text=text, html=html)
            return True
        mailer._send = fake

    def tearDown(self):
        self.mailer._send = self._real

    def _receipt(self, **extra):
        return {"receipt_id": "abc123", "created_at": "2026-08-06T00:00:00+00:00",
                "hash_hex": "a" * 64, "calendars_ok": 5, "calendars_total": 5,
                **extra}

    def test_folder_email_names_the_manifest_not_the_original_file(self):
        self.mailer.send_receipt_email(
            "x@example.com", self._receipt(kind="folder", leaf_count=12))
        text = self.sent["text"]
        self.assertIn("manifest", text.lower(),
                      "a folder customer is not told to keep the manifest, "
                      "without which the root cannot be re-derived")
        self.assertNotIn("the original file together", text,
                         "folder email still refers to a single original "
                         "file, which does not exist for a folder anchor")
        self.assertIn("12", text, "leaf count not reported")
        self.assertIn("Merkle root", text)

    def test_single_file_email_is_unchanged(self):
        self.mailer.send_receipt_email("x@example.com", self._receipt())
        text = self.sent["text"]
        self.assertIn("the original file together", text)
        self.assertIn("SHA-256", text)
        self.assertNotIn("manifest", text.lower())


class TestRenewalPolish(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.receipts = Path(self._tmp.name) / "receipts"
        self.receipts.mkdir(parents=True)
        sys.modules.pop("renewal", None)
        import renewal
        self.renewal = renewal

    def tearDown(self):
        sys.modules.pop("renewal", None)
        self._tmp.cleanup()

    def _receipt(self, rid, manifest=None):
        d = self.receipts / rid
        d.mkdir(parents=True, exist_ok=True)
        (d / "receipt.json").write_text(json.dumps({
            "receipt_id": rid, "created_at": "2026-08-06T00:00:00+00:00",
            "hash_hex": "b" * 64, "sha512_hex": "c" * 128,
            "client_label": None, "source": "free", "attestation": None,
            "c2pa_manifest_hash": None, "metadata": {},
            "calendars_ok": 5, "calendars_total": 5,
            "successes": [], "failures": [],
        }))
        if manifest is not None:
            (d / "manifest.json").write_text(json.dumps(manifest))
        return d

    def _anchor(self, root_hex):
        return {"receipt_id": "BATCHRID00000001", "hash_hex": root_hex}

    def test_manifest_sha256_is_populated_for_folder_receipts(self):
        import hashlib
        d = self._receipt("folderreceipt01", manifest={"root_hex": "b" * 64,
                                                       "leaves": []})
        want = hashlib.sha256((d / "manifest.json").read_bytes()).hexdigest()
        out = self.renewal.renew_corpus(self.receipts, self._anchor)
        rec = json.loads((d / "renewal" / "001.json").read_text())
        self.assertEqual(rec["target"]["manifest_sha256"], want,
                         "folder receipt renewed with a null manifest digest "
                         "— the field was structurally always null")
        self.assertEqual(out["renewed"], 1)

    def test_manifest_sha256_stays_null_for_single_file_receipts(self):
        d = self._receipt("singlefile00001")
        self.renewal.renew_corpus(self.receipts, self._anchor)
        rec = json.loads((d / "renewal" / "001.json").read_text())
        self.assertIsNone(rec["target"]["manifest_sha256"])

    def test_batch_manifest_is_written_and_reported(self):
        """It used to be skipped silently when the anchor receipt directory
        did not exist yet — success reported, manifest dropped on the floor."""
        self._receipt("singlefile00001")
        out = self.renewal.renew_corpus(self.receipts, self._anchor)
        self.assertTrue(out["batch_manifest_written"])
        p = self.receipts / "BATCHRID00000001" / "renewal_batch.json"
        self.assertTrue(p.is_file(),
                        "renewal_batch.json was not written — the only "
                        "artifact listing every leaf in the batch tree")
        self.assertEqual(json.loads(p.read_text())["root_hex"], out["root_hex"])


class TestExportIncludesRenewals(unittest.TestCase):
    """The exported bundle must be verifiable by the verifier shipped in the
    same download. verify_renewal.py treats a missing batch block as a hard
    FAIL, so omitting renewal/ produced a bundle our own tool rejects."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        os.environ["ORPHO_RECEIPTS_DIR"] = str(Path(self._tmp.name) / "receipts")
        sys.modules.pop("receipt_export", None)
        import receipt_export
        self.mod = receipt_export
        self.dir = Path(self._tmp.name) / "receipts" / "rid001"
        self.dir.mkdir(parents=True)
        (self.dir / "receipt.json").write_text(json.dumps({"receipt_id": "rid001"}))
        (self.dir / "a.ots").write_bytes(b"\x00ots")

    def tearDown(self):
        sys.modules.pop("receipt_export", None)
        os.environ.pop("ORPHO_RECEIPTS_DIR", None)
        self._tmp.cleanup()

    def test_renewal_records_are_in_the_zip(self):
        import io, zipfile
        rn = self.dir / "renewal"
        rn.mkdir()
        (rn / "001.json").write_text('{"sequence": 1}')
        (rn / "002.json").write_text('{"sequence": 2}')
        data, err = self.mod.export_zip("rid001")
        self.assertIsNone(err)
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            names = sorted(zf.namelist())
        self.assertIn("renewal/001.json", names,
                      f"renewal records missing from export: {names}")
        self.assertIn("renewal/002.json", names)

    def test_export_without_renewals_is_unchanged(self):
        import io, zipfile
        data, err = self.mod.export_zip("rid001")
        self.assertIsNone(err)
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            self.assertEqual(sorted(zf.namelist()), ["a.ots", "receipt.json"])

    def test_readable_json_does_not_claim_a_chain_check(self):
        summary, err = self.mod.export_readable_json("rid001")
        self.assertIsNone(err)
        how = summary["how_to_verify"]
        self.assertNotIn("orphograph/verifier", how, "dead 404 repo URL")
        self.assertIn("OpenTimestamps", how)
        self.assertIn("does NOT consult Bitcoin", how)


if __name__ == "__main__":
    unittest.main()
