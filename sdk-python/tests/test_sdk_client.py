"""Unit tests for the orphograph Python SDK.

The HTTP layer is stubbed via ``unittest.mock`` so the tests never make a
real network call. The Merkle layer is exercised end-to-end against a
temporary folder on disk.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

# Ensure the in-tree package is importable when running ``pytest`` from
# the sdk-python/ directory.
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

import orphograph  # noqa: E402
from orphograph import _client, _merkle  # noqa: E402


def _make_folder(root: Path) -> dict:
    (root / "a.txt").write_bytes(b"alpha")
    (root / "b.txt").write_bytes(b"beta")
    (root / "sub").mkdir()
    (root / "sub" / "c.txt").write_bytes(b"gamma")
    tree = _merkle.MerkleTree.from_folder(root)
    return tree.manifest()


class TestMerkleReexport(unittest.TestCase):
    def test_algorithm_tag_matches_server(self):
        self.assertEqual(_merkle.ALGORITHM, "orphograph-merkle-v1-rfc6962")
        self.assertEqual(orphograph.ALGORITHM, _merkle.ALGORITHM)

    def test_sha256_banner_present_in_module(self):
        src = Path(_merkle.__file__).read_text()
        self.assertIn("AUTO-COPIED from server/merkle.py", src)
        self.assertIn("e68c897382a41e5cb479d00af5fb31e8cb50a45490702ead82d03a25948a87f5", src)


class TestAnchorFolder(unittest.TestCase):
    def test_anchor_folder_transmits_manifest_only(self):
        with tempfile.TemporaryDirectory() as td:
            folder = Path(td)
            manifest = _make_folder(folder)
            root_hex = manifest["root_hex"]

            captured = {}

            def fake_post(manifest, **kwargs):
                captured["manifest"] = manifest
                captured["kwargs"] = kwargs
                return {
                    "receipt_id": "rid-test",
                    "root_hex": root_hex,
                    "leaf_count": len(manifest["leaves"]),
                    "calendars_ok": 5,
                    "calendars_total": 5,
                }

            with mock.patch.object(orphograph._client, "post_anchor_folder", side_effect=fake_post):
                result = orphograph.anchor_folder(
                    str(folder),
                    server_url="https://example.invalid",
                    api_key="k-test",
                    client_label="unit-test",
                )

        self.assertEqual(result["receipt_id"], "rid-test")
        self.assertEqual(result["root_hex"], root_hex)
        self.assertEqual(result["leaf_count"], 3)
        # The captured payload must NOT contain any file body — only the
        # manifest. The manifest's leaves carry path + sha256, never bytes.
        sent_manifest = captured["manifest"]
        self.assertEqual(sent_manifest["algorithm"], "orphograph-merkle-v1-rfc6962")
        for leaf in sent_manifest["leaves"]:
            self.assertIn("path", leaf)
            self.assertIn("file_sha256_hex", leaf)
            self.assertNotIn("content", leaf)
            self.assertNotIn("bytes", leaf)
            self.assertNotIn("body", leaf)

    def test_anchor_rejects_nonexistent_folder(self):
        with self.assertRaises(ValueError):
            orphograph.anchor_folder("/nonexistent/path/that/does/not/exist")


class TestVerifyFolder(unittest.TestCase):
    def test_verify_folder_matches(self):
        with tempfile.TemporaryDirectory() as td:
            folder = Path(td)
            manifest = _make_folder(folder)

            def fake_get(receipt_id, **kwargs):
                return {"receipt": {"receipt_id": receipt_id}, "manifest": manifest}

            with mock.patch.object(orphograph._client, "get_verify_folder", side_effect=fake_get):
                ok = orphograph.verify_folder(str(folder), "rid-test")
            self.assertTrue(ok)

    def test_verify_folder_mismatch_on_tampered_file(self):
        with tempfile.TemporaryDirectory() as td:
            folder = Path(td)
            manifest = _make_folder(folder)
            # Tamper after building the manifest the server "knows".
            (folder / "a.txt").write_bytes(b"alpha-tampered")

            def fake_get(receipt_id, **kwargs):
                return {"receipt": {"receipt_id": receipt_id}, "manifest": manifest}

            with mock.patch.object(orphograph._client, "get_verify_folder", side_effect=fake_get):
                ok = orphograph.verify_folder(str(folder), "rid-test")
            self.assertFalse(ok)

    def test_verify_folder_default_excludes(self):
        # A folder anchored with the DEFAULT deny-list must verify with the
        # default deny-list, even when OS detritus appears on disk afterward.
        with tempfile.TemporaryDirectory() as td:
            folder = Path(td)
            manifest = _make_folder(folder)  # built with defaults
            (folder / ".DS_Store").write_bytes(b"finder junk")  # excluded by default

            def fake_get(receipt_id, **kwargs):
                return {"receipt": {"receipt_id": receipt_id}, "manifest": manifest}

            with mock.patch.object(orphograph._client, "get_verify_folder", side_effect=fake_get):
                ok = orphograph.verify_folder(str(folder), "rid-test")
            self.assertTrue(ok)

    def test_verify_folder_custom_excludes_roundtrip(self):
        # AUDIT D2: a folder anchored with a CUSTOM exclude list must verify
        # when the same list is supplied — verify_folder mirrors
        # anchor_folder's exclude parameter.
        with tempfile.TemporaryDirectory() as td:
            folder = Path(td)
            (folder / "a.txt").write_bytes(b"alpha")
            (folder / "scratch.log").write_bytes(b"working notes, not evidence")
            custom = ["*.log"]
            manifest = _merkle.MerkleTree.from_folder(folder, exclude=custom).manifest()
            self.assertEqual([l["path"] for l in manifest["leaves"]], ["a.txt"])

            def fake_get(receipt_id, **kwargs):
                return {"receipt": {"receipt_id": receipt_id}, "manifest": manifest}

            with mock.patch.object(orphograph._client, "get_verify_folder", side_effect=fake_get):
                ok = orphograph.verify_folder(str(folder), "rid-test", exclude=custom)
            self.assertTrue(ok)

    def test_verify_folder_custom_exclude_anchor_needs_matching_excludes(self):
        # The D2 failure case, pinned: verifying a custom-exclude anchor
        # WITHOUT the matching excludes recomputes a different root and
        # returns False. Before the fix this was permanent (no way to pass
        # excludes at all); now it is simply the documented contract.
        with tempfile.TemporaryDirectory() as td:
            folder = Path(td)
            (folder / "a.txt").write_bytes(b"alpha")
            (folder / "scratch.log").write_bytes(b"working notes, not evidence")
            manifest = _merkle.MerkleTree.from_folder(folder, exclude=["*.log"]).manifest()

            def fake_get(receipt_id, **kwargs):
                return {"receipt": {"receipt_id": receipt_id}, "manifest": manifest}

            with mock.patch.object(orphograph._client, "get_verify_folder", side_effect=fake_get):
                ok = orphograph.verify_folder(str(folder), "rid-test")
            self.assertFalse(ok)


class TestInclusionProof(unittest.TestCase):
    def test_round_trip_inclusion_proof(self):
        with tempfile.TemporaryDirectory() as td:
            folder = Path(td)
            manifest = _make_folder(folder)
            tree = _merkle.MerkleTree.from_manifest(manifest)
            proof = tree.inclusion_proof("sub/c.txt")
            server_payload = {
                "receipt_id": "rid-test",
                "root_hex": manifest["root_hex"],
                "path": "sub/c.txt",
                "file_sha256_hex": hashlib.sha256(b"gamma").hexdigest(),
                "merkle_algorithm": manifest["algorithm"],
                # JSON serialisation collapses tuples to lists — emulate that
                # so the SDK's coercion path is exercised.
                "proof": [list(step) for step in proof],
            }

            with mock.patch.object(
                orphograph._client, "get_inclusion_proof", return_value=server_payload
            ):
                p = orphograph.inclusion_proof("rid-test", "sub/c.txt")

            ok = orphograph.verify_inclusion(
                file_path=str(folder / "sub" / "c.txt"),
                rel_path="sub/c.txt",
                proof=p["proof"],
                root_hex=p["root_hex"],
            )
            self.assertTrue(ok)

    def test_verify_inclusion_missing_file_raises(self):
        # AUDIT D7: a missing local file is an I/O precondition failure and
        # must surface as FileNotFoundError — never a silent False that
        # masquerades as a "not included" verdict (matches sdk-node, which
        # rejects with the filesystem error).
        with tempfile.TemporaryDirectory() as td:
            missing = str(Path(td) / "does-not-exist.txt")
            with self.assertRaises(FileNotFoundError):
                orphograph.verify_inclusion(
                    file_path=missing,
                    rel_path="does-not-exist.txt",
                    proof=[],
                    root_hex="00" * 32,
                )

    def test_verify_inclusion_rejects_wrong_root(self):
        with tempfile.TemporaryDirectory() as td:
            folder = Path(td)
            manifest = _make_folder(folder)
            tree = _merkle.MerkleTree.from_manifest(manifest)
            proof = tree.inclusion_proof("a.txt")
            wrong_root = "00" * 32
            ok = orphograph.verify_inclusion(
                file_path=str(folder / "a.txt"),
                rel_path="a.txt",
                proof=[list(step) for step in proof],
                root_hex=wrong_root,
            )
            self.assertFalse(ok)


class TestClientPrivacyContract(unittest.TestCase):
    def test_client_module_declares_no_file_contents(self):
        src = Path(_client.__file__).read_text()
        self.assertIn("never reads file contents", src)

    def test_post_anchor_folder_sends_only_manifest_json(self):
        captured = {}

        def fake_request(method, url, *, body=None, headers=None, timeout=60.0):
            captured["method"] = method
            captured["url"] = url
            captured["body"] = body
            captured["headers"] = headers
            return {"receipt_id": "rid-x", "root_hex": "00" * 32, "leaf_count": 0,
                    "calendars_ok": 0, "calendars_total": 0}

        manifest = {
            "algorithm": "orphograph-merkle-v1-rfc6962",
            "version": 1,
            "root_hex": "ab" * 32,
            "leaves": [],
        }
        with mock.patch.object(_client, "_request", side_effect=fake_request):
            _client.post_anchor_folder(
                manifest,
                server_url="https://example.invalid",
                api_key="k-test",
                client_label="hello",
            )

        self.assertEqual(captured["method"], "POST")
        self.assertEqual(captured["url"], "https://example.invalid/api/anchor_folder")
        payload = json.loads(captured["body"].decode("utf-8"))
        self.assertEqual(payload["manifest"], manifest)
        self.assertEqual(payload["client_label"], "hello")
        self.assertEqual(captured["headers"].get("X-Orpho-Api-Key"), "k-test")
        self.assertEqual(captured["headers"].get("Content-Type"), "application/json")



    def test_user_agent_identifies_itself_and_impersonates_nothing(self):
        """The SDK reaches third parties through the public repo, so a spoofed
        agent propagated the lie to every user of it -- and to our own server
        logs, which could no longer tell SDK traffic from a browser. Nothing
        asserted this until 2026-08-20.

        A UA must be SET: the CDN 403s urllib's default `Python-urllib/3.x`,
        and that is the ONLY agent it rejects.
        """
        ua = _client.USER_AGENT
        self.assertTrue(ua, "USER_AGENT must be set or urllib falls back to a 403'd default")
        self.assertNotIn("Python-urllib", ua)
        for product in ("Mozilla/", "Chrome/", "Safari/", "AppleWebKit/", "Gecko/"):
            self.assertNotIn(product, ua,
                             f"SDK User-Agent impersonates a browser ({product!r}): {ua!r}")
        self.assertIn("orphograph", ua.lower(),
                      f"SDK User-Agent must identify itself, got {ua!r}")


class TestCli(unittest.TestCase):
    def test_cli_anchor_prints_json(self):
        from orphograph import _cli

        with tempfile.TemporaryDirectory() as td:
            folder = Path(td)
            (folder / "x.txt").write_bytes(b"x")
            stub = {
                "receipt_id": "cli-rid",
                "root_hex": "ab" * 32,
                "leaf_count": 1,
                "calendars_ok": 5,
                "calendars_total": 5,
            }
            with mock.patch.object(orphograph, "anchor_folder", return_value=stub), \
                 mock.patch("orphograph._cli.anchor_folder", return_value=stub), \
                 mock.patch("sys.stdout", new_callable=io.StringIO) as fake_out:
                rc = _cli.main(["anchor", str(folder)])
            self.assertEqual(rc, 0)
            out = json.loads(fake_out.getvalue().strip())
            self.assertEqual(out["receipt_id"], "cli-rid")

    def test_cli_verify_returns_exit_code_1_on_mismatch(self):
        from orphograph import _cli

        with tempfile.TemporaryDirectory() as td:
            folder = Path(td)
            (folder / "x.txt").write_bytes(b"x")
            with mock.patch("orphograph._cli.verify_folder", return_value=False), \
                 mock.patch("sys.stdout", new_callable=io.StringIO):
                rc = _cli.main(["verify", str(folder), "rid-bad"])
            self.assertEqual(rc, 1)

    def test_cli_verify_custom_excludes_end_to_end(self):
        # AUDIT D2, CLI surface: before the fix `orphograph verify` had no
        # --exclude flag, so a custom-exclude anchor could NEVER verify from
        # the CLI (permanent false negative). Exercise the real verify path
        # with only the HTTP layer stubbed.
        from orphograph import _cli

        with tempfile.TemporaryDirectory() as td:
            folder = Path(td)
            (folder / "a.txt").write_bytes(b"alpha")
            (folder / "scratch.log").write_bytes(b"working notes")
            manifest = _merkle.MerkleTree.from_folder(folder, exclude=["*.log"]).manifest()

            def fake_get(receipt_id, **kwargs):
                return {"receipt": {"receipt_id": receipt_id}, "manifest": manifest}

            with mock.patch.object(orphograph._client, "get_verify_folder", side_effect=fake_get):
                # The failure case: no excludes -> wrong root -> exit 1.
                with mock.patch("sys.stdout", new_callable=io.StringIO) as out_bad:
                    rc_bad = _cli.main(["verify", str(folder), "rid-x"])
                # The fix: same excludes as at anchor time -> exit 0.
                with mock.patch("sys.stdout", new_callable=io.StringIO) as out_ok:
                    rc_ok = _cli.main(["verify", str(folder), "rid-x", "--exclude", "*.log"])
            self.assertEqual(rc_bad, 1)
            self.assertEqual(json.loads(out_bad.getvalue())["match"], False)
            self.assertEqual(rc_ok, 0)
            self.assertEqual(json.loads(out_ok.getvalue())["match"], True)

    def test_cli_anchor_passes_excludes_through(self):
        from orphograph import _cli

        with tempfile.TemporaryDirectory() as td:
            folder = Path(td)
            (folder / "a.txt").write_bytes(b"alpha")
            (folder / "scratch.log").write_bytes(b"working notes")
            captured = {}

            def fake_post(manifest, **kwargs):
                captured["manifest"] = manifest
                return {"receipt_id": "rid-cli", "root_hex": manifest["root_hex"]}

            with mock.patch.object(orphograph._client, "post_anchor_folder", side_effect=fake_post), \
                 mock.patch("sys.stdout", new_callable=io.StringIO):
                rc = _cli.main(["anchor", str(folder), "--exclude", "*.log"])
            self.assertEqual(rc, 0)
            self.assertEqual(
                [l["path"] for l in captured["manifest"]["leaves"]], ["a.txt"]
            )


if __name__ == "__main__":
    unittest.main()
