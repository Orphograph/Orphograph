"""test_x402_payment_flow.py — x402 pay-per-anchor over the REAL HTTP handler.

Drives tests/_srv.py's real server subprocess (the current convention:
tests/test_server_fixture_hygiene.py fails any new server-spinning module
that does not use it). The mock facilitator is enabled via
ORPHO_X402_BACKEND=mock + ORPHO_X402_ALLOW_MOCK=1 (server/x402.py); a test
controls verify()/settle() outcomes by setting mock_outcome on the signed
payload it builds, so "paid" or "rejected" is always an explicit test
action, never a default. Each test gets its own server (function-scoped
fixture), so the free-tier bucket and the x402 ledgers both start empty —
no cross-test bleed, no shared nonces needed.

The order this file pins: verify -> settle -> record PAID -> claim -> anchor
-> deliver. The first cut anchored BEFORE settling, and a settle() failure
after the anchor changed nothing about the 200 already given; the tests
named *_before_any_anchor / *_no_receipt / *_recased_nonce_* are the ones
that fail against that ordering.
"""
from __future__ import annotations

import base64
import json
import sys
from pathlib import Path

import pytest

import _srv

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "server"))
import x402 as x402_const  # noqa: E402 — constants + pure helpers only; no
                            # shared mutable state with the server subprocess.

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

# While this file exists in the data dir, the stubbed calendars all refuse
# (tests/_run_server.py). A total outage a test can START and END on one
# server, which a settle-then-anchor rail needs: the same settled payment
# must be shown held through the outage and redeemed after it.
CALENDARS_DOWN = "stub_calendars_down"


@pytest.fixture()
def server(tmp_path):
    yield from _srv.server_processes(tmp_path, stub_calendars=True, **X402_ENV)


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


def _settled_rows(tmp_path: Path) -> list[dict]:
    """Rows that record a settle() that SUCCEEDED — one per charge."""
    return [r for r in _ledger_rows(tmp_path) if r.get("settled") and not r.get("delivered")]


def _receipts_on_disk_for(tmp_path: Path, hash_hex: str) -> list[str]:
    """Receipt ids whose receipt.json anchors hash_hex. Reading the receipts
    directory, not a response flag: a rejected request must have written
    nothing servable at /r/<id>, and only the disk can say that."""
    found = []
    for receipt in (tmp_path / "receipts").glob("*/receipt.json"):
        try:
            if json.loads(receipt.read_text()).get("hash_hex") == hash_hex:
                found.append(receipt.parent.name)
        except (OSError, json.JSONDecodeError):
            continue
    return found


def _payment_response(headers) -> dict:
    return json.loads(base64.b64decode(headers[x402_const.PAYMENT_RESPONSE_HEADER]))


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


def test_valid_payment_settles_then_anchors_and_delivers_exactly_once(server, tmp_path):
    """The happy path, end to end: one settle, one anchor, delivered. The
    ledger carries both halves in order — the charge (no receipt yet, it
    happened BEFORE the anchor) and then the delivery that links it to the
    receipt it bought."""
    _exhaust_free_tier(server)
    before = len(_ledger_rows(tmp_path))
    headers = _payment_headers(nonce="n1")
    s, h, b = _post(server, "/api/anchor", {"hash_hex": HASH_B}, headers)
    assert s == 200, b
    rid = b["receipt_id"]
    assert b["x402_settled"] is True
    assert b["x402_payment_held"] is False
    decoded = _payment_response(h)
    assert decoded["success"] is True
    assert decoded["transaction"].startswith("0xmock")
    on_disk = json.loads((tmp_path / "receipts" / rid / "receipt.json").read_text())
    assert on_disk["source"].startswith("x402:")
    rows = _ledger_rows(tmp_path)
    assert len(rows) == before + 2, rows
    charge, delivery = rows[-2], rows[-1]
    assert charge["id"] == "nonce:n1"
    assert charge["settled"] is True
    assert charge["delivered"] is False
    assert charge["receipt_id"] == ""          # charged before any receipt existed
    assert charge["amount_atomic"] == "50000"
    assert charge["tx_hash"] == decoded["transaction"]
    assert delivery["id"] == "nonce:n1"
    assert delivery["delivered"] is True
    assert delivery["receipt_id"] == rid
    assert delivery["tx_hash"] == decoded["transaction"]


