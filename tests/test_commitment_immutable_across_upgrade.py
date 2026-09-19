"""test_commitment_immutable_across_upgrade.py

The anchored commitment never changes after issuance (2026-08-26).

This is the product's deepest promise: a receipt's SHA-256 is what was
committed to Bitcoin, and nothing may rewrite it afterwards. `upgrade_worker`
is the ONE background process that rewrites receipt.json in place, upgrading
pending .ots proofs as calendars pin them. Its own comment says it changes
"only polling cadence -- never the proof bytes or the commitment walk".

That was a comment, not a guard. `renewal.py` protects the same idea properly
with an explicit CORE / CORE_IF_PRESENT preservation set that raises
RenewalError on a broken chain; the upgrade path had no equivalent and no test
asserting the commitment survived the rewrite. Verified at the time of writing
that the invariant DOES hold (the worker never assigns hash_hex, sha512_hex,
receipt_id or created_at), so this pins a true invariant rather than fixing a
break. A post-hoc mutator that silently altered an anchored hash would not
raise anything: every receipt would still verify against ITSELF while no longer
matching the customer's file.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "server"))

# The fields that ARE the commitment. If the worker ever writes one of these,
# a receipt stops matching the file it was issued for.
COMMITMENT_FIELDS = ("receipt_id", "created_at", "hash_hex", "sha512_hex")

from conftest import PINNED_BODY as _PINNED_BODY  # noqa: E402


def _make_receipt_dir(base: Path, receipt_id: str, calendars: list[str]) -> Path:
    rd = base / receipt_id
    rd.mkdir(parents=True, exist_ok=True)
    successes = []
    for cal in calendars:
        short = cal.split("//", 1)[1].split(".", 1)[0]
        (rd / f"{short}.ots").write_bytes(b"OTS_BLOB_PLACEHOLDER")
        successes.append({
            "calendar": cal,
            "ots_path": f"receipts/{receipt_id}/{short}.ots",
            "ots_bytes": len(b"OTS_BLOB_PLACEHOLDER"),
        })
    record = {
        "receipt_id": receipt_id,
        "created_at": "2026-05-17T00:00:00+00:00",
        "hash_hex": "a" * 64,
        "sha512_hex": "b" * 128,
        "client_label": None,
        "source": "pack:demo",
        "private": False,
        "owner_id": None,
        "attestation": None,
        "metadata": None,
        "calendars_ok": len(successes),
        "calendars_total": len(calendars),
        "successes": successes,
        "failures": [],
        "status": "pending",
    }
    (rd / "receipt.json").write_text(json.dumps(record, indent=2))
    return rd


class TestCommitmentImmutable(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        base = Path(self._tmp.name)
        self.receipts = base / "receipts"
        self.receipts.mkdir(parents=True)
        import importlib
        import engine
        engine.RECEIPTS_DIR = self.receipts
        import upgrade_worker
        importlib.reload(upgrade_worker)
        upgrade_worker.RECEIPTS_DIR = self.receipts
        self.uw = upgrade_worker

    def tearDown(self):
        self._tmp.cleanup()

    def _patch_all_pin(self):
        commit = mock.patch.object(self.uw, "_commitment_for_pending",
                                   return_value=("c" * 64, 100))
        fetch = mock.patch.object(self.uw, "_fetch_upgrade",
                                  return_value=(True, _PINNED_BODY))
        return commit, fetch

    def _run(self, rid: str, cals: list[str]):
        rd = _make_receipt_dir(self.receipts, rid, cals)
        before = json.loads((rd / "receipt.json").read_text())
        commit, fetch = self._patch_all_pin()
        with commit, fetch, mock.patch("urllib.request.urlopen"):
            # The worker gets its OWN parse of the file. `dict(before)` was a
            # shallow copy: the worker mutating an entry of `successes` also
            # mutated `before`, so nested changes compared equal to themselves.
            result = self.uw._upgrade_one(
                rd, json.loads((rd / "receipt.json").read_text()))
        after = json.loads((rd / "receipt.json").read_text())
        return before, after, result

    def test_commitment_fields_are_byte_identical_after_upgrade(self):
        """THE GUARD. Whatever else the worker records, these must not move."""
        before, after, _ = self._run(
            "rid_immutable",
            ["https://a.pool.opentimestamps.org", "https://b.pool.opentimestamps.org"])
        for field in COMMITMENT_FIELDS:
            self.assertEqual(
                before.get(field), after.get(field),
                f"upgrade_worker CHANGED {field}: {before.get(field)!r} -> "
                f"{after.get(field)!r}. A receipt that no longer carries the "
                f"anchored value stops matching the customer's file, and nothing "
                f"raises."
            )

    def test_the_worker_actually_did_something(self):
        """NEGATIVE CONTROL. If the worker no-oped, the assertions above would
        pass by comparing an unchanged file to itself and prove nothing."""
        before, after, result = self._run(
            "rid_control", ["https://a.pool.opentimestamps.org"])
        self.assertNotEqual(before, after, "upgrade_worker made no change at all")
        self.assertEqual(result["status"], "pinned")
        self.assertIn("btc_pinned_at", after)
        self.assertNotIn("btc_pinned_at", before)

    def test_only_expected_keys_were_added(self):
        """A new key is not automatically wrong, but an UNREVIEWED one on the
        commitment path is. This fails when the worker starts writing something
        new, so the addition gets read rather than absorbed."""
        allowed = {
            "btc_pinned_at", "integration_email_sent_at", "pin_email_sent_at",
            "pinned_count", "pinned_total", "status", "upgrade_attempts",
            "upgrade_frozen", "upgrade_frozen_at", "upgrade_frozen_reason",
            "upgrade_stalls", "upgrade_schema", "upgrade_thawed_at", "successes", "failures", "calendars_ok",
        }
        before, after, _ = self._run(
            "rid_keys", ["https://a.pool.opentimestamps.org"])
        changed = {k for k in set(before) | set(after)
                   if before.get(k) != after.get(k)}
        unexpected = changed - allowed
        self.assertFalse(
            unexpected,
            f"upgrade_worker changed unreviewed field(s) {sorted(unexpected)} on a "
            f"receipt. Confirm they are not part of the commitment, then add them here."
        )


class TestRenewalCoreSurvivesTheRealWorker(unittest.TestCase):
    """The renewal record commits to a 13-field core (renewal.CORE_ALWAYS), not
    just the four commitment fields above. Until 2026-09-19 the only guards
    were a hand-copied dict of "what the worker writes" and a regex over the
    worker's SOURCE for `record["x"] =`. Both are textual proxies: a worker
    doing `record.update({"calendars_ok": n})`, or `entry["k"] = v` on an item
    of `successes`, passed all 54 tests (planted and observed) while changing
    the core digest -- which silently voids every renewal record for that
    receipt. This drives the REAL `_upgrade_one` against a receipt on disk and
    compares the digests renewal actually computes.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.receipts = Path(self._tmp.name) / "receipts"
        self.receipts.mkdir(parents=True)
        import importlib
        import engine
        engine.RECEIPTS_DIR = self.receipts
        import upgrade_worker
        importlib.reload(upgrade_worker)
        upgrade_worker.RECEIPTS_DIR = self.receipts
        self.uw = upgrade_worker
        import renewal
        self.renewal = renewal

    def tearDown(self):
        self._tmp.cleanup()

    def _drive(self, rid: str, fetch_result):
        rd = _make_receipt_dir(self.receipts, rid, [
            "https://a.pool.opentimestamps.org",
            "https://b.pool.opentimestamps.org"])
        rpath = rd / "receipt.json"
        record = json.loads(rpath.read_text())
        # The shared fixture predates this field; renewal refuses a receipt
        # without it rather than defaulting, so the fixture must carry it.
        record["c2pa_manifest_hash"] = None
        rpath.write_text(json.dumps(record, indent=2))
        before = json.loads(rpath.read_text())
        with mock.patch.object(self.uw, "_commitment_for_pending",
                               return_value=("c" * 64, 100)), \
                mock.patch.object(self.uw, "_fetch_upgrade",
                                  return_value=fetch_result), \
                mock.patch("urllib.request.urlopen"):
            # An independent parse, never `dict(before)`: a shallow copy shares
            # the nested `successes` entries with `before`, and a worker that
            # mutates one would move both sides of the comparison together
            # (planted 2026-09-19: the nested plant passed until this changed).
            self.uw._upgrade_one(rd, json.loads(rpath.read_text()))
        return before, json.loads(rpath.read_text())

    def _assert_core_unmoved(self, before, after):
        self.assertNotEqual(before, after,
                            "the worker wrote nothing; this run proves nothing")
        self.assertEqual(
            self.renewal.core_digests(before), self.renewal.core_digests(after),
            "upgrade_worker changed the renewal CORE of a receipt on disk. "
            "Every renewal record already issued for it no longer verifies, "
            "and nothing raises. Changed core fields: "
            f"{[k for k in self.renewal.CORE_ALWAYS + self.renewal.CORE_IF_PRESENT if before.get(k) != after.get(k)]}")

    def test_core_digest_survives_a_real_pin(self):
        before, after = self._drive("rid_core_pin", (True, _PINNED_BODY))
        self.assertEqual(after.get("status"), "pinned")
        self._assert_core_unmoved(before, after)

    def test_core_digest_survives_a_real_stalled_attempt(self):
        before, after = self._drive("rid_core_stall", (False, "HTTP 404"))
        self.assertNotEqual(after.get("status"), "pinned")
        self._assert_core_unmoved(before, after)

    def test_the_fixture_core_is_complete(self):
        """NEGATIVE CONTROL on the harness: if the fixture lacked a CORE_ALWAYS
        field, core_digests would raise and the two tests above would error
        rather than assert -- make that failure mode explicit and named."""
        before, _ = self._drive("rid_core_fixture", (True, _PINNED_BODY))
        for key in self.renewal.CORE_ALWAYS:
            self.assertIn(key, before)


if __name__ == "__main__":
    unittest.main()
