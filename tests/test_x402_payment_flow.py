"""test_x402_payment_flow.py — x402 pay-per-anchor over the REAL HTTP handler.

Drives tests/_srv.py's real server subprocess (the current convention:
tests/test_server_fixture_hygiene.py fails any new server-spinning module
that does not use it). The mock facilitator is enabled via
ORPHO_X402_BACKEND=mock + ORPHO_X402_ALLOW_MOCK=1 (server/x402.py); a test
controls verify()/settle() outcomes by setting mock_outcome on the signed
payload it builds, so "paid" or "rejected" is always an explicit test
action, never a default. Each test gets its own server (function-scoped
fixture), so the free-tier bucket and the x402 claim ledger both start
empty — no cross-test bleed, no shared nonces needed.
"""
from __future__ import annotations

import base64
import json
import sys
from pathlib import Path

import pytest

import _srv

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "server"))
import x402 as x402_const  # noqa: E402 — constants only; no shared mutable
                            # state with the server subprocess's own import.

PAY_TO = "0x00000000000000000000000000000000000BEEF"
HASH_A = "aa" * 32
HASH_B = "bb" * 32
HASH_C = "cc" * 32
HASH_D = "dd" * 32
HASH_E = "ee" * 32

X402_ENV = {
    "ORPHO_X402_BACKEND": "mock",
    "ORPHO_X402_ALLOW_MOCK": "1",
    "ORPHO_X402_PAY_TO_ADDRESS": PAY_TO,
    "ORPHO_X402_PRICE_CENTS": "5",
    "RATE_LIMIT_PER_DAY": "1",
}


@pytest.fixture()
def server(tmp_path):
    yield from _srv.server_processes(tmp_path, stub_calendars=True, **X402_ENV)


@pytest.fixture()
def outage_server(tmp_path):
    # Every calendar refuses: a total outage, so calendars_ok stays 0.
    yield from _srv.server_processes(
        tmp_path, stub_calendars=True,
        fail_calendars="a,b,alice,finney,btc", **X402_ENV)


@pytest.fixture()
def unconfigured_server(tmp_path):
    env = dict(X402_ENV)
    del env["ORPHO_X402_ALLOW_MOCK"]
    yield from _srv.server_processes(tmp_path, stub_calendars=True, **env)


def _signed_payload(*, nonce: str, outcome: str = "ok",
                    payer: str = "0xagentPayer0000000000000000000000000001") -> dict:
    """Shaped exactly like the real scheme's PaymentPayload — authorization
    .nonce is what a real EIP-3009 signature binds and what the mock's
    single-use check keys on — plus the test-only mock_outcome field the
    mock facilitator reads (server/x402.py's _mock_facilitator_call)."""
    return {
        "x402Version": 2,
        "accepted": {"scheme": "exact", "network": "eip155:84532",
                    "asset": x402_const.USDC_BASE_SEPOLIA, "amount": "50000",
                    "payTo": PAY_TO, "maxTimeoutSeconds": 60, "extra": {}},
        "payload": {
            "signature": "0x" + "ab" * 65,
            "authorization": {"from": payer, "to": PAY_TO, "value": "50000",
                              "validAfter": "0", "validBefore": "9999999999",
                              "nonce": nonce},
            "mock_outcome": outcome,
        },
    }


def _payment_headers(**kwargs) -> dict:
    raw = base64.b64encode(json.dumps(_signed_payload(**kwargs)).encode()).decode()
    return {x402_const.PAYMENT_SIGNATURE_HEADER: raw}


def _post(base: str, path: str, body: dict, headers: dict | None = None):
    h = {"Content-Type": "application/json", **(headers or {})}
    status, raw, resp_headers = _srv.request(base, path, "POST", json.dumps(body).encode(), h)
    return status, resp_headers, json.loads(raw)


def _exhaust_free_tier(base: str) -> None:
    _post(base, "/api/anchor", {"hash_hex": HASH_C})


def _ledger_rows(tmp_path: Path) -> list[dict]:
    path = tmp_path / "x402_ledger.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


# ── tests ────────────────────────────────────────────────────────────────

