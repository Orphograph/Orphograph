"""test_order_status.py

Tests for the status-only order-credited poll endpoint added for
web/pay/success.html:

    GET /api/nowpayments/order/<order_id>
      -> {"ok": true, "order_id": <echo>, "credited": <bool>, "credits": <int|null>}

The success page polls this after a crypto payment to tell the buyer whether
their claim code has been minted + emailed yet.

SECURITY CONTRACT (the reason this endpoint is narrow): the response is
STATUS ONLY. It must NEVER carry the claim code (a "pk_..." bearer token),
the customer email, or any secret. The underlying ledger row DOES contain
those fields; the handler projects out only a boolean + the integer credit
count. Test (c) is the explicit leak guard.

Live-server subprocess harness mirrored from
tests/test_money_surface_hardening_2026_05_29.py; a credit_ledger row is
seeded on disk under ORPHO_DATA_DIR before the server boots so the endpoint
reads it through the real credits module.
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
from datetime import datetime, timezone
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

# A credited order seeded into the ledger before the server starts. The source
# mirrors the real webhook format ("nowpayments:<invoice_id>:<order_id>"), which
# find_claim_code_by_source() substring-matches on the order_id.
CREDITED_ORDER = "np_pack_50_Ab12Cd34"
CREDITED_CREDITS = 50
SEED_CLAIM_CODE = "pk_SECRETcodeMustNeverLeak"
SEED_EMAIL = "buyer-leak-canary@example.com"


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    port = _free_port()
    data_dir = tmp_path_factory.mktemp("orderstatus_data")

    # Seed a credited-order row into the credit ledger BEFORE the server boots.
    # The row deliberately carries a claim_code ("pk_...") and an email so the
    # leak-guard test has something real to assert is NOT echoed.
    ledger = data_dir / "credit_ledger.jsonl"
    row = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "claim_code": SEED_CLAIM_CODE,
        "email": SEED_EMAIL,
        "credits_delta": CREDITED_CREDITS,
        "source": f"nowpayments:inv_test_001:{CREDITED_ORDER}",
    }
    ledger.write_text(json.dumps(row, separators=(",", ":")) + "\n")

    env = {
        **os.environ,
        "PORT": str(port),
        "HOST": "127.0.0.1",
        "ORPHO_DATA_DIR": str(data_dir),
        "ORPHO_COOKIE_SECURE": "0",
        "RATE_LIMIT_PER_DAY": "100000",  # don't trip limits during these probes
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
        out, err = proc.communicate(timeout=5)
        pytest.fail(f"server did not start: {err.decode(errors='replace')[:2000]}")
    yield base
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()


def _get(url, timeout=5):
    """Return (status, body_bytes). On HTTPError, return its code + body so the
    leak-guard can inspect 4xx bodies too."""
    req = urllib.request.Request(url, method="GET",
                                 headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()
    except Exception:
        return -1, b""


# --------------------------------------------------------------- (a) credited

def test_credited_order_reports_credited_true_with_count(server):
    status, body = _get(f"{server}/api/nowpayments/order/{CREDITED_ORDER}")
    assert status == 200, body
    data = json.loads(body)
    assert data["ok"] is True
    assert data["order_id"] == CREDITED_ORDER
    assert data["credited"] is True
    assert data["credits"] == CREDITED_CREDITS


# --------------------------------------------------------------- (b) unknown

def test_unknown_order_reports_not_credited_with_null_credits(server):
    status, body = _get(f"{server}/api/nowpayments/order/np_does_not_exist_999")
    assert status == 200, body
    data = json.loads(body)
    assert data["ok"] is True
    assert data["order_id"] == "np_does_not_exist_999"
    assert data["credited"] is False
    assert data["credits"] is None


# ------------------------------------------------------------- (c) leak guard

def test_response_never_exposes_claim_code_or_email(server):
    """THE leak guard. The ledger row for this order DOES contain a "pk_"
    claim code and a customer email — assert neither ever appears in the
    status response body, and that no "pk_" / email substring leaks at all.
    Exercise both the credited and the unknown path."""
    for order in (CREDITED_ORDER, "np_does_not_exist_999"):
        status, body = _get(f"{server}/api/nowpayments/order/{order}")
        assert status == 200, body
        text = body.decode("utf-8")
        # No claim code (never expose a "pk_..." bearer token).
        assert "pk_" not in text, f"claim-code prefix leaked for {order}: {text}"
        assert SEED_CLAIM_CODE not in text, f"claim code leaked for {order}"
        # No customer email.
        assert SEED_EMAIL not in text, f"email leaked for {order}"
        assert "@" not in text, f"unexpected email-like token leaked for {order}: {text}"
        # Response carries exactly the status-only keys — nothing else.
        data = json.loads(text)
        assert set(data.keys()) == {"ok", "order_id", "credited", "credits"}


# ----------------------------------------------------------- (d) bad shape

def test_bad_shape_order_id_returns_400(server):
    # Contains characters outside [A-Za-z0-9_-] -> 400.
    status, body = _get(f"{server}/api/nowpayments/order/bad%20id%21")
    assert status == 400, body
    # And the 400 body must also never leak a code or email.
    text = body.decode("utf-8")
    assert "pk_" not in text
    assert SEED_EMAIL not in text


def test_overlong_order_id_returns_400(server):
    long_id = "a" * 65  # len 65 > 64 -> rejected
    status, body = _get(f"{server}/api/nowpayments/order/{long_id}")
    assert status == 400, body
