#!/usr/bin/env python3
"""test_ots_chain_verdict.py — the chain check must report the CLIENT's verdict.

DEFECT (2026-08-06 Stage 3e hunt, vacuous-pass sweep)
-----------------------------------------------------
`dist/orphograph-verify/verify.py::_ots_subcheck` and
`dist/orphograph-verify/verify_lineage.py::_ots_binary_check` both decided
whether Bitcoin confirmed a timestamp by testing

    if hash_hex.lower() in (stdout + stderr).lower():

while printing — and discarding — the client's exit code. Two failures at once:

  * A REJECTED verification passed. The real client's failure line names the
    hash it failed on ("Failed! Attestation for aabb… could not be verified"),
    so the substring test was satisfied by the error message itself.
    Reproduced against a stand-in client that exited 1: the verifier returned 0.
  * A GENUINE success would have been reported as FAIL, because the client's
    success line ("Success! Bitcoin block 700000 attests existence as of …")
    does not contain the hash.

This was the only chain-consulting path in the published toolchain, on a
product whose entire promise is independent verifiability.

These tests drive the REAL entry points with a stand-in `ots` on PATH, which
is the wire-path check — asserting on otscheck alone would not prove the two
call sites actually use it.
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
sys.path.insert(0, str(BUNDLE))

import otscheck  # noqa: E402

HASH = "aabbccddeeff00112233445566778899aabbccddeeff00112233445566778899"
OTHER = "1111111111111111111111111111111111111111111111111111111111111111"

# The client's real output shapes, as of opentimestamps-client 0.7.x.
CLIENT_FAILS = ("Assuming target filename is 'x'\n"
                f"Failed! Attestation for {HASH} could not be verified\n", 1)
CLIENT_SUCCEEDS = ("Assuming target filename is 'x'\n"
                   "Success! Bitcoin block 700000 attests existence as of "
                   "2021-09-11 CEST\n", 0)
CLIENT_PENDING = ("Assuming target filename is 'x'\n"
                  "Pending confirmation in Bitcoin blockchain\n", 0)
CLIENT_SILENT = ("", 0)
# opentimestamps-client v0.7.2 with no Bitcoin node reachable. Verified by
# running the real client on this machine: it exits 1 and does NOT fall back
# to a block explorer.
CLIENT_NO_NODE = (
    "Could not connect to Bitcoin node: Cookie file unusable ([Errno 2] No "
    "such file or directory: '~/Library/Application Support/Bitcoin/.cookie') "
    "and rpcpassword not specified\n", 1)


def _write_ots(path: Path, hash_hex: str) -> Path:
    """A structurally valid .ots committing to hash_hex."""
    path.write_bytes(otscheck.OTS_HEADER_MAGIC + b"\x01\x08"
                     + bytes.fromhex(hash_hex) + b"\x00" * 16)
    return path


class _FakeClient:
    """Puts a stand-in `ots` on PATH for the duration of the block."""

    def __init__(self, stdout: str, code: int):
        self.stdout, self.code = stdout, code

    def __enter__(self):
        self.tmp = tempfile.TemporaryDirectory()
        binf = Path(self.tmp.name) / "ots"
        binf.write_text("#!/bin/sh\ncat <<'EOF'\n" + self.stdout
                        + "EOF\nexit " + str(self.code) + "\n")
        binf.chmod(0o755)
        self._old = os.environ.get("PATH", "")
        os.environ["PATH"] = f"{self.tmp.name}{os.pathsep}{self._old}"
        return self

    def __exit__(self, *a):
        os.environ["PATH"] = self._old
        self.tmp.cleanup()


class TestChainVerdict(unittest.TestCase):
    """otscheck must never call a non-confirmation a pass."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self.ots = _write_ots(self.dir / "a.ots", HASH)

    def tearDown(self):
        self._tmp.cleanup()

    def test_rejected_verification_is_not_a_pass(self):
        """THE defect: client exits 1 and says 'Failed!', naming our hash."""
        with _FakeClient(*CLIENT_FAILS):
            status, _, msg = otscheck.chain_verdict(self.ots, HASH)
        self.assertEqual(status, otscheck.FAILED,
                         f"the client REJECTED this attestation; verdict was "
                         f"{status!r} ({msg})")
        self.assertNotIn(status, otscheck.PASSING)

    def test_genuine_success_is_recognised(self):
        """The success line does not echo the hash — the old substring test
        would have called this a FAIL."""
        with _FakeClient(*CLIENT_SUCCEEDS):
            status, height, _ = otscheck.chain_verdict(self.ots, HASH)
        self.assertEqual(status, otscheck.VERIFIED)
        self.assertEqual(height, 700000)

    def test_pending_is_distinct_and_not_a_pass(self):
        with _FakeClient(*CLIENT_PENDING):
            status, _, msg = otscheck.chain_verdict(self.ots, HASH)
        self.assertEqual(status, otscheck.PENDING, msg)
        self.assertNotIn(status, otscheck.PASSING)

    def test_silent_success_exit_fails_closed(self):
        """Exit 0 with no recognisable attestation must not be assumed good."""
        with _FakeClient(*CLIENT_SILENT):
            status, _, _ = otscheck.chain_verdict(self.ots, HASH)
        self.assertEqual(status, otscheck.INDETERMINATE)
        self.assertNotIn(status, otscheck.PASSING)

    def test_unreachable_bitcoin_node_is_UNAVAILABLE_not_FAILED(self):
        """A check that could not RUN must never be reported as a proof that
        is BAD. The client exits 1 either way, so exit code alone cannot tell
        them apart — and calling a good receipt 'FAILED' is a false alarm on
        the one thing a trust product must get right.
        """
        with _FakeClient(*CLIENT_NO_NODE):
            status, _, msg = otscheck.chain_verdict(self.ots, HASH)
        self.assertEqual(status, otscheck.UNAVAILABLE,
                         f"a node-connection failure was classified {status!r}")
        self.assertNotIn(status, otscheck.PASSING)
        self.assertIn("did NOT run", msg)

    def test_the_client_is_asked_about_our_digest_explicitly(self):
        """Without -d the client infers a target filename by stripping .ots
        and errors when the original file is absent — the normal case for
        someone checking a receipt they were handed."""
        seen = {}
        real = subprocess.run

        def spy(argv, *a, **kw):
            seen["argv"] = argv
            return real(["true"], *a, **kw)

        otscheck.subprocess.run = spy
        try:
            otscheck.chain_verdict(self.ots, HASH)
        finally:
            otscheck.subprocess.run = real
        self.assertIn("-d", seen["argv"])
        self.assertIn(HASH, seen["argv"])

    def test_missing_client_is_not_a_pass(self):
        """'The check did not run' must never read as 'confirmed'."""
        old = os.environ.get("PATH", "")
        try:
            os.environ["PATH"] = str(Path(self._tmp.name) / "empty")
            status, _, _ = otscheck.chain_verdict(self.ots, HASH)
        finally:
            os.environ["PATH"] = old
        self.assertEqual(status, otscheck.UNAVAILABLE)
        self.assertNotIn(status, otscheck.PASSING)

    def test_proof_about_a_different_hash_is_rejected_before_the_client_runs(self):
        """Binding is local. A client success about some OTHER timestamp is
        not evidence about ours — scraping stdout never established this."""
        with _FakeClient(*CLIENT_SUCCEEDS):
            status, _, msg = otscheck.chain_verdict(self.ots, OTHER)
        self.assertEqual(status, otscheck.UNBOUND, msg)
        self.assertIn("different file", msg)

    def test_empty_directory_is_a_failure_not_a_vacuous_pass(self):
        empty = self.dir / "none"
        empty.mkdir()
        ok, _, msgs = otscheck.check_dir(empty, HASH)
        self.assertFalse(ok)
        self.assertTrue(any("NO .ots FILES" in m for m in msgs), msgs)


