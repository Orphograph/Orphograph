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
import hashlib
import hmac
import time
import json
import zipfile
from pathlib import Path

import pytest

import _srv

EMAIL = "export-canary@example.test"
SECRET = "export-test-hmac-secret"
OWNER_ID = hmac.new(SECRET.encode(), EMAIL.encode(), hashlib.sha256).hexdigest()[:16]
SESSION = "export-owner-session"
OWNER_HEADERS = {"Cookie": "orpho_sid=" + SESSION}
UNKNOWN = "unknown-private-field-canary"
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
    _receipt(data_dir, "PubEmail01234567", notify_email=EMAIL, owner_id=None, source="sub:" + OWNER_ID,
             future_internal=UNKNOWN)
    folder = _receipt(data_dir, "PubFolder0123456", kind="folder", leaf_count=1)
    _manifest(folder, "PubFolder0123456")
    shared = _receipt(data_dir, "PubPaths01234567", kind="folder", leaf_count=1, paths_public=True)
    _manifest(shared, "PubPaths01234567")
    private = _receipt(data_dir, "Private012345678", private=True, owner_id=OWNER_ID,
                       notify_email=EMAIL, kind="folder", leaf_count=1)
    _manifest(private, "Private012345678")
    special = _receipt(data_dir, "Special012345678", kind="folder", leaf_count=2)
    _manifest(special, "Special012345678")
    m = json.loads((special / "manifest.json").read_text())
    m.update(notify_email=EMAIL, future_internal=UNKNOWN)
    m["leaves"][0].update(path=".orphograph/customer-secret", future_internal=UNKNOWN)
    m["leaves"].append({"path": ".orphograph/parent", "file_sha256_hex": "cd" * 32,
                        "leaf_hex": "ef" * 32, "size_bytes": 0})
    (special / "manifest.json").write_text(json.dumps(m))
    for rid, renewal in [("NoRenew012345678", False), ("Renewed012345678", True)]:
        d = _receipt(data_dir, rid, source="free")
        (d / "renewal").mkdir()
        (d / "renewal" / "README.txt").write_text("not a renewal")
        if renewal:
            (d / "renewal" / "001.json").write_text("{}")
    (data_dir / "auth_sessions.jsonl").write_text(json.dumps({
        "event": "created", "session_hash": hashlib.sha256(SESSION.encode()).hexdigest(),
        "email": EMAIL, "expires_unix": time.time() + 3600,
    }) + "\n")
    for base in _srv.server_processes(data_dir, stub_calendars=True,
                                      ORPHO_HMAC_SECRET=SECRET):
        yield base


def _zip_members(base: str, path: str, headers=None) -> dict:
    status, body, _h = _srv.request(base, path, headers=headers, timeout=15)
    assert status == 200, (path, status, body[:200])
    with zipfile.ZipFile(io.BytesIO(body)) as z:
        return {name: z.read(name) for name in z.namelist()}


@pytest.mark.parametrize("prefix", ["/api/receipt/", "/api/verify/"])
def test_the_summary_carries_no_email_and_no_owner_id(server, prefix):
    status, body, _h = _srv.request(server, f"{prefix}PubEmail01234567/summary", timeout=15)
    assert status == 200, body[:200]
    data = json.loads(body)
    assert EMAIL not in body.decode(), "a buyer's email is in a public receipt summary"
    assert OWNER_ID not in body.decode()
    assert UNKNOWN not in body.decode()
    assert data["receipt_id"] == "PubEmail01234567", "control: the summary is the right receipt"


@pytest.mark.parametrize("prefix", ["/api/receipt/", "/api/verify/"])
def test_the_zip_carries_no_email_and_no_owner_id(server, prefix):
    members = _zip_members(server, f"{prefix}PubEmail01234567.zip")
    rec = json.loads(members["receipt.json"])
    blob = b"".join(members.values()).decode("latin-1")
    assert EMAIL not in blob, "a buyer's email is in a public receipt bundle"
    assert OWNER_ID not in blob, "owner_id (clusters a person's receipts) is in a public bundle"
    assert UNKNOWN not in blob
    assert rec["hash_hex"] == "ab" * 32, "control: the bundle still carries the proof data"
    assert "a.ots" in members


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


