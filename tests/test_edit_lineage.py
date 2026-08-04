#!/usr/bin/env python3
"""test_edit_lineage.py — pins for the edit-lineage layer (DESIGN_EDIT_LINEAGE.md).

Covers, fully offline (engine._submit stubbed; no calendar contact):

  * engine: parent_root/parent_receipt_id kwargs on anchor_hash (hints,
    recorded_only), derive_lineage_from_manifest / attach_lineage (the
    committed mirror, recomputed from the reserved leaf — hints never
    trusted), verify_receipt passthrough, shape stability.
  * dist/orphograph-verify/verify_lineage.py: happy chain of 3 drafts,
    tampered intermediate (content + manifest), hint-vs-commitment
    mismatch, forged/reordered parent pointers, missing intermediate,
    fork reported-not-failed, reserved-path abuse (size_bytes != 0),
    verbatim root compare, --dir content recompute with the synthetic
    parent leaf, tampered .ots binding.
  * MCP: orphograph_verify_lineage result shape per design §5, hint
    mismatch failure, broken chain, depth cap, tool registration.

ORPHO_DATA_DIR is pointed at a temp dir BEFORE importing server engine
(module paths resolve at import time), mirroring test_folder_anchor.py's
evict/restore pattern so later test files are not poisoned.
"""
from __future__ import annotations

import copy
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SERVER_DIR = ROOT / "server"
DIST_DIR = ROOT / "dist" / "orphograph-verify"
VERIFY_LINEAGE = DIST_DIR / "verify_lineage.py"
MCP_PATH = ROOT / "mcp" / "orphograph_mcp.py"

_POLLUTED = ("engine", "merkle")
_ENV_KEYS = ("ORPHO_DATA_DIR", "ORPHO_RECEIPTS_DIR", "ORPHO_LEDGER")

_TMP: tempfile.TemporaryDirectory | None = None
_OLD_MODULES: dict = {}
_OLD_ENV: dict = {}
_ORIG_SUBMIT = None
ENGINE = None
MERKLE = None

# Populated once by _ensure_chain(): a 3-link chain D1 → D2 → D3.
_CHAIN: dict | None = None


def setUpModule() -> None:
    global _TMP, _ORIG_SUBMIT, ENGINE, MERKLE
    _TMP = tempfile.TemporaryDirectory(prefix="orpho_lineage_")
    for k in _ENV_KEYS:
        _OLD_ENV[k] = os.environ.get(k)
    os.environ["ORPHO_DATA_DIR"] = _TMP.name
    os.environ.pop("ORPHO_RECEIPTS_DIR", None)
    os.environ.pop("ORPHO_LEDGER", None)
    for m in _POLLUTED:
        if m in sys.modules:
            _OLD_MODULES[m] = sys.modules.pop(m)
    if str(SERVER_DIR) not in sys.path:
        sys.path.insert(0, str(SERVER_DIR))
    import engine as engine_mod  # noqa: PLC0415 — must import AFTER env is set
    import merkle as merkle_mod  # noqa: PLC0415
    ENGINE = engine_mod
    MERKLE = merkle_mod
    _ORIG_SUBMIT = engine_mod._submit
    engine_mod._submit = lambda cal, h: (False, "stubbed: lineage test mode")


def tearDownModule() -> None:
    if ENGINE is not None and _ORIG_SUBMIT is not None:
        ENGINE._submit = _ORIG_SUBMIT
    for m in _POLLUTED:
        sys.modules.pop(m, None)
    for m, mod in _OLD_MODULES.items():
        sys.modules[m] = mod
    for k, v in _OLD_ENV.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    if _TMP is not None:
        _TMP.cleanup()


# ── fixture helpers ────────────────────────────────────────────────────


def _reserved_leaf(parent_root: str) -> dict:
    import hashlib
    leaf_hex = hashlib.sha256(
        b"\x00" + ENGINE.RESERVED_PARENT_PATH.encode("utf-8") + b"\x00" + bytes.fromhex(parent_root)
    ).hexdigest()
    return {
        "path": ENGINE.RESERVED_PARENT_PATH,
        "file_sha256_hex": parent_root,
        "leaf_hex": leaf_hex,
        "size_bytes": 0,
    }


