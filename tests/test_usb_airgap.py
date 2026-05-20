"""test_usb_airgap.py — coverage for the two USB-facing air-gap scripts.

Every test simulates the USB and the source folder via tempfile.TemporaryDirectory.
Every HTTP call is mocked through unittest.mock so no real network is touched.
"""
from __future__ import annotations

import io
import json
import sys
import tempfile
from pathlib import Path
from unittest import mock

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = REPO_ROOT / "scripts"
SERVER_DIR = REPO_ROOT / "server"
for d in (SCRIPTS_DIR, SERVER_DIR):
    if str(d) not in sys.path:
        sys.path.insert(0, str(d))


import usb_air_gap_hash  # noqa: E402
import usb_offline_anchor_submit  # noqa: E402


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _make_source_folder(parent: Path) -> Path:
    folder = parent / "src"
    folder.mkdir()
    (folder / "a.txt").write_bytes(b"file a contents 1234567890")
    (folder / "b.txt").write_bytes(b"file b contents abcdefghij")
    sub = folder / "sub"
    sub.mkdir()
    (sub / "c.txt").write_bytes(b"file c nested contents zzzz")
    return folder


def _find_subdir(usb_root: Path, kind: str) -> Path:
    candidates = [
        p for p in usb_root.iterdir()
        if p.is_dir() and p.name.startswith(f"orphograph_{kind}_")
    ]
    assert len(candidates) == 1, f"expected one {kind} subdir, found {candidates}"
    return candidates[0]


def _synthetic_receipt(root_hex: str) -> dict:
    return {
        "receipt_id": "RID-TEST-0001",
        "root_hex": root_hex,
        "calendars_ok": 5,
        "calendars_total": 5,
        "private": True,
        "anchored_at_utc": "2026-05-20T00:00:00Z",
    }


def _mock_urlopen_returning(payload: dict, status: int = 200):
    """Return a callable that mocks urlopen and yields ``payload`` as JSON."""
    raw = json.dumps(payload).encode("utf-8")

    class _Resp:
        def __init__(self):
            self.status = status
            self._buf = io.BytesIO(raw)

        def read(self):
            return self._buf.read()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    captured = {}

    def _fake_urlopen(req, timeout=None):
        captured["request"] = req
        captured["timeout"] = timeout
        captured["body"] = req.data
        captured["headers"] = dict(req.header_items())
        captured["url"] = req.full_url
        return _Resp()

    return _fake_urlopen, captured


# --------------------------------------------------------------------------- #
# usb_air_gap_hash.py
# --------------------------------------------------------------------------- #


def test_hash_writes_manifest_only():
    with tempfile.TemporaryDirectory() as workdir:
        work = Path(workdir)
        folder = _make_source_folder(work)
        usb = work / "usb"
        usb.mkdir()

        rc = usb_air_gap_hash.main([
            "--usb", str(usb),
            "--folder", str(folder),
        ])
        assert rc == 0

        sub = _find_subdir(usb, "offline_manifest")
        manifest_path = sub / "manifest.json"
        assert manifest_path.is_file()

        manifest = json.loads(manifest_path.read_text())
        assert "merkle" in manifest
        assert manifest["private"] is True
        assert isinstance(manifest["merkle"]["root_hex"], str)
        assert len(manifest["merkle"]["leaves"]) == 3

        # Confirm NO source-file content is anywhere on the USB.
        forbidden_blobs = [
            b"file a contents 1234567890",
            b"file b contents abcdefghij",
            b"file c nested contents zzzz",
        ]
        for p in usb.rglob("*"):
            if p.is_file():
                data = p.read_bytes()
                for blob in forbidden_blobs:
                    assert blob not in data, f"source content leaked to {p}"


def test_hash_makes_no_network_call():
    with tempfile.TemporaryDirectory() as workdir:
        work = Path(workdir)
        folder = _make_source_folder(work)
        usb = work / "usb"
        usb.mkdir()

        # If anything in the hash script reaches urllib, this raises.
        import urllib.request as _ureq

        def _boom(*a, **kw):
            raise AssertionError("the offline hash script must not open the network")

        with mock.patch.object(_ureq, "urlopen", _boom):
            rc = usb_air_gap_hash.main([
                "--usb", str(usb),
                "--folder", str(folder),
            ])
        assert rc == 0


