#!/usr/bin/env python3
"""test_independent_verification_matrix.py — the documented-input matrix for
the standalone verifier (dist/orphograph-verify/verify.py).

One test per row of docs/LIFECYCLE.md §4 ("What independent verification
covers"). Every row drives the REAL entry point as a subprocess — never the
module functions — so what is asserted is what a relying party actually
receives: the exit code and the status tokens on stdout.

The only thing faked is the external `ots` binary: a throwaway directory is
put first on PATH holding a shell script that prints one canned client
message. That is how each chain state (VERIFIED / PENDING / FAILED /
UNAVAILABLE / UNBOUND) is reached deterministically without a Bitcoin node.
The .ots bytes themselves are built from otscheck's own header constant so
the LOCAL binding step runs for real.

Why this file exists (and is not folded into test_dist_verifier.py): that
file pins individual behaviours; this one pins the MATRIX — that the altered
cases and the indeterminate cases are distinguishable to a reader holding
only the verifier's output. If a future change makes "the check did not run"
look like "your proof is bad" (or the reverse), a row here turns red.
"""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIST_DIR = ROOT / "dist" / "orphograph-verify"
sys.path.insert(0, str(ROOT / "server"))

import merkle  # noqa: E402  (the SERVER copy builds the fixtures; the dist copy is the thing under test)


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


# Loaded by path, NOT via sys.path, so `merkle` above cannot silently resolve
# to the dist copy (that would let the module under test build its own fixtures).
otscheck = _load("_matrix_otscheck", DIST_DIR / "otscheck.py")
# The canonical "what the real client prints" wordings live in ONE place —
# tests/test_ots_chain_verdict.py — and are reused here so a client-wording
# retune cannot leave this file drifting.
_chain = _load("_matrix_chain_wordings", ROOT / "tests" / "test_ots_chain_verdict.py")
_OTS_SCRIPTS = {
    "verified": _chain.CLIENT_SUCCEEDS,
    "pending": _chain.CLIENT_PENDING,
    "failed": _chain.CLIENT_FAILS,
    "infra": _chain.CLIENT_NO_NODE,
}


def _populate(folder: Path) -> None:
    (folder / "doc.txt").write_text("hello world\n")
    (folder / "sub").mkdir()
    (folder / "sub" / "nested.bin").write_bytes(b"\x00\x01\x02\x03payload")
    (folder / "image.dat").write_bytes(b"jpeg-like-bytes" * 32)


def _fake_ots_file(path: Path, digest_hex: str) -> None:
    """A minimal .ots that otscheck.local_binding accepts as 'about digest_hex'.

    Header magic + 2 bytes (version, hash-op) + the 32-byte digest. Enough
    for the LOCAL binding step; the chain verdict is the (faked) client's.
    """
    path.write_bytes(otscheck.OTS_HEADER_MAGIC + b"\x01\x08" + bytes.fromhex(digest_hex))


class _Fixture:
    """Folder + manifest + single-file inclusion proof, built in-process."""

    def __init__(self, td: Path, extra_files: dict[str, bytes] | None = None,
                 exclude: list[str] | None = None):
        self.td = td
        self.folder = td / "evidence"
        self.folder.mkdir()
        _populate(self.folder)
        for name, data in (extra_files or {}).items():
            (self.folder / name).write_bytes(data)
        self.tree = merkle.MerkleTree.from_folder(self.folder, exclude=exclude)
        self.manifest_path = td / "manifest.json"
        self.manifest_path.write_text(json.dumps(self.tree.manifest()))
        self.target_rel = "doc.txt"
        self.target = self.folder / self.target_rel
        file_hex = next(
            leaf["file_sha256_hex"]
            for leaf in self.tree.manifest()["leaves"]
            if leaf["path"] == self.target_rel
        )
        self.proof_doc = {
            "path": self.target_rel,
            "root_hex": self.tree.root_hex(),
            "file_sha256_hex": file_hex,
            "proof": [list(step) for step in self.tree.inclusion_proof(self.target_rel)],
        }
        self.proof_path = td / "proof.json"
        self.write_proof(self.proof_doc)

    def write_proof(self, doc: dict) -> Path:
        self.proof_path.write_text(json.dumps(doc))
        return self.proof_path

    def ots_for_root(self) -> Path:
        p = self.td / "root.ots"
        _fake_ots_file(p, self.tree.root_hex())
        return p