@pytest.mark.parametrize("prefix", ["/api/receipt/", "/api/verify/"])
@pytest.mark.parametrize("suffix", ["", "/summary", ".zip"])
def test_private_exports_deny_strangers(server, prefix, suffix):
    status, body, _ = _srv.request(server, prefix + "Private012345678" + suffix)
    assert status in (401, 403, 404)
    assert EMAIL not in body.decode()
    assert PATH_CANARY not in body.decode()
    assert OWNER_ID not in body.decode()


@pytest.mark.parametrize("prefix", ["/api/receipt/", "/api/verify/"])
def test_private_owner_bundle_keeps_paths_and_owner_but_never_email(server, prefix):
    members = _zip_members(server, prefix + "Private012345678.zip", OWNER_HEADERS)
    blob = b"".join(members.values()).decode("latin-1")
    assert EMAIL not in blob
    assert "notify_email" not in json.loads(members["receipt.json"])
    assert json.loads(members["receipt.json"])["owner_id"] == OWNER_ID
    assert PATH_CANARY in members["manifest.json"].decode()
    status, body, _ = _srv.request(server, prefix + "Private012345678/summary",
                                   headers=OWNER_HEADERS)
    assert status == 200, body[:200]
    assert EMAIL not in body.decode()
    assert json.loads(body)["owner_id"] == OWNER_ID


@pytest.mark.parametrize("rid,retained", [("NoRenew012345678", False), ("Renewed012345678", True)])
def test_public_source_requires_actual_renewal_json(server, rid, retained):
    members = _zip_members(server, "/api/receipt/" + rid + ".zip")
    rec = json.loads(members["receipt.json"])
    assert ("source" in rec) is retained
    status, body, _ = _srv.request(server, "/api/receipt/" + rid + "/summary")
    assert status == 200, body[:200]
    assert "source" not in json.loads(body), "summary does not transport renewal proofs"
    if retained:
        assert rec["source"] == "free"


def test_manifest_allowlist_and_exact_reserved_path(server):
    members = _zip_members(server, "/api/receipt/Special012345678.zip")
    blob = members["manifest.json"].decode()
    assert UNKNOWN not in blob
    assert EMAIL not in blob
    assert ".orphograph/customer-secret" not in blob
    assert ".orphograph/parent" in blob
    status, body, _ = _srv.request(server, "/api/verify_folder/Special012345678")
    assert status == 200, body[:200]
    assert UNKNOWN not in body.decode()
    assert EMAIL not in body.decode()
    assert ".orphograph/customer-secret" not in body.decode()
    assert ".orphograph/parent" in body.decode()


@pytest.mark.parametrize("wrapped", [False, True])
def test_anchor_folder_persists_only_manifest_fields(tmp_path, wrapped):
    file_hash = "cd" * 32
    leaf_hash = hashlib.sha256(b"\x00" + PATH_CANARY.encode() + b"\x00" + bytes.fromhex(file_hash)).hexdigest()
    manifest = {
        "algorithm": "orphograph-merkle-v1-rfc6962", "version": 1,
        "root_hex": leaf_hash, "notify_email": EMAIL, "future_internal": UNKNOWN,
        "leaves": [{"path": PATH_CANARY, "file_sha256_hex": file_hash,
                    "leaf_hex": leaf_hash, "size_bytes": 7, "future_internal": UNKNOWN}],
    }
    payload = {"manifest": manifest, "notify_email": EMAIL, "future_internal": UNKNOWN} if wrapped else manifest
    for base in _srv.server_processes(tmp_path, stub_calendars=True):
        status, body, _ = _srv.request(base, "/api/anchor_folder", method="POST",
            body=json.dumps(payload).encode(), headers={"Content-Type": "application/json"}, timeout=60)
        assert status == 200, body[:500]
        rid = json.loads(body)["receipt_id"]
        stored = (tmp_path / "receipts" / rid / "manifest.json").read_text()
        assert EMAIL not in stored
        assert "notify_email" not in stored
        assert UNKNOWN not in stored
        assert "future_internal" not in stored
        assert json.loads(stored)["leaves"][0]["path"] == PATH_CANARY
        assert json.loads(stored)["root_hex"] == leaf_hash


