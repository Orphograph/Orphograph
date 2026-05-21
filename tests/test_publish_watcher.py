"""Tests for scripts/publish_watcher.py.

Every test mocks ``urllib.request.urlopen`` so the suite never touches
the network and never POSTs to production.
"""
from __future__ import annotations

import importlib
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

import publish_watcher  # noqa: E402


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _http_response(status: int, body: bytes) -> mock.MagicMock:
    resp = mock.MagicMock()
    resp.status = status
    resp.getcode.return_value = status
    resp.read.return_value = body
    resp.__enter__ = lambda s: s
    resp.__exit__ = lambda s, *a: None
    return resp


def _http_error(code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(
        "https://example.invalid", code, "err", {}, io.BytesIO(b"")
    )


def _pypi_meta(version: str = "0.1.0") -> dict:
    return {
        "info": {"version": version, "name": "orphograph"},
        "urls": [
            {
                "filename": f"orphograph-{version}-py3-none-any.whl",
                "url": f"https://files.pypi/orphograph-{version}-py3-none-any.whl",
            },
            {
                "filename": f"orphograph-{version}.tar.gz",
                "url": f"https://files.pypi/orphograph-{version}.tar.gz",
            },
        ],
    }


def _npm_meta(version: str = "0.1.0") -> dict:
    return {
        "name": "orphograph",
        "dist-tags": {"latest": version},
        "versions": {
            version: {
                "name": "orphograph",
                "version": version,
                "dist": {
                    "tarball": f"https://registry.npmjs.org/orphograph/-/orphograph-{version}.tgz",
                },
            }
        },
    }


def _isolate(monkey_root: Path) -> None:
    """Point the watcher's outbox paths at a temp directory."""
    publish_watcher.OUTBOX = monkey_root
    publish_watcher.STATE_PYPI = monkey_root / "PUBLISH_STATE_PYPI.json"
    publish_watcher.STATE_NPM = monkey_root / "PUBLISH_STATE_NPM.json"
    publish_watcher.BADGES = monkey_root / "HOMEPAGE_BADGES.json"


def _reload_module() -> None:
    """Reload publish_watcher to reset module-level paths after a test."""
    importlib.reload(publish_watcher)


# --------------------------------------------------------------------------- #
# Tests
# --------------------------------------------------------------------------- #


def test_both_registries_404_writes_nothing():
    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        _isolate(td_path)
        try:
            with mock.patch(
                "publish_watcher.urllib.request.urlopen",
                side_effect=_http_error(404),
            ):
                rc = publish_watcher.main([])
            assert rc == 0
            # No state file should exist.
            assert not (td_path / "PUBLISH_STATE_PYPI.json").exists()
            assert not (td_path / "PUBLISH_STATE_NPM.json").exists()
            assert not (td_path / "HOMEPAGE_BADGES.json").exists()
        finally:
            _reload_module()


def test_pypi_first_sighting_records_and_posts_anchor():
    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        _isolate(td_path)
        try:
            meta = _pypi_meta("0.1.0")
            wheel_bytes = b"PK\x03\x04wheel-content"
            sdist_bytes = b"sdist-tar-content"
            anchor_calls: list[dict] = []

            def fake_urlopen(req, timeout=None):
                url = req.full_url if hasattr(req, "full_url") else req
                method = req.get_method() if hasattr(req, "get_method") else "GET"
                if method == "POST":
                    body = json.loads(req.data.decode("utf-8"))
                    anchor_calls.append({
                        "url": url,
                        "body": body,
                    })
                    return _http_response(200, json.dumps({
                        "receipt_id": "rid_pypi_001",
                        "root_hex": body["manifest"]["root_hex"],
                        "calendars_ok": 5,
                    }).encode("utf-8"))
                if url == publish_watcher.PYPI_URL:
                    return _http_response(200, json.dumps(meta).encode("utf-8"))
                if url.endswith(".whl"):
                    return _http_response(200, wheel_bytes)
                if url.endswith(".tar.gz"):
                    return _http_response(200, sdist_bytes)
                if url == publish_watcher.NPM_URL:
                    raise _http_error(404)
                raise AssertionError(f"unexpected url: {url}")

            with mock.patch(
                "publish_watcher.urllib.request.urlopen",
                side_effect=fake_urlopen,
            ):
                rc = publish_watcher.main([])
            assert rc == 0

            # State file exists with exactly one record for 0.1.0.
            state_path = td_path / "PUBLISH_STATE_PYPI.json"
            assert state_path.exists()
            lines = state_path.read_text().strip().splitlines()
            assert len(lines) == 1
            row = json.loads(lines[0])
            assert row["registry"] == "pypi"
            assert row["version"] == "0.1.0"
            assert row["anchor_receipt_id"] == "rid_pypi_001"
            assert row["anchor_calendars_ok"] == 5
            filenames = {f["filename"] for f in row["files"]}
            assert "orphograph-0.1.0-py3-none-any.whl" in filenames
            assert "orphograph-0.1.0.tar.gz" in filenames

            # Anchor was POSTed exactly once.
            assert len(anchor_calls) == 1
            assert anchor_calls[0]["url"].endswith("/api/anchor_folder")
            assert anchor_calls[0]["body"]["private"] is True
            assert anchor_calls[0]["body"]["client_label"] == "pypi:orphograph@0.1.0"

            # Badges file written and well-formed.
            badges_path = td_path / "HOMEPAGE_BADGES.json"
            assert badges_path.exists()
            badges = json.loads(badges_path.read_text())
            assert badges["pypi"]["version"] == "0.1.0"
            assert badges["pypi"]["installed_via"] == "pip install orphograph"
            assert badges["pypi"]["anchor_receipt_id"] == "rid_pypi_001"
        finally:
            _reload_module()


def test_pypi_idempotent_second_run_no_duplicate():
    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        _isolate(td_path)
        try:
            meta = _pypi_meta("0.1.0")
            anchor_call_count = {"n": 0}

            def fake_urlopen(req, timeout=None):
                url = req.full_url if hasattr(req, "full_url") else req
                method = req.get_method() if hasattr(req, "get_method") else "GET"
                if method == "POST":
                    anchor_call_count["n"] += 1
                    body = json.loads(req.data.decode("utf-8"))
                    return _http_response(200, json.dumps({
                        "receipt_id": "rid_pypi_001",
                        "root_hex": body["manifest"]["root_hex"],
                        "calendars_ok": 5,
                    }).encode("utf-8"))
                if url == publish_watcher.PYPI_URL:
                    return _http_response(200, json.dumps(meta).encode("utf-8"))
                if url.endswith(".whl") or url.endswith(".tar.gz"):
                    return _http_response(200, b"artefact-bytes")
                if url == publish_watcher.NPM_URL:
                    raise _http_error(404)
                raise AssertionError(f"unexpected url: {url}")

            with mock.patch(
                "publish_watcher.urllib.request.urlopen",
                side_effect=fake_urlopen,
            ):
                publish_watcher.main([])
                publish_watcher.main([])

            state_path = td_path / "PUBLISH_STATE_PYPI.json"
            lines = state_path.read_text().strip().splitlines()
            assert len(lines) == 1, "second run must not append a duplicate line"
            assert anchor_call_count["n"] == 1, (
                "second run must not POST a second anchor"
            )
        finally:
            _reload_module()


def test_npm_first_sighting_records_and_posts():
    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        _isolate(td_path)
        try:
            meta = _npm_meta("0.1.0")
            tarball_bytes = b"\x1f\x8b" + b"x" * 32  # gzip-magic-ish content

            anchor_calls: list[dict] = []

            def fake_urlopen(req, timeout=None):
                url = req.full_url if hasattr(req, "full_url") else req
                method = req.get_method() if hasattr(req, "get_method") else "GET"
                if method == "POST":
                    body = json.loads(req.data.decode("utf-8"))
                    anchor_calls.append({"url": url, "body": body})
                    return _http_response(200, json.dumps({
                        "receipt_id": "rid_npm_001",
                        "root_hex": body["manifest"]["root_hex"],
                        "calendars_ok": 5,
                    }).encode("utf-8"))
                if url == publish_watcher.PYPI_URL:
                    raise _http_error(404)
                if url == publish_watcher.NPM_URL:
                    return _http_response(200, json.dumps(meta).encode("utf-8"))
                if url.endswith(".tgz"):
                    return _http_response(200, tarball_bytes)
                raise AssertionError(f"unexpected url: {url}")

            with mock.patch(
                "publish_watcher.urllib.request.urlopen",
                side_effect=fake_urlopen,
            ):
                rc = publish_watcher.main([])
            assert rc == 0

            state_path = td_path / "PUBLISH_STATE_NPM.json"
            assert state_path.exists()
            lines = state_path.read_text().strip().splitlines()
            assert len(lines) == 1
            row = json.loads(lines[0])
            assert row["registry"] == "npm"
            assert row["version"] == "0.1.0"
            assert row["anchor_receipt_id"] == "rid_npm_001"
            assert row["files"][0]["filename"] == "orphograph-0.1.0.tgz"

            assert len(anchor_calls) == 1
            assert anchor_calls[0]["body"]["client_label"] == "npm:orphograph@0.1.0"

            badges = json.loads((td_path / "HOMEPAGE_BADGES.json").read_text())
            assert badges["npm"]["version"] == "0.1.0"
            assert badges["npm"]["installed_via"] == "npm install orphograph"
        finally:
            _reload_module()


def test_network_error_exits_0_and_leaves_state_untouched(capsys):
    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        _isolate(td_path)
        try:
            with mock.patch(
                "publish_watcher.urllib.request.urlopen",
                side_effect=urllib.error.URLError("connection refused"),
            ):
                rc = publish_watcher.main([])
            assert rc == 0
            assert not (td_path / "PUBLISH_STATE_PYPI.json").exists()
            assert not (td_path / "PUBLISH_STATE_NPM.json").exists()
            captured = capsys.readouterr()
            assert "network error" in captured.err
        finally:
            _reload_module()


def test_homepage_badges_is_well_formed_json_after_both_runs():
    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        _isolate(td_path)
        try:
            pypi = _pypi_meta("0.1.0")
            npm = _npm_meta("0.1.0")

            def fake_urlopen(req, timeout=None):
                url = req.full_url if hasattr(req, "full_url") else req
                method = req.get_method() if hasattr(req, "get_method") else "GET"
                if method == "POST":
                    body = json.loads(req.data.decode("utf-8"))
                    label = body["client_label"]
                    receipt = "rid_pypi" if label.startswith("pypi:") else "rid_npm"
                    return _http_response(200, json.dumps({
                        "receipt_id": receipt,
                        "root_hex": body["manifest"]["root_hex"],
                        "calendars_ok": 5,
                    }).encode("utf-8"))
                if url == publish_watcher.PYPI_URL:
                    return _http_response(200, json.dumps(pypi).encode("utf-8"))
                if url == publish_watcher.NPM_URL:
                    return _http_response(200, json.dumps(npm).encode("utf-8"))
                if url.endswith(".whl") or url.endswith(".tar.gz") or url.endswith(".tgz"):
                    return _http_response(200, b"artefact-bytes-" + url.encode())
                raise AssertionError(f"unexpected url: {url}")

            with mock.patch(
                "publish_watcher.urllib.request.urlopen",
                side_effect=fake_urlopen,
            ):
                publish_watcher.main([])

            badges_path = td_path / "HOMEPAGE_BADGES.json"
            assert badges_path.exists()
            raw = badges_path.read_text()
            # Must round-trip as JSON.
            data = json.loads(raw)
            assert isinstance(data, dict)
            assert "pypi" in data
            assert "npm" in data
            assert "updated_at_utc" in data
            # No emojis, no token-shaped strings.
            assert "X-Orpho-Api-Key" not in raw
        finally:
            _reload_module()


def test_pypi_200_npm_404_only_pypi_state_written():
    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        _isolate(td_path)
        try:
            pypi = _pypi_meta("0.2.0")

            def fake_urlopen(req, timeout=None):
                url = req.full_url if hasattr(req, "full_url") else req
                method = req.get_method() if hasattr(req, "get_method") else "GET"
                if method == "POST":
                    body = json.loads(req.data.decode("utf-8"))
                    return _http_response(200, json.dumps({
                        "receipt_id": "rid_pypi_002",
                        "root_hex": body["manifest"]["root_hex"],
                        "calendars_ok": 5,
                    }).encode("utf-8"))
                if url == publish_watcher.PYPI_URL:
                    return _http_response(200, json.dumps(pypi).encode("utf-8"))
                if url == publish_watcher.NPM_URL:
                    raise _http_error(404)
                if url.endswith(".whl") or url.endswith(".tar.gz"):
                    return _http_response(200, b"artefact-bytes")
                raise AssertionError(f"unexpected url: {url}")

            with mock.patch(
                "publish_watcher.urllib.request.urlopen",
                side_effect=fake_urlopen,
            ):
                rc = publish_watcher.main([])
            assert rc == 0
            assert (td_path / "PUBLISH_STATE_PYPI.json").exists()
            assert not (td_path / "PUBLISH_STATE_NPM.json").exists()
            badges = json.loads((td_path / "HOMEPAGE_BADGES.json").read_text())
            assert "pypi" in badges
            assert "npm" not in badges
        finally:
            _reload_module()