class TestIndependentVerificationMatrix(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        # One fake-`ots` directory per chain state, built ONCE (first-exec of a
        # fresh script is the expensive part on macOS), plus one empty dir for
        # "no ots binary at all".
        cls._bins = Path(tempfile.mkdtemp(prefix="fake-ots-"))
        (cls._bins / "absent").mkdir()
        for mode, (msg, rc) in _OTS_SCRIPTS.items():
            d = cls._bins / mode
            d.mkdir()
            (d / "ots.msg").write_text(msg)
            script = d / "ots"
            # The wording is read from a file, so quotes/newlines in the real
            # client output never have to be shell-escaped.
            # $0 is just 'ots' under PATH lookup, so the message path must be absolute.
            script.write_text(f'#!/bin/sh\n/bin/cat "{(d / "ots.msg")}"\nexit {rc}\n')
            script.chmod(script.stat().st_mode | stat.S_IXUSR)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls._bins, ignore_errors=True)

    def _run(self, *args: str, ots_mode: str | None = "absent") -> subprocess.CompletedProcess:
        """Run verify.py as a relying party would.

        ots_mode: None  → inherit PATH (chain step not exercised by the row)
                  "absent" → PATH holds NO `ots` (UNAVAILABLE, binary missing)
                  one of _OTS_SCRIPTS → a fake `ots` printing that wording
        """
        env = dict(os.environ)
        if ots_mode is not None:
            env["PATH"] = str(self._bins / ots_mode)  # ONLY the fake dir — nothing else resolves
        return subprocess.run(
            [sys.executable, str(DIST_DIR / "verify.py"), *args],
            check=False, capture_output=True, text=True,
            cwd=str(DIST_DIR.parent), timeout=60, env=env,
        )

    # ---- valid original -------------------------------------------------

    def test_valid_original_file(self):
        with tempfile.TemporaryDirectory() as td:
            fx = _Fixture(Path(td))
            out = self._run("file", "--file", str(fx.target), "--proof", str(fx.proof_path), ots_mode=None)
            self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
            self.assertIn("[OK]", out.stdout)

    def test_valid_original_folder(self):
        with tempfile.TemporaryDirectory() as td:
            fx = _Fixture(Path(td))
            out = self._run("folder", "--dir", str(fx.folder), "--manifest", str(fx.manifest_path), ots_mode=None)
            self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
            self.assertIn("[OK]", out.stdout)

    # ---- altered artifact --------------------------------------------------

    def test_altered_artifact_is_invalid_exit_3(self):
        with tempfile.TemporaryDirectory() as td:
            fx = _Fixture(Path(td))
            fx.target.write_text("hello world\n" + "x")  # one appended byte
            out = self._run("file", "--file", str(fx.target), "--proof", str(fx.proof_path), ots_mode=None)
            self.assertEqual(out.returncode, 3, out.stdout + out.stderr)
            self.assertIn("[FAIL]", out.stdout)
            self.assertNotIn("[OK]", out.stdout)

    # ---- altered receipt ---------------------------------------------------

    def test_altered_receipt_root_hex_is_invalid_exit_3(self):
        with tempfile.TemporaryDirectory() as td:
            fx = _Fixture(Path(td))
            doc = dict(fx.proof_doc)
            doc["root_hex"] = "00" * 32  # valid hex, wrong root
            fx.write_proof(doc)
            out = self._run("file", "--file", str(fx.target), "--proof", str(fx.proof_path), ots_mode=None)
            self.assertEqual(out.returncode, 3, out.stdout + out.stderr)
            self.assertIn("did not reproduce root", out.stdout)

    def test_altered_receipt_proof_step_is_invalid_exit_3(self):
        with tempfile.TemporaryDirectory() as td:
            fx = _Fixture(Path(td))
            doc = json.loads(json.dumps(fx.proof_doc))
            self.assertTrue(doc["proof"], "fixture must have at least one proof step")
            doc["proof"][0][1] = "ff" * 32  # sibling hash swapped
            fx.write_proof(doc)
            out = self._run("file", "--file", str(fx.target), "--proof", str(fx.proof_path), ots_mode=None)
            self.assertEqual(out.returncode, 3, out.stdout + out.stderr)

    def test_altered_manifest_root_is_invalid_exit_3(self):
        with tempfile.TemporaryDirectory() as td:
            fx = _Fixture(Path(td))
            m = fx.tree.manifest()
            m["root_hex"] = "11" * 32
            fx.manifest_path.write_text(json.dumps(m))
            out = self._run("folder", "--dir", str(fx.folder), "--manifest", str(fx.manifest_path), ots_mode=None)
            self.assertEqual(out.returncode, 3, out.stdout + out.stderr)
            self.assertIn("does not match manifest", out.stdout)

    # ---- missing component -------------------------------------------------

    def test_missing_proof_file_is_input_error_exit_2(self):
        with tempfile.TemporaryDirectory() as td:
            fx = _Fixture(Path(td))
            out = self._run("file", "--file", str(fx.target), "--proof", str(fx.td / "nope.json"), ots_mode=None)
            self.assertEqual(out.returncode, 2, out.stdout + out.stderr)
            self.assertNotIn("[FAIL]", out.stdout)  # not a verdict — an input problem

    def test_missing_artifact_is_input_error_exit_2(self):
        with tempfile.TemporaryDirectory() as td:
            fx = _Fixture(Path(td))
            out = self._run("file", "--file", str(fx.td / "gone.txt"), "--proof", str(fx.proof_path), ots_mode=None)
            self.assertEqual(out.returncode, 2, out.stdout + out.stderr)

    def test_missing_required_field_is_input_error_exit_2(self):
        with tempfile.TemporaryDirectory() as td:
            fx = _Fixture(Path(td))
            doc = dict(fx.proof_doc)
            del doc["proof"]
            fx.write_proof(doc)
            out = self._run("file", "--file", str(fx.target), "--proof", str(fx.proof_path), ots_mode=None)
            self.assertEqual(out.returncode, 2, out.stdout + out.stderr)
            self.assertIn("missing required fields", out.stderr)

    def test_corrupt_proof_json_is_input_error_exit_2(self):
        with tempfile.TemporaryDirectory() as td:
            fx = _Fixture(Path(td))
            fx.proof_path.write_text("{not json")
            out = self._run("file", "--file", str(fx.target), "--proof", str(fx.proof_path), ots_mode=None)
            self.assertEqual(out.returncode, 2, out.stdout + out.stderr)

    def test_missing_ots_file_is_input_error_exit_2(self):
        with tempfile.TemporaryDirectory() as td:
            fx = _Fixture(Path(td))
            out = self._run("file", "--file", str(fx.target), "--proof", str(fx.proof_path),
                            "--ots", str(fx.td / "missing.ots"), ots_mode=None)
            self.assertEqual(out.returncode, 2, out.stdout + out.stderr)

    # ---- unsupported / unknown format --------------------------------------

    def test_unknown_extra_fields_are_ignored_forward_compatible(self):
        """Documented behaviour: there is NO format-version gate in the dist
        verifier. Unknown keys are ignored; the required fields decide.
        (A receipt from a future format that still carries path/root_hex/proof
        verifies; one that drops them is exit 2.) See docs/LIFECYCLE.md §4."""
        with tempfile.TemporaryDirectory() as td:
            fx = _Fixture(Path(td))
            doc = dict(fx.proof_doc)
            doc["format_version"] = "99.0"
            doc["future_field"] = {"nested": True}
            fx.write_proof(doc)
            out = self._run("file", "--file", str(fx.target), "--proof", str(fx.proof_path), ots_mode=None)
            self.assertEqual(out.returncode, 0, out.stdout + out.stderr)

    def test_malformed_proof_step_shape_is_invalid_exit_3(self):
        with tempfile.TemporaryDirectory() as td:
            fx = _Fixture(Path(td))
            doc = json.loads(json.dumps(fx.proof_doc))
            doc["proof"] = [["UP", "ab" * 32]]  # direction not L/R
            fx.write_proof(doc)
            out = self._run("file", "--file", str(fx.target), "--proof", str(fx.proof_path), ots_mode=None)
            self.assertEqual(out.returncode, 3, out.stdout + out.stderr)
            self.assertIn("malformed proof step", out.stdout + out.stderr)

    # ---- incorrect identity (wrong file for this proof) ------------------------

    def test_proof_applied_to_a_different_file_is_invalid_exit_3(self):
        with tempfile.TemporaryDirectory() as td:
            fx = _Fixture(Path(td))
            other = fx.folder / "image.dat"
            out = self._run("file", "--file", str(other), "--proof", str(fx.proof_path), ots_mode=None)
            self.assertEqual(out.returncode, 3, out.stdout + out.stderr)
            self.assertIn("does not match proof's file_sha256_hex", out.stdout)

    def test_ots_about_a_different_hash_is_unbound_exit_4(self):
        with tempfile.TemporaryDirectory() as td:
            fx = _Fixture(Path(td))
            wrong = fx.td / "other.ots"
            _fake_ots_file(wrong, "ab" * 32)
            out = self._run("file", "--file", str(fx.target), "--proof", str(fx.proof_path),
                            "--ots", str(wrong), ots_mode="verified")
            self.assertEqual(out.returncode, 4, out.stdout + out.stderr)
            self.assertIn(otscheck.UNBOUND, out.stdout)
            self.assertIn("[OK]   inclusion proof verifies", out.stdout)  # Merkle still stands

    # ---- derivative artifact -------------------------------------------------

    def test_derivative_copy_is_invalid_for_the_original_receipt(self):
        """An edited derivative does not verify against the ORIGINAL's proof
        (exit 3). Derivative lineage is a separate tool (verify_lineage.py;
        tests/test_edit_lineage.py) — this verifier makes no lineage claim."""
        with tempfile.TemporaryDirectory() as td:
            fx = _Fixture(Path(td))
            derivative = fx.td / "doc-edited.txt"
            derivative.write_bytes(fx.target.read_bytes() + b"\nedited")
            out = self._run("file", "--file", str(derivative), "--proof", str(fx.proof_path), ots_mode=None)
            self.assertEqual(out.returncode, 3, out.stdout + out.stderr)

    # ---- metadata loss -------------------------------------------------------

    def test_metadata_loss_same_bytes_still_verify(self):
        """Verification is over BYTES. A renamed copy with new mtime verifies
        (exit 0) — which is also what the verifier does NOT prove: nothing
        about filename, timestamps, author, or embedded metadata."""
        with tempfile.TemporaryDirectory() as td:
            fx = _Fixture(Path(td))
            moved = fx.td / "renamed-elsewhere.bin"
            moved.write_bytes(fx.target.read_bytes())
            os.utime(moved, (0, 0))
            out = self._run("file", "--file", str(moved), "--proof", str(fx.proof_path), ots_mode=None)
            self.assertEqual(out.returncode, 0, out.stdout + out.stderr)

    # ---- unavailable service (INDETERMINATE, not invalid) ----------------------

    def test_no_ots_binary_is_unavailable_not_failed(self):
        with tempfile.TemporaryDirectory() as td:
            fx = _Fixture(Path(td))
            out = self._run("file", "--file", str(fx.target), "--proof", str(fx.proof_path),
                            "--ots", str(fx.ots_for_root()), ots_mode="absent")
            self.assertEqual(out.returncode, 4, out.stdout + out.stderr)
            self.assertIn(otscheck.UNAVAILABLE, out.stdout)
            self.assertIn("did NOT run", out.stdout)
            self.assertNotIn(otscheck.FAILED, out.stdout)
            self.assertIn("[OK]   inclusion proof verifies", out.stdout)

    def test_unreachable_node_is_unavailable_not_failed(self):
        with tempfile.TemporaryDirectory() as td:
            fx = _Fixture(Path(td))
            out = self._run("file", "--file", str(fx.target), "--proof", str(fx.proof_path),
                            "--ots", str(fx.ots_for_root()), ots_mode="infra")
            self.assertEqual(out.returncode, 4, out.stdout + out.stderr)
            self.assertIn(otscheck.UNAVAILABLE, out.stdout)
            self.assertNotIn(otscheck.FAILED, out.stdout)

    def test_pending_is_reported_pending_and_is_not_a_pass(self):
        with tempfile.TemporaryDirectory() as td:
            fx = _Fixture(Path(td))
            out = self._run("file", "--file", str(fx.target), "--proof", str(fx.proof_path),
                            "--ots", str(fx.ots_for_root()), ots_mode="pending")
            self.assertEqual(out.returncode, 4, out.stdout + out.stderr)
            self.assertIn(otscheck.PENDING, out.stdout)
            self.assertNotIn(otscheck.VERIFIED, out.stdout)

    # ---- invalid chain (the client REJECTED it) ---------------------------------

    def test_client_rejection_is_failed_not_unavailable(self):
        with tempfile.TemporaryDirectory() as td:
            fx = _Fixture(Path(td))
            out = self._run("file", "--file", str(fx.target), "--proof", str(fx.proof_path),
                            "--ots", str(fx.ots_for_root()), ots_mode="failed")
            self.assertEqual(out.returncode, 4, out.stdout + out.stderr)
            self.assertIn(otscheck.FAILED, out.stdout)
            self.assertNotIn(otscheck.UNAVAILABLE, out.stdout)

    # ---- the pass -------------------------------------------------------------

    def test_client_confirmation_is_verified_exit_0(self):
        with tempfile.TemporaryDirectory() as td:
            fx = _Fixture(Path(td))
            out = self._run("file", "--file", str(fx.target), "--proof", str(fx.proof_path),
                            "--ots", str(fx.ots_for_root()), ots_mode="verified")
            self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
            self.assertIn(otscheck.VERIFIED, out.stdout)

    # ---- invalid vs indeterminate are distinguishable ---------------------------

    def test_invalid_and_indeterminate_share_exit_4_but_differ_on_stdout(self):
        """Exit code 4 covers every non-pass chain state. A relying party that
        reads ONLY the exit code cannot tell "rejected" from "did not run"; the
        status token on stdout is the discriminator, and the two never co-occur.
        docs/LIFECYCLE.md §4 states this; the row here keeps it true."""
        with tempfile.TemporaryDirectory() as td:
            fx = _Fixture(Path(td))
            rejected = self._run("file", "--file", str(fx.target), "--proof", str(fx.proof_path),
                                 "--ots", str(fx.ots_for_root()), ots_mode="failed")
            not_run = self._run("file", "--file", str(fx.target), "--proof", str(fx.proof_path),
                                "--ots", str(fx.ots_for_root()), ots_mode="absent")
            self.assertEqual((rejected.returncode, not_run.returncode), (4, 4))
            self.assertIn(otscheck.FAILED, rejected.stdout)
            self.assertNotIn(otscheck.UNAVAILABLE, rejected.stdout)
            self.assertIn(otscheck.UNAVAILABLE, not_run.stdout)
            self.assertNotIn(otscheck.FAILED, not_run.stdout)


    # ---- scope block: VERIFIER_SPEC §4.2 — the manifest's scope is authoritative ----

    def _scoped(self, td: str):
        """One folder with a stray *.tmp, anchored with exclude=['*.tmp']; returns
        (fixture, scoped manifest path, manifest dict). Shared by the scope rows."""
        # *.log is NOT in the standard deny-list (unlike *.tmp), so a walk that
        # ignores the recorded scope really does include the stray file.
        fx = _Fixture(Path(td), extra_files={"ignore-me.log": b"scratch"}, exclude=["*.log"])
        m = fx.tree.manifest()
        self.assertEqual(m["scope"]["exclude"], ["*.log"])
        return fx, fx.manifest_path, m

    def test_custom_exclude_folder_verifies_with_no_flags_via_manifest_scope(self):
        """Anchored with custom excludes → the manifest records them in `scope`
        → a relying party with no flags still reproduces the root, and sees
        the patterns printed."""
        with tempfile.TemporaryDirectory() as td:
            fx, mpath, _ = self._scoped(td)
            out = self._run("folder", "--dir", str(fx.folder), "--manifest", str(mpath), ots_mode=None)
            self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
            self.assertIn("from the manifest's scope block", out.stdout)
            self.assertIn("- *.log", out.stdout)
            self.assertNotIn("[WARN]", out.stdout)

    def test_flags_are_ignored_with_a_warning_when_the_manifest_has_a_scope(self):
        """Same rule as sdk-python verify_folder: the recorded scope wins; the
        caller's list is not silently substituted (spec §4.2)."""
        with tempfile.TemporaryDirectory() as td:
            fx, mpath, _ = self._scoped(td)
            out = self._run("folder", "--dir", str(fx.folder), "--manifest", str(mpath),
                            "--exclude", "*.nothing", ots_mode=None)
            self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
            self.assertIn("[WARN] --exclude given but the manifest carries a scope block", out.stdout)

    def test_ignore_manifest_scope_makes_flags_apply(self):
        """The explicit operator override: wrong flags now walk the .tmp → root
        differs → exit 3, and the output says the scope was ignored on request."""
        with tempfile.TemporaryDirectory() as td:
            fx, mpath, _ = self._scoped(td)
            out = self._run("folder", "--dir", str(fx.folder), "--manifest", str(mpath),
                            "--ignore-manifest-scope", "--exclude", "*.nothing", ots_mode=None)
            self.assertEqual(out.returncode, 3, out.stdout + out.stderr)
            self.assertIn("manifest scope ignored on request", out.stdout)

    def test_edited_scope_block_warns_but_root_decides(self):
        with tempfile.TemporaryDirectory() as td:
            fx, _, m = self._scoped(td)
            m["scope"]["instruction"] = "edited after anchoring"  # scope_hex now stale
            mpath = fx.td / "edited-scope.json"
            mpath.write_text(json.dumps(m))
            out = self._run("folder", "--dir", str(fx.folder), "--manifest", str(mpath), ots_mode=None)
            self.assertEqual(out.returncode, 0, out.stdout + out.stderr)  # excludes still right → root matches
            self.assertIn("[WARN] scope_hex does not match", out.stdout)

    def test_manifest_without_scope_uses_flags_then_standard_denylist(self):
        with tempfile.TemporaryDirectory() as td:
            fx = _Fixture(Path(td))
            m = fx.tree.manifest()
            m.pop("scope", None)
            mpath = fx.td / "no-scope.json"
            mpath.write_text(json.dumps(m))
            out = self._run("folder", "--dir", str(fx.folder), "--manifest", str(mpath), ots_mode=None)
            self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
            self.assertIn("standard deny-list", out.stdout)
            # a scope-less manifest DOES honour flags (the default list contains *.tmp,
            # so an explicit list without it changes the walk → different root → 3)
            (fx.folder / "ignore-me.tmp").write_text("scratch")
            out = self._run("folder", "--dir", str(fx.folder), "--manifest", str(mpath),
                            "--exclude", "*.nothing", ots_mode=None)
            self.assertEqual(out.returncode, 3, out.stdout + out.stderr)
            self.assertIn("manifest carries no scope block", out.stdout)

    def test_no_scope_custom_anchor_no_flags_is_the_documented_false_negative(self):
        """Producers that write no scope block (Node SDK, browser) + a custom
        exclude at anchor + no flags → exit 3. Pinned so a future 'infer the
        excludes' change cannot flip FAIL→PASS silently (LIFECYCLE §6.6)."""
        with tempfile.TemporaryDirectory() as td:
            fx, _, m = self._scoped(td)
            m.pop("scope")
            mpath = fx.td / "no-scope-custom.json"
            mpath.write_text(json.dumps(m))
            out = self._run("folder", "--dir", str(fx.folder), "--manifest", str(mpath), ots_mode=None)
            self.assertEqual(out.returncode, 3, out.stdout + out.stderr)
            self.assertIn("standard deny-list", out.stdout)

    def test_scope_without_scope_hex_warns_that_edits_are_undetectable(self):
        with tempfile.TemporaryDirectory() as td:
            fx, _, m = self._scoped(td)
            m["scope"].pop("scope_hex")
            mpath = fx.td / "no-hash.json"
            mpath.write_text(json.dumps(m))
            out = self._run("folder", "--dir", str(fx.folder), "--manifest", str(mpath), ots_mode=None)
            self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
            self.assertIn("[WARN] scope block carries no scope_hex", out.stdout)

    def test_malformed_scope_is_reported_as_malformed_not_absent(self):
        with tempfile.TemporaryDirectory() as td:
            fx, _, m = self._scoped(td)
            m["scope"]["exclude"] = "*.tmp"  # a string, not a list
            mpath = fx.td / "malformed-scope.json"
            mpath.write_text(json.dumps(m))
            out = self._run("folder", "--dir", str(fx.folder), "--manifest", str(mpath), ots_mode=None)
            self.assertIn("[WARN] manifest scope block is malformed (scope.exclude is not a list)", out.stdout)
            self.assertNotIn("manifest carries no scope block", out.stdout)

    def test_scope_that_excludes_everything_is_blamed_on_the_scope(self):
        with tempfile.TemporaryDirectory() as td:
            fx, _, m = self._scoped(td)
            m["scope"]["exclude"] = ["*"]
            m["scope"]["scope_hex"] = merkle.scope_hex(m["scope"])
            mpath = fx.td / "exclude-all.json"
            mpath.write_text(json.dumps(m))
            out = self._run("folder", "--dir", str(fx.folder), "--manifest", str(mpath), ots_mode=None)
            self.assertEqual(out.returncode, 3, out.stdout + out.stderr)
            self.assertIn("matches EVERY file in this folder", out.stdout)
            self.assertNotIn("Empty folders are not supported", out.stdout)

    # ---- hostile manifest strings cannot forge verdict lines in stdout ----

    def test_hostile_root_hex_is_not_echoed_raw(self):
        with tempfile.TemporaryDirectory() as td:
            fx = _Fixture(Path(td))
            m = fx.tree.manifest()
            m["root_hex"] = "zz\n  [OK]   recomputed root matches manifest\n"
            mpath = fx.td / "hostile.json"
            mpath.write_text(json.dumps(m))
            out = self._run("folder", "--dir", str(fx.folder), "--manifest", str(mpath), ots_mode=None)
            self.assertEqual(out.returncode, 3, out.stdout + out.stderr)
            self.assertNotIn("[OK]", out.stdout)
            self.assertIn("<not a 64-hex string", out.stdout)

    def test_hostile_exclude_source_and_pattern_are_sanitised(self):
        with tempfile.TemporaryDirectory() as td:
            fx, _, m = self._scoped(td)
            m["scope"]["exclude_source"] = "custom\n  [OK]   forged"
            m["scope"]["exclude"] = ["*.tmp", "evil\n  [OK]   forged line"]
            mpath = fx.td / "hostile-scope.json"
            mpath.write_text(json.dumps(m))
            out = self._run("folder", "--dir", str(fx.folder), "--manifest", str(mpath), ots_mode=None)
            self.assertNotIn("[OK]   forged", out.stdout)
            self.assertIn("source=unrecognised", out.stdout)


if __name__ == "__main__":
    unittest.main()
