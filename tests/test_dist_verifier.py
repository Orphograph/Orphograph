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

import hashlib
import io
import json
import os
import subprocess
import sys
import tarfile
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIST_DIR = ROOT / "dist" / "orphograph-verify"
MARKETPLACE_VERIFY = (
    ROOT / "marketplace" / "orphograph-plugin" / "skills" / "orphograph-verify" / "verify.py"
)
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

    # ---- AUDIT D1 (case-tamper) — the stored side is compared verbatim ----

    def test_folder_mode_rejects_uppercased_manifest_root(self):
        """A manifest whose root_hex was tampered to uppercase must FAIL.

        Pre-fix, verify.py lowercased BOTH sides of the folder-root
        comparison, so a byte-for-byte-different edited manifest still
        printed [OK] / exit 0 (same bug class as AUDIT_VERIFIER_DRIFT D1;
        spec §4.2 requires an exact match of lowercase hex strings).
        """
        with tempfile.TemporaryDirectory() as td:
            folder = Path(td) / "evidence"
            folder.mkdir()
            _populate(folder)
            tree = merkle.MerkleTree.from_folder(folder)
            manifest = tree.manifest()
            manifest["root_hex"] = manifest["root_hex"].upper()
            manifest_path = Path(td) / "manifest.json"
            manifest_path.write_text(json.dumps(manifest))
            out = self._run_cli(
                "folder", "--dir", str(folder), "--manifest", str(manifest_path)
            )
            self.assertEqual(out.returncode, 3, msg=out.stdout + out.stderr)
            self.assertIn("not in canonical form", out.stdout)

    def test_file_mode_rejects_uppercased_file_sha256_hex(self):
        """A proof whose file_sha256_hex was tampered to uppercase must FAIL.

        Pre-fix, verify.py lowercased the STORED file_sha256_hex before
        comparing, so the tampered proof sailed through to an [OK] exit 0.
        """
        with tempfile.TemporaryDirectory() as td:
            folder = Path(td) / "evidence"
            folder.mkdir()
            _populate(folder)
            tree = merkle.MerkleTree.from_folder(folder)
            target_rel = "doc.txt"
            file_hex = next(
                leaf["file_sha256_hex"]
                for leaf in tree.manifest()["leaves"]
                if leaf["path"] == target_rel
            )
            proof_doc = {
                "path": target_rel,
                "root_hex": tree.root_hex(),
                "file_sha256_hex": file_hex.upper(),
                "proof": [list(s) for s in tree.inclusion_proof(target_rel)],
            }
            proof_path = Path(td) / "proof.json"
            proof_path.write_text(json.dumps(proof_doc))
            out = self._run_cli(
                "file", "--file", str(folder / target_rel), "--proof", str(proof_path)
            )
            self.assertEqual(out.returncode, 3, msg=out.stdout + out.stderr)
            self.assertIn("not in canonical form", out.stdout)

    # ---- AUDIT D2 (custom excludes) — folder mode accepts --exclude ----

    def test_folder_mode_custom_exclude_roundtrip(self):
        """A folder anchored with custom excludes verifies with the SAME
        excludes — supplied explicitly, or (since 2026-08-22) read from the
        manifest's own `scope` block when no flag is given; DIFFERENT
        explicit excludes are a root mismatch, exit 3.

        Pre-fix, verify.py had no --exclude at all: custom-exclude
        manifests could NEVER verify (permanent false negative, exit 3,
        and --exclude was an argparse error). Before the scope read, a
        holder who did not retype the flags got the same false negative.
        """
        with tempfile.TemporaryDirectory() as td:
            folder = Path(td) / "evidence"
            folder.mkdir()
            _populate(folder)
            (folder / "scratch.log").write_text("excluded at anchor time\n")
            tree = merkle.MerkleTree.from_folder(folder, exclude=["*.log"])
            manifest_path = Path(td) / "manifest.json"
            manifest_path.write_text(json.dumps(tree.manifest()))

            # Same excludes as the anchor: verifies.
            ok = self._run_cli(
                "folder", "--dir", str(folder), "--manifest", str(manifest_path),
                "--exclude", "*.log",
            )
            self.assertEqual(ok.returncode, 0, msg=ok.stdout + ok.stderr)
            self.assertIn("[OK]", ok.stdout)

            # No flags: the manifest's scope block supplies `*.log` → verifies.
            scoped = self._run_cli(
                "folder", "--dir", str(folder), "--manifest", str(manifest_path)
            )
            self.assertEqual(scoped.returncode, 0, msg=scoped.stdout + scoped.stderr)
            self.assertIn("from the manifest's scope block", scoped.stdout)

            # DIFFERENT explicit excludes: scratch.log is walked, root differs.
            bad = self._run_cli(
                "folder", "--dir", str(folder), "--manifest", str(manifest_path),
                "--exclude", "*.nothing",
            )
            self.assertEqual(bad.returncode, 3, msg=bad.stdout + bad.stderr)


