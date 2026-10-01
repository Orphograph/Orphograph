"""The public Standing Record lists only anchors the office itself made.

Found 2026-09-28: /api/standing-record listed ANY public receipt whose
client_label started with "weekly-", so anyone could anchor with that label
and appear on the page as if the office had published it. Founder decision
(option A): a row is listed only when it carries the office's Ed25519
signature over its own label and hash, checked against a pinned office key
every time the list is built, or when its receipt id is in the closed list of
rows the weekly job made before it signed anything.

Everything is set up before the first GET, because the listing is cached for
300 s inside the server process. The server runs with `cryptography` hidden,
so it verifies with the stdlib Ed25519 code that production (python:3.11-slim,
nothing installed) actually runs.
"""
from __future__ import annotations

import hashlib
import json
import sys
import time
from pathlib import Path

import pytest

import _srv

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "server"))
sys.path.insert(0, str(ROOT / "scripts"))
import manifest_signature  # noqa: E402
import standing_record  # noqa: E402
import weekly_anchor  # noqa: E402

# Throwaway keys. The office test key reaches the server through
# ORPHO_OFFICE_PUBLIC_KEYS; the stranger key is trusted nowhere.
OFFICE_SEED = hashlib.sha256(b"standing-record test office key, never pinned").digest()
STRANGER_SEED = hashlib.sha256(b"standing-record test stranger key").digest()

# Two ids from the closed historical list in server/standing_record.py (the
# weekly job's own log). Planted here as receipts in a temp data dir only.
ALLOWLISTED_PUBLIC_ID = "xgHsOc463fnB74Yu"
ALLOWLISTED_PRIVATE_ID = "oRfnPlcYH3pI95UG"


def _statement(label: str, hash_hex: str) -> bytes:
    # Written out here rather than imported, so a change to the server's
    # statement format breaks this test instead of moving with it.
    return (b"orphograph-standing-record-v1\n" + label.encode("utf-8")
            + b"\n" + hash_hex.encode("ascii"))


def _sign(seed: bytes, label: str, hash_hex: str) -> str:
    sig, _pk = manifest_signature._sign_raw(_statement(label, hash_hex), seed)
    return sig.hex()


def _public_hex(seed: bytes) -> str:
    _sig, pk = manifest_signature._sign_raw(b"", seed)
    return pk.hex()


def _manifest(tag: str) -> dict:
    leaves = [{"path": f"{tag}/{i}.txt",
               "file_sha256_hex": hashlib.sha256(f"{tag}-{i}".encode()).hexdigest(),
               "size_bytes": 10 + i} for i in range(3)]
    return weekly_anchor.build_manifest(leaves)


def _hash(tag: str) -> str:
    return hashlib.sha256(tag.encode()).hexdigest()


def _plant(receipts: Path, rid: str, label: str, hash_hex: str, created_at: str,
           *, private: bool = False, **extra) -> None:
    """A receipt on disk, as an earlier server (or a tampered volume) left it."""
    d = receipts / rid
    d.mkdir(parents=True)
    rec = {
        "receipt_id": rid, "created_at": created_at, "hash_hex": hash_hex,
        "sha512_hex": None, "client_label": label, "source": "free",
        "private": private, "owner_id": None, "attestation": None,
        "c2pa_manifest_hash": None, "metadata": None, "calendars_ok": 0,
        "calendars_total": 5, "successes": [], "failures": [], **extra,
    }
    (d / "receipt.json").write_text(json.dumps(rec, indent=2))


