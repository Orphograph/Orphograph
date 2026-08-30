"""The offline path, driven through the REAL command-line entry point.

Every check here calls ``orphograph._cli.main`` with the network cut at the
socket layer. The library-level ``verify_inclusion`` was proven offline on
2026-08-28 against the published 0.1.0 wheel; what had never existed was a
CLI spelling of it, so a relying party holding only ``proof.json`` and the
file had to write Python to check it. The Node CLI shipped
``verify-inclusion`` from day one; this test holds the Python CLI to the
same shape.

The network block is a NEGATIVE CONTROL first: if ``getaddrinfo`` does not
raise under the patch, the offline claim was never tested and the suite
must say so rather than pass.
"""
from __future__ import annotations

import io
import json
import os
import socket
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import orphograph  # noqa: E402
from orphograph import _cli, _merkle  # noqa: E402

SDK_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = SDK_ROOT.parent


class _NetworkCut(Exception):
    pass


def _raise_cut(*_a, **_k):
    raise _NetworkCut("network is cut for this test")


def _run(argv):
    out, err = io.StringIO(), io.StringIO()
    with mock.patch.object(socket, "getaddrinfo", _raise_cut), \
            mock.patch.object(socket, "create_connection", _raise_cut), \
            redirect_stdout(out), redirect_stderr(err):
        rc = _cli.main(argv)
    return rc, out.getvalue(), err.getvalue()


def _folder_with_proof(td: Path):
    (td / "sub").mkdir()
    (td / "a.txt").write_bytes(b"alpha")
    (td / "sub" / "c.txt").write_bytes(b"gamma")
    tree = _merkle.MerkleTree.from_folder(td)
    manifest = tree.manifest()
    proof = tree.inclusion_proof("sub/c.txt")
    # Exactly the JSON the `inclusion-proof` subcommand writes to stdout.
    payload = {
        "receipt_id": "rid-test",
        "root_hex": manifest["root_hex"],
        "path": "sub/c.txt",
        "proof": [list(step) for step in proof],
    }
    proof_path = td / "proof.json"
    proof_path.write_text(json.dumps(payload))
    return proof_path, manifest["root_hex"]


class TestNetworkCutBites(unittest.TestCase):
    def test_negative_control_getaddrinfo_raises_under_patch(self):
        with mock.patch.object(socket, "getaddrinfo", _raise_cut):
            with self.assertRaises(_NetworkCut):
                socket.getaddrinfo("orphograph.com", 443)


