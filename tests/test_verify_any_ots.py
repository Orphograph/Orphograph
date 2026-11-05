#!/usr/bin/env python3
"""`verify.py ots` checks ANY OpenTimestamps proof, not only ours.

The acceptance side of the product is the verifier: a holder of a proof made
by another tool should be able to check it here, with their own Bitcoin node,
and get the same honest verdicts our own receipts get. These tests drive the
REAL command line against a REAL proof made by the OpenTimestamps project's own
client (tests/vectors/foreign_ots/, see its README), with a stand-in `ots` on
PATH so no Bitcoin node or network is needed.

The binding rule also gained two checks: the format version byte, and the
file-hash operation. A proof that commits to a file's SHA-1, RIPEMD-160 or
Keccak-256 is not a proof about its SHA-256, and must say so instead of
comparing unrelated bytes.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BUNDLE = ROOT / "dist" / "orphograph-verify"
VERIFY = BUNDLE / "verify.py"
VEC = ROOT / "tests" / "vectors" / "foreign_ots"
sys.path.insert(0, str(BUNDLE))

import otscheck  # noqa: E402

FOREIGN_FILE = VEC / "hello-world.txt"
FOREIGN_OTS = VEC / "hello-world.txt.ots"
FOREIGN_SHA256 = "03ba204e50d126e4674c005e04d82e84c21366780af1f43bd54a37816b6ab340"

SUCCESS = ("Success! Bitcoin block 358391 attests existence as of "
           "2015-05-28 EDT\n", 0)
PENDING = ("Pending confirmation in Bitcoin blockchain\n", 0)
NO_NODE = ("Could not connect to Bitcoin node: Cookie file unusable "
           "and rpcpassword not specified\n", 1)
REJECTED = (f"Failed! Attestation for {FOREIGN_SHA256} could not be "
            "verified\n", 1)


class _FakeClient:
    """A stand-in `ots` on PATH that prints fixed output and records argv."""

    def __init__(self, stdout: str, code: int):
        self.stdout, self.code = stdout, code

    def __enter__(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.argv_log = Path(self.tmp.name) / "argv"
        binf = Path(self.tmp.name) / "ots"
        binf.write_text(
            "#!/bin/sh\n"
            f"printf '%s\\n' \"$@\" > '{self.argv_log}'\n"
            "cat <<'EOF'\n" + self.stdout + "EOF\n"
            f"exit {self.code}\n")
        binf.chmod(0o755)
        self.env = dict(os.environ)
        self.env["PATH"] = f"{self.tmp.name}{os.pathsep}{self.env.get('PATH', '')}"
        return self

    def __exit__(self, *a):
        self.tmp.cleanup()

    def run(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, str(VERIFY), "ots", *args],
                              capture_output=True, text=True, env=self.env,
                              timeout=60)


class TestForeignProofFixture(unittest.TestCase):
    """The fixture is what its README says it is."""

    def test_fixture_is_a_sha256_proof_of_the_file(self):
        import hashlib
        self.assertEqual(hashlib.sha256(FOREIGN_FILE.read_bytes()).hexdigest(),
                         FOREIGN_SHA256)
        ok, why = otscheck.local_binding(FOREIGN_OTS, FOREIGN_SHA256)
        self.assertTrue(ok, why)


class TestVerifyAnyOts(unittest.TestCase):

    def test_confirmed_foreign_proof_exits_0(self):
        with _FakeClient(*SUCCESS) as c:
            r = c.run("--file", str(FOREIGN_FILE), "--ots", str(FOREIGN_OTS))
            argv = c.argv_log.read_text().split()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("VERIFIED", r.stdout)
        # The client was asked about THIS digest, not a filename it guessed.
        self.assertEqual(argv[:3], ["verify", "-d", FOREIGN_SHA256])

    def test_sha256_instead_of_file(self):
        with _FakeClient(*SUCCESS) as c:
            r = c.run("--sha256", FOREIGN_SHA256, "--ots", str(FOREIGN_OTS))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_different_file_is_unbound_and_client_never_runs(self):
        with tempfile.TemporaryDirectory() as d:
            other = Path(d) / "other.txt"
            other.write_text("not the stamped file\n")
            with _FakeClient(*SUCCESS) as c:
                r = c.run("--file", str(other), "--ots", str(FOREIGN_OTS))
                ran = c.argv_log.exists()
        self.assertEqual(r.returncode, 4, r.stdout + r.stderr)
        self.assertIn("[OTS] UNBOUND", r.stdout)
        self.assertFalse(ran, "a proof about another file must be refused "
                              "before the client is asked anything")

    def test_rejection_exits_3(self):
        with _FakeClient(*REJECTED) as c:
            r = c.run("--file", str(FOREIGN_FILE), "--ots", str(FOREIGN_OTS))
        self.assertEqual(r.returncode, 4, r.stdout + r.stderr)
        self.assertIn("[OTS] FAILED", r.stdout)

    def test_pending_is_not_a_pass(self):
        with _FakeClient(*PENDING) as c:
            r = c.run("--file", str(FOREIGN_FILE), "--ots", str(FOREIGN_OTS))
        self.assertEqual(r.returncode, 4, r.stdout + r.stderr)
        self.assertIn("PENDING", r.stdout)

    def test_no_node_is_unavailable_not_failed(self):
        with _FakeClient(*NO_NODE) as c:
            r = c.run("--file", str(FOREIGN_FILE), "--ots", str(FOREIGN_OTS))
        self.assertEqual(r.returncode, 4, r.stdout + r.stderr)
        self.assertIn("UNAVAILABLE", r.stdout)
        self.assertNotIn("FAILED", r.stdout)

    def test_bad_sha256_input_exits_2(self):
        with _FakeClient(*SUCCESS) as c:
            r = c.run("--sha256", "XYZ", "--ots", str(FOREIGN_OTS))
        self.assertEqual(r.returncode, 2)

    def test_file_and_sha256_are_mutually_exclusive(self):
        with _FakeClient(*SUCCESS) as c:
            r = c.run("--file", str(FOREIGN_FILE), "--sha256", FOREIGN_SHA256,
                      "--ots", str(FOREIGN_OTS))
        self.assertEqual(r.returncode, 2)


class TestBindingChecksTheFileHashOp(unittest.TestCase):
    """The 32 bytes after the header are a SHA-256 only when the op says so."""

    def _proof(self, version: int, op: int, digest_hex: str) -> Path:
        p = Path(self._tmp.name) / f"v{version}op{op}.ots"
        p.write_bytes(otscheck.OTS_HEADER_MAGIC + bytes([version, op])
                      + bytes.fromhex(digest_hex) + b"\x00" * 16)
        return p

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self._tmp.cleanup()

    def test_sha256_op_binds(self):
        ok, why = otscheck.local_binding(self._proof(1, 0x08, FOREIGN_SHA256),
                                         FOREIGN_SHA256)
        self.assertTrue(ok, why)

    def test_keccak_op_with_equal_bytes_does_not_bind(self):
        """Same 32 bytes, but committed as Keccak-256 of some file: that says
        nothing about any file's SHA-256."""
        ok, why = otscheck.local_binding(self._proof(1, 0x67, FOREIGN_SHA256),
                                         FOREIGN_SHA256)
        self.assertFalse(ok)
        self.assertIn("Keccak-256", why)

    def test_sha1_op_does_not_bind(self):
        ok, why = otscheck.local_binding(self._proof(1, 0x02, FOREIGN_SHA256),
                                         FOREIGN_SHA256)
        self.assertFalse(ok)
        self.assertIn("SHA-1", why)

    def test_unknown_version_does_not_bind(self):
        ok, why = otscheck.local_binding(self._proof(2, 0x08, FOREIGN_SHA256),
                                         FOREIGN_SHA256)
        self.assertFalse(ok)
        self.assertIn("version 2", why)

    def test_truncated_proof_does_not_bind(self):
        p = Path(self._tmp.name) / "short.ots"
        p.write_bytes(otscheck.OTS_HEADER_MAGIC + b"\x01\x08" + b"\x03\xba")
        ok, why = otscheck.local_binding(p, FOREIGN_SHA256)
        self.assertFalse(ok)
        self.assertIn("truncated", why)


if __name__ == "__main__":
    unittest.main()