def _folder(base: str, manifest: dict, label: str, signature: str | None = None):
    body = {"manifest": manifest, "client_label": label}
    if signature is not None:
        body["office_signature"] = signature
    return _srv.post_json(base, "/api/anchor_folder", body)


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    data = tmp_path_factory.mktemp("standing-record")
    receipts = data / "receipts"
    receipts.mkdir()

    # (d) a row the job made before signing existed: listed, unsigned.
    _plant(receipts, ALLOWLISTED_PUBLIC_ID, "weekly-2026-09-27-17-artifacts",
           _hash("allowlisted"), "2026-09-27T07:32:40+00:00")
    # (f) private rows, never listed: one office-signed, one allowlisted.
    label_f = "weekly-2026-09-13-17-artifacts"
    _plant(receipts, "plantPrivateSign", label_f, _hash("private-signed"),
           "2026-09-13T07:30:01+00:00", private=True,
           office_signature=_sign(OFFICE_SEED, label_f, _hash("private-signed")))
    _plant(receipts, ALLOWLISTED_PRIVATE_ID, "weekly-2026-09-20-17-artifacts",
           _hash("private-allowlisted"), "2026-09-20T07:30:00+00:00", private=True)
    # (b) a stored signature by another key, next to stored "verified" flags
    # the listing must not believe.
    label_b = "weekly-2026-09-06-17-artifacts"
    _plant(receipts, "plantOtherKey000", label_b, _hash("other-key"),
           "2026-09-06T07:39:46+00:00",
           office_signature=_sign(STRANGER_SEED, label_b, _hash("other-key")),
           signature_verified=True, office_signature_verified=True)
    # (g) the office's signature copied onto a label, or a hash, one
    # character away from what it signed.
    label_g = "weekly-2026-08-30-17-artifacts"
    _plant(receipts, "plantLabelOffBy1", label_g[:-1] + "x", _hash("label-off"),
           "2026-08-30T09:00:15+00:00",
           office_signature=_sign(OFFICE_SEED, label_g, _hash("label-off")))
    hash_g = _hash("hash-off")
    hash_g_off = hash_g[:-1] + ("0" if hash_g[-1] != "0" else "1")
    _plant(receipts, "plantHashOffBy1", label_g, hash_g_off,
           "2026-08-30T09:00:16+00:00",
           office_signature=_sign(OFFICE_SEED, label_g, hash_g))

    # A stand-in `cryptography` that refuses to import, ahead of the real one.
    # It leaves a mark so the test can tell the server really ran without it.
    shim = data / "no-cryptography"
    (shim / "cryptography").mkdir(parents=True)
    (shim / "cryptography" / "__init__.py").write_text(
        "import os\n"
        "open(os.path.join(os.environ['ORPHO_DATA_DIR'], 'cryptography-refused'), 'a').close()\n"
        "raise ImportError('hidden by the test: run the stdlib Ed25519 code')\n")

    out: dict = {"data": data}
    for base in _srv.server_processes(
            data, stub_calendars=True,
            ORPHO_OFFICE_PUBLIC_KEYS=_public_hex(OFFICE_SEED),
            PYTHONPATH=str(shim)):
        # (a) a stranger borrows the label, on both anchoring routes.
        code, rec = _srv.anchor(base, {"hash_hex": _hash("stranger-single"),
                                       "client_label": "weekly-2026-10-04"})
        out["stranger_single"] = _srv.ok_json(code, rec)["receipt_id"]
        code, rec = _folder(base, _manifest("stranger"), "weekly-2026-10-04-17-artifacts-")
        out["stranger_folder"] = _srv.ok_json(code, rec)["receipt_id"]
        # (b) over the wire: a signature by a key the office does not hold.
        m_b = _manifest("other-key-wire")
        out["other_key"] = _folder(base, m_b, "weekly-2026-10-04-other",
                                   _sign(STRANGER_SEED, "weekly-2026-10-04-other",
                                         m_b["root_hex"]))
        # (c) the office's own anchor.
        m_c = _manifest("office")
        label_c = "weekly-2026-10-04-17-artifacts"
        sig_c = _sign(OFFICE_SEED, label_c, m_c["root_hex"])
        code, rec = _folder(base, m_c, label_c, sig_c)
        out["office"] = _srv.ok_json(code, rec)
        out["office_sig"] = sig_c
        out["office_label"] = label_c
        # (e) a stranger re-anchors the same statement with the copied
        # signature, a full second later so created_at differs.
        time.sleep(1.1)
        code, rec = _folder(base, m_c, label_c, sig_c)
        out["replay"] = _srv.ok_json(code, rec)
        # (g) over the wire: the copied signature on a label one character off.
        out["label_off"] = _folder(base, m_c, label_c[:-1] + "z", sig_c)

        code, listing = _srv.get_json(base, "/api/standing-record")
        out["rows"] = _srv.ok_json(code, listing)["anchors"]
        code, out["office_summary"] = _srv.get_json(
            base, f"/api/receipt/{out['office']['receipt_id']}/summary")
        out["summary_code"] = code
    return out


def _ids(world) -> set:
    return {r["receipt_id"] for r in world["rows"]}


def test_a_stranger_with_a_weekly_label_is_not_listed(world):
    assert world["stranger_single"] not in _ids(world)
    assert world["stranger_folder"] not in _ids(world)


def test_b_another_keys_signature_is_refused_and_never_listed(world):
    code, rec = world["other_key"]
    assert code == 400, (code, rec)
    assert "office_signature" in rec.get("error", ""), rec
    # Stored on disk with "verified" flags beside it: still not listed.
    assert "plantOtherKey000" not in _ids(world)


def test_c_an_office_signed_row_is_listed(world):
    assert world["office"].get("office_signed") is True, world["office"]
    assert world["office"]["receipt_id"] in _ids(world)