def _manifest_from_leaves(leaves: list[dict], parent: dict | None = None) -> dict:
    leaves = sorted(leaves, key=lambda leaf: leaf["path"].encode("utf-8"))
    levels = MERKLE._build_levels([bytes.fromhex(leaf["leaf_hex"]) for leaf in leaves])
    manifest = {
        "algorithm": MERKLE.ALGORITHM,
        "version": MERKLE.VERSION,
        "root_hex": levels[-1][0].hex(),
        "leaves": leaves,
    }
    if parent is not None:
        manifest["parent"] = parent
    return manifest


def _lineage_manifest(folder: Path, parent_rid: str, parent_root: str) -> dict:
    leaves = MERKLE.MerkleTree.from_folder(folder).manifest()["leaves"]
    leaves.append(_reserved_leaf(parent_root))
    return _manifest_from_leaves(
        leaves, parent={"receipt_id": parent_rid, "root_hex": parent_root}
    )


def _anchor_link(manifest: dict) -> str:
    """Anchor a manifest root and persist the bundle the way the folder path
    does (manifest.json + kind/leaf_count rewrite + lineage mirror), plus
    fabricated .ots files (the stubbed calendars write none)."""
    record = ENGINE.anchor_hash(manifest["root_hex"])
    rid = record["receipt_id"]
    rdir = ENGINE.RECEIPTS_DIR / rid
    stored = dict(manifest)
    stored["receipt_id"] = rid
    stored["kind"] = "folder"
    (rdir / "manifest.json").write_text(json.dumps(stored, indent=2))
    on_disk = json.loads((rdir / "receipt.json").read_text())
    on_disk["kind"] = "folder"
    on_disk["leaf_count"] = len(manifest["leaves"])
    on_disk["merkle_algorithm"] = MERKLE.ALGORITHM
    (rdir / "receipt.json").write_text(json.dumps(on_disk, indent=2))
    ENGINE.attach_lineage(rid, stored)
    ots = ENGINE._build_ots(bytes.fromhex(manifest["root_hex"]), b"\xf0test-calendar-body")
    (rdir / "alice.ots").write_bytes(ots)
    (rdir / "finney.ots").write_bytes(ots)
    return rid


def _ensure_chain() -> dict:
    """Build (once) the canonical 3-draft chain: D1 genesis, D2 → D1, D3 → D2."""
    global _CHAIN
    if _CHAIN is not None:
        return _CHAIN
    base = Path(_TMP.name) / "drafts"
    base.mkdir(exist_ok=True)
    folders = {}
    for n, body in (("d1", "draft v1\n"), ("d2", "draft v2 — revised\n"), ("d3", "draft v3 final\n")):
        f = base / n
        f.mkdir()
        (f / "draft.md").write_text(body)
        (f / "notes.txt").write_text(f"notes for {n}\n")
        folders[n] = f

    m1 = MERKLE.MerkleTree.from_folder(folders["d1"]).manifest()
    rid1 = _anchor_link(m1)
    root1 = m1["root_hex"]

    m2 = _lineage_manifest(folders["d2"], rid1, root1)
    rid2 = _anchor_link(m2)
    root2 = m2["root_hex"]

    m3 = _lineage_manifest(folders["d3"], rid2, root2)
    rid3 = _anchor_link(m3)
    root3 = m3["root_hex"]

    _CHAIN = {
        "rids": [rid1, rid2, rid3],
        "roots": [root1, root2, root3],
        "folders": folders,
        "manifests": [m1, m2, m3],
    }
    return _CHAIN


def _copy_chain(dest: Path, rids: list[str]) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    for rid in rids:
        shutil.copytree(ENGINE.RECEIPTS_DIR / rid, dest / rid)