def test_private_folder_manifest_requires_owner_session(server):
    status, body, _ = _srv.request(server, "/api/verify_folder/Private012345678")
    assert status in (401, 403, 404)
    assert PATH_CANARY not in body.decode()
    status, body, _ = _srv.request(server, "/api/verify_folder/Private012345678",
                                   headers=OWNER_HEADERS)
    assert status == 200, body[:200]
    assert EMAIL not in body.decode()
    assert PATH_CANARY in body.decode()


def _signed_manifest(**extra):
    from server import manifest_signature

    file_hash = "cd" * 32
    leaf_hash = hashlib.sha256(
        b"\x00" + PATH_CANARY.encode() + b"\x00" + bytes.fromhex(file_hash)
    ).hexdigest()
    manifest = {
        "algorithm": "orphograph-merkle-v1-rfc6962", "version": 1,
        "root_hex": leaf_hash,
        "leaves": [{"path": PATH_CANARY, "file_sha256_hex": file_hash,
                    "leaf_hex": leaf_hash, "size_bytes": 7}],
        **extra,
    }
    return manifest_signature.sign_manifest(manifest, bytes(range(32)))


def test_signed_folder_preserves_valid_stored_signature_and_explains_redacted_export(tmp_path):
    from server import manifest_signature

    manifest = _signed_manifest()
    for base in _srv.server_processes(tmp_path, stub_calendars=True):
        status, body, _ = _srv.request(base, "/api/anchor_folder", method="POST",
            body=json.dumps({"manifest": manifest}).encode(),
            headers={"Content-Type": "application/json"}, timeout=60)
        assert status == 200, body[:500]
        rid = json.loads(body)["receipt_id"]
        stored = json.loads((tmp_path / "receipts" / rid / "manifest.json").read_text())
        assert stored["signature"] == manifest["signature"]
        valid, reason = manifest_signature.verify_manifest_signature(stored)
        assert valid, reason
        exported = json.loads(_zip_members(base, "/api/receipt/" + rid + ".zip")["manifest.json"])
        assert "signature" not in exported
        assert exported["signature_unavailable_reason"]
        assert PATH_CANARY not in json.dumps(exported)
        assert exported["root_hex"] == manifest["root_hex"]
        status, body, _ = _srv.request(base, "/api/verify_folder/" + rid)
        assert status == 200, body[:500]
        public_manifest = json.loads(body)["manifest"]
        assert "signature" not in public_manifest
        assert public_manifest["signature_unavailable_reason"]


def test_signed_unknown_fields_rejected_before_receipt_is_persisted(tmp_path):
    from server import manifest_signature

    manifest = _signed_manifest(future_internal=UNKNOWN)
    valid, reason = manifest_signature.verify_manifest_signature(manifest)
    assert valid, reason
    for base in _srv.server_processes(tmp_path, stub_calendars=True):
        before = set((tmp_path / "receipts").glob("*/receipt.json"))
        status, body, _ = _srv.request(base, "/api/anchor_folder", method="POST",
            body=json.dumps({"manifest": manifest}).encode(),
            headers={"Content-Type": "application/json"}, timeout=60)
        assert status == 400, body[:500]
        assert "unsupported fields" in json.loads(body)["error"]
        assert set((tmp_path / "receipts").glob("*/receipt.json")) == before


@pytest.mark.parametrize("prefix", ["/api/receipt/", "/api/verify/"])
@pytest.mark.parametrize("method", ["GET", "HEAD"])
def test_export_zip_never_cacheable(server, prefix, method):
    for rid, headers in [("PubEmail01234567", {}), ("Private012345678", OWNER_HEADERS)]:
        status, body, response_headers = _srv.request(server, prefix + rid + '.zip',
                                                     method=method, headers=headers)
        assert status == 200
        assert response_headers.get('Cache-Control') == 'no-store'
        if method == 'HEAD':
            assert body == b''