def test_d_an_allowlisted_row_without_a_signature_is_listed(world):
    assert ALLOWLISTED_PUBLIC_ID in _ids(world)


def test_e_a_replayed_statement_yields_one_row_the_earliest(world):
    assert world["replay"].get("office_signed") is True
    same = [r for r in world["rows"] if r["client_label"] == world["office_label"]]
    assert [r["receipt_id"] for r in same] == [world["office"]["receipt_id"]]


def test_f_a_private_receipt_is_never_listed(world):
    assert "plantPrivateSign" not in _ids(world)
    assert ALLOWLISTED_PRIVATE_ID not in _ids(world)


def test_g_one_character_off_does_not_verify(world):
    code, rec = world["label_off"]
    assert code == 400, (code, rec)
    assert "plantLabelOffBy1" not in _ids(world)
    assert "plantHashOffBy1" not in _ids(world)


def test_the_listing_is_exactly_the_office_rows_newest_first(world):
    assert [r["receipt_id"] for r in world["rows"]] == [
        world["office"]["receipt_id"], ALLOWLISTED_PUBLIC_ID]
    # The public row shape did not change.
    assert all(set(r) == {"receipt_id", "client_label", "created_at", "btc_pinned_at"}
               for r in world["rows"])


def test_the_public_export_carries_the_office_signature(world):
    # Evidence, not a secret: anyone can check it against the pinned key.
    assert world["summary_code"] == 200, world["office_summary"]
    assert world["office_summary"].get("office_signature") == world["office_sig"]


def test_the_server_ran_the_stdlib_ed25519_code(world):
    # The listing above was verified without `cryptography`, as in production.
    assert (world["data"] / "cryptography-refused").exists()


# ---- the listing rule without a server ------------------------------------------


def _signed_rec(i: int, seed: bytes = OFFICE_SEED, *, sig: str | None = None,
                label: str | None = None) -> dict:
    label = label or f"weekly-2026-10-{i:02d}-17-artifacts"
    h = _hash(f"row-{i}")
    return {"receipt_id": f"unit{i:012d}", "created_at": f"2026-10-04T07:{i // 60:02d}:{i % 60:02d}+00:00",
            "hash_hex": h, "client_label": label, "private": False,
            "office_signature": sig or _sign(seed, label, h)}


@pytest.fixture
def counted(monkeypatch):
    calls = {"n": 0}
    real = manifest_signature._verify_raw

    def counting(msg, sig, key):
        calls["n"] += 1
        return real(msg, sig, key)

    monkeypatch.setattr(manifest_signature, "_verify_raw", counting)
    return calls


def test_pins_are_well_formed_and_the_historical_list_is_closed():
    assert standing_record.PINNED_OFFICE_KEYS
    assert len(standing_record._pinned()) == len(standing_record.PINNED_OFFICE_KEYS)
    # 27 ids, from the job's own log. A change here means the closed list was
    # reopened, which the design forbids: new rows must be signed.
    assert len(standing_record.HISTORICAL_RECEIPT_IDS) == 27
    assert {ALLOWLISTED_PUBLIC_ID, ALLOWLISTED_PRIVATE_ID} <= standing_record.HISTORICAL_RECEIPT_IDS


def test_a_flood_of_forged_rows_costs_at_most_the_cap(counted):
    key = bytes.fromhex(_public_hex(OFFICE_SEED))
    junk = [_signed_rec(i, sig=_sign(STRANGER_SEED, f"weekly-2026-10-{i:02d}-17-artifacts",
                                     _hash(f"row-{i}"))) for i in range(200)]
    assert standing_record.listed(junk, keys=(key,)) == []
    assert counted["n"] == standing_record.MAX_SIGNATURE_CHECKS


def test_copies_of_one_signature_cost_one_check(counted):
    key = bytes.fromhex(_public_hex(OFFICE_SEED))
    original = _signed_rec(0, label="weekly-2026-10-04-17-artifacts")
    copies = [{**original, "receipt_id": f"copy{i:012d}",
               "created_at": f"2026-10-05T00:00:{i:02d}+00:00"} for i in range(50)]
    rows = standing_record.listed(copies + [original], keys=(key,))
    assert [r["receipt_id"] for r in rows] == [original["receipt_id"]]
    assert counted["n"] == 1


def test_a_normal_listing_checks_one_signature_per_row_shown(counted):
    key = bytes.fromhex(_public_hex(OFFICE_SEED))
    rows = standing_record.listed([_signed_rec(i) for i in range(30)], keys=(key,))
    assert len(rows) == 16
    assert rows[0]["receipt_id"] == "unit000000000029"
    assert counted["n"] == 16
