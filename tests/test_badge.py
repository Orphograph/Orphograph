"""test_badge.py — verifier badge SVG renderer + HTTP route.

Two layers of coverage:

1. Pure-function tests against ``badge_svg.render()`` to lock in the
   privacy contract (no filename, no email, no hash bytes leak).
2. Live in-process HTTP server tests against ``/api/badge/<id>.svg``
   to confirm route shape, content type, caching headers, and the
   400/404 attack-surface responses.
"""
from __future__ import annotations

import hashlib
import os
import re
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import pytest

import badge_svg
import engine

REPO_ROOT = Path(__file__).resolve().parent.parent

# A 64-char hex SHA-256 ought to never appear in the badge output —
# the receipt page may show it, but the embeddable badge must not.
_HEX64_RE = re.compile(r"[0-9a-f]{64}")
# Likewise for email addresses (loose match — catches anything @ anything).
_EMAIL_RE = re.compile(r"[^\s@<>\"']+@[^\s@<>\"']+\.[^\s@<>\"']+")


# ── Pure-function tests ─────────────────────────────────────────────────

def _hash_of(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


@pytest.fixture
def isolated_storage(tmp_path, monkeypatch):
    monkeypatch.setattr(engine, "ROOT", tmp_path)
    monkeypatch.setattr(engine, "RECEIPTS_DIR", tmp_path / "receipts")
    monkeypatch.setattr(engine, "LEDGER", tmp_path / "ledger.jsonl")
    yield


def _fake_submit_all_ok(_url, hash_bytes):
    return True, b"calendar-body-for-" + hash_bytes[:4]


def test_render_returns_svg_document():
    svg = badge_svg.render({
        "receipt_id": "abc123_XYZdef45",
        "created_at": "2026-05-14T12:34:56+00:00",
    })
    assert svg.startswith("<?xml") or svg.startswith("<svg")
    assert svg.rstrip().endswith("</svg>")


def test_render_includes_short_id_last_8_chars():
    svg = badge_svg.render({
        "receipt_id": "abc123_XYZdef45",   # last 8 chars = "YZdef45" → 7 chars only
        "created_at": "2026-05-14T12:34:56+00:00",
    })
    # "_XYZdef45" → last 8 chars = "XYZdef45"
    assert "XYZdef45" in svg


def test_render_includes_date_only_no_time():
    svg = badge_svg.render({
        "receipt_id": "abc123_xyz",
        "created_at": "2026-05-14T12:34:56+00:00",
    })
    assert "2026-05-14" in svg
    # Time component must not leak — we only show the date.
    assert "12:34:56" not in svg


def test_render_links_to_receipt_url():
    svg = badge_svg.render({
        "receipt_id": "abc123_xyz",
        "created_at": "2026-05-14T12:34:56+00:00",
    })
    assert "/r/abc123_xyz" in svg


def test_render_honors_base_url():
    svg = badge_svg.render(
        {"receipt_id": "abc123_xyz", "created_at": "2026-05-14T12:34:56+00:00"},
        base_url="https://orphograph.com",
    )
    assert "https://orphograph.com/r/abc123_xyz" in svg


def test_render_folder_receipt_is_dataset_aware():
    """A folder (dataset) receipt shows the file count and links to the cert."""
    svg = badge_svg.render(
        {
            "receipt_id": "DatasetSample",
            "created_at": "2026-06-30T04:16:08+00:00",
            "kind": "folder",
            "leaf_count": 8,
        },
        base_url="https://orphograph.com",
    )
    assert "dataset" in svg
    assert "8 files" in svg
    # Folder badges link to the certificate view, not the single-file /r/ view.
    assert "https://orphograph.com/certificate/DatasetSample" in svg
    assert "/r/DatasetSample" not in svg


def test_render_folder_without_leaf_count_still_links_to_certificate():
    """The certificate link is independent of the subtitle: a folder receipt
    with no leaf_count links to /certificate/ and omits the 'dataset · N files'
    subtitle rather than emitting a bogus '0 files'."""
    svg = badge_svg.render(
        {"receipt_id": "FolderNoLeaf", "kind": "folder",
         "created_at": "2026-06-30T04:16:08+00:00"},
        base_url="https://orphograph.com",
    )
    assert "https://orphograph.com/certificate/FolderNoLeaf" in svg
    assert "/r/FolderNoLeaf" not in svg
    assert "dataset" not in svg      # no dataset subtitle without a positive count
    assert "files" not in svg


def test_render_omits_client_label_and_email_and_hash():
    """Privacy invariant: receipt extras must never bleed into the badge."""
    rid = "abc123_xyz"
    full_hash = _hash_of("the-secret-file.png")
    receipt = {
        "receipt_id": rid,
        "created_at": "2026-05-14T12:34:56+00:00",
        # The next three fields are real fields on a verify_receipt() result.
        # The badge must IGNORE them all.
        "client_label": "wedding-2026-bride-private.cr2",
        "email": "client@example.com",
        "hash_hex": full_hash,
        "sha512_hex": "f" * 128,
    }
    svg = badge_svg.render(receipt)

    # Filename leak check.
    assert "wedding-2026-bride-private" not in svg
    assert "cr2" not in svg.lower()
    # Email leak check.
    assert "client@example.com" not in svg
    assert not _EMAIL_RE.search(svg), "no email-shaped strings allowed"
    # Hash leak check — neither the full hex nor any 64-char hex blob.
    assert full_hash not in svg
    assert not _HEX64_RE.search(svg.lower()), "no 64-char hex blob in badge"
    # SHA-512 leak check.
    assert "f" * 128 not in svg


def test_render_handles_missing_created_at():
    svg = badge_svg.render({"receipt_id": "abc123_xyz"})
    # No date → subtitle should still read; should not throw.
    assert "anchored to Bitcoin" in svg


def test_render_handles_malformed_created_at():
    svg = badge_svg.render({
        "receipt_id": "abc123_xyz",
        "created_at": "not-a-date",
    })
    # Bad date must not leak through into the badge text.
    assert "not-a-date" not in svg


def test_render_handles_empty_dict():
    """An empty receipt dict shouldn't blow up — return a graceful SVG."""
    svg = badge_svg.render({})
    assert svg.startswith("<?xml") or svg.startswith("<svg")
    assert svg.rstrip().endswith("</svg>")


def test_render_contains_brand_wordmark():
    svg = badge_svg.render({
        "receipt_id": "abc123_xyz",
        "created_at": "2026-05-14T12:34:56+00:00",
    })
    assert "Verified by Orphograph" in svg
    assert "anchored to Bitcoin" in svg


def test_render_no_external_assets():
    """No external font/image references — the SVG must paint offline."""
    svg = badge_svg.render({
        "receipt_id": "abc123_xyz",
        "created_at": "2026-05-14T12:34:56+00:00",
    })
    # No remote image references, no @import / @font-face, no script tags.
    assert "<image" not in svg
    assert "@font-face" not in svg
    assert "@import" not in svg
    assert "<script" not in svg


# ── HTTP integration tests (in-process — no subprocess) ──────────────────

def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    """Spin up the HTTP handler in-process on a background thread.

    Subprocess-based fixtures fail in restricted CI sandboxes; running
    the same handler on a thread inside the test process gives us all
    the same routing coverage without the subprocess permission cost.
    """
    import threading
    from http.server import ThreadingHTTPServer

    data_dir = tmp_path_factory.mktemp("badge_data")
    receipts_dir = data_dir / "receipts"
    receipts_dir.mkdir(parents=True, exist_ok=True)

    # Repoint engine's data paths to the test directory BEFORE importing
    # app — app imports engine, but engine's module-level paths can be
    # reassigned after import.
    engine.RECEIPTS_DIR = receipts_dir
    engine.LEDGER = data_dir / "ledger.jsonl"

    import app  # noqa: WPS433 (intentional in-test import)
    # Make sure app sees the same engine paths we just rewrote.
    app.engine.RECEIPTS_DIR = receipts_dir
    app.engine.LEDGER = data_dir / "ledger.jsonl"

    port = _free_port()
    httpd = ThreadingHTTPServer(("127.0.0.1", port), app.Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()

    base = f"http://127.0.0.1:{port}"
    # Confirm the server answers before any test runs.
    deadline = time.time() + 5
    started = False
    while time.time() < deadline:
        try:
            urllib.request.urlopen(base + "/api/health", timeout=1).read()
            started = True
            break
        except Exception:
            time.sleep(0.1)
    if not started:
        httpd.shutdown()
        pytest.fail("in-process server did not respond")
    yield base, data_dir
    httpd.shutdown()
    httpd.server_close()


def _create_receipt_in(data_dir: Path, *, client_label: str | None = None) -> str:
    """Drop a receipt directory directly into the server's data dir.

    We bypass the calendar HTTP submit by writing the receipt JSON +
    a fake .ots file ourselves — the badge route only consults
    verify_receipt(), which is happy with magic-prefix .ots files.
    """
    import json
    rid = "abcDEF_TestId01"
    receipt_dir = data_dir / "receipts" / rid
    receipt_dir.mkdir(parents=True, exist_ok=True)
    sample_hash = _hash_of("badge-test-payload")
    record = {
        "receipt_id": rid,
        "created_at": "2026-05-14T12:34:56+00:00",
        "hash_hex": sample_hash,
        "sha512_hex": None,
        "client_label": client_label,
        "source": "free",
        "calendars_ok": 1,
        "calendars_total": 1,
        "successes": [],
        "failures": [],
    }
    (receipt_dir / "receipt.json").write_text(json.dumps(record, indent=2))
    # Write a single .ots-shaped file so verify_receipt() returns found=True.
    ots_blob = (
        engine.OTS_HEADER_MAGIC
        + engine.OTS_VERSION
        + engine.OTS_TAG_SHA256
        + bytes.fromhex(sample_hash)
        + b"fake-calendar-body"
    )
    (receipt_dir / "test.ots").write_bytes(ots_blob)
    return rid


def test_badge_route_renders_for_valid_receipt(server):
    base, data_dir = server
    rid = _create_receipt_in(data_dir, client_label="private-filename.jpg")
    req = urllib.request.Request(f"{base}/api/badge/{rid}.svg")
    with urllib.request.urlopen(req, timeout=5) as r:
        assert r.status == 200
        ctype = r.headers.get("Content-Type", "")
        assert ctype.startswith("image/svg+xml")
        cache = r.headers.get("Cache-Control", "")
        assert "public" in cache
        assert "max-age=3600" in cache
        # Body may be gzip-compressed if the client advertised it,
        # but urllib doesn't by default — so we can read it raw.
        body = r.read().decode("utf-8")
    assert body.startswith("<?xml") or body.startswith("<svg")
    assert body.rstrip().endswith("</svg>")
    # Privacy: the filename we stuffed into client_label must not surface.
    assert "private-filename" not in body
    assert "jpg" not in body.lower()


def test_badge_route_returns_404_for_unknown_receipt(server):
    base, _ = server
    code = _http_status(f"{base}/api/badge/nonexistentidXYZ.svg")
    assert code == 404


@pytest.mark.parametrize("rid", [
    "../../../etc/passwd",
    "..%2F..%2Fetc",
    "with/slash",
    "with spaces",
    "<script>",
    "a" * 200,    # too long
    "",
])
def test_badge_route_rejects_malformed_ids(server, rid):
    base, _ = server
    encoded = urllib.parse.quote(rid, safe="")
    code = _http_status(f"{base}/api/badge/{encoded}.svg")
    # 400 (bad shape) or 404 (route didn't match) are both acceptable.
    # Never 5xx, never 200 for an attack input.
    assert code in (400, 404), f"{rid!r} returned {code}"


def test_badge_route_content_type_header(server):
    base, data_dir = server
    rid = _create_receipt_in(data_dir)
    req = urllib.request.Request(f"{base}/api/badge/{rid}.svg")
    with urllib.request.urlopen(req, timeout=5) as r:
        assert r.headers.get("Content-Type", "").startswith("image/svg+xml")


def test_badge_route_no_filename_or_email_or_hash_leak(server):
    """End-to-end privacy check: even with sensitive fields in the receipt
    on disk, the rendered badge body must not contain any of them."""
    base, data_dir = server
    rid = _create_receipt_in(data_dir, client_label="leaky-photo-name.cr2")
    req = urllib.request.Request(f"{base}/api/badge/{rid}.svg")
    with urllib.request.urlopen(req, timeout=5) as r:
        body = r.read().decode("utf-8")
    assert "leaky-photo-name" not in body
    assert "cr2" not in body.lower()
    assert not _EMAIL_RE.search(body)
    assert not _HEX64_RE.search(body.lower())


# ── Helpers ────────────────────────────────────────────────────────────

def _http_status(url: str) -> int:
    try:
        with urllib.request.urlopen(url, timeout=5) as r:
            r.read()
            return r.status
    except urllib.error.HTTPError as e:
        return e.code
    except Exception:
        return -1