def test_invalid_payment_rejected_before_any_anchor(server, tmp_path):
    _exhaust_free_tier(server)
    before = len(_ledger_rows(tmp_path))
    headers = _payment_headers(nonce="n2", outcome="invalid")
    s, _h, b = _post(server, "/api/anchor", {"hash_hex": HASH_B}, headers)
    assert s == 402, b
    assert "invalid" in b["invalid_reason"]
    assert len(_ledger_rows(tmp_path)) == before, "settle() must never be attempted"
    assert _receipts_on_disk_for(tmp_path, HASH_B) == []


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
    assert _receipts_on_disk_for(tmp_path, HASH_B) == []
    # The same signed payload still works once the facilitator answers —
    # verify() never claimed anything.
    headers2 = _payment_headers(nonce="n3", outcome="ok")
    s2, _h2, b2 = _post(server, "/api/anchor", {"hash_hex": HASH_B}, headers2)
    assert s2 == 200, b2


def test_facilitator_unreachable_on_settle_anchors_nothing_and_records_nothing(server, tmp_path):
    """A distinct case now that settle() runs BEFORE the anchor: verify()
    answered, settle() did not. The outcome on-chain is unknown, so nothing
    durable is written that would block the retry — and no anchor is made
    on an unproven payment."""
    _exhaust_free_tier(server)
    before = len(_ledger_rows(tmp_path))
    headers = _payment_headers(nonce="n3s", outcome="unreachable_settle")
    s, _h, b = _post(server, "/api/anchor", {"hash_hex": HASH_B}, headers)
    assert s == 503, b
    assert "settle" in b["error"]
    assert "receipt_id" not in b and "receipt_url" not in b
    assert _receipts_on_disk_for(tmp_path, HASH_B) == []
    assert len(_ledger_rows(tmp_path)) == before, "an unknown outcome is not a charge"
    # Same payload, facilitator back: a fresh settle, then the anchor.
    headers2 = _payment_headers(nonce="n3s", outcome="ok")
    s2, _h2, b2 = _post(server, "/api/anchor", {"hash_hex": HASH_B}, headers2)
    assert s2 == 200, b2
    assert b2["x402_settled"] is True


def test_replayed_signature_is_rejected_locally_before_a_second_anchor(server, tmp_path):
    """A delivered payment is final: the same signature against a DIFFERENT
    hash is refused before verify(), settle() or the anchor run again."""
    _exhaust_free_tier(server)
    headers = _payment_headers(nonce="n4")
    s, _h, b = _post(server, "/api/anchor", {"hash_hex": HASH_B}, headers)
    assert s == 200, b
    before = len(_ledger_rows(tmp_path))
    s2, _h2, b2 = _post(server, "/api/anchor", {"hash_hex": HASH_D}, headers)
    assert s2 == 401, b2
    assert "already used" in b2["error"]
    assert len(_ledger_rows(tmp_path)) == before, "a replay must not even attempt settlement"
    assert "receipt_id" not in b2, "no second anchor was created"
    assert _receipts_on_disk_for(tmp_path, HASH_D) == []


def test_recased_nonce_resubmission_is_the_same_payment_and_is_rejected(server, tmp_path):
    """THE reported exploit. The single-use identifier used to be the raw
    nonce STRING, so 0xAABB... and 0xaabb... — the same bytes32 on-chain,
    the same signed authorization — were two different local claims. The
    re-cased resubmission read as a brand-new payment, bought a second
    anchor, and only then failed to settle, which (settle-after-anchor)
    changed nothing about the 200 already sent. Same payment, same answer."""
    _exhaust_free_tier(server)
    upper = "0x" + "AB" * 32
    s, _h, b = _post(server, "/api/anchor", {"hash_hex": HASH_B},
                     _payment_headers(nonce=upper))
    assert s == 200, b
    before = len(_ledger_rows(tmp_path))
    s2, _h2, b2 = _post(server, "/api/anchor", {"hash_hex": HASH_D},
                        _payment_headers(nonce=upper.lower()))
    assert s2 == 401, b2
    assert "already used" in b2["error"]
    assert "receipt_id" not in b2 and "receipt_url" not in b2
    assert _receipts_on_disk_for(tmp_path, HASH_D) == [], "the re-cased nonce bought an anchor"
    assert len(_ledger_rows(tmp_path)) == before, "a replay must not even attempt settlement"