def test_hash_additive_preexisting_untouched():
    with tempfile.TemporaryDirectory() as workdir:
        work = Path(workdir)
        folder = _make_source_folder(work)
        usb = work / "usb"
        usb.mkdir()

        pre_file = usb / "pre_existing.bin"
        pre_bytes = b"existing customer data on the drive"
        pre_file.write_bytes(pre_bytes)
        pre_dir = usb / "their_folder"
        pre_dir.mkdir()
        (pre_dir / "their_file.txt").write_bytes(b"do not touch me")

        rc = usb_air_gap_hash.main([
            "--usb", str(usb),
            "--folder", str(folder),
        ])
        assert rc == 0
        assert pre_file.read_bytes() == pre_bytes
        assert (pre_dir / "their_file.txt").read_bytes() == b"do not touch me"


def test_hash_empty_folder_exits_3():
    with tempfile.TemporaryDirectory() as workdir:
        work = Path(workdir)
        usb = work / "usb"
        usb.mkdir()
        empty = work / "empty"
        empty.mkdir()

        rc = usb_air_gap_hash.main([
            "--usb", str(usb),
            "--folder", str(empty),
        ])
        assert rc == 3


# --------------------------------------------------------------------------- #
# usb_offline_anchor_submit.py
# --------------------------------------------------------------------------- #


def _prepare_manifest_on_usb(work: Path) -> tuple[Path, Path, str]:
    """Run the hash script to produce a manifest subdir, return (usb, subdir, root_hex)."""
    folder = _make_source_folder(work)
    usb = work / "usb"
    usb.mkdir()
    rc = usb_air_gap_hash.main(["--usb", str(usb), "--folder", str(folder)])
    assert rc == 0
    sub = _find_subdir(usb, "offline_manifest")
    manifest = json.loads((sub / "manifest.json").read_text())
    return usb, sub, manifest["merkle"]["root_hex"]


def test_submit_posts_correct_shape():
    with tempfile.TemporaryDirectory() as workdir:
        work = Path(workdir)
        usb, sub, root_hex = _prepare_manifest_on_usb(work)
        fake, captured = _mock_urlopen_returning(_synthetic_receipt(root_hex))

        with mock.patch("usb_offline_anchor_submit.urllib.request.urlopen", fake):
            rc = usb_offline_anchor_submit.main([
                "--usb", str(usb),
                "--manifest-subdir", sub.name,
                "--server-url", "https://orphograph.com",
            ])

        assert rc == 0
        assert captured["url"].endswith("/api/anchor_folder")
        # Headers are stored title-cased by urllib.
        ua = captured["headers"].get("User-agent") or captured["headers"].get("User-Agent")
        assert ua and "Mozilla/5.0" in ua and "Chrome/" in ua
        body = json.loads(captured["body"].decode("utf-8"))
        assert body["private"] is True
        assert body["merkle"]["root_hex"] == root_hex
        assert len(body["merkle"]["leaves"]) == 3


def test_submit_writes_receipt_to_new_subdir():
    with tempfile.TemporaryDirectory() as workdir:
        work = Path(workdir)
        usb, sub, root_hex = _prepare_manifest_on_usb(work)

        manifest_before = {
            p.relative_to(sub): p.read_bytes()
            for p in sub.rglob("*") if p.is_file()
        }

        fake, _ = _mock_urlopen_returning(_synthetic_receipt(root_hex))
        with mock.patch("usb_offline_anchor_submit.urllib.request.urlopen", fake):
            rc = usb_offline_anchor_submit.main([
                "--usb", str(usb),
                "--manifest-subdir", sub.name,
            ])
        assert rc == 0

        # Original manifest subdir untouched.
        manifest_after = {
            p.relative_to(sub): p.read_bytes()
            for p in sub.rglob("*") if p.is_file()
        }
        assert manifest_before == manifest_after

        receipt_sub = _find_subdir(usb, "airgap_receipt")
        assert receipt_sub != sub
        receipt = json.loads((receipt_sub / "receipt.json").read_text())
        assert receipt["receipt_id"] == "RID-TEST-0001"
        assert receipt["root_hex"] == root_hex
        assert (receipt_sub / "RECEIPT_README.txt").is_file()
        assert (receipt_sub / "verifier" / "verify.py").is_file()
        assert (receipt_sub / "verifier" / "merkle.py").is_file()
        assert (receipt_sub / "WHAT_WAS_ADDED.json").is_file()


