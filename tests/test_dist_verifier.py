#!/usr/bin/env python3
"""test_dist_verifier.py — pin the standalone dist/orphograph-verify CLI.

Builds a small fixture folder, anchors it via the in-process merkle module
(not the HTTP server), then invokes dist/orphograph-verify/verify.py as a
subprocess and asserts an OK / exit-0 outcome. Also runs the file mode
against a single inclusion proof.

The dist tree is deliberately standalone: it ships its own vendored
merkle.py with a sha256 banner. These tests run verify.py from outside
the repo's server/ path so any accidental coupling to the server tree
would fail.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIST_DIR = ROOT / "dist" / "orphograph-verify"
sys.path.insert(0, str(ROOT / "server"))

import merkle  # noqa: E402  (the server copy; used only to build fixtures)


def _populate(folder: Path) -> None:
    (folder / "doc.txt").write_text("hello world\n")
    (folder / "sub").mkdir()
    (folder / "sub" / "nested.bin").write_bytes(b"\x00\x01\x02\x03payload")
    (folder / "image.dat").write_bytes(b"jpeg-like-bytes" * 32)


class TestDistVerifier(unittest.TestCase):

    def _run_cli(self, *args: str) -> subprocess.CompletedProcess:
        # Run from a neutral cwd so accidental relative-path imports would fail.
        return subprocess.run(
            [sys.executable, str(DIST_DIR / "verify.py"), *args],
            check=False,
            capture_output=True,
            text=True,
            cwd=str(DIST_DIR.parent),
            timeout=60,
        )

    def test_help_exits_zero(self):
        out = self._run_cli("--help")
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("verify.py", out.stdout)
        self.assertIn("file", out.stdout)
        self.assertIn("folder", out.stdout)

    def test_folder_mode_ok(self):
        with tempfile.TemporaryDirectory() as td:
            folder = Path(td) / "evidence"
            folder.mkdir()
            _populate(folder)
            tree = merkle.MerkleTree.from_folder(folder)
            manifest_path = Path(td) / "manifest.json"
            manifest_path.write_text(json.dumps(tree.manifest()))

            out = self._run_cli(
                "folder",
                "--dir", str(folder),
                "--manifest", str(manifest_path),
            )
            self.assertEqual(out.returncode, 0, msg=out.stdout + out.stderr)
            self.assertIn("[OK]", out.stdout)
            self.assertIn(tree.root_hex(), out.stdout)

    def test_folder_mode_detects_tamper(self):
        with tempfile.TemporaryDirectory() as td:
            folder = Path(td) / "evidence"
            folder.mkdir()
            _populate(folder)
            tree = merkle.MerkleTree.from_folder(folder)
            manifest_path = Path(td) / "manifest.json"
            manifest_path.write_text(json.dumps(tree.manifest()))
            # Modify a file after the manifest was committed.
            (folder / "doc.txt").write_text("hello tampered world\n")
            out = self._run_cli(
                "folder",
                "--dir", str(folder),
                "--manifest", str(manifest_path),
            )
            self.assertEqual(out.returncode, 3, msg=out.stdout + out.stderr)
            self.assertIn("FAIL", out.stdout)

    def test_file_mode_ok(self):
        with tempfile.TemporaryDirectory() as td:
            folder = Path(td) / "evidence"
            folder.mkdir()
            _populate(folder)
            tree = merkle.MerkleTree.from_folder(folder)
            target_rel = "doc.txt"
            proof_steps = tree.inclusion_proof(target_rel)
            # Resolve file_sha256_hex from the manifest for the cross-check.
            file_hex = None
            for leaf in tree.manifest()["leaves"]:
                if leaf["path"] == target_rel:
                    file_hex = leaf["file_sha256_hex"]
                    break
            proof_doc = {
                "path": target_rel,
                "root_hex": tree.root_hex(),
                "file_sha256_hex": file_hex,
                "proof": [list(step) for step in proof_steps],
            }
            proof_path = Path(td) / "proof.json"
            proof_path.write_text(json.dumps(proof_doc))
            out = self._run_cli(
                "file",
                "--file", str(folder / target_rel),
                "--proof", str(proof_path),
            )
            self.assertEqual(out.returncode, 0, msg=out.stdout + out.stderr)
            self.assertIn("[OK]", out.stdout)

    def test_file_mode_detects_tamper(self):
        with tempfile.TemporaryDirectory() as td:
            folder = Path(td) / "evidence"
            folder.mkdir()
            _populate(folder)
            tree = merkle.MerkleTree.from_folder(folder)
            target_rel = "doc.txt"
            proof_steps = tree.inclusion_proof(target_rel)
            proof_doc = {
                "path": target_rel,
                "root_hex": tree.root_hex(),
                "proof": [list(step) for step in proof_steps],
            }
            proof_path = Path(td) / "proof.json"
            proof_path.write_text(json.dumps(proof_doc))
            # Tamper the local file before re-verification.
            (folder / target_rel).write_text("evidence has been altered\n")
            out = self._run_cli(
                "file",
                "--file", str(folder / target_rel),
                "--proof", str(proof_path),
            )
            self.assertEqual(out.returncode, 3, msg=out.stdout + out.stderr)


if __name__ == "__main__":
    unittest.main()
