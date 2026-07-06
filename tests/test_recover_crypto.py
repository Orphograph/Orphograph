"""test_recover_crypto.py

Money-surface tests for crypto (NOWPayments) self-serve claim-code recovery
on POST /api/recover (the np_ branch added 2026-06-05).

The endpoint must let a crypto buyer re-trigger their EXISTING claim-code
email WITHOUT minting a new code, and ONLY when the requester's email
matches the email on the ledger row. The cross-customer-leak guard is the
load-bearing property under test:

  (a) matching email      -> existing code re-sent; NO new positive ledger row
  (b) WRONG email         -> generic error; NO email sent (no leak)
  (c) unknown np_ order   -> generic error (indistinguishable from (b))

Design notes:
  * The live server is launched as a subprocess (same harness as
    test_money_surface_hardening_2026_05_29.py), seeded via ORPHO_DATA_DIR.
  * RESEND_API_KEY is left UNSET, so mailer is inert: a real "send" emits
    `[email:inert] would send to=...` to stderr and the handler logs
    `[recover] resent crypto claim_code ...`. We capture server stderr to a
    file and assert on those lines to distinguish send vs. no-send WITHOUT
    delivering real email.
  * "No new positive ledger row" is asserted by counting positive-delta rows
    in the ledger before and after the request.
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

# A claim code already minted for a crypto order. Email is the address on file.
ORDER_ID = "np_pack10_abc123XYZ"
LEDGER_EMAIL = "Buyer@Example.com"          # mixed case on purpose
CLAIM_CODE = "pk_existing_crypto_code_001"
CREDIT_DELTA = 10
# Webhook source format: "nowpayments:<invoice_or_order>:<order_id>".
LEDGER_SOURCE = f"nowpayments:inv_777:{ORDER_ID}"


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def _count_positive_rows(ledger_path: Path) -> int:
    """Number of positive-delta (mint/credit) rows currently in the ledger."""
    if not ledger_path.exists():
        return 0
    n = 0
    for line in ledger_path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if int(row.get("credits_delta", 0)) > 0:
            n += 1
    return n


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    port = _free_port()
    data_dir = tmp_path_factory.mktemp("recover_crypto_data")
    ledger_path = data_dir / "credit_ledger.jsonl"
    # Seed ONE positive ledger row for ORDER_ID, exactly as the webhook would.
    seed = {
        "ts": "2026-06-01T00:00:00+00:00",
        "claim_code": CLAIM_CODE,
        "email": LEDGER_EMAIL,
        "credits_delta": CREDIT_DELTA,
        "source": LEDGER_SOURCE,
    }
    ledger_path.write_text(json.dumps(seed, separators=(",", ":")) + "\n")

    stderr_log = data_dir / "server.stderr.log"
    env = {
        **os.environ,
        "PORT": str(port),
        "HOST": "127.0.0.1",
        "ORPHO_DATA_DIR": str(data_dir),
        "ORPHO_COOKIE_SECURE": "0",
        "RATE_LIMIT_PER_DAY": "100000",
    }
    # RESEND_API_KEY must be UNSET so the mailer is inert (no real send).
    env.pop("RESEND_API_KEY", None)

    err_fh = stderr_log.open("wb")
    proc = subprocess.Popen(
        [sys.executable, str(REPO_ROOT / "server" / "app.py")],
        env=env, stdout=subprocess.PIPE, stderr=err_fh,
    )
    base = f"http://127.0.0.1:{port}"
    deadline = time.time() + 10
    started = False
    while time.time() < deadline:
        try:
            urllib.request.urlopen(base + "/api/health", timeout=1).read()
            started = True
            break
        except Exception:
            time.sleep(0.2)
    if not started:
        proc.kill()
        err_fh.close()
        pytest.fail("server did not start")

    yield {"base": base, "ledger": ledger_path, "stderr_log": stderr_log}

    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
    err_fh.close()


def _post(url, payload, timeout=5):
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=body, method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode("utf-8"))
        except Exception:
            return e.code, {}


def _read_stderr(stderr_log: Path) -> str:
    # Give the server a beat to flush its line-buffered stderr.
    time.sleep(0.3)
    try:
        return stderr_log.read_text(errors="replace")
    except OSError:
        return ""


# --------------------------------------------------------------- (a) match

def test_matching_email_resends_existing_code_and_does_not_mint(server):
    base = server["base"]
    ledger = server["ledger"]
    stderr_log = server["stderr_log"]

    before = _count_positive_rows(ledger)
    err_before = len(_read_stderr(stderr_log))

    # Email differs only in case/whitespace from the ledger row -> must match.
    status, body = _post(f"{base}/api/recover", {
        "stripe_session_id": ORDER_ID,
        "email": "  buyer@example.com  ",
    })

    assert status == 200, f"expected 200, got {status}: {body}"
    assert body.get("ok") is True
    assert body.get("mode") == "payment"

    # The EXISTING code was the one re-sent: the inert mailer logs the send,
    # and the handler logs that it resent FOR THIS ORDER. Because the mailer
    # is inert it never reveals the code in stderr, so we assert the send
    # happened for this order_id (the only seeded code).
    new_err = _read_stderr(stderr_log)[err_before:]
    assert "[email:inert] would send" in new_err, (
        "mailer was not invoked to (re-)send the claim email"
    )
    assert f"resent crypto claim_code for order={ORDER_ID}" in new_err, (
        "handler did not log re-send of the existing code for this order"
    )

    # NO new positive ledger row -> no mint.
    after = _count_positive_rows(ledger)
    assert after == before, (
        f"a new positive ledger row was created (mint!): {before} -> {after}"
    )


# --------------------------------------------------------------- (b) wrong email

def test_wrong_email_generic_error_and_no_send(server):
    base = server["base"]
    ledger = server["ledger"]
    stderr_log = server["stderr_log"]

    before = _count_positive_rows(ledger)
    err_before = len(_read_stderr(stderr_log))

    status, body = _post(f"{base}/api/recover", {
        "stripe_session_id": ORDER_ID,
        "email": "attacker@evil.example",   # real order, wrong email
    })

    # Generic error, identical to the unknown-order case (no enumeration).
    assert status == 400, f"expected 400, got {status}: {body}"
    assert body.get("error") == "invalid request"
    assert "claim" not in json.dumps(body).lower()  # never leak the code

    new_err = _read_stderr(stderr_log)[err_before:]
    # NO email send must have occurred for the wrong requester.
    assert "[email:inert] would send" not in new_err, (
        "an email was (would have been) sent to a mismatched requester — LEAK"
    )
    assert "resent crypto claim_code" not in new_err

    # No mint either.
    assert _count_positive_rows(ledger) == before


# --------------------------------------------------------------- (c) unknown order

def test_unknown_order_generic_error(server):
    base = server["base"]
    ledger = server["ledger"]
    stderr_log = server["stderr_log"]

    before = _count_positive_rows(ledger)
    err_before = len(_read_stderr(stderr_log))

    status, body = _post(f"{base}/api/recover", {
        "stripe_session_id": "np_does_not_exist_999",
        "email": "buyer@example.com",       # valid-but-irrelevant email
    })

    # EXACT same generic error as the wrong-email case.
    assert status == 400, f"expected 400, got {status}: {body}"
    assert body.get("error") == "invalid request"

    new_err = _read_stderr(stderr_log)[err_before:]
    assert "[email:inert] would send" not in new_err
    assert "resent crypto claim_code" not in new_err
    assert _count_positive_rows(ledger) == before


# --------------------------------------------------------- shape rejection

def test_malformed_order_id_generic_error(server):
    """An np_ id with illegal chars collapses to the same generic 400."""
    base = server["base"]
    status, body = _post(f"{base}/api/recover", {
        "stripe_session_id": "np_bad/../id",     # slash + dots not allowed
        "email": "buyer@example.com",
    })
    assert status == 400
    assert body.get("error") == "invalid request"


def test_stripe_path_untouched_invalid_id_still_400(server):
    """Regression guard: a non-cs_/non-np_ id is still rejected generically,
    proving the Stripe shape check below the branch is intact."""
    base = server["base"]
    status, body = _post(f"{base}/api/recover", {
        "stripe_session_id": "garbage_session",
        "email": "buyer@example.com",
    })
    assert status == 400
    assert body.get("error") == "invalid request"


# ----------------------------------------------- prefix/substring abuse blocked

def test_prefix_substring_order_id_rejected(server):
    """A SHORT prefix of a real order_id substring-matches the seeded ledger
    source, but must NOT recover — order_id must be the EXACT final source
    segment. Even with the CORRECT on-file email, the prefix is rejected; under
    the old substring match this exact combo would have re-sent the code (an
    enumeration oracle + unsolicited-resend vector). No code is sent."""
    base = server["base"]
    ledger = server["ledger"]
    stderr_log = server["stderr_log"]

    before = _count_positive_rows(ledger)
    err_before = len(_read_stderr(stderr_log))

    # "np_pack10_" IS a substring of LEDGER_SOURCE but is NOT the exact order_id.
    status, body = _post(f"{base}/api/recover", {
        "stripe_session_id": "np_pack10_",
        "email": LEDGER_EMAIL,            # the correct address on file
    })

    assert status == 400, f"prefix substring must be rejected, got {status}: {body}"
    assert body.get("error") == "invalid request"

    new_err = _read_stderr(stderr_log)[err_before:]
    assert "resent crypto claim_code" not in new_err, "must NOT re-send for a prefix match"
    assert "[email:inert] would send" not in new_err
    assert _count_positive_rows(ledger) == before
