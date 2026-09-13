#!/usr/bin/env python3
"""test_privacy_table_claims.py — README "Privacy properties" table, row by row,
driven through the real HTTP entry point.

The README makes six claims about what touches what (file bytes, SHA-256,
SHA-512 sibling, filename/label, email, IP address). Every one is a promise on
a trust product, so each row here is a test that reads what the server
actually wrote (ledger, receipts, .ots blobs, analytics rows, access log) and
what it actually sent to the calendars — never the source that was supposed
to implement the claim.

Harness: the server runs in-process on a loopback port with a temporary data
directory, calendar submission stubbed to a well-formed pending body, and
proxy headers trusted so the IP rows can inject a synthetic visitor address.
"""
from __future__ import annotations

import io
import json
import os
import re
import sys
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SERVER_DIR = ROOT / "server"
sys.path.insert(0, str(SERVER_DIR))

from conftest import PENDING_BODY  # noqa: E402

HASH = "ab" * 32
SHA512 = "cd" * 64
# TEST-NET addresses (RFC 5737): never routable, never a real visitor.
CDN_IP = "203.0.113.77"
REAL_IP = "198.51.100.23"
XFF_IP = "192.0.2.99"
FULL_IPS = (CDN_IP, REAL_IP, XFF_IP)
IP_HEADERS = {
    "CF-Connecting-IP": CDN_IP,
    "Fly-Client-IP": REAL_IP,
    "X-Forwarded-For": XFF_IP,
}


class _Stack:
    """One in-process server + the calendar submissions it made."""

    def __init__(self, data_dir: Path):
        self.data_dir = data_dir
        self.submitted: list[bytes] = []
        os.environ["ORPHO_DATA_DIR"] = str(data_dir)
        os.environ["HOST"] = "127.0.0.1"
        os.environ["PORT"] = "0"
        os.environ["ORPHO_COOKIE_SECURE"] = "0"
        os.environ["ORPHO_TRUST_PROXY_HEADERS"] = "1"
        # Every server module captures DATA_DIR at import; reload them all so
        # the ledger, receipts and analytics rows land in the temp dir.
        for name, mod in list(sys.modules.items()):
            f = getattr(mod, "__file__", None) or ""
            if f.startswith(str(SERVER_DIR)):
                sys.modules.pop(name, None)
        import engine  # noqa: WPS433

        def recorder(_calendar_url: str, hash_bytes: bytes):
            self.submitted.append(bytes(hash_bytes))
            return True, PENDING_BODY

        engine._submit = recorder
        import app  # noqa: WPS433

        self.engine = engine
        self.app = app
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.stderr = io.StringIO()

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        os.environ.pop("ORPHO_TRUST_PROXY_HEADERS", None)

    def request(self, path: str, method: str = "GET", body: bytes | None = None,
                headers: dict | None = None) -> tuple[int, bytes]:
        req = urllib.request.Request(self.base + path, data=body, method=method,
                                     headers=headers or {})
        real_stderr = sys.stderr
        sys.stderr = self.stderr   # Handler.log_message writes here
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()
        finally:
            sys.stderr = real_stderr

    def anchor_json(self, payload: dict, headers: dict | None = None) -> tuple[int, dict]:
        h = {"Content-Type": "application/json", **(headers or {})}
        code, raw = self.request("/api/anchor", "POST", json.dumps(payload).encode(), h)
        try:
            return code, json.loads(raw or b"{}")
        except ValueError:
            return code, {"_raw": raw.decode("utf-8", "replace")}

    def persisted_bytes(self) -> bytes:
        """Everything the server wrote to disk plus its access log."""
        chunks = [self.stderr.getvalue().encode()]
        for p in sorted(self.data_dir.rglob("*")):
            if p.is_file():
                chunks.append(p.read_bytes())
        return b"\n".join(chunks)


@pytest.fixture
def stack(tmp_path):
    s = _Stack(tmp_path)
    try:
        yield s
    finally:
        s.close()


# ── Row 1: File bytes — stays on your machine · sent to server: never ────────

def test_row1_file_bytes_never_accepted_by_the_anchor_endpoint(stack):
    """A multipart upload with a file part is refused and writes no receipt.
    The server has no file intake at all: the only anchor input is a digest."""
    boundary = "----orphograph-claims"
    body = (f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="file"; filename="secret.txt"\r\n'
            "Content-Type: text/plain\r\n\r\n"
            "THE-FILE-BODY-MUST-NEVER-ARRIVE\r\n"
            f"--{boundary}--\r\n").encode()
    code, raw = stack.request("/api/anchor", "POST", body,
                              {"Content-Type": f"multipart/form-data; boundary={boundary}"})
    assert code not in (200, 201), (code, raw[:200])
    receipts = stack.data_dir / "receipts"
    assert not receipts.exists() or not list(receipts.rglob("*")), "a receipt was written for a file upload"
    assert b"THE-FILE-BODY-MUST-NEVER-ARRIVE" not in stack.persisted_bytes()
    assert stack.submitted == []


