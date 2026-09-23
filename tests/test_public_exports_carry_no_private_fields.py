"""A public receipt's downloads must not carry what its JSON withholds.

The JSON route (/api/receipt/<id>, /api/verify/<id>) has always projected out
operational fields. The /summary and .zip exports spread the raw receipt.json
instead, so (found 2026-09-23, in production) they served:

  * `notify_email` — the buyer's address, on every public paid receipt that
    asked for upgrade notices (23 public receipts in production);
  * `owner_id` in the .zip — an HMAC of the owner's email that clusters every
    receipt one person made (the summary had a separate patch for this);
  * a folder's full leaf-path list in the .zip, although folder paths are
    owner-only unless the owner publishes them (`paths_public`) — the folder
    verify route redacts them (80 public folder receipts in production).

Drives a real server and reads the bytes a stranger downloads.
"""
from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

import pytest

import _srv

EMAIL = "export-canary@example.test"
OWNER_ID = "ownerid_canary_0123456789abcdef"
PATH_CANARY = "secret-project/board-minutes-canary.pdf"


def _receipt(data_dir: Path, rid: str, **extra) -> Path:
    d = data_dir / "receipts" / rid
    d.mkdir(parents=True)
    rec = {
        "receipt_id": rid, "created_at": "2026-09-23T00:00:00+00:00",
        "hash_hex": "ab" * 32, "sha512_hex": None, "client_label": "canary",
        "source": "test", "attestation": None, "c2pa_manifest_hash": None,
        "metadata": {}, "calendars_ok": 1, "calendars_total": 1,
        "successes": ["a"], "failures": [], "private": False,
        **extra,
    }
    (d / "receipt.json").write_text(json.dumps(rec, indent=2))
    (d / "a.ots").write_bytes(b"\x00OpenTimestamps\x00\x00Proof\x00\xbf\x89\xe2\xe8\x84\xe8\x92\x94\x01\x08" + bytes.fromhex("ab" * 32))
    return d


def _manifest(d: Path, rid: str) -> None:
    (d / "manifest.json").write_text(json.dumps({
        "receipt_id": rid, "kind": "folder", "root_hex": "ab" * 32,
        "leaves": [{"path": PATH_CANARY, "file_sha256_hex": "cd" * 32,
                    "leaf_hex": "ef" * 32, "size_bytes": 7}],
    }))


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    data_dir = tmp_path_factory.mktemp("export_privacy")
    _receipt(data_dir, "PubEmail01234567", notify_email=EMAIL, owner_id=OWNER_ID)
    folder = _receipt(data_dir, "PubFolder0123456", kind="folder", leaf_count=1)
    _manifest(folder, "PubFolder0123456")
    shared = _receipt(data_dir, "PubPaths01234567", kind="folder", leaf_count=1, paths_public=True)
    _manifest(shared, "PubPaths01234567")
    for base in _srv.server_processes(data_dir, stub_calendars=True):
        yield base


def _zip_members(base: str, path: str) -> dict:
    status, body, _h = _srv.request(base, path, timeout=15)
    assert status == 200, (path, status, body[:200])
    with zipfile.ZipFile(io.BytesIO(body)) as z:
        return {name: z.read(name) for name in z.namelist()}


@pytest.mark.parametrize("prefix", ["/api/receipt/", "/api/verify/"])
def test_the_summary_carries_no_email_and_no_owner_id(server, prefix):
    status, body, _h = _srv.request(server, f"{prefix}PubEmail01234567/summary", timeout=15)
    assert status == 200, body[:200]
    data = json.loads(body)
    assert data["receipt_id"] == "PubEmail01234567", "control: the summary is the right receipt"
    assert EMAIL not in body.decode(), "a buyer's email is in a public receipt summary"
    assert OWNER_ID not in body.decode()


@pytest.mark.parametrize("prefix", ["/api/receipt/", "/api/verify/"])
def test_the_zip_carries_no_email_and_no_owner_id(server, prefix):
    members = _zip_members(server, f"{prefix}PubEmail01234567.zip")
    rec = json.loads(members["receipt.json"])
    assert rec["hash_hex"] == "ab" * 32, "control: the bundle still carries the proof data"
    assert "a.ots" in members
    blob = b"".join(members.values()).decode("latin-1")
    assert EMAIL not in blob, "a buyer's email is in a public receipt bundle"
    assert OWNER_ID not in blob, "owner_id (clusters a person's receipts) is in a public bundle"


def test_a_public_folder_zip_withholds_paths_like_the_verify_route(server):
    members = _zip_members(server, "/api/receipt/PubFolder0123456.zip")
    manifest = json.loads(members["manifest.json"])
    assert PATH_CANARY not in members["manifest.json"].decode(), "owner-only folder paths shipped publicly"
    assert manifest["paths_redacted"] is True
    assert manifest["leaves"][0]["leaf_hex"] == "ef" * 32, "control: leaf hashes stay verifiable"
    # Same answer as the folder verify route, from one implementation.
    status, body, _h = _srv.request(server, "/api/verify_folder/PubFolder0123456", timeout=15)
    assert status == 200 and PATH_CANARY not in body.decode()
    assert json.loads(body)["manifest"]["leaves"] == manifest["leaves"]


def test_published_paths_still_ship(server):
    members = _zip_members(server, "/api/receipt/PubPaths01234567.zip")
    assert PATH_CANARY in members["manifest.json"].decode(), \
        "the owner published these paths; the bundle must keep them"