def _run_verifier(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(VERIFY_LINEAGE), *args],
        check=False,
        capture_output=True,
        text=True,
        cwd=str(DIST_DIR.parent),  # neutral cwd: script must self-locate merkle.py
        timeout=120,
    )


# ── engine layer ───────────────────────────────────────────────────────


class TestEngineLineage(unittest.TestCase):

    def test_anchor_hash_no_parent_shape_stable(self):
        record = ENGINE.anchor_hash("ab" * 32)
        self.assertNotIn("lineage", record)
        on_disk = json.loads(
            (ENGINE.RECEIPTS_DIR / record["receipt_id"] / "receipt.json").read_text()
        )
        self.assertNotIn("lineage", on_disk)
        self.assertNotIn("lineage", ENGINE.verify_receipt(record["receipt_id"]))

    def test_anchor_hash_parent_kwargs_recorded_only(self):
        record = ENGINE.anchor_hash(
            "cd" * 32, parent_root="ef" * 32, parent_receipt_id="XwTULwlh76PcCst9"
        )
        self.assertEqual(record["lineage"], {
            "parent_receipt_id": "XwTULwlh76PcCst9",
            "parent_root": "ef" * 32,
            "committed": False,   # bare-hash path can never commit the link
        })
        out = ENGINE.verify_receipt(record["receipt_id"])
        self.assertEqual(out["lineage"]["committed"], False)

    def test_anchor_hash_parent_root_bad_hex_rejected(self):
        with self.assertRaises(ValueError):
            ENGINE.anchor_hash("ab" * 32, parent_root="EF" * 32)  # uppercase
        with self.assertRaises(ValueError):
            ENGINE.anchor_hash("ab" * 32, parent_root="zz" * 32)
        with self.assertRaises(ValueError):
            ENGINE.anchor_hash("ab" * 32, parent_root="abcd")
        with self.assertRaises(ValueError):
            ENGINE.anchor_hash("ab" * 32, parent_receipt_id="not/a/valid id!")

    def _happy_manifest(self) -> dict:
        chain = _ensure_chain()
        return copy.deepcopy(chain["manifests"][1])  # D2: reserved leaf + parent block

    def test_derive_lineage_happy(self):
        chain = _ensure_chain()
        lineage = ENGINE.derive_lineage_from_manifest(self._happy_manifest())
        self.assertEqual(lineage, {
            "parent_receipt_id": chain["rids"][0],
            "parent_root": chain["roots"][0],
            "committed": True,
        })

    def test_derive_lineage_none_for_plain_manifest(self):
        chain = _ensure_chain()
        self.assertIsNone(
            ENGINE.derive_lineage_from_manifest(copy.deepcopy(chain["manifests"][0]))
        )

    def test_derive_reserved_leaf_without_parent_block_rejected(self):
        m = self._happy_manifest()
        del m["parent"]
        with self.assertRaises(ValueError):
            ENGINE.derive_lineage_from_manifest(m)

    def test_derive_parent_block_without_reserved_leaf_rejected(self):
        chain = _ensure_chain()
        m = copy.deepcopy(chain["manifests"][0])
        m["parent"] = {"receipt_id": chain["rids"][0], "root_hex": chain["roots"][0]}
        with self.assertRaises(ValueError):
            ENGINE.derive_lineage_from_manifest(m)

    def test_derive_parent_block_root_mismatch_rejected(self):
        m = self._happy_manifest()
        m["parent"]["root_hex"] = "00" * 32
        with self.assertRaises(ValueError):
            ENGINE.derive_lineage_from_manifest(m)

    def test_derive_reserved_path_nonzero_size_rejected(self):
        # Q2 working assumption: a real file may not shadow the reserved path.
        m = self._happy_manifest()
        for leaf in m["leaves"]:
            if leaf["path"] == ENGINE.RESERVED_PARENT_PATH:
                leaf["size_bytes"] = 7
        with self.assertRaises(ValueError):
            ENGINE.derive_lineage_from_manifest(m)

    def test_derive_forged_reserved_leaf_hex_rejected(self):
        m = self._happy_manifest()
        for leaf in m["leaves"]:
            if leaf["path"] == ENGINE.RESERVED_PARENT_PATH:
                leaf["leaf_hex"] = "11" * 32
        with self.assertRaises(ValueError):
            ENGINE.derive_lineage_from_manifest(m)

    def test_derive_uppercase_parent_root_rejected(self):
        # Canonical-form discipline: never "helpfully" lowercase the stored side.
        m = self._happy_manifest()
        for leaf in m["leaves"]:
            if leaf["path"] == ENGINE.RESERVED_PARENT_PATH:
                leaf["file_sha256_hex"] = leaf["file_sha256_hex"].upper()
        with self.assertRaises(ValueError):
            ENGINE.derive_lineage_from_manifest(m)

    def test_derive_tree_inconsistency_rejected(self):
        # Reserved leaf itself derives, but the manifest root does not fold
        # from its leaves — verify_tree must refuse (committed is recomputed,
        # never assumed).
        m = self._happy_manifest()
        m["root_hex"] = "22" * 32
        with self.assertRaises(ValueError):
            ENGINE.derive_lineage_from_manifest(m)

    def test_attach_lineage_mirror_and_passthrough(self):
        chain = _ensure_chain()
        rid2, rid3 = chain["rids"][1], chain["rids"][2]
        for rid, parent_idx in ((rid2, 0), (rid3, 1)):
            on_disk = json.loads((ENGINE.RECEIPTS_DIR / rid / "receipt.json").read_text())
            self.assertEqual(on_disk["lineage"], {
                "parent_receipt_id": chain["rids"][parent_idx],
                "parent_root": chain["roots"][parent_idx],
                "committed": True,
            })
            out = ENGINE.verify_receipt(rid)
            self.assertEqual(out["lineage"], on_disk["lineage"])
        # Genesis receipt stays shape-stable.
        self.assertNotIn(
            "lineage",
            json.loads((ENGINE.RECEIPTS_DIR / chain["rids"][0] / "receipt.json").read_text()),
        )

    def test_attach_lineage_unknown_parent_still_attaches(self):
        chain = _ensure_chain()
        folder = Path(_TMP.name) / "drafts" / "orphan"
        folder.mkdir(exist_ok=True)
        (folder / "draft.md").write_text("orphan draft\n")
        manifest = _lineage_manifest(folder, "NoSuchReceipt0000", "ab" * 32)
        record = ENGINE.anchor_hash(manifest["root_hex"])
        rid = record["receipt_id"]
        (ENGINE.RECEIPTS_DIR / rid / "manifest.json").write_text(json.dumps(manifest))
        result = ENGINE.attach_lineage(rid, manifest)
        self.assertTrue(result["committed"])
        self.assertFalse(result["parent_receipt_found"])
        _ = chain  # chain only ensures the module fixture exists

    def test_attach_lineage_local_parent_root_conflict_rejected(self):
        chain = _ensure_chain()
        rid1 = chain["rids"][0]
        folder = Path(_TMP.name) / "drafts" / "conflict"
        folder.mkdir(exist_ok=True)
        (folder / "draft.md").write_text("conflict draft\n")
        # Claims rid1 as parent but commits a DIFFERENT root than rid1 anchored.
        manifest = _lineage_manifest(folder, rid1, "33" * 32)
        record = ENGINE.anchor_hash(manifest["root_hex"])
        with self.assertRaises(ValueError):
            ENGINE.attach_lineage(record["receipt_id"], manifest)

    def test_attach_lineage_wrong_receipt_binding_rejected(self):
        chain = _ensure_chain()
        record = ENGINE.anchor_hash("44" * 32)
        with self.assertRaises(ValueError):
            # Manifest root != this receipt's anchored hash.
            ENGINE.attach_lineage(record["receipt_id"], copy.deepcopy(chain["manifests"][1]))

    def test_reserved_leaf_sort_position(self):
        # The reserved leaf sorts by UTF-8 byte order among sibling paths and
        # rides from_manifest UNCHANGED (no merkle.py delta, same tag).
        import hashlib
        digest = hashlib.sha256(b"x").hexdigest()
        leaves = []
        for path in (".a", "draft.md", "zzz"):
            leaf_hex = hashlib.sha256(
                b"\x00" + path.encode() + b"\x00" + bytes.fromhex(digest)
            ).hexdigest()
            leaves.append({"path": path, "file_sha256_hex": digest,
                           "leaf_hex": leaf_hex, "size_bytes": 1})
        leaves.append(_reserved_leaf("ab" * 32))
        manifest = _manifest_from_leaves(
            leaves, parent={"receipt_id": "SomeParent123", "root_hex": "ab" * 32}
        )
        ordered = [leaf["path"] for leaf in manifest["leaves"]]
        self.assertEqual(ordered, [".a", ".orphograph/parent", "draft.md", "zzz"])
        tree = MERKLE.MerkleTree.from_manifest(manifest)  # unchanged validation
        self.assertEqual(tree.root_hex(), manifest["root_hex"])
        lineage = ENGINE.derive_lineage_from_manifest(manifest)
        self.assertEqual(lineage["parent_root"], "ab" * 32)