def test_payment_identifier_ignores_hex_case_and_nothing_else():
    """Unit-level pin of the same defect, with its negative control."""
    upper = _signed_payload(nonce="0x" + "AB" * 32)
    lower = _signed_payload(nonce="0x" + "ab" * 32)
    other = _signed_payload(nonce="0x" + "ac" * 32)
    assert x402_const.payment_identifier(upper) == x402_const.payment_identifier(lower)
    assert x402_const.payment_identifier(upper) != x402_const.payment_identifier(other)


def test_settle_failure_means_no_anchor_and_no_receipt(server, tmp_path):
    """The core of the fix: settle() runs — and must SUCCEED — before
    engine.anchor_hash runs at all. A refused settlement gets a 402 and no
    receipt anywhere: not in the body, not on disk, not at /r/<id>. The
    failed attempt is recorded for observability but is not a charge, so
    the same payload can be resubmitted and settle for real."""
    _exhaust_free_tier(server)
    before = len(_ledger_rows(tmp_path))
    headers = _payment_headers(nonce="n6", outcome="settle_fail")
    s, h, b = _post(server, "/api/anchor", {"hash_hex": HASH_B}, headers)
    assert s == 402, b
    assert "settlement failed" in b["settle_reason"]
    assert "receipt_id" not in b and "receipt_url" not in b
    assert x402_const.PAYMENT_RESPONSE_HEADER not in h
    assert _receipts_on_disk_for(tmp_path, HASH_B) == [], "anchored before settlement"
    rows = _ledger_rows(tmp_path)
    assert len(rows) == before + 1
    assert rows[-1]["settled"] is False
    assert "settlement failed" in rows[-1]["reason"]
    assert _settled_rows(tmp_path) == []
    s2, h2, b2 = _post(server, "/api/anchor", {"hash_hex": HASH_B},
                       _payment_headers(nonce="n6", outcome="ok"))
    assert s2 == 200, b2
    assert b2["x402_settled"] is True
    assert _payment_response(h2)["success"] is True


def test_settled_payment_is_held_through_a_calendar_outage_and_redeemed_after(server, tmp_path):
    """Fair retry. settle() succeeded, then every calendar refused: the
    anchor is worthless (no Bitcoin commitment, can never upgrade), so the
    payment is HELD, not spent and not lost. The identical payload, once
    calendars answer again, produces a real anchor on the money already
    collected — settle() is not called a second time (the transaction in
    the delivered PAYMENT-RESPONSE is the ORIGINAL one, and the ledger holds
    exactly one charge) — and after that it is a replay like any other."""
    _exhaust_free_tier(server)
    headers = _payment_headers(nonce="n5")
    down = tmp_path / CALENDARS_DOWN
    down.write_text("")
    s, h, b = _post(server, "/api/anchor", {"hash_hex": HASH_E}, headers)
    assert s == 200, b  # receipt still returned, per existing policy
    assert b["calendars_ok"] == 0
    assert b.get("x402_settled") is True, "settle() must have run before the anchor"
    assert b["x402_payment_held"] is True
    assert b["credit_refunded"] is True
    assert x402_const.PAYMENT_RESPONSE_HEADER not in h, "not delivered, so no delivery header"
    charges = _settled_rows(tmp_path)
    assert len(charges) == 1
    original_tx = charges[0]["tx_hash"]
    assert original_tx.startswith("0xmock")
    down.unlink()
    s2, h2, b2 = _post(server, "/api/anchor", {"hash_hex": HASH_E}, headers)
    assert s2 == 200, b2
    assert b2["calendars_ok"] > 0
    assert b2["x402_settled"] is True
    assert b2["x402_payment_held"] is False
    assert _payment_response(h2)["transaction"] == original_tx, "a second settle() ran"
    assert len(_settled_rows(tmp_path)) == 1, "the held payment was charged again"
    delivered = [r for r in _ledger_rows(tmp_path) if r.get("delivered")]
    assert [r["receipt_id"] for r in delivered] == [b2["receipt_id"]]
    s3, _h3, b3 = _post(server, "/api/anchor", {"hash_hex": HASH_D}, headers)
    assert s3 == 401, b3
    assert _receipts_on_disk_for(tmp_path, HASH_D) == []


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
