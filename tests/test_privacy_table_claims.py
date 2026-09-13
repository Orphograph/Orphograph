#!/usr/bin/env python3
"""test_privacy_table_claims.py — README "Privacy properties" table, row by row,
driven through the real HTTP entry point.

The README makes six claims about what touches what (file bytes, SHA-256,
SHA-512 sibling, filename/label, email, IP address). Every one is a promise on
a trust product, so each row here reads what the server actually wrote
(ledger, receipts, .ots blobs, analytics rows, server log) — never the source
that was supposed to implement the claim.

Harness: the shared subprocess helper (tests/_srv.py) with calendar submission
stubbed to a well-formed pending body and proxy headers trusted, so the IP row
can inject a synthetic visitor address through every header the server
honours. The server's own stdout/stderr land in `server-<port>.log` inside the
data dir, so the readback covers the access log too and a handler traceback is
never thrown away.

What the static checks on web/v2.js are: textual signals on the shipped
client, scoped to the anchor request slice. They are named as such and are
weaker than the wire checks; the wire is the evidence.
"""
from __future__ import annotations

import json
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "server"))
sys.path.insert(0, str(ROOT / "tests"))

import _srv  # noqa: E402
from conftest import PENDING_BODY  # noqa: E402

HASH = "ab" * 32
SHA512 = "cd" * 64
# TEST-NET addresses (RFC 5737 / RFC 3849): never routable, never a real visitor.
CDN_IP = "203.0.113.77"
REAL_IP = "198.51.100.23"
XFF_IP = "192.0.2.99"
XFF_IP_WITH_PORT = "192.0.2.99:1234"
V6_IP = "2001:db8:85a3:8d3:1319:8a2e:370:7348"


@pytest.fixture(scope="module")
def data_dir(tmp_path_factory):
    return tmp_path_factory.mktemp("privacy-table")


@pytest.fixture(scope="module")
def server(data_dir):
    yield from _srv.server_processes(data_dir, stub_calendars=True,
                                     ORPHO_TRUST_PROXY_HEADERS="1")