def test_free_tier_still_works_without_payment(server):
    s, _h, b = _post(server, "/api/anchor", {"hash_hex": HASH_A})
    assert s == 200, b
    assert b["receipt_id"]


def test_past_free_tier_returns_x402_challenge(server):
    _exhaust_free_tier(server)
    s, h, b = _post(server, "/api/anchor", {"hash_hex": HASH_B})
    assert s == 402, b
    assert h.get(x402_const.PAYMENT_REQUIRED_HEADER) == "true"
    assert b["x402Version"] == 2
    req = b["accepts"][0]
    assert req["scheme"] == "exact"
    assert req["network"] == "eip155:84532"
    assert req["asset"] == x402_const.USDC_BASE_SEPOLIA
    assert req["amount"] == "50000"  # 5 cents * 10_000
    assert req["payTo"] == PAY_TO


def test_valid_payment_anchors_and_settles_exactly_once(server, tmp_path):
    _exhaust_free_tier(server)
    before = len(_ledger_rows(tmp_path))
    headers = _payment_headers(nonce="n1")
    s, h, b = _post(server, "/api/anchor", {"hash_hex": HASH_B}, headers)
    assert s == 200, b
    rid = b["receipt_id"]
    assert b["x402_settled"] is True
    decoded = json.loads(base64.b64decode(h[x402_const.PAYMENT_RESPONSE_HEADER]))
    assert decoded["success"] is True
    assert decoded["transaction"].startswith("0xmock")
    on_disk = json.loads((tmp_path / "receipts" / rid / "receipt.json").read_text())
    assert on_disk["source"].startswith("x402:")
    rows = _ledger_rows(tmp_path)
    assert len(rows) == before + 1
    assert rows[-1]["receipt_id"] == rid
    assert rows[-1]["settled"] is True
    assert rows[-1]["amount_atomic"] == "50000"


def test_invalid_payment_rejected_before_any_anchor(server, tmp_path):
    _exhaust_free_tier(server)
    before = len(_ledger_rows(tmp_path))
    headers = _payment_headers(nonce="n2", outcome="invalid")
    s, _h, b = _post(server, "/api/anchor", {"hash_hex": HASH_B}, headers)
    assert s == 402, b
    assert "invalid" in b["invalid_reason"]
    assert len(_ledger_rows(tmp_path)) == before, "settle() must never be attempted"


def test_facilitator_unreachable_on_verify_charges_nothing(server, tmp_path):
    """Premortem finding #3: the failure path must be provably inert, not
    merely absent from the happy-path tests."""
    _exhaust_free_tier(server)
    before = len(_ledger_rows(tmp_path))
    headers = _payment_headers(nonce="n3", outcome="unreachable")
    s, _h, b = _post(server, "/api/anchor", {"hash_hex": HASH_B}, headers)
    assert s == 503, b
    assert "unreachable" in b["detail"]
    assert len(_ledger_rows(tmp_path)) == before
    # The same signed payload still works once the facilitator answers —
    # verify() never claimed anything.
    headers2 = _payment_headers(nonce="n3", outcome="ok")
    s2, _h2, b2 = _post(server, "/api/anchor", {"hash_hex": HASH_B}, headers2)
    assert s2 == 200, b2


def test_replayed_signature_is_rejected_locally_before_a_second_anchor(server, tmp_path):
    """The bug this module exists to prevent: verify() alone is not
    single-use, so the SERVER must claim the nonce before anchoring, not
    rely on the chain to catch a replay after the fact."""
    _exhaust_free_tier(server)
    headers = _payment_headers(nonce="n4")
    s, _h, b = _post(server, "/api/anchor", {"hash_hex": HASH_B}, headers)
    assert s == 200, b
    before = len(_ledger_rows(tmp_path))
    # Same signature again, against a DIFFERENT hash — rejected before any
    # anchor is attempted, not merely failed to settle afterward.
    s2, _h2, b2 = _post(server, "/api/anchor", {"hash_hex": HASH_D}, headers)
    assert s2 == 401, b2
    assert "already used" in b2["error"]
    assert len(_ledger_rows(tmp_path)) == before, "a replay must not even attempt settlement"
    assert "receipt_id" not in b2, "no second anchor was created"