class TestVerifyInclusionCli(unittest.TestCase):
    def test_rel_path_disagreeing_with_proof_json_path_warns_on_stderr(self):
        with tempfile.TemporaryDirectory() as d:
            td = Path(d)
            proof_path, _root = _folder_with_proof(td)
            rc, out, err = _run([
                "verify-inclusion", str(td / "sub" / "c.txt"), "other/c.txt", str(proof_path),
            ])
            self.assertEqual(rc, 1)
            self.assertFalse(json.loads(out)["ok"])
            warning = json.loads(err)
            self.assertIn("rel_path", warning["warning"])
            self.assertEqual(warning["proof_json_path"], "sub/c.txt")
            self.assertEqual(warning["rel_path"], "other/c.txt")

    def test_agreeing_rel_path_prints_no_warning(self):
        with tempfile.TemporaryDirectory() as d:
            td = Path(d)
            proof_path, _root = _folder_with_proof(td)
            rc, _out, err = _run([
                "verify-inclusion", str(td / "sub" / "c.txt"), "sub/c.txt", str(proof_path),
            ])
            self.assertEqual(rc, 0)
            self.assertEqual(err, "")

    def test_match_exits_zero_with_ok_true(self):
        with tempfile.TemporaryDirectory() as d:
            td = Path(d)
            proof_path, _root = _folder_with_proof(td)
            rc, out, err = _run([
                "verify-inclusion", str(td / "sub" / "c.txt"), "sub/c.txt", str(proof_path),
            ])
            self.assertEqual(rc, 0, err)
            verdict = json.loads(out)
            self.assertTrue(verdict["ok"])
            # The verdict names the root it was checked against and where
            # that root came from, so the relying party can match it to the
            # receipt. A root read from the bundle is self-attested.
            self.assertEqual(verdict["root_hex"], _root)
            self.assertEqual(verdict["root_source"], "proof_json")

    def test_explicit_root_positional_matches_node_cli_shape(self):
        with tempfile.TemporaryDirectory() as d:
            td = Path(d)
            proof_path, root = _folder_with_proof(td)
            rc, out, _ = _run([
                "verify-inclusion", str(td / "sub" / "c.txt"), "sub/c.txt",
                str(proof_path), root,
            ])
            self.assertEqual(rc, 0)
            verdict = json.loads(out)
            self.assertTrue(verdict["ok"])
            self.assertEqual(verdict["root_hex"], root)
            self.assertEqual(verdict["root_source"], "argument")

    def test_tampered_file_exits_one_with_ok_false(self):
        with tempfile.TemporaryDirectory() as d:
            td = Path(d)
            proof_path, _ = _folder_with_proof(td)
            (td / "sub" / "c.txt").write_bytes(b"gamma-tampered")
            rc, out, _ = _run([
                "verify-inclusion", str(td / "sub" / "c.txt"), "sub/c.txt", str(proof_path),
            ])
            self.assertEqual(rc, 1)
            self.assertFalse(json.loads(out)["ok"])

    def test_explicit_root_overrides_the_one_inside_proof_json(self):
        # A relying party handed the root out-of-band pins THAT root; a
        # proof.json carrying a different root must not win silently.
        with tempfile.TemporaryDirectory() as d:
            td = Path(d)
            proof_path, _ = _folder_with_proof(td)
            rc, out, _ = _run([
                "verify-inclusion", str(td / "sub" / "c.txt"), "sub/c.txt",
                str(proof_path), "00" * 32,
            ])
            self.assertEqual(rc, 1)
            verdict = json.loads(out)
            self.assertFalse(verdict["ok"])
            self.assertEqual(verdict["root_hex"], "00" * 32)
            self.assertEqual(verdict["root_source"], "argument")

    def test_bare_proof_array_is_accepted_like_node(self):
        with tempfile.TemporaryDirectory() as d:
            td = Path(d)
            proof_path, root = _folder_with_proof(td)
            bare = td / "bare.json"
            bare.write_text(json.dumps(json.loads(proof_path.read_text())["proof"]))
            rc, out, _ = _run([
                "verify-inclusion", str(td / "sub" / "c.txt"), "sub/c.txt", str(bare), root,
            ])
            self.assertEqual(rc, 0)
            self.assertTrue(json.loads(out)["ok"])

    def test_bare_proof_array_without_root_is_a_usage_error(self):
        with tempfile.TemporaryDirectory() as d:
            td = Path(d)
            proof_path, _ = _folder_with_proof(td)
            bare = td / "bare.json"
            bare.write_text(json.dumps(json.loads(proof_path.read_text())["proof"]))
            rc, out, err = _run([
                "verify-inclusion", str(td / "sub" / "c.txt"), "sub/c.txt", str(bare),
            ])
            self.assertEqual(rc, 2)
            self.assertIn("root_hex", json.loads(err)["error"])

    def test_missing_local_file_exits_two_not_false(self):
        # AUDIT D7: an I/O precondition failure is distinguishable from a
        # "not included" verdict.
        with tempfile.TemporaryDirectory() as d:
            td = Path(d)
            proof_path, _ = _folder_with_proof(td)
            rc, out, err = _run([
                "verify-inclusion", str(td / "nope.txt"), "nope.txt", str(proof_path),
            ])
            self.assertEqual(rc, 2)
            self.assertEqual(out, "")
            self.assertIn("error", json.loads(err))

    def test_unparseable_proof_file_exits_two(self):
        with tempfile.TemporaryDirectory() as d:
            td = Path(d)
            _folder_with_proof(td)
            bad = td / "bad.json"
            bad.write_text("{not json")
            rc, out, err = _run([
                "verify-inclusion", str(td / "a.txt"), "a.txt", str(bad),
            ])
            self.assertEqual(rc, 2)
            self.assertIn("error", json.loads(err))

    def test_offline_path_never_touches_the_transport(self):
        with tempfile.TemporaryDirectory() as d:
            td = Path(d)
            proof_path, _ = _folder_with_proof(td)
            with mock.patch.object(orphograph._client, "_request", side_effect=AssertionError("transport used")):
                rc, out, _ = _run([
                    "verify-inclusion", str(td / "sub" / "c.txt"), "sub/c.txt", str(proof_path),
                ])
            self.assertEqual(rc, 0)
            self.assertTrue(json.loads(out)["ok"])

    def test_a_crash_is_exit_two_never_the_mismatch_code(self):
        # Exit 1 means "not included". Python's default for an uncaught
        # exception is ALSO 1, so a crash inside json.load (here: nesting
        # deep enough for RecursionError) would have read as a verdict.
        with tempfile.TemporaryDirectory() as d:
            td = Path(d)
            _folder_with_proof(td)
            deep = td / "deep.json"
            deep.write_text("[" * 200_000 + "]" * 200_000)
            rc, out, err = _run([
                "verify-inclusion", str(td / "a.txt"), "a.txt", str(deep),
            ])
            self.assertEqual(rc, 2)
            self.assertEqual(out, "")
            self.assertIn("RecursionError", json.loads(err)["error"])


class TestCliParityWithNode(unittest.TestCase):
    """Both CLIs are documented as interchangeable. Hold the subcommand sets."""

    NODE_CLI = REPO_ROOT / "sdk-node" / "src" / "cli.ts"

    def test_node_cli_source_is_present(self):
        # Green-by-skip is not allowed: if the sibling tree moves, this fails.
        self.assertTrue(self.NODE_CLI.is_file(), self.NODE_CLI)

    def test_every_node_subcommand_has_a_python_spelling(self):
        node_src = self.NODE_CLI.read_text()
        py_sub = _cli._build_parser()._subparsers._group_actions[0].choices  # type: ignore[union-attr]
        # Node spells the proof fetch `proof`; Python spells it `inclusion-proof`.
        mapping = {"anchor": "anchor", "verify": "verify",
                   "proof": "inclusion-proof", "verify-inclusion": "verify-inclusion"}
        for node_name, py_name in mapping.items():
            self.assertIn(f'case "{node_name}":', node_src, node_name)
            self.assertIn(py_name, py_sub, py_name)


class TestVersionAndCopyDrift(unittest.TestCase):
    def test_dunder_version_matches_pyproject(self):
        text = (SDK_ROOT / "pyproject.toml").read_text()
        for line in text.splitlines():
            if line.startswith("version = "):
                declared = line.split("=", 1)[1].strip().strip('"')
                break
        else:
            self.fail("pyproject.toml has no version line")
        self.assertEqual(orphograph.__version__, declared)

    def test_published_summary_leads_with_independent_verification(self):
        # The first clause a relying party reads must be what they can do
        # WITHOUT the issuer, not what the issuer hosts.
        text = (SDK_ROOT / "pyproject.toml").read_text()
        desc = next(l for l in text.splitlines() if l.startswith("description = "))
        first_clause = desc.split("=", 1)[1].strip().strip('"').split(".")[0].lower()
        self.assertNotIn("hosted service", first_clause)
        self.assertIn("without", first_clause)


if __name__ == "__main__":
    unittest.main()
