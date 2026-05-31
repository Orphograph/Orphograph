#!/usr/bin/env python3
"""Tests for capture/orphograph_usb.py — the USB provenance recorder.

A temp dir stands in for the mounted drive; a fake anchor function stands in for
the network so these run offline and deterministically.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "capture"))

import orphograph_usb as usb  # noqa: E402


def make_fake_anchor(ok=True, response=None, rate_limit=False):
    """Return (anchor_fn, calls) — calls records (hash_hex, sha512_hex, label)."""
    calls = []
    rid_counter = {"n": 0}

    def fake(endpoint, hash_hex, sha512_hex, label, api_key):
        calls.append({"hash_hex": hash_hex, "sha512_hex": sha512_hex, "label": label})
        if rate_limit:
            return False, {"status_code": 429, "error": "rate limit exceeded"}
        if not ok:
            return False, {"status_code": 500, "error": "boom"}
        rid_counter["n"] += 1
        return True, {
            "receipt_id": f"R{rid_counter['n']:04d}",
            "created_at": "2026-05-31T00:00:00+00:00",
            "calendars_ok": 5, "calendars_total": 5,
            "hash_hex": hash_hex, "sha512_hex": sha512_hex,
        }
    return fake, calls


class TestUsbWatch(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.mount = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def _write(self, rel, content="data"):
        p = self.mount / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
        return p

    def _scan(self, **kw):
        defaults = dict(endpoint="https://x", api_key="", include_names=False,
                        extensions=set(), min_age=0)
        defaults.update(kw)
        return usb.scan_once(self.mount, **defaults)

    def test_recursive_anchor_and_on_drive_sidecar(self):
        self._write("a.txt", "one")
        self._write("sub/b.txt", "two")
        self._write("sub/deep/c.txt", "three")
        fake, calls = make_fake_anchor()
        counts = self._scan(anchor_fn=fake)
        self.assertEqual(counts["anchored"], 3, counts)
        self.assertEqual(len(calls), 3)
        # .orphograph/ index + receipts written ON the drive
        base, index_file, receipts = usb._orpho_paths(self.mount)
        self.assertTrue(index_file.exists(), "on-drive index missing")
        self.assertEqual(len(list(receipts.glob("*.json"))), 3, "receipts not on drive")
        rows = [json.loads(l) for l in index_file.read_text().splitlines() if l.strip()]
        self.assertTrue(all(r["status"] == "anchored" for r in rows))

    def test_content_dedup_same_bytes_anchored_once(self):
        self._write("x.txt", "identical")
        self._write("copy/x2.txt", "identical")  # same content, different path
        fake, calls = make_fake_anchor()
        counts = self._scan(anchor_fn=fake)
        self.assertEqual(counts["anchored"], 1, "same content anchored more than once")
        self.assertEqual(counts["skipped_seen"], 1)

    def test_rescan_skips_already_anchored(self):
        self._write("a.txt", "one")
        fake, calls = make_fake_anchor()
        self._scan(anchor_fn=fake)
        counts2 = self._scan(anchor_fn=fake)  # second pass
        self.assertEqual(counts2["anchored"], 0)
        self.assertEqual(counts2["skipped_seen"], 1)
        self.assertEqual(len(calls), 1, "re-anchored on rescan")

    def test_privacy_filename_not_sent_by_default(self):
        self._write("secret-name.txt", "p")
        fake, calls = make_fake_anchor()
        self._scan(anchor_fn=fake, include_names=False)
        self.assertEqual(calls[0]["label"], "", "filename leaked to server by default")

    def test_include_names_sends_relpath(self):
        self._write("sub/secret-name.txt", "p")
        fake, calls = make_fake_anchor()
        self._scan(anchor_fn=fake, include_names=True)
        self.assertIn("secret-name.txt", calls[0]["label"])

    def test_skips_os_junk_and_orphograph_dir(self):
        self._write("real.txt", "r")
        self._write(".DS_Store", "junk")
        self._write("._appledouble", "junk")
        self._write(".Spotlight-V100/store.db", "junk")
        self._write(f"{usb.ORPHO_DIR}/index.jsonl", "{}")  # pre-existing on-drive folder
        fake, calls = make_fake_anchor()
        counts = self._scan(anchor_fn=fake)
        self.assertEqual(counts["anchored"], 1, "anchored junk/system files")
        self.assertEqual(calls[0]["hash_hex"], usb.hash_file(self.mount / "real.txt")[0])

    def test_min_age_debounce_skips_young_files(self):
        self._write("fresh.txt", "f")
        fake, calls = make_fake_anchor()
        counts = self._scan(anchor_fn=fake, min_age=10_000)  # everything is "young"
        self.assertEqual(counts["anchored"], 0)
        self.assertEqual(counts["skipped_young"], 1)

    def test_extension_filter(self):
        self._write("keep.txt", "k")
        self._write("skip.bin", "s")
        fake, calls = make_fake_anchor()
        counts = self._scan(anchor_fn=fake, extensions={".txt"})
        self.assertEqual(counts["anchored"], 1)
        self.assertEqual(counts["skipped_ext"], 1)

    def test_rate_limit_aborts_pass_and_marks_pending(self):
        self._write("a.txt", "one")
        self._write("b.txt", "two")
        fake, calls = make_fake_anchor(rate_limit=True)
        counts = self._scan(anchor_fn=fake)
        self.assertEqual(counts["anchored"], 0)
        self.assertGreaterEqual(counts["rate_limited"], 1)
        # aborted the pass after the first rate-limit (didn't hammer)
        self.assertEqual(len(calls), 1, "kept calling after rate-limit")
        rows = load_rows(self.mount)
        self.assertTrue(any(r.get("status") == "pending" for r in rows))

    def test_failure_recorded_not_fatal(self):
        self._write("a.txt", "one")
        fake, calls = make_fake_anchor(ok=False)
        counts = self._scan(anchor_fn=fake)
        self.assertEqual(counts["failed"], 1)
        self.assertEqual(counts["anchored"], 0)
        rows = load_rows(self.mount)
        self.assertTrue(any(r.get("status") == "failed" for r in rows))

    def test_dry_run_writes_nothing(self):
        self._write("a.txt", "one")
        fake, calls = make_fake_anchor()
        counts = self._scan(anchor_fn=fake, dry_run=True)
        self.assertEqual(counts["dry_run"], 1)
        self.assertEqual(len(calls), 0, "dry-run hit the network")
        self.assertFalse((self.mount / usb.ORPHO_DIR).exists(), "dry-run wrote to drive")

    def test_fetch_proofs_pulls_bundle_onto_drive(self):
        self._write("a.txt", "one")
        fake, calls = make_fake_anchor()
        fetched = []

        def fake_fetch(endpoint, rid, dest_dir, api_key=""):
            # simulate the .zip extraction: drop a receipt.json under dest/<rid>/
            (dest_dir / rid).mkdir(parents=True, exist_ok=True)
            (dest_dir / rid / "receipt.json").write_text("{}")
            fetched.append(rid)
            return True

        counts = self._scan(anchor_fn=fake, fetch_proofs=True, fetch_fn=fake_fetch)
        self.assertEqual(counts["anchored"], 1)
        self.assertEqual(counts["proofs_fetched"], 1)
        self.assertEqual(len(fetched), 1)
        _, _, receipts = usb._orpho_paths(self.mount)
        self.assertTrue((receipts / fetched[0] / "receipt.json").exists(),
                        "proof bundle not extracted onto the drive")

    def test_fetch_proofs_failure_is_non_fatal(self):
        self._write("a.txt", "one")
        fake, _ = make_fake_anchor()
        counts = self._scan(anchor_fn=fake, fetch_proofs=True,
                            fetch_fn=lambda *a, **k: False)
        self.assertEqual(counts["anchored"], 1, "anchor should still count when bundle fetch fails")
        self.assertEqual(counts["proofs_fetched"], 0)

    def test_status_reports_counts(self):
        self._write("a.txt", "one")
        fake, _ = make_fake_anchor()
        self._scan(anchor_fn=fake)
        st = usb.status(self.mount)
        self.assertEqual(st["anchored"], 1)
        self.assertTrue(st["mounted"])


def load_rows(mount):
    _, index_file, _ = usb._orpho_paths(mount)
    if not index_file.exists():
        return []
    return [json.loads(l) for l in index_file.read_text().splitlines() if l.strip()]


if __name__ == "__main__":
    unittest.main()
