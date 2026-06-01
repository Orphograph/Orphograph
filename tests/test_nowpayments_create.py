"""test_nowpayments_create.py — guards on the crypto checkout CREATE endpoint
and on crypto claim-code visibility for support.

Money-safety regressions locked in by the 2026-06-01 pre-launch audit:

  1. Email is REQUIRED on /api/nowpayments/create. The claim code is delivered
     by email and the webhook refuses to mint a Pack for an order carrying no
     customer email — so a blank-email crypto order would be a PAID (irreversible)
     purchase that can never receive its code. The endpoint must reject a
     missing/invalid email BEFORE the invoice is created.
  2. The create endpoint is rate-limited like every other public money POST
     (an unauthenticated create hits the NOWPayments /invoice API).
  3. support_tools.lookup_customer surfaces crypto (NOWPayments) claim codes
     from the credit ledger, so a "paid but never got my code" ticket is
     resolvable from the founder dashboard (manual recovery path).
"""
from __future__ import annotations

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
sys.path.insert(0, str(REPO_ROOT / "server"))


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def _start_server(data_dir: Path, rate_per_day: str):
    """Start app.py as a subprocess with NOWPayments 'configured' (a dummy key
    makes is_configured() True so the create handler runs past the config gate;
    blank-email requests 400 before any network call to NOWPayments)."""
    port = _free_port()
    env = {
        **os.environ,
        "PORT": str(port),
        "HOST": "127.0.0.1",
        "ORPHO_DATA_DIR": str(data_dir),
        "ORPHO_COOKIE_SECURE": "0",
        "RATE_LIMIT_PER_DAY": rate_per_day,
        "NOWPAYMENTS_API_KEY": "test_dummy_key_not_real",
    }
    proc = subprocess.Popen(
        [sys.executable, str(REPO_ROOT / "server" / "app.py")],
        env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    base = f"http://127.0.0.1:{port}"
    deadline = time.time() + 10
    while time.time() < deadline:
        try:
            urllib.request.urlopen(base + "/api/health", timeout=1).read()
            break
        except Exception:
            time.sleep(0.2)
    else:
        proc.kill()
        pytest.fail("server did not start")
    return proc, base


def _stop(proc):
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()


def _post(url, obj):
    body = json.dumps(obj).encode("utf-8")
    req = urllib.request.Request(
        url, data=body, method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.getcode(), r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


@pytest.fixture()
def server(tmp_path):
    proc, base = _start_server(tmp_path, "100000")  # don't trip limits here
    yield base
    _stop(proc)


@pytest.mark.parametrize("email", ["", "   ", "noatsign", "a@" + "x" * 260])
def test_create_rejects_missing_or_invalid_email(server, email):
    """A crypto order with no deliverable email is a money-loss trap — reject 400."""
    code, body = _post(
        server + "/api/nowpayments/create",
        {"currency": "btc", "plan": "writer_pack", "email": email},
    )
    assert code == 400, f"expected 400 for email={email!r}, got {code}: {body[:200]!r}"
    assert b"email" in body.lower(), f"400 reason should mention email: {body[:200]!r}"


def test_create_is_rate_limited(tmp_path):
    """Capacity 2 → the third create from the same client is throttled (429),
    not allowed to hammer the NOWPayments invoice API."""
    proc, base = _start_server(tmp_path, "2")
    try:
        codes = []
        for _ in range(3):
            c, _b = _post(
                base + "/api/nowpayments/create",
                {"currency": "btc", "plan": "writer_pack", "email": ""},
            )
            codes.append(c)
        # First two pass the limiter (then 400 on the missing email); the
        # third is denied by the limiter before reaching the body.
        assert codes[2] == 429, f"expected 3rd request 429, got {codes}"
    finally:
        _stop(proc)


def test_support_lookup_surfaces_crypto_claim_codes(tmp_path, monkeypatch):
    """A crypto buyer's minted claim code must be visible to founder support
    via the credit ledger — with cross-customer isolation and no spend rows."""
    import support_tools
    monkeypatch.setattr(support_tools, "DATA_DIR", tmp_path)
    rows = [
        {"ts": "2026-06-01T00:00:00+00:00", "claim_code": "pk_cryptoBuyer",
         "email": "buyer@example.com", "credits_delta": 10,
         "source": "nowpayments:inv1:np_writer_pack_abc"},
        {"ts": "2026-06-01T00:01:00+00:00", "claim_code": "pk_cryptoBuyer",
         "email": "", "credits_delta": -1, "source": "anchor-spend"},
        {"ts": "2026-06-01T00:02:00+00:00", "claim_code": "pk_someoneElse",
         "email": "other@example.com", "credits_delta": 50,
         "source": "nowpayments:inv2:np_pack_50_xyz"},
        # A hand-corrupted ledger row must be tolerated, not crash the lookup.
        {"claim_code": "pk_corrupt", "email": "buyer@example.com",
         "credits_delta": None, "source": "x"},
    ]
    (tmp_path / "credit_ledger.jsonl").write_text(
        "\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")

    result = support_tools.lookup_customer("buyer@example.com")
    assert result is not None
    claims = result.get("pack_claims", [])
    codes = [c["claim_code"] for c in claims]
    assert "pk_cryptoBuyer" in codes, f"crypto claim code not surfaced: {claims}"
    assert "pk_someoneElse" not in codes, "cross-customer leak"
    assert "pk_corrupt" not in codes, "corrupt (null-delta) row must be skipped, not surfaced"
    assert all(c["credits"] > 0 for c in claims), "spend rows must not appear as claims"