def test_submit_4xx_exits_4():
    import urllib.error as _uerr

    with tempfile.TemporaryDirectory() as workdir:
        work = Path(workdir)
        usb, sub, _ = _prepare_manifest_on_usb(work)

        def _raise_http(req, timeout=None):
            raise _uerr.HTTPError(
                req.full_url, 401, "Unauthorized", hdrs=None,
                fp=io.BytesIO(b'{"error":"no api key"}'),
            )

        with mock.patch("usb_offline_anchor_submit.urllib.request.urlopen", _raise_http):
            rc = usb_offline_anchor_submit.main([
                "--usb", str(usb),
                "--manifest-subdir", sub.name,
            ])
        assert rc == 4


def test_submit_network_failure_exits_5():
    import urllib.error as _uerr

    with tempfile.TemporaryDirectory() as workdir:
        work = Path(workdir)
        usb, sub, _ = _prepare_manifest_on_usb(work)

        def _raise_url(req, timeout=None):
            raise _uerr.URLError("name resolution failed")

        with mock.patch("usb_offline_anchor_submit.urllib.request.urlopen", _raise_url):
            rc = usb_offline_anchor_submit.main([
                "--usb", str(usb),
                "--manifest-subdir", sub.name,
            ])
        assert rc == 5


def test_submit_manifest_subdir_missing_exits_3():
    with tempfile.TemporaryDirectory() as workdir:
        work = Path(workdir)
        usb = work / "usb"
        usb.mkdir()
        rc = usb_offline_anchor_submit.main([
            "--usb", str(usb),
            "--manifest-subdir", "orphograph_offline_manifest_does_not_exist",
        ])
        assert rc == 3


def test_dry_run_makes_no_writes_for_both_scripts():
    with tempfile.TemporaryDirectory() as workdir:
        work = Path(workdir)
        folder = _make_source_folder(work)
        usb = work / "usb"
        usb.mkdir()

        rc = usb_air_gap_hash.main([
            "--usb", str(usb),
            "--folder", str(folder),
            "--dry-run",
        ])
        assert rc == 0
        assert list(usb.iterdir()) == [], "hash dry-run wrote to the usb"

        # Now seed a real manifest, then dry-run the submit.
        rc = usb_air_gap_hash.main(["--usb", str(usb), "--folder", str(folder)])
        assert rc == 0
        sub = _find_subdir(usb, "offline_manifest")
        before = sorted(p.relative_to(usb) for p in usb.rglob("*"))

        def _boom(*a, **kw):
            raise AssertionError("dry-run must not call urlopen")

        with mock.patch("usb_offline_anchor_submit.urllib.request.urlopen", _boom):
            rc = usb_offline_anchor_submit.main([
                "--usb", str(usb),
                "--manifest-subdir", sub.name,
                "--dry-run",
            ])
        assert rc == 0
        after = sorted(p.relative_to(usb) for p in usb.rglob("*"))
        assert before == after, "submit dry-run wrote to the usb"


def test_root_filesystem_refused_for_both_scripts():
    with tempfile.TemporaryDirectory() as workdir:
        work = Path(workdir)
        folder = _make_source_folder(work)

        rc = usb_air_gap_hash.main([
            "--usb", "/",
            "--folder", str(folder),
        ])
        assert rc == 2

        rc = usb_offline_anchor_submit.main([
            "--usb", "/",
            "--manifest-subdir", "orphograph_offline_manifest_x",
        ])
        assert rc == 2


def test_submit_includes_api_key_header_when_passed():
    with tempfile.TemporaryDirectory() as workdir:
        work = Path(workdir)
        usb, sub, root_hex = _prepare_manifest_on_usb(work)

        fake, captured = _mock_urlopen_returning(_synthetic_receipt(root_hex))
        with mock.patch("usb_offline_anchor_submit.urllib.request.urlopen", fake):
            rc = usb_offline_anchor_submit.main([
                "--usb", str(usb),
                "--manifest-subdir", sub.name,
                "--api-key", "secret-token-123",
            ])
        assert rc == 0
        # urllib title-cases header names.
        headers = {k.lower(): v for k, v in captured["headers"].items()}
        assert headers.get("x-orpho-api-key") == "secret-token-123"
