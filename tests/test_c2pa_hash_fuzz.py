"""test_c2pa_hash_fuzz.py

Nothing but a canonical 64-hex digest may reach the c2pa field of a receipt
(audit 2026-08-25, backlog item A: "C2PA manifest handling — find the manifest
parser; fuzz a malformed manifest; confirm no parser-level RCE/path-traversal
and that a bad manifest is rejected, not trusted").

THE ITEM'S PREMISE WAS WRONG, and that is the finding. Orphograph parses no
C2PA manifest anywhere. It accepts `c2pa_manifest_hash` — a hash the CLIENT
computes over the manifest — and the only mention of JUMBF in the tree is a
comment in engine.py saying exactly that. No parser means no parser-level RCE
and no path traversal: the surface the item assumed does not exist.

What DOES exist is one caller-supplied string that lands in a stored receipt
and in the JSON-LD vault export (app.py builds `node["c2paManifestHash"]` from
it). So the real question is whether anything but canonical hex can get there.
It cannot: engine.anchor_hash lowercases, strips, and requires _is_hex(...,64),
raising otherwise.

This pins that empirically, through the real HTTP entry point.

A NOTE ON WHY THE RATE-LIMIT ASSERTION IS HERE. The first version of this fuzz
ran against a default-configured server and was VACUOUS: after three requests
every subsequent vector came back 429, and a 429 is the rate limiter's verdict,
not the validator's. Sixteen vectors were reported "rejected" without the
validator ever seeing them. So the suite raises the limit AND asserts no 429
was observed — the rejection branch must be reached before it can be trusted.
"""
from __future__ import annotations

import hashlib
import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

# (label, value, must_be_rejected_with_400)
# False = accepted by the wire but the field must NOT be stored (type confusion
# is dropped, matching how hardware_attestation and zk_proof treat wrong types).
VECTORS = [
    ("path_traversal", "../../../../etc/passwd", True),
    ("abs_path", "/etc/passwd", True),
    ("null_byte", "a" * 63 + "\x00", True),
    ("short_63", "a" * 63, True),
    ("long_65", "a" * 65, True),
    ("non_hex", "z" * 64, True),
    ("json_injection", '"}]},"evil":"' + "a" * 40, True),
    ("xss", "<script>alert(1)</script>" + "a" * 39, True),
    ("sql_ish", "' OR 1=1 --" + "a" * 53, True),
    ("rtl_override", "‮" + "a" * 63, True),
    ("newline", "a" * 32 + "\n" + "a" * 31, True),
    ("all_spaces", " " * 64, True),
    ("huge_100k", "a" * 100_000, True),
    # 2026-08-25: wrong TYPE now 400s too. It used to be accepted with the
    # field silently dropped, so a client sending c2pa_manifest_hash: 12345 got
    # a 200 and a receipt with no binding and never learned. JSON null still
    # means "not supplied" and is accepted.
    ("type_int", 12345, True),
    ("type_list", ["a" * 64], True),
    ("type_dict", {"h": "a" * 64}, True),
    ("type_bool", True, True),
    ("type_null", None, False),
]


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    data_dir = tmp_path_factory.mktemp("c2pa_fuzz_data")
    port = _free_port()
    env = {
        **os.environ,
        "PORT": str(port), "HOST": "127.0.0.1",
        "ORPHO_DATA_DIR": str(data_dir), "ORPHO_COOKIE_SECURE": "0",
        # Without this the fuzz is vacuous — see the module docstring.
        "RATE_LIMIT_PER_DAY": "100000",
        "ORPHO_OFFLINE_CALENDARS": "1",
    }
    env.pop("RESEND_API_KEY", None)
    proc = subprocess.Popen(
        [sys.executable, str(REPO_ROOT / "server" / "app.py")],
        env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    base = f"http://127.0.0.1:{port}"
    deadline = time.time() + 45
    while time.time() < deadline:
        try:
            urllib.request.urlopen(base + "/api/health", timeout=1).read()
            break
        except Exception:
            time.sleep(0.2)
    else:
        proc.kill()
        pytest.fail("server did not start")
    yield base
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()


def _anchor(base: str, tag: str, c2pa):
    body = {"hash_hex": hashlib.sha256(tag.encode()).hexdigest(),
            "c2pa_manifest_hash": c2pa}
    req = urllib.request.Request(
        base + "/api/anchor", data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, None


def _stored_c2pa(base: str, receipt_id: str):
    with urllib.request.urlopen(f"{base}/api/receipt/{receipt_id}", timeout=20) as r:
        return json.loads(r.read()).get("c2pa_manifest_hash")


@pytest.mark.parametrize("tag,value,expect_400", VECTORS, ids=[v[0] for v in VECTORS])
def test_hostile_c2pa_value_never_reaches_a_receipt(server, tag, value, expect_400):
    status, body = _anchor(server, tag, value)
    assert status != 429, (
        f"{tag} was rate-limited, not validated — this assertion would be "
        "vacuous. Raise RATE_LIMIT_PER_DAY in the fixture."
    )
    if expect_400:
        assert status == 400, f"{tag} was NOT rejected (got {status})"
        return
    # Only JSON null reaches here: absent-or-null means "not supplied", so the
    # anchor succeeds and the field is simply absent from the receipt.
    assert status == 200, f"{tag} got {status}"
    assert _stored_c2pa(server, body["receipt_id"]) is None, (
        f"{tag} was STORED despite being null"
    )


def test_a_real_manifest_hash_is_accepted_and_preserved(server):
    """POSITIVE CONTROL. Without this the suite would pass for a server that
    rejects every c2pa value, which is not the behaviour we want to pin."""
    good = hashlib.sha256(b"a real c2pa manifest").hexdigest()
    status, body = _anchor(server, "positive-control", good)
    assert status == 200, status
    assert _stored_c2pa(server, body["receipt_id"]) == good


def test_uppercase_hex_is_normalized_not_rejected(server):
    """The engine lowercases before validating, so an uppercase digest is
    canonicalised rather than refused. Pinned so a future 'stricter' change
    cannot silently start rejecting valid input."""
    good = hashlib.sha256(b"uppercase case").hexdigest()
    status, body = _anchor(server, "uppercase", good.upper())
    assert status == 200, status
    assert _stored_c2pa(server, body["receipt_id"]) == good


def test_no_c2pa_manifest_parser_exists():
    """The item asked us to fuzz a parser. There is none, and that is a
    security PROPERTY worth pinning: the office never ingests manifest bytes,
    only a digest of them. If a real parser is ever added, this fails and the
    RCE / path-traversal review the item asked for becomes genuinely required."""
    import re
    hits = []
    for d in ("server", "capture", "mcp", "web/mcp"):
        root = REPO_ROOT / d
        if not root.exists():
            continue
        for py in root.rglob("*.py"):
            src = py.read_text(encoding="utf-8", errors="replace")
            # Strip comments/docstrings-ish: only flag real parsing calls.
            for m in re.finditer(
                r"^\s*(?!#).*\b(jumbf|c2pa)\w*\s*\.\s*(parse|read|load|open)\b",
                src, re.I | re.M,
            ):
                hits.append(f"{py.relative_to(REPO_ROOT)}: {m.group(0).strip()[:80]}")
    assert not hits, (
        "Something now parses C2PA manifest bytes. The no-parser property this "
        "audit relied on is gone; redo the RCE / path-traversal review:\n  "
        + "\n  ".join(hits)
    )