def _request(base: str, path: str, method: str = "GET", body: bytes | None = None,
             headers: dict | None = None) -> tuple[int, bytes]:
    req = urllib.request.Request(base + path, data=body, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def _anchor(base: str, payload: dict, headers: dict | None = None) -> tuple[int, dict]:
    h = {"Content-Type": "application/json", **(headers or {})}
    code, raw = _request(base, "/api/anchor", "POST", json.dumps(payload).encode(), h)
    try:
        return code, json.loads(raw or b"{}")
    except ValueError:
        return code, {"_raw": raw.decode("utf-8", "replace")}


def _receipt(base: str, rid: str) -> dict:
    code, raw = _request(base, f"/api/receipt/{rid}")
    assert code == 200, (code, raw[:200])
    return json.loads(raw)


def _persisted(data_dir: Path) -> bytes:
    """Everything the server wrote: data files AND its own log (server-<port>.log)."""
    return b"\n".join(p.read_bytes() for p in sorted(data_dir.rglob("*")) if p.is_file())


def _ledger_lines(data_dir: Path) -> list[str]:
    p = data_dir / "ledger.jsonl"
    return p.read_text().splitlines() if p.exists() else []


def _anchor_slice(v2: str) -> str:
    """The homepage anchor request: from the fetch call to the end of its options object."""
    start = v2.index('fetch("/api/anchor"')
    return v2[start:v2.index("});", start) + 3]


# ── Row 1: File bytes — stays on your machine · sent to server: never ────────

def test_row1_a_file_part_is_refused_and_persists_nothing(server, data_dir):
    """What this proves: a multipart body carrying a file part is rejected
    before dispatch and leaves no ledger line, no receipt, no log echo of the
    body. What it does NOT prove: that JSON fields are size-bounded (they are
    bounded elsewhere: attestation 500 chars/field, metadata 200 chars/key)."""
    before_ledger = len(_ledger_lines(data_dir))
    before_receipts = sorted(str(p) for p in (data_dir / "receipts").rglob("*")) \
        if (data_dir / "receipts").exists() else []
    boundary = "----orphograph-claims"
    body = (f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="file"; filename="secret.txt"\r\n'
            "Content-Type: text/plain\r\n\r\n"
            "THE-FILE-BODY-MUST-NEVER-ARRIVE\r\n"
            f"--{boundary}--\r\n").encode()
    code, raw = _request(server, "/api/anchor", "POST", body,
                         {"Content-Type": f"multipart/form-data; boundary={boundary}"})
    assert code not in (200, 201), (code, raw[:200])
    after_receipts = sorted(str(p) for p in (data_dir / "receipts").rglob("*")) \
        if (data_dir / "receipts").exists() else []
    assert after_receipts == before_receipts, "a receipt was written for a file upload"
    assert len(_ledger_lines(data_dir)) == before_ledger, "a ledger line was written for a file upload"
    assert b"THE-FILE-BODY-MUST-NEVER-ARRIVE" not in _persisted(data_dir)


def test_row1_textual_homepage_anchor_request_is_json_hashes_only():
    index = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
    assert re.search(r'<script src="/v2\.js\?v=\d+"', index), "homepage no longer loads v2.js — re-target this test"
    assert "app.js" not in index, "legacy app.js is back on the homepage — re-target this test"
    call = _anchor_slice((ROOT / "web" / "v2.js").read_text(encoding="utf-8"))
    assert "FormData" not in call and "Blob" not in call
    assert '"Content-Type": "application/json"' in call
    assert "body: JSON.stringify(" in call
    assert "hash_hex: sha256" in call and "sha512_hex: sha512" in call
    # No File object property crosses into the body: no `.name`, `.size`,
    # `.type`, `.lastModified`, and no metadata/attestation object at all.
    assert not re.search(r"\.(name|size|type|lastModified)\b", call), call
    assert "metadata" not in call and "attestation" not in call


# ── Rows 2 + 3: SHA-256 is the only thing on-chain; SHA-512 stored, never anchored

def test_row2_row3_proof_commits_to_sha256_only_sha512_lives_in_the_receipt(server, data_dir):
    """The stubbed calendar (tests/_run_server.py) accepts only a 32-byte
    submission and returns one fixed pending body; every .ots on disk is
    therefore `_build_ots(<what was submitted>, PENDING_BODY)`. Equality with
    the SHA-256 form proves the submission was the SHA-256 and nothing else."""
    import engine  # noqa: WPS433 — for _build_ots / CALENDARS only; no server state
    code, rec = _anchor(server, {"hash_hex": HASH, "sha512_hex": SHA512})
    assert code in (200, 201), (code, rec)
    rid = rec["receipt_id"]
    stored = _receipt(server, rid)
    assert stored["sha512_hex"] == SHA512                       # row 3: stored …
    sha512_bytes = bytes.fromhex(SHA512)
    blobs = list((data_dir / "receipts" / rid).glob("*.ots"))
    assert len(blobs) == len(engine.CALENDARS), "one proof per calendar expected"
    for blob in blobs:
        data = blob.read_bytes()
        assert data == engine._build_ots(bytes.fromhex(HASH), PENDING_BODY)   # row 2
        assert sha512_bytes not in data and sha512_bytes[:32] not in data    # … never anchored


# ── Row 4: Filename / label — opt-in, off by default ───────────────────────────

def test_row4_no_label_and_no_filename_unless_the_client_sends_them(server):
    code, rec = _anchor(server, {"hash_hex": HASH})
    assert code in (200, 201), (code, rec)
    stored = _receipt(server, rec["receipt_id"])
    assert stored.get("client_label") in (None, "")
    meta = stored.get("metadata") or {}
    assert "filename" not in meta and not any("name" in k for k in meta), meta


def test_row4_textual_homepage_sends_a_constant_tag_never_the_filename():
    """The web client tags anchors with a fixed source name. The README's
    `--label` opt-in is the CLI/MCP path; the homepage never puts the browser
    File's name (or any other File property) on the wire — see the row-1
    textual check for the property scan of the same slice."""
    call = _anchor_slice((ROOT / "web" / "v2.js").read_text(encoding="utf-8"))
    assert re.search(r'client_label:\s*"v2-homepage"', call)
    assert "file" not in call.replace("hashFile", ""), call


# ── Row 5: Email — only when needed ─────────────────────────────────────────────

def test_row5_a_free_anchor_records_no_identity(server, data_dir):
    code, rec = _anchor(server, {"hash_hex": HASH, "sha512_hex": SHA512})
    assert code in (200, 201), (code, rec)
    rid = rec["receipt_id"]
    stored = _receipt(server, rid)
    assert stored.get("owner_id") in (None, "")
    assert "email" not in json.dumps(stored).lower()
    row = next(json.loads(l) for l in _ledger_lines(data_dir) if rid in l)
    assert row.get("owner_id") in (None, "")
    assert not any("email" in k.lower() for k in row), sorted(row)
    assert not str(row.get("source", "")).startswith("sub:"), row.get("source")


# ── Row 6: IP address — truncated to /24 or /48; full IPs never persisted ──────

@pytest.mark.parametrize("header,value,expect_trunc", [
    ("CF-Connecting-IP", CDN_IP, "203.0.113.0/24"),
    ("X-Forwarded-For", XFF_IP, "192.0.2.0/24"),
    ("X-Forwarded-For", XFF_IP_WITH_PORT, "192.0.2.0/24"),   # host:port from a proxy
    ("CF-Connecting-IP", V6_IP, "2001:db8:85a3::/48"),
])
def test_row6_each_honoured_header_is_persisted_truncated_never_in_full(
        server, data_dir, header, value, expect_trunc):
    """One header per request so each source branch of `_resolve_analytics_ip`
    is the one that answers; the /api/event row is the positive control that
    the branch ran and wrote a truncated form."""
    full = value.split("]")[0].lstrip("[").rsplit(":", 1)[0] if value.count(":") == 1 else value
    before = _persisted(data_dir)
    assert full.encode() not in before, "fixture already contaminated — re-check earlier rows"
    hdr = {header: value}
    code, rec = _anchor(server, {"hash_hex": HASH}, headers=hdr)
    assert code in (200, 201), (code, rec)
    code, _ = _request(server, "/api/event", "POST",
                       json.dumps({"event": "page_view", "page": "/"}).encode(),
                       {"Content-Type": "application/json", **hdr})
    assert code in (200, 201, 202, 204), code
    persisted = _persisted(data_dir)
    assert HASH.encode() in persisted, "anchor was not persisted — the check would be vacuous"
    assert expect_trunc.encode() in persisted, f"{header}={value}: no truncated form recorded — vacuous"
    assert full.encode() not in persisted, f"full IP {full} was persisted (header {header})"


def test_row6_platform_real_ip_header_is_never_persisted_in_full(server, data_dir):
    """Fly-Client-IP feeds rate-limit bucketing (in-memory, snapshot on an
    interval) and no analytics row, so there is no deterministic truncated
    form to positive-control here. The claim tested is only the negative:
    the full address is nowhere the server writes."""
    hdr = {"Fly-Client-IP": REAL_IP}
    code, rec = _anchor(server, {"hash_hex": HASH}, headers=hdr)
    assert code in (200, 201, 429), (code, rec)
    assert REAL_IP.encode() not in _persisted(data_dir)


def test_row6_truncation_shapes():
    from rate_limit import truncate_ip
    assert truncate_ip("203.0.113.77") == "203.0.113.0/24"
    assert truncate_ip("203.0.113.77:1234") == "203.0.113.0/24"          # host:port
    assert truncate_ip(V6_IP) == "2001:db8:85a3::/48"
    assert truncate_ip("2001::1") == "2001::/48"                          # compressed
    assert truncate_ip("[2001:db8::1]:443") == "2001:db8::/48"           # bracketed host:port
    assert truncate_ip("") == ""
    assert truncate_ip("not-an-ip") == "unknown"
    # Nothing below the truncation boundary survives, ever.
    assert "77" not in truncate_ip("203.0.113.77:1234")
    assert "7348" not in truncate_ip(V6_IP)
