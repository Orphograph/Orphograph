"""Tests for scripts/weekly_anchor.py — the one-receipt folder-anchor job.

Network-free: only the pure seams (collect_leaves, build_manifest) are
exercised. The root produced here is checked against the canonical
server/merkle.py so a drift between the script and the server's
recomputation would fail the suite instead of surfacing as a live 400.
"""
from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = ROOT / "scripts"
SERVER_DIR = ROOT / "server"
for p in (str(SCRIPTS_DIR), str(SERVER_DIR)):
    if p not in sys.path:
        sys.path.insert(0, p)

import merkle  # noqa: E402
import weekly_anchor  # noqa: E402


def _write(root: Path, rel: str, data: bytes) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)


def test_collect_leaves_hashes_present_skips_missing(tmp_path):
    _write(tmp_path, "LICENSE", b"mit license text")
    _write(tmp_path, "web/seal.png", b"\x89PNG fake bytes")
    artifacts = ["LICENSE", "web/seal.png", "does/not/exist.txt"]

    leaves = weekly_anchor.collect_leaves(tmp_path, artifacts, head="")

    paths = [lf["path"] for lf in leaves]
    assert "LICENSE" in paths
    assert "web/seal.png" in paths
    assert "does/not/exist.txt" not in paths  # missing skipped, not fatal
    # File hash is the plain SHA-256 of the content.
    lic = next(lf for lf in leaves if lf["path"] == "LICENSE")
    assert lic["file_sha256_hex"] == hashlib.sha256(b"mit license text").hexdigest()
    assert lic["size_bytes"] == len(b"mit license text")


def test_collect_leaves_includes_git_head_and_sorts(tmp_path):
    _write(tmp_path, "b.txt", b"b")
    _write(tmp_path, "a.txt", b"a")
    head = "ab59e3fdd359deadbeef"

    leaves = weekly_anchor.collect_leaves(tmp_path, ["b.txt", "a.txt"], head=head)

    paths = [lf["path"] for lf in leaves]
    # Sorted by UTF-8 byte order of the path (the canonical leaf order).
    assert paths == sorted(paths, key=lambda s: s.encode("utf-8"))
    # The synthetic git-HEAD leaf is present and commits sha256(head).
    gh = next(lf for lf in leaves if lf["path"] == weekly_anchor.GIT_HEAD_LEAF_PATH)
    assert gh["file_sha256_hex"] == hashlib.sha256(head.encode()).hexdigest()


def test_build_manifest_root_matches_canonical_merkle(tmp_path):
    _write(tmp_path, "LICENSE", b"mit")
    _write(tmp_path, "web/seal.png", b"png")
    leaves = weekly_anchor.collect_leaves(tmp_path, ["LICENSE", "web/seal.png"], head="deadbeef")

    manifest = weekly_anchor.build_manifest(leaves)

    assert manifest["algorithm"] == merkle.ALGORITHM
    assert manifest["version"] == merkle.VERSION
    # The server reconstructs with exactly this call; it must not raise and the
    # root it derives must equal what we submitted.
    tree = merkle.MerkleTree.from_manifest(manifest)
    assert tree.root_hex() == manifest["root_hex"]
    assert len(manifest["leaves"]) == len(leaves)


def test_build_manifest_rejects_empty():
    with pytest.raises(ValueError):
        weekly_anchor.build_manifest([])


def test_build_manifest_tamper_breaks_reconstruction(tmp_path):
    _write(tmp_path, "LICENSE", b"mit")
    leaves = weekly_anchor.collect_leaves(tmp_path, ["LICENSE"], head="")
    manifest = weekly_anchor.build_manifest(leaves)
    # Flip the committed file hash but leave root_hex/leaf_hex intact: the
    # server's from_manifest recomputes the leaf from file_sha256_hex and must
    # reject the inconsistency.
    manifest["leaves"][0]["file_sha256_hex"] = "00" * 32
    with pytest.raises(ValueError):
        merkle.MerkleTree.from_manifest(manifest)


# ── network retry (2026-11-16) ────────────────────────────────────────────────
# The 2026-11-15 run lost its week to one DNS failure ("nodename nor servname
# provided"): the job had no retry. Retry ONLY failures where the request never
# reached the server (DNS, refused, unreachable), so a retry cannot mint a
# second receipt; a timeout after sending is never retried.
import errno  # noqa: E402
import socket  # noqa: E402
import urllib.error  # noqa: E402


class _Resp:
    def __init__(self, payload: bytes):
        self._p = payload

    def read(self):
        return self._p

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _scripted(monkeypatch, outcomes):
    calls = []

    def fake(req, timeout):
        calls.append(req.full_url)
        o = outcomes[min(len(calls) - 1, len(outcomes) - 1)]
        if isinstance(o, BaseException):
            raise o
        return _Resp(o)

    monkeypatch.setattr(weekly_anchor, "_urlopen", fake)
    monkeypatch.setattr(weekly_anchor, "_sleep", lambda s: None)
    monkeypatch.setenv("ORPHO_WEEKLY_RETRY_DELAYS", "1,1")
    return calls


_OK = b'{"receipt_id": "r1", "calendars_ok": 5, "calendars_total": 5}'
_DNS = urllib.error.URLError(socket.gaierror(8, "nodename nor servname provided, or not known"))


def test_dns_failure_is_retried_until_success(monkeypatch):
    calls = _scripted(monkeypatch, [_DNS, _DNS, _OK])
    out = weekly_anchor.anchor_folder({"root_hex": "00"}, "l", "s")
    assert out.get("receipt_id") == "r1"
    assert len(calls) == 3


def test_refused_and_unreachable_are_retried(monkeypatch):
    refused = urllib.error.URLError(ConnectionRefusedError(61, "refused"))
    unreach = urllib.error.URLError(OSError(errno.ENETUNREACH, "Network is unreachable"))
    calls = _scripted(monkeypatch, [refused, unreach, _OK])
    assert weekly_anchor.anchor_folder({"root_hex": "00"}, "l", "s").get("receipt_id") == "r1"
    assert len(calls) == 3


def test_dns_failure_gives_up_after_all_attempts(monkeypatch):
    calls = _scripted(monkeypatch, [_DNS])
    out = weekly_anchor.anchor_folder({"root_hex": "00"}, "l", "s")
    assert out.get("receipt_id") is None
    assert "after 3 attempt" in out["error"]
    assert len(calls) == 3


@pytest.mark.parametrize("exc", [
    urllib.error.URLError(TimeoutError("timed out")),   # may have been sent
    TimeoutError("timed out"),
    urllib.error.HTTPError("u", 503, "busy", {}, None),  # the server answered
])
def test_failures_after_send_are_never_retried(monkeypatch, exc):
    calls = _scripted(monkeypatch, [exc, _OK])
    out = weekly_anchor.anchor_folder({"root_hex": "00"}, "l", "s")
    assert out.get("receipt_id") is None
    assert len(calls) == 1