class TestMarketplaceVerifier(unittest.TestCase):
    """The Claude-plugin skill verifier is a shipped surface too (AUDIT D1)."""

    def _run(self, receipt: Path, file: Path) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(MARKETPLACE_VERIFY), str(receipt), str(file)],
            check=False, capture_output=True, text=True, timeout=60,
        )

    def _fixture(self, td: str, hash_transform=lambda h: h):
        sample = Path(td) / "sample.txt"
        sample.write_bytes(b"the anchored document\n")
        digest = hashlib.sha256(sample.read_bytes()).hexdigest()
        receipt = Path(td) / "receipt.json"
        receipt.write_text(json.dumps({
            "receipt_id": "TESTFIXTURE00001",
            "created_at": "2026-05-20T00:00:00Z",
            "hash_hex": hash_transform(digest),
        }))
        return receipt, sample

    def test_honest_receipt_matches(self):
        with tempfile.TemporaryDirectory() as td:
            receipt, sample = self._fixture(td)
            out = self._run(receipt, sample)
            self.assertEqual(out.returncode, 0, msg=out.stdout + out.stderr)
            self.assertIn("File matches", out.stdout)

    def test_rejects_uppercased_receipt_hash(self):
        """Pre-fix, the skill lowercased the STORED hash_hex, so an
        out-of-band case-tampered receipt still printed a checkmark."""
        with tempfile.TemporaryDirectory() as td:
            receipt, sample = self._fixture(td, str.upper)
            out = self._run(receipt, sample)
            self.assertEqual(out.returncode, 1, msg=out.stdout + out.stderr)
            self.assertIn("not in canonical form", out.stdout)

    def test_rejects_alias_only_receipt(self):
        """hash_hex and nowhere else (spec §3.3) — sha256 alias is corrupt."""
        with tempfile.TemporaryDirectory() as td:
            receipt, sample = self._fixture(td)
            doc = json.loads(receipt.read_text())
            doc["sha256"] = doc.pop("hash_hex")
            receipt.write_text(json.dumps(doc))
            out = self._run(receipt, sample)
            self.assertEqual(out.returncode, 2, msg=out.stdout + out.stderr)


class TestShippedBundlesInSync(unittest.TestCase):
    """The committed archives users download must be byte-identical to the
    tracked sources — the drift this guards against is how the pre-fix
    verifier kept shipping after the sources were corrected. Rebuild with
    scripts/build_verifier_dist.py after any source change."""

    ZIPS = (
        ROOT / "dist" / "orphograph-verify.zip",
        ROOT / "web" / "dist" / "orphograph-verify.zip",
    )
    TARBALL = ROOT / "web" / "verify" / "orphograph-verify-0.1.tar.gz"
    WEB_VERIFY = ROOT / "web" / "verify"

    def test_both_zips_identical(self):
        a, b = (p.read_bytes() for p in self.ZIPS)
        self.assertEqual(a, b, "repo zip and served web/dist zip differ")

    def test_zip_members_match_sources(self):
        expectations = {
            "verify.py": DIST_DIR / "verify.py",
            "merkle.py": DIST_DIR / "merkle.py",
            # verify.py imports it at module scope — a zip without it is
            # dead on arrival.
            "otscheck.py": DIST_DIR / "otscheck.py",
            "README.md": DIST_DIR / "README.md",
            "LICENSE.txt": DIST_DIR / "LICENSE",
            "QUICKSTART.txt": DIST_DIR / "QUICKSTART.txt",
        }
        for zip_path in self.ZIPS:
            with zipfile.ZipFile(zip_path) as zf:
                self.assertEqual(sorted(zf.namelist()), sorted(expectations),
                                 msg=str(zip_path))
                for member, src in expectations.items():
                    self.assertEqual(
                        zf.read(member), src.read_bytes(),
                        msg=f"{zip_path.name}:{member} drifted from {src.relative_to(ROOT)} "
                            "— run scripts/build_verifier_dist.py",
                    )

    def test_tarball_members_match_sources(self):
        expected_files = {"verify.py", "README.md", "LICENSE"} | {
            f"examples/{p.relative_to(self.WEB_VERIFY / 'examples')}"
            for p in (self.WEB_VERIFY / "examples").rglob("*") if p.is_file()
        }
        seen = set()
        with tarfile.open(self.TARBALL, mode="r:gz") as tf:
            for ti in tf.getmembers():
                if not ti.isfile():
                    continue
                seen.add(ti.name)
                src = self.WEB_VERIFY / ti.name
                self.assertTrue(src.is_file(), f"tarball member {ti.name} has no source")
                self.assertEqual(
                    tf.extractfile(ti).read(), src.read_bytes(),
                    msg=f"tarball:{ti.name} drifted from web/verify/{ti.name} "
                        "— run scripts/build_verifier_dist.py",
                )
        self.assertEqual(seen, expected_files)

    def test_shipped_zip_verifier_rejects_case_tamper(self):
        """Belt-and-braces: execute the verifier AS SHIPPED in the zip."""
        with tempfile.TemporaryDirectory() as td:
            with zipfile.ZipFile(self.ZIPS[1]) as zf:
                zf.extractall(td)
            folder = Path(td) / "evidence"
            folder.mkdir()
            _populate(folder)
            tree = merkle.MerkleTree.from_folder(folder)
            manifest = tree.manifest()
            manifest["root_hex"] = manifest["root_hex"].upper()
            manifest_path = Path(td) / "manifest.json"
            manifest_path.write_text(json.dumps(manifest))
            out = subprocess.run(
                [sys.executable, str(Path(td) / "verify.py"),
                 "folder", "--dir", str(folder), "--manifest", str(manifest_path)],
                check=False, capture_output=True, text=True, cwd=td, timeout=60,
            )
            self.assertEqual(out.returncode, 3, msg=out.stdout + out.stderr)
            self.assertIn("not in canonical form", out.stdout)


if __name__ == "__main__":
    unittest.main()
