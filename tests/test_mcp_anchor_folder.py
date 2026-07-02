#!/usr/bin/env python3
"""Tests for the MCP orphograph_anchor_folder tool — folder (dataset) anchoring.

The tool replicates server/merkle.py locally so the manifest it builds is
accepted by /api/anchor_folder. These tests lock that cross-compatibility
(the MCP root equals what the canonical server implementation computes) and
the tool wiring — no network call.
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "mcp"))
sys.path.insert(0, str(ROOT / "server"))

import orphograph_mcp as mcp  # noqa: E402
import merkle  # noqa: E402


def _make_bundle() -> Path:
    d = Path(tempfile.mkdtemp())
    (d / "data").mkdir()
    (d / "data" / "a.txt").write_text("alpha")
    (d / "data" / "b.txt").write_text("beta")
    (d / "licenses").mkdir()
    (d / "licenses" / "LICENSE.txt").write_text("MIT")
    (d / "acquisition_log.json").write_text('{"sources": []}')
    return d


def test_manifest_is_server_compatible():
    """The MCP manifest must reconstruct to the same root under the canonical
    server implementation — otherwise /api/anchor_folder rejects it."""
    bundle = _make_bundle()
    manifest, total = mcp._build_folder_manifest(str(bundle))
    assert manifest["algorithm"] == "orphograph-merkle-v1-rfc6962"
    assert len(manifest["leaves"]) == 4
    assert total == len(b"alpha") + len(b"beta") + len(b"MIT") + len(b'{"sources": []}')
    # /api/anchor_folder does exactly this: rebuild from the leaves, verify the
    # recomputed root equals manifest.root_hex.
    tree = merkle.MerkleTree.from_manifest(manifest)
    assert tree.root_hex() == manifest["root_hex"]


def test_manifest_byte_identical_to_server_from_folder():
    """Same folder via the server must yield the same root AND leaf order —
    the MCP walk + leaf hashing are byte-identical to the office's."""
    bundle = _make_bundle()
    mcp_manifest, _ = mcp._build_folder_manifest(str(bundle))
    server_manifest = merkle.MerkleTree.from_folder(bundle).manifest()
    assert mcp_manifest["root_hex"] == server_manifest["root_hex"]
    assert [l["path"] for l in mcp_manifest["leaves"]] == \
           [l["path"] for l in server_manifest["leaves"]]
    assert [l["leaf_hex"] for l in mcp_manifest["leaves"]] == \
           [l["leaf_hex"] for l in server_manifest["leaves"]]


def test_manifest_matches_server_with_nested_excluded_dirs():
    """Regression for the exclusion divergence: slash patterns (node_modules/*,
    .git/*) exclude only at the TOP level, so nested copies must be KEPT and
    must appear in the tree — MCP and server must still agree on the root."""
    d = Path(tempfile.mkdtemp())
    (d / "data").mkdir()
    (d / "data" / "a.txt").write_text("alpha")
    (d / "data" / "node_modules").mkdir()
    (d / "data" / "node_modules" / "dep.js").write_text("vendored")  # nested -> kept
    (d / "sub").mkdir()
    (d / "sub" / ".git").mkdir()
    (d / "sub" / ".git" / "config").write_text("cfg")                # nested -> kept
    (d / ".git").mkdir()
    (d / ".git" / "HEAD").write_text("ref")                          # top-level -> dropped
    (d / ".DS_Store").write_text("junk")                             # basename -> dropped
    mcp_manifest, _ = mcp._build_folder_manifest(str(d))
    server_manifest = merkle.MerkleTree.from_folder(d).manifest()
    assert mcp_manifest["root_hex"] == server_manifest["root_hex"]
    assert sorted(l["path"] for l in mcp_manifest["leaves"]) == [
        "data/a.txt", "data/node_modules/dep.js", "sub/.git/config",
    ]


def test_empty_folder_rejected():
    empty = Path(tempfile.mkdtemp())
    with pytest.raises(ValueError):
        mcp._build_folder_manifest(str(empty))


def test_tool_registered_with_required_path():
    tdef = next(t for t in mcp.TOOL_DEFINITIONS if t["name"] == "orphograph_anchor_folder")
    assert tdef["inputSchema"]["required"] == ["path"]
    # tools/list surfaces it.
    resp = mcp.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}})
    names = [t["name"] for t in resp["result"]["tools"]]
    assert "orphograph_anchor_folder" in names


def test_tool_arg_validation():
    assert "error" in mcp.tool_anchor_folder({})
    assert "error" in mcp.tool_anchor_folder({"path": "/no/such/dir/xyz123"})
