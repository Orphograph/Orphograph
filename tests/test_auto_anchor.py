"""Tests for scripts/auto_anchor_repo.py.

These tests never reach the network. All ``urllib`` calls are mocked.
"""
from __future__ import annotations

import io
import json
import sys
import tempfile
import urllib.error
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = ROOT / "scripts"
SERVER_DIR = ROOT / "server"
for p in (str(SCRIPTS_DIR), str(SERVER_DIR)):
    if p not in sys.path:
        sys.path.insert(0, p)

import auto_anchor_repo  # noqa: E402
import merkle  # noqa: E402


def _seed_repo(root: Path) -> None:
    """Create a minimal repo layout with a few in-scope files."""
    (root / "README.md").write_text("# Hello\n")
    (root / "server").mkdir()
    (root / "server" / "merkle.py").write_text("# stub\n")
    (root / "scripts").mkdir()
    (root / "scripts" / "x.py").write_text("# x\n")
    # Excluded path (should NOT appear in the manifest).
    (root / "data").mkdir()
    (root / "data" / "huge.bin").write_text("ignore me")


def test_manifest_roundtrips_through_from_manifest():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _seed_repo(root)
        manifest = auto_anchor_repo.build_manifest(root)
        # The manifest must reconstruct without error and the root must
        # match the value the server would recompute.
        tree = merkle.MerkleTree.from_manifest(manifest)
        assert tree.root_hex() == manifest["root_hex"]
        # Excluded path is absent.
        paths = [leaf["path"] for leaf in manifest["leaves"]]
        assert not any(p.startswith("data/") for p in paths)


def _fake_http_response(status: int, body: dict) -> mock.MagicMock:
    resp = mock.MagicMock()
    resp.getcode.return_value = status
    resp.read.return_value = json.dumps(body).encode("utf-8")
    resp.__enter__ = lambda s: s
    resp.__exit__ = lambda s, *a: None
    return resp


def test_post_shape_matches_anchor_folder_contract():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _seed_repo(root)
        manifest = auto_anchor_repo.build_manifest(root)

        captured: dict = {}

        def fake_urlopen(req, timeout=None):
            captured["url"] = req.full_url
            captured["method"] = req.get_method()
            captured["headers"] = {k.lower(): v for k, v in req.header_items()}
            captured["body"] = json.loads(req.data.decode("utf-8"))
            return _fake_http_response(200, {
                "receipt_id": "rid_abc",
                "root_hex": manifest["root_hex"],
                "calendars_ok": 5,
            })

        with mock.patch("auto_anchor_repo.urllib.request.urlopen", side_effect=fake_urlopen):
            status, payload = auto_anchor_repo.post_anchor(
                manifest, "deadbee auto-anchor", "https://example.invalid", ""
            )
        assert status == 200
        assert payload["receipt_id"] == "rid_abc"
        # The endpoint must be /api/anchor_folder.
        assert captured["url"].endswith("/api/anchor_folder")
        assert captured["method"] == "POST"
        # Body shape: wraps manifest + sets private=True + carries client_label.
        body = captured["body"]
        assert body["private"] is True
        assert body["client_label"] == "deadbee auto-anchor"
        assert body["manifest"]["algorithm"] == merkle.ALGORITHM
        assert body["manifest"]["root_hex"] == manifest["root_hex"]
        # Content-Type and UA are present.
        assert "content-type" in captured["headers"]


def test_post_shape_includes_api_key_header_when_provided():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _seed_repo(root)
        manifest = auto_anchor_repo.build_manifest(root)

        captured: dict = {}

        def fake_urlopen(req, timeout=None):
            captured["headers"] = {k.lower(): v for k, v in req.header_items()}
            return _fake_http_response(200, {
                "receipt_id": "rid",
                "root_hex": manifest["root_hex"],
                "calendars_ok": 5,
            })

        with mock.patch("auto_anchor_repo.urllib.request.urlopen", side_effect=fake_urlopen):
            auto_anchor_repo.post_anchor(
                manifest, "label", "https://example.invalid", "sk_test_xyz"
            )
        assert captured["headers"].get("x-orpho-api-key") == "sk_test_xyz"


# NOTE (2026-08-07): auto_anchor_repo now REFUSES before any network call when
# ORPHO_AUTO_ANCHOR_KEY is empty, because an anonymous caller cannot be granted
# the private anchor the script asks for — that silent downgrade published 51
# daily anchors over 78 days. The four transport-behaviour tests below assert
# on what happens AFTER the request goes out, so they patch API_KEY to get past
# that guard. The guard itself is covered in test_private_fails_closed.py.

def test_network_failure_exit_code_1():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _seed_repo(root)
        with mock.patch(
            "auto_anchor_repo.urllib.request.urlopen",
            side_effect=urllib.error.URLError("connection refused"),
        ):
            with mock.patch.object(auto_anchor_repo, "API_KEY", "sk_test_xyz"):
                rc = auto_anchor_repo.main(["--root", str(root), "--quiet"])
        assert rc == 1


def test_api_rejection_exit_code_2():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _seed_repo(root)
        err = urllib.error.HTTPError(
            "https://example.invalid/api/anchor_folder",
            429,
            "Too Many Requests",
            {},
            io.BytesIO(b'{"error":"rate limit"}'),
        )
        with mock.patch(
            "auto_anchor_repo.urllib.request.urlopen", side_effect=err
        ):
            with mock.patch.object(auto_anchor_repo, "API_KEY", "sk_test_xyz"):
                rc = auto_anchor_repo.main(["--root", str(root), "--quiet"])
        assert rc == 2


def test_5xx_exit_code_2():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _seed_repo(root)
        err = urllib.error.HTTPError(
            "https://example.invalid/api/anchor_folder",
            502,
            "Bad Gateway",
            {},
            io.BytesIO(b'{"error":"upstream"}'),
        )
        with mock.patch(
            "auto_anchor_repo.urllib.request.urlopen", side_effect=err
        ):
            with mock.patch.object(auto_anchor_repo, "API_KEY", "sk_test_xyz"):
                rc = auto_anchor_repo.main(["--root", str(root), "--quiet"])
        assert rc == 2


def test_timeout_exit_code_1():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _seed_repo(root)
        with mock.patch(
            "auto_anchor_repo.urllib.request.urlopen",
            side_effect=TimeoutError("timed out"),
        ):
            with mock.patch.object(auto_anchor_repo, "API_KEY", "sk_test_xyz"):
                rc = auto_anchor_repo.main(["--root", str(root), "--quiet"])
        assert rc == 1