def test_row1_live_homepage_script_sends_a_digest_not_the_file():
    """Textual signal on the shipped client: the homepage anchor request is
    JSON carrying hashes; no FormData/file field exists in the script."""
    index = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
    assert re.search(r'<script src="/v2\.js\?v=\d+"', index), "homepage no longer loads v2.js — re-target this test"
    assert "app.js" not in index, "legacy app.js is back on the homepage — re-target this test"
    v2 = (ROOT / "web" / "v2.js").read_text(encoding="utf-8")
    assert "FormData" not in v2
    anchor_call = v2[v2.index('fetch("/api/anchor"'):]
    anchor_call = anchor_call[:anchor_call.index("});") + 3]
    assert '"Content-Type": "application/json"' in anchor_call
    assert "hash_hex: sha256" in anchor_call and "sha512_hex: sha512" in anchor_call
    assert "body: JSON.stringify(" in anchor_call


# ── Rows 2 + 3: SHA-256 is the only thing on-chain; SHA-512 stored, never anchored

def test_row2_row3_only_sha256_reaches_the_calendars_sha512_stays_in_the_receipt(stack):
    code, rec = stack.anchor_json({"hash_hex": HASH, "sha512_hex": SHA512})
    assert code in (200, 201), (code, rec)
    rid = rec["receipt_id"]
    # Row 2: every calendar submission is exactly the 32-byte SHA-256.
    assert stack.submitted, "no calendar submission was recorded"
    assert len(stack.submitted) == len(stack.engine.CALENDARS)
    assert all(h == bytes.fromhex(HASH) for h in stack.submitted)
    # Row 3: the sibling is stored in the receipt …
    code, raw = stack.request(f"/api/receipt/{rid}")
    assert code == 200
    assert json.loads(raw)["sha512_hex"] == SHA512
    # … and never anchored: no submission carried it, and no proof commits to it.
    sha512_bytes = bytes.fromhex(SHA512)
    assert all(sha512_bytes not in h and h != sha512_bytes[:32] for h in stack.submitted)
    ots_blobs = list((stack.data_dir / "receipts" / rid).glob("*.ots"))
    assert ots_blobs, "no .ots proof was written"
    for blob in ots_blobs:
        data = blob.read_bytes()
        assert data == stack.engine._build_ots(bytes.fromhex(HASH), PENDING_BODY)
        assert sha512_bytes not in data


# ── Row 4: Filename / label — opt-in, off by default ───────────────────────────

def test_row4_no_label_unless_the_client_sends_one(stack):
    code, rec = stack.anchor_json({"hash_hex": HASH})
    assert code in (200, 201), (code, rec)
    code, raw = stack.request(f"/api/receipt/{rec['receipt_id']}")
    assert json.loads(raw).get("client_label") in (None, "")


def test_row4_live_homepage_never_puts_the_filename_on_the_wire():
    """Textual signal: the homepage tags anchors with a constant source label
    and never assigns the browser File name into the request body."""
    v2 = (ROOT / "web" / "v2.js").read_text(encoding="utf-8")
    assert re.search(r'client_label:\s*"v2-homepage"', v2)
    assert not re.search(r"client_label:\s*file\.name", v2)


# ── Row 5: Email — only when needed ─────────────────────────────────────────────

def test_row5_anchoring_needs_no_email(stack):
    payload = {"hash_hex": HASH, "sha512_hex": SHA512}
    assert "email" not in json.dumps(payload)
    code, rec = stack.anchor_json(payload)
    assert code in (200, 201), (code, rec)
    assert rec.get("receipt_id")


# ── Row 6: IP address — truncated to /24 or /48; full IPs never persisted ──────

def test_row6_full_visitor_ip_is_never_persisted_truncated_form_is(stack):
    """Inject a synthetic visitor through every header the server honours,
    exercise the writers (anchor → ledger + receipt; event → analytics row;
    both → access log), then read back everything on disk and in the log."""
    code, rec = stack.anchor_json({"hash_hex": HASH}, headers=IP_HEADERS)
    assert code in (200, 201), (code, rec)
    code, _ = stack.request("/api/event", "POST",
                            json.dumps({"event": "page_view", "page": "/"}).encode(),
                            {"Content-Type": "application/json", **IP_HEADERS})
    assert code in (200, 201, 202, 204), code
    persisted = stack.persisted_bytes()
    # Positive controls: the write paths ran, and a truncated form was recorded.
    assert HASH.encode() in persisted, "anchor was not persisted — the check would be vacuous"
    assert b"203.0.113.0/24" in persisted, "no truncated visitor IP was recorded — the check would be vacuous"
    # The claim: no full IP survives anywhere the server writes.
    for ip in FULL_IPS:
        assert ip.encode() not in persisted, f"full IP {ip} was persisted"


def test_row6_truncation_shapes():
    from rate_limit import truncate_ip
    assert truncate_ip("203.0.113.77") == "203.0.113.0/24"
    assert truncate_ip("2001:db8:85a3:8d3:1319:8a2e:370:7348") == "2001:db8:85a3::/48"
    assert truncate_ip("") == ""