class TestCallSitesUseIt(unittest.TestCase):
    """Wire-path: the shipped scripts must actually route through otscheck.
    Engine-level green says nothing about what the tools do."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self.ots = _write_ots(self.dir / "a.ots", HASH)

    def tearDown(self):
        self._tmp.cleanup()

    def test_verify_py_subcheck_fails_on_a_rejected_attestation(self):
        import verify
        with _FakeClient(*CLIENT_FAILS):
            rc = verify._ots_subcheck(self.ots, HASH)
        self.assertNotEqual(rc, 0,
                            "verify.py returned OK for an attestation the "
                            "OpenTimestamps client explicitly rejected")

    def test_verify_py_subcheck_passes_a_real_success(self):
        import verify
        with _FakeClient(*CLIENT_SUCCEEDS):
            self.assertEqual(verify._ots_subcheck(self.ots, HASH), 0)

    def test_lineage_binary_check_fails_on_a_rejected_attestation(self):
        import verify_lineage
        with _FakeClient(*CLIENT_FAILS):
            ok, _, msgs = verify_lineage._ots_binary_check(self.dir, HASH)
        self.assertFalse(ok, f"verify_lineage blessed a rejected "
                             f"attestation: {msgs}")

    def test_lineage_binary_check_passes_a_real_success(self):
        import verify_lineage
        with _FakeClient(*CLIENT_SUCCEEDS):
            ok, height, msgs = verify_lineage._ots_binary_check(self.dir, HASH)
        self.assertTrue(ok, msgs)
        self.assertEqual(height, 700000)

    def test_no_call_site_re_derives_the_verdict_itself(self):
        """Root-cause guard: if a third script starts scraping the client's
        stdout for a hash, this catches it before it ships."""
        offenders = []
        for p in sorted(BUNDLE.glob("*.py")):
            if p.name == "otscheck.py":
                continue
            src = p.read_text()
            if '"ots", "verify"' in src or "'ots', 'verify'" in src:
                offenders.append(p.name)
        self.assertEqual(
            offenders, [],
            f"{offenders} invoke the ots client directly instead of going "
            f"through otscheck.chain_verdict. That is how the two call sites "
            f"drifted into the same inverted-verdict bug.")


if __name__ == "__main__":
    unittest.main()
