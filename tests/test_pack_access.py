"""test_pack_access.py

Covers the discoverable Pack-access surface added 2026-07-23:

  * credits.find_claim_codes_by_email  — read-only recovery lookup
      (case-insensitive, dedupe, mint-rows-only)                [unit]
  * GET  /pack                         — page route serves 200  [http]
  * GET  /api/pack/balance/{code}      — happy path returns bal [http]
  * POST /api/pack/recover             — ALWAYS neutral 200;
      identical body whether or not a pack exists (no email
      enumeration); triggers the mailer only on a real hit;
      rate-limited per IP.                                      [http]

The mailer is left inert (RESEND_API_KEY unset): a "send" only emits
`[email:inert] would send to=… subject='… Pack of N …'` to stderr, which we
capture to assert send-vs-no-send WITHOUT delivering real email — the same
harness the crypto-recovery suite uses.
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

import credits

REPO_ROOT = Path(__file__).resolve().parent.parent


# ───────────────────────── unit: find_claim_codes_by_email ─────────────────

@pytest.fixture()
def isolated_ledger(tmp_path, monkeypatch):
    monkeypatch.setattr(credits, "LEDGER_PATH", tmp_path / "credit_ledger.jsonl")
    yield


def test_find_by_email_returns_minted_codes(isolated_ledger):
    credits.add_credits("pk_aaa", "buyer@example.com", 10, "stripe:cs_1")
    credits.add_credits("pk_bbb", "buyer@example.com", 50, "stripe:cs_2")
    credits.add_credits("pk_ccc", "someone@else.com", 10, "stripe:cs_3")
    codes = credits.find_claim_codes_by_email("buyer@example.com")
    assert codes == ["pk_aaa", "pk_bbb"]


def test_find_by_email_is_case_insensitive(isolated_ledger):
    credits.add_credits("pk_aaa", "Buyer@Example.COM", 10, "stripe:cs_1")
    # The recover endpoint lowercases the address before lookup.
    assert credits.find_claim_codes_by_email("  buyer@example.com  ") == ["pk_aaa"]


def test_find_by_email_dedupes_preserving_order(isolated_ledger):
    # Same code minted twice (e.g. original + a referral bonus row).
    credits.add_credits("pk_aaa", "buyer@example.com", 10, "stripe:cs_1")
    credits.add_credits("pk_aaa", "buyer@example.com", 10, "referral-bonus")
    credits.add_credits("pk_bbb", "buyer@example.com", 50, "stripe:cs_2")
    assert credits.find_claim_codes_by_email("buyer@example.com") == ["pk_aaa", "pk_bbb"]


def test_find_by_email_ignores_consume_rows(isolated_ledger):
    # Consume/refund rows carry email="" — they must never be matched, and a
    # spent code still surfaces (dedupe by claim_code, not by balance).
    credits.add_credits("pk_aaa", "buyer@example.com", 1, "stripe:cs_1")
    credits.consume_credit("pk_aaa")  # writes an email="" row
    assert credits.balance("pk_aaa") == 0
    assert credits.find_claim_codes_by_email("buyer@example.com") == ["pk_aaa"]
    assert credits.find_claim_codes_by_email("") == []


def test_find_by_email_unknown_returns_empty(isolated_ledger):
    credits.add_credits("pk_aaa", "buyer@example.com", 10, "stripe:cs_1")
    assert credits.find_claim_codes_by_email("nobody@example.com") == []


# ───────────────────────────── http integration ───────────────────────────

# Seeded ledger fixtures.
PACK_EMAIL   = "Seller@Example.com"          # mixed case on purpose
CODE_A       = "pk_writerpack10AAA"          # 10 minted, 1 consumed -> 9 left
CODE_B       = "pk_packfifty50BBB"           # 50 minted -> 50 left
SPENT_EMAIL  = "spent@example.com"
CODE_SPENT   = "pk_spentpackCCC"             # 1 minted, 1 consumed -> 0 left


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def _seed_ledger(path: Path) -> None:
    rows = [
        {"ts": "2026-07-01T00:00:00+00:00", "claim_code": CODE_A, "email": PACK_EMAIL,
         "credits_delta": 10, "source": "stripe:cs_A"},
        {"ts": "2026-07-01T00:01:00+00:00", "claim_code": CODE_A, "email": "",
         "credits_delta": -1, "source": "anchor"},
        {"ts": "2026-07-01T00:02:00+00:00", "claim_code": CODE_B, "email": PACK_EMAIL,
         "credits_delta": 50, "source": "stripe:cs_B"},
        {"ts": "2026-07-01T00:03:00+00:00", "claim_code": CODE_SPENT, "email": SPENT_EMAIL,
         "credits_delta": 1, "source": "stripe:cs_C"},
        {"ts": "2026-07-01T00:04:00+00:00", "claim_code": CODE_SPENT, "email": "",
         "credits_delta": -1, "source": "anchor"},
    ]
    path.write_text("".join(json.dumps(r, separators=(",", ":")) + "\n" for r in rows))


def _start_server(data_dir: Path, rate_limit_per_day: str = "100000"):
    port = _free_port()
    ledger_path = data_dir / "credit_ledger.jsonl"
    _seed_ledger(ledger_path)
    stderr_log = data_dir / "server.stderr.log"
    env = {
        **os.environ,
        "PORT": str(port),
        "HOST": "127.0.0.1",
        "ORPHO_DATA_DIR": str(data_dir),
        "ORPHO_COOKIE_SECURE": "0",
        "RATE_LIMIT_PER_DAY": rate_limit_per_day,
    }
    env.pop("RESEND_API_KEY", None)  # inert mailer
    err_fh = stderr_log.open("wb")
    proc = subprocess.Popen(
        [sys.executable, str(REPO_ROOT / "server" / "app.py")],
        env=env, stdout=subprocess.PIPE, stderr=err_fh,
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
        err_fh.close()
        pytest.fail("server did not start")
    return proc, err_fh, {"base": base, "ledger": ledger_path, "stderr_log": stderr_log}


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    data_dir = tmp_path_factory.mktemp("pack_access_data")
    proc, err_fh, info = _start_server(data_dir)
    yield info
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
    err_fh.close()


def _get(url, timeout=5):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")


def _post(url, payload, timeout=5):
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST",
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")


def _read_stderr(stderr_log: Path) -> str:
    time.sleep(0.3)  # allow line-buffered stderr to flush
    try:
        return stderr_log.read_text(errors="replace")
    except OSError:
        return ""


# ---- /pack route ----------------------------------------------------------

def test_pack_page_route_200(server):
    status, body = _get(server["base"] + "/pack")
    assert status == 200
    assert "Access your Pack" in body
    # External JS/CSS only (CSP): no inline <script> handlers on the page.
    assert "/assets/pack.js" in body
    assert "onclick=" not in body.lower()


# ---- balance --------------------------------------------------------------

def test_balance_happy_path(server):
    status, body = _get(server["base"] + f"/api/pack/balance/{CODE_A}")
    assert status == 200
    j = json.loads(body)
    assert j["claim_code"] == CODE_A
    assert j["balance"] == 9   # 10 minted - 1 consumed


def test_balance_full_pack(server):
    status, body = _get(server["base"] + f"/api/pack/balance/{CODE_B}")
    assert status == 200
    assert json.loads(body)["balance"] == 50


# ---- recover: neutral response + mailer trigger ---------------------------

def test_recover_hit_sends_and_is_neutral(server):
    """An address with packs: the response is the neutral confirmation AND the
    mailer is invoked once per code that still has anchors (CODE_A, CODE_B)."""
    err_before = len(_read_stderr(server["stderr_log"]))
    status, body = _post(server["base"] + "/api/pack/recover",
                         {"email": "  seller@example.com  "})  # case/space differ
    assert status == 200
    j = json.loads(body)
    assert j["ok"] is True
    assert "sent the code" in j["message"]
    new_err = _read_stderr(server["stderr_log"])[err_before:]
    # Two codes with positive balance -> two inert sends. CODE_SPENT (0 left)
    # belongs to a different email and is not touched here anyway.
    assert new_err.count("[email:inert] would send") == 2, new_err
    # send_pack_claim_email's subject carries the pack size ("Pack of N …").
    assert "Pack of" in new_err


def test_recover_miss_is_identical_and_sends_nothing(server):
    """An address with NO packs returns a byte-identical body and sends no
    mail — the endpoint cannot be used to tell which emails own a pack."""
    _, hit_body = _post(server["base"] + "/api/pack/recover",
                        {"email": "seller@example.com"})
    err_before = len(_read_stderr(server["stderr_log"]))
    status, miss_body = _post(server["base"] + "/api/pack/recover",
                             {"email": "nobody-here@example.com"})
    assert status == 200
    # Same wording whether or not the address has a pack (no enumeration).
    assert miss_body == hit_body
    new_err = _read_stderr(server["stderr_log"])[err_before:]
    assert "[email:inert] would send" not in new_err


def test_recover_malformed_email_still_neutral(server):
    """A malformed address gets the SAME neutral 200 (never a distinguishing
    400) and sends nothing — mirrors the waitlist endpoint."""
    err_before = len(_read_stderr(server["stderr_log"]))
    status, body = _post(server["base"] + "/api/pack/recover",
                        {"email": "not-an-email"})
    assert status == 200
    assert json.loads(body)["ok"] is True
    new_err = _read_stderr(server["stderr_log"])[err_before:]
    assert "[email:inert] would send" not in new_err


def test_recover_spent_pack_email_sends_nothing(server):
    """An address whose only pack is fully spent: neutral response, and no
    mail (a 'Pack of 0' notice would be misleading)."""
    err_before = len(_read_stderr(server["stderr_log"]))
    status, body = _post(server["base"] + "/api/pack/recover",
                        {"email": SPENT_EMAIL})
    assert status == 200
    assert json.loads(body)["ok"] is True
    new_err = _read_stderr(server["stderr_log"])[err_before:]
    assert "[email:inert] would send" not in new_err


# ---- recover: rate limit --------------------------------------------------

def test_recover_is_rate_limited(tmp_path_factory):
    """A low per-IP budget must eventually 429 the recover endpoint."""
    data_dir = tmp_path_factory.mktemp("pack_recover_rl")
    proc, err_fh, info = _start_server(data_dir, rate_limit_per_day="3")
    try:
        codes = []
        for _ in range(8):
            status, _ = _post(info["base"] + "/api/pack/recover",
                             {"email": "nobody@example.com"})
            codes.append(status)
        assert 429 in codes, f"expected a 429 within the budget, got {codes}"
        # Non-429 responses are the neutral 200 (never a 4xx that would leak).
        assert set(codes) <= {200, 429}, codes
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        err_fh.close()