# ── offline verifier ───────────────────────────────────────────────────


class TestOfflineLineageVerifier(unittest.TestCase):

    def setUp(self):
        self.chain = _ensure_chain()
        self.work = Path(tempfile.mkdtemp(prefix="lineage_cli_", dir=_TMP.name))

    def _fresh_chain_dir(self, rids=None) -> Path:
        dest = self.work / "chain"
        _copy_chain(dest, rids if rids is not None else self.chain["rids"])
        return dest

    def test_chain_of_three_ok(self):
        out = self._run_ok(self._fresh_chain_dir())
        rid1, rid2, rid3 = self.chain["rids"]
        self.assertIn("genesis", out.stdout)
        pos = [out.stdout.rindex(r) for r in (rid1, rid2, rid3)]
        self.assertEqual(pos, sorted(pos), "summary must run oldest → newest")
        self.assertIn("does NOT establish", out.stdout)
        self.assertIn("anchor-time ordering", out.stdout)

    def _run_ok(self, chain_dir: Path, *extra: str) -> subprocess.CompletedProcess:
        out = _run_verifier("--chain", str(chain_dir), f"--tip={self.chain['rids'][2]}", *extra)
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        return out

    def test_tip_autodetected(self):
        out = _run_verifier("--chain", str(self._fresh_chain_dir()))
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertIn(self.chain["rids"][2], out.stdout)

    def test_content_recheck_with_synthetic_leaf(self):
        rids = self.chain["rids"]
        folders = self.chain["folders"]
        out = self._run_ok(
            self._fresh_chain_dir(),
            f"--dir={rids[0]}={folders['d1']}",
            f"--dir={rids[1]}={folders['d2']}",
            f"--dir={rids[2]}={folders['d3']}",
        )
        self.assertIn("synthetic parent leaf", out.stdout)

    def test_tampered_intermediate_content_detected(self):
        tampered = self.work / "d2_tampered"
        shutil.copytree(self.chain["folders"]["d2"], tampered)
        (tampered / "draft.md").write_text("draft v2 — silently rewritten after anchoring\n")
        out = _run_verifier(
            "--chain", str(self._fresh_chain_dir()),
            f"--tip={self.chain['rids'][2]}",
            f"--dir={self.chain['rids'][1]}={tampered}",
        )
        self.assertEqual(out.returncode, 3, out.stdout + out.stderr)
        self.assertIn("draft bytes differ", out.stdout)

    def test_tampered_manifest_leaf_detected(self):
        chain_dir = self._fresh_chain_dir()
        rid2 = self.chain["rids"][1]
        mpath = chain_dir / rid2 / "manifest.json"
        manifest = json.loads(mpath.read_text())
        for leaf in manifest["leaves"]:
            if leaf["path"] == "draft.md":
                leaf["file_sha256_hex"] = "55" * 32
        mpath.write_text(json.dumps(manifest))
        out = _run_verifier("--chain", str(chain_dir), f"--tip={self.chain['rids'][2]}")
        self.assertEqual(out.returncode, 3, out.stdout + out.stderr)

    def test_hint_vs_commitment_mismatch_detected(self):
        chain_dir = self._fresh_chain_dir()
        rid3 = self.chain["rids"][2]
        rpath = chain_dir / rid3 / "receipt.json"
        receipt = json.loads(rpath.read_text())
        receipt["lineage"]["parent_root"] = self.chain["roots"][0]  # points at root1
        rpath.write_text(json.dumps(receipt))
        out = _run_verifier("--chain", str(chain_dir), f"--tip={rid3}")
        self.assertEqual(out.returncode, 3, out.stdout + out.stderr)
        self.assertIn("committed leaf wins", out.stdout)

    def test_reordered_parent_pointer_hints_rejected(self):
        # Case 4.2: rewriting parent-id hints cannot reverse the order —
        # the committed leaf resolves to rid2 and the edited hint loses.
        chain_dir = self._fresh_chain_dir()
        rid3 = self.chain["rids"][2]
        rpath = chain_dir / rid3 / "receipt.json"
        receipt = json.loads(rpath.read_text())
        receipt["lineage"]["parent_receipt_id"] = self.chain["rids"][0]
        rpath.write_text(json.dumps(receipt))
        out = _run_verifier("--chain", str(chain_dir), f"--tip={rid3}")
        self.assertEqual(out.returncode, 3, out.stdout + out.stderr)
        self.assertIn("disagrees", out.stdout)

    def test_missing_intermediate_breaks_chain(self):
        chain_dir = self._fresh_chain_dir(
            [self.chain["rids"][0], self.chain["rids"][2]]  # R2 withheld
        )
        out = _run_verifier("--chain", str(chain_dir), f"--tip={self.chain['rids'][2]}")
        self.assertEqual(out.returncode, 5, out.stdout + out.stderr)
        self.assertIn("BROKEN", out.stdout)
        self.assertIn(self.chain["roots"][1][:16], out.stdout)

    def test_fork_reported_not_failed(self):
        # Second child of root1 (a legitimate parallel branch).
        fork_folder = Path(_TMP.name) / "drafts" / "d2b"
        if not fork_folder.exists():
            fork_folder.mkdir()
            (fork_folder / "draft.md").write_text("draft v2 ALTERNATE branch\n")
        m2b = _lineage_manifest(fork_folder, self.chain["rids"][0], self.chain["roots"][0])
        rid2b = _anchor_link(m2b)
        chain_dir = self._fresh_chain_dir(self.chain["rids"] + [rid2b])
        out = _run_verifier("--chain", str(chain_dir), f"--tip={self.chain['rids'][2]}")
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertIn("FORK", out.stdout)
        self.assertIn(rid2b, out.stdout)

    def test_reserved_path_abuse_nonzero_size_detected(self):
        # size_bytes is NOT hash-committed, so from_manifest alone would pass;
        # the Q2 rule must catch it independently.
        chain_dir = self._fresh_chain_dir()
        rid2 = self.chain["rids"][1]
        mpath = chain_dir / rid2 / "manifest.json"
        manifest = json.loads(mpath.read_text())
        for leaf in manifest["leaves"]:
            if leaf["path"] == ".orphograph/parent":
                leaf["size_bytes"] = 7
        mpath.write_text(json.dumps(manifest))
        out = _run_verifier("--chain", str(chain_dir), f"--tip={self.chain['rids'][2]}")
        self.assertEqual(out.returncode, 3, out.stdout + out.stderr)
        self.assertIn("size_bytes", out.stdout)

    def test_verbatim_root_compare(self):
        chain_dir = self._fresh_chain_dir()
        rid2 = self.chain["rids"][1]
        mpath = chain_dir / rid2 / "manifest.json"
        manifest = json.loads(mpath.read_text())
        manifest["root_hex"] = manifest["root_hex"].upper()
        mpath.write_text(json.dumps(manifest))
        out = _run_verifier("--chain", str(chain_dir), f"--tip={self.chain['rids'][2]}")
        self.assertEqual(out.returncode, 3, out.stdout + out.stderr)

    def test_tampered_ots_binding_detected(self):
        chain_dir = self._fresh_chain_dir()
        rid2 = self.chain["rids"][1]
        opath = chain_dir / rid2 / "alice.ots"
        data = bytearray(opath.read_bytes())
        offset = len(b"\x00OpenTimestamps\x00\x00Proof\x00\xbf\x89\xe2\xe8\x84\xe8\x92\x94") + 2
        data[offset] ^= 0xFF  # flip a byte of the embedded hash
        opath.write_bytes(bytes(data))
        out = _run_verifier("--chain", str(chain_dir), f"--tip={self.chain['rids'][2]}")
        self.assertEqual(out.returncode, 3, out.stdout + out.stderr)
        self.assertIn(".ots binding failed", out.stdout)

    def test_bad_arguments(self):
        out = _run_verifier("--chain", str(self.work / "nonexistent"))
        self.assertEqual(out.returncode, 2)
        out = _run_verifier("--chain", str(self._fresh_chain_dir()), "--tip=NoSuchRid")
        self.assertEqual(out.returncode, 2)
        out = _run_verifier("--chain", str(self.work / "chain"), "--dir=malformed")
        self.assertEqual(out.returncode, 2)

    def test_leading_dash_receipt_id_not_misparsed_as_flag(self):
        # Regression: token_urlsafe ids can begin with '-'. Passed as
        # `--tip <id>` argparse read the id as an option flag and died with
        # "expected one argument" (exit 2 + usage) — which is how the deploy
        # workflow's test step failed on a 1-in-64 unlucky id. The supported
        # `--tip=<id>` form must reach the verifier's own not-found path,
        # never the argparse usage error.
        chain_dir = self._fresh_chain_dir()
        out = _run_verifier("--chain", str(chain_dir), "--tip=-DashLeadingRid")
        self.assertEqual(out.returncode, 2)
        self.assertNotIn("expected one argument", out.stderr)
        out = _run_verifier("--chain", str(chain_dir),
                            "--dir=-DashRid=/nonexistent")
        self.assertEqual(out.returncode, 2)
        self.assertNotIn("expected one argument", out.stderr)