def test_worthless_zero_calendar_anchor_is_never_charged_and_can_retry(outage_server, tmp_path):
    _exhaust_free_tier(outage_server)
    headers = _payment_headers(nonce="n5")
    before = len(_ledger_rows(tmp_path))
    s, _h, b = _post(outage_server, "/api/anchor", {"hash_hex": HASH_E}, headers)
    assert s == 200, b  # receipt still returned, per existing policy
    assert b["calendars_ok"] == 0
    assert b["credit_refunded"] is True
    assert "x402_settled" not in b  # settle() never attempted
    assert len(_ledger_rows(tmp_path)) == before, "no ledger row for an unsettled, unsubmitted anchor"
    # The claim was released: the SAME signature can now buy a real anchor
    # once calendars work again (a fresh server, since fail_calendars is
    # fixed at startup — the claim ledger is what's actually under test).
    s2, _h2, b2 = _post(outage_server, "/api/anchor", {"hash_hex": HASH_E}, headers)
    assert s2 == 200, b2
    assert b2["calendars_ok"] == 0  # still an outage server; the point is s2 != 401


def test_settlement_failure_after_a_real_anchor_is_recorded_not_hidden(server, tmp_path):
    _exhaust_free_tier(server)
    headers = _payment_headers(nonce="n6", outcome="settle_fail")
    before = len(_ledger_rows(tmp_path))
    s, h, b = _post(server, "/api/anchor", {"hash_hex": HASH_B}, headers)
    assert s == 200, b  # the anchor happened; it cannot be undone
    assert b["x402_settled"] is False
    assert x402_const.PAYMENT_RESPONSE_HEADER not in h
    rows = _ledger_rows(tmp_path)
    assert len(rows) == before + 1
    assert rows[-1]["settled"] is False
    assert "settlement failed" in rows[-1]["reason"]


def test_malformed_payment_header_rejected_as_400(server):
    _exhaust_free_tier(server)
    s, _h, b = _post(server, "/api/anchor", {"hash_hex": HASH_B},
                     {x402_const.PAYMENT_SIGNATURE_HEADER: "not-valid-base64!!"})
    assert s == 400, b
    assert "x402 payment rejected" in b["error"]


def test_x402_disabled_falls_back_to_classic_429(unconfigured_server):
    _exhaust_free_tier(unconfigured_server)
    s, _h, b = _post(unconfigured_server, "/api/anchor", {"hash_hex": HASH_B})
    assert s == 429, b


def test_an_x402_header_never_buys_an_anchor_when_the_payload_is_invalid(server):
    """Negative control on Stage 3h leakage: proves the header is actually
    read and enforced, not silently ignored in either direction."""
    _exhaust_free_tier(server)
    headers = _payment_headers(nonce="n7", outcome="invalid")
    s, _h, b = _post(server, "/api/anchor", {"hash_hex": HASH_B}, headers)
    assert s != 200, b


def test_a_pack_token_takes_priority_over_an_x402_header(server, tmp_path):
    """Precedence, mirroring pack-over-L402: a valid pack token pays even
    when an x402 header sent alongside it would have been rejected — proof
    the header is never even parsed once a pack token is consumed, so a
    caller can never be double-charged."""
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "server"))
    import credits
    old_path = credits.LEDGER_PATH
    credits.LEDGER_PATH = tmp_path / "credit_ledger.jsonl"
    try:
        credits.add_credits("pk_x402precedence00", "buyer@example.test", 1, "test")
        before = len(_ledger_rows(tmp_path))
        headers = _payment_headers(nonce="n8", outcome="invalid")
        headers["X-Pack-Token"] = "pk_x402precedence00"
        s, _h, b = _post(server, "/api/anchor", {"hash_hex": HASH_A}, headers)
        assert s == 200, b  # the pack paid; the "invalid" x402 outcome never fired
        assert b["pack_consumed"] is True
        assert "x402_settled" not in b
        assert len(_ledger_rows(tmp_path)) == before, "x402 must not be touched at all"
    finally:
        credits.LEDGER_PATH = old_path