# ── MCP tool ───────────────────────────────────────────────────────────


def _load_mcp():
    spec = importlib.util.spec_from_file_location("orphograph_mcp_lineage_test", MCP_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestMcpVerifyLineage(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.chain = _ensure_chain()
        cls.mcp = _load_mcp()
        cls.store = {}
        for rid in cls.chain["rids"]:
            receipt = ENGINE.verify_receipt(rid)
            manifest = json.loads((ENGINE.RECEIPTS_DIR / rid / "manifest.json").read_text())
            cls.store[f"/api/verify_folder/{rid}"] = {"receipt": receipt, "manifest": manifest}

    def _patch(self, store: dict):
        def fake_http(method: str, path: str, body: dict | None = None) -> dict:
            resp = store.get(path)
            if resp is None:
                return {"error": "http_error", "status": 404, "body": "not found"}
            return copy.deepcopy(resp)
        self._orig_http = self.mcp._http
        self.mcp._http = fake_http

    def tearDown(self):
        if getattr(self, "_orig_http", None) is not None:
            self.mcp._http = self._orig_http
            self._orig_http = None

    def test_shape_happy_chain(self):
        self._patch(self.store)
        rid1, rid2, rid3 = self.chain["rids"]
        result = self.mcp.tool_verify_lineage({"receipt_id": rid3})
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["tip"], rid3)
        self.assertEqual(result["depth"], 3)
        self.assertIsNone(result["broken_at"])
        self.assertEqual(result["forks_seen"], [])
        self.assertIn("does not establish", result["note"])
        links = [(entry["receipt_id"], entry["link"], entry["checks_ok"])
                 for entry in result["chain"]]
        self.assertEqual(links, [
            (rid1, "genesis", True),
            (rid2, "committed", True),
            (rid3, "committed", True),
        ])
        for entry in result["chain"]:
            for key in ("root_hex", "created_at", "btc_pinned_at", "status"):
                self.assertIn(key, entry)

    def test_hint_mismatch_fails(self):
        store = copy.deepcopy(self.store)
        rid3 = self.chain["rids"][2]
        store[f"/api/verify_folder/{rid3}"]["receipt"]["lineage"]["parent_root"] = "66" * 32
        self._patch(store)
        result = self.mcp.tool_verify_lineage({"receipt_id": rid3})
        self.assertFalse(result["ok"])
        self.assertEqual(result["broken_at"], rid3)
        self.assertTrue(any("disagrees" in p
                            for p in result["chain"][-1].get("problems", [])))

    def test_missing_parent_breaks(self):
        store = copy.deepcopy(self.store)
        rid2, rid3 = self.chain["rids"][1], self.chain["rids"][2]
        del store[f"/api/verify_folder/{rid2}"]
        self._patch(store)
        result = self.mcp.tool_verify_lineage({"receipt_id": rid3})
        self.assertFalse(result["ok"])
        self.assertEqual(result["broken_at"], rid2)
        self.assertEqual(result["depth"], 1)

    def test_forged_parent_hint_root_checked(self):
        # Hint id points at rid1, whose anchored root is NOT the committed
        # parent root — the walk must refuse, never trust the hint.
        store = copy.deepcopy(self.store)
        rid1, rid3 = self.chain["rids"][0], self.chain["rids"][2]
        resp = store[f"/api/verify_folder/{rid3}"]
        resp["receipt"]["lineage"]["parent_receipt_id"] = rid1
        # Keep manifest hint consistent so only the committed-root check trips.
        resp["manifest"]["parent"]["receipt_id"] = rid1
        self._patch(store)
        result = self.mcp.tool_verify_lineage({"receipt_id": rid3})
        self.assertFalse(result["ok"])
        self.assertTrue(any(
            "does not match the committed parent root" in p
            for entry in result["chain"] for p in entry.get("problems", [])
        ))

    def test_max_depth_capped(self):
        self._patch(self.store)
        result = self.mcp.tool_verify_lineage(
            {"receipt_id": self.chain["rids"][2], "max_depth": 1}
        )
        self.assertFalse(result["ok"])
        self.assertEqual(result["depth"], 1)
        self.assertTrue(result.get("depth_capped"))

    def test_tool_registered_and_dispatched(self):
        names = [t["name"] for t in self.mcp.TOOL_DEFINITIONS]
        self.assertIn("orphograph_verify_lineage", names)
        tool = next(t for t in self.mcp.TOOL_DEFINITIONS
                    if t["name"] == "orphograph_verify_lineage")
        self.assertEqual(tool["inputSchema"]["properties"]["max_depth"]["default"], 32)
        self.assertEqual(tool["inputSchema"]["required"], ["receipt_id"])
        self.assertNotIn("court", tool["description"].lower())
        self._patch(self.store)
        reply = self.mcp.handle({
            "jsonrpc": "2.0", "id": 7, "method": "tools/call",
            "params": {"name": "orphograph_verify_lineage",
                       "arguments": {"receipt_id": self.chain["rids"][2]}},
        })
        payload = json.loads(reply["result"]["content"][0]["text"])
        self.assertTrue(payload["ok"])

    def test_bad_receipt_id_rejected(self):
        result = self.mcp.tool_verify_lineage({"receipt_id": "../etc/passwd"})
        self.assertIn("error", result)


if __name__ == "__main__":
    unittest.main()
