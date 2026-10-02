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
    # The header IS the requirements, base64 JSON, exactly as the reference
    # server sends it (x402 http/utils.py encode_payment_required_header): a
    # v2 client reads the requirements only from here. It used to say "true",
    # which this test pinned, and no real v2 client could have paid.
    hdr = h.get(x402_const.PAYMENT_REQUIRED_HEADER)
    decoded = json.loads(base64.b64decode(hdr))
    assert decoded["x402Version"] == 2 and decoded["accepts"][0]["payTo"] == PAY_TO
    for field in ("scheme", "network", "asset", "amount", "payTo", "maxTimeoutSeconds"):
        assert field in decoded["accepts"][0], field
    assert None not in decoded.values()
    # A caller that does not speak x402 still gets the classic answer's facts.
    assert h.get("Retry-After") and h.get("Cache-Control") == "no-store"
    assert b["retry_after_seconds"] >= 1 and b["limit_per_day"] >= 1 and "Pack" in b["hint"]
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


def test_facilitator_unreachable_on_settle_anchors_nothing_and_charges_nothing(server, tmp_path):
    """A distinct case now that settle() runs BEFORE the anchor: verify()
    answered, settle() did not. The outcome on-chain is unknown, so no
    anchor is made on an unproven payment and nothing counts as a charge.
    What IS written is one audit-only attempt row (settled=false, the
    no-answer reason): the only record that the office asked the network to
    move this customer's money, and it must not block the retry."""
    _exhaust_free_tier(server)
    before = len(_ledger_rows(tmp_path))
    assert _settled_rows(tmp_path) == []
    headers = _payment_headers(nonce="n3s", outcome="unreachable_settle")
    s, _h, b = _post(server, "/api/anchor", {"hash_hex": HASH_B}, headers)
    assert s == 503, b
    assert "settle" in b["error"]
    assert "receipt_id" not in b and "receipt_url" not in b
    assert _receipts_on_disk_for(tmp_path, HASH_B) == []
    assert _settled_rows(tmp_path) == [], "an unknown outcome is not a charge"
    rows = _ledger_rows(tmp_path)
    assert len(rows) == before + 1, "exactly one attempt row, no more"
    assert rows[-1]["settled"] is False
    assert rows[-1]["reason"] == x402_const.NO_ANSWER_REASON
    assert rows[-1]["id"] == x402_const.payment_identifier(
        _signed_payload(nonce="n3s", outcome="unreachable_settle"))
    # Same payload, facilitator back: a fresh settle, then the anchor. The
    # attempt row did not block it.
    headers2 = _payment_headers(nonce="n3s", outcome="ok")
    s2, _h2, b2 = _post(server, "/api/anchor", {"hash_hex": HASH_B}, headers2)
    assert s2 == 200, b2
    assert b2["x402_settled"] is True
    # One settle event (settled, not yet delivered) plus the delivery marker
    # mark_delivered() appends for it — never a second settle.
    settled = _settled_rows(tmp_path)
    assert len(settled) == 1, "the retry settled for real, exactly once"
    assert settled[0]["id"] == rows[-1]["id"], "same identifier as the attempt it redeems"
    delivered = [r for r in _ledger_rows(tmp_path) if r.get("delivered")]
    assert [r["receipt_id"] for r in delivered] == [b2["receipt_id"]]


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
    # A different request body: no second anchor, and no receipt named —
    # only the identical request that paid is told what it bought.
    assert "receipt_id" not in b2 and "receipt_url" not in b2, "no second anchor was created"
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
    # The identical request, re-cased nonce: the SAME payment, told its receipt.
    s3, _h3, b3 = _post(server, "/api/anchor", {"hash_hex": HASH_B},
                        _payment_headers(nonce=upper.lower()))
    assert s3 == 401, b3
    assert b3.get("receipt_id") == b["receipt_id"], "the re-cased nonce is the SAME payment"
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


# ── second review round (2026-09-30): findings on the settle-before-anchor fix ──
#
# A two-lens adversarial review of a2d37d2 confirmed, with independent
# reproductions: (1) the "held" branch bound redemption to nothing but the
# nonce, so a stranger who knew a held payment's nonce got the anchor on the
# victim's money; (2) `private: true` from a non-subscriber was charged before
# its own (payment-free) check refused it; (3) a failure between claim() and
# release()/mark_delivered() stranded a charged payment as "delivered" with
# no way back; (4) a settle() answer lost after the transfer landed was later
# refused with a hint that said nothing was charged; (5) the atomic claim()
# could be deleted with this file staying green. Each test below failed on
# a2d37d2 for the stated reason before the fix.

import concurrent.futures
import os


def _headers_for(payload: dict) -> dict:
    raw = base64.b64encode(json.dumps(payload).encode()).decode()
    return {x402_const.PAYMENT_SIGNATURE_HEADER: raw}


def test_a_stranger_with_only_the_nonce_cannot_redeem_a_held_payment(server, tmp_path):
    """Review finding 1 (HIGH). Redemption of a HELD payment must be bound to
    the signed payload that paid, not to the nonce, which is public on-chain
    once settled."""
    _exhaust_free_tier(server)
    victim = _signed_payload(nonce="n9", payer="0x1111111111111111111111111111111111111111")
    down = tmp_path / CALENDARS_DOWN
    down.write_text("")
    s, _h, b = _post(server, "/api/anchor", {"hash_hex": HASH_E}, _headers_for(victim))
    assert s == 200 and b["x402_payment_held"] is True, b
    down.unlink()
    original_tx = _settled_rows(tmp_path)[0]["tx_hash"]
    # The stranger knows the nonce (even re-cased), nothing else that is
    # the victim's: their own from-address, a junk signature, and a payload
    # the facilitator would refuse if it were ever asked.
    stranger = _signed_payload(nonce="N9", outcome="invalid",
                               payer="0x2222222222222222222222222222222222222222")
    stranger["payload"]["signature"] = "0x" + "00" * 65
    s2, h2, b2 = _post(server, "/api/anchor", {"hash_hex": HASH_D}, _headers_for(stranger))
    assert s2 == 401, b2
    assert _receipts_on_disk_for(tmp_path, HASH_D) == [], "the stranger got an anchor on the victim's money"
    assert x402_const.PAYMENT_RESPONSE_HEADER not in h2
    assert len(_settled_rows(tmp_path)) == 1
    # The victim's identical payload still redeems the held payment.
    s3, h3, b3 = _post(server, "/api/anchor", {"hash_hex": HASH_E}, _headers_for(victim))
    assert s3 == 200 and b3["x402_payment_held"] is False, b3
    assert _payment_response(h3)["transaction"] == original_tx
    # Delivered now. The stranger's replay is still a bare 401: the nonce is
    # public on-chain, so the receipt it bought is named only to the payload
    # that paid (test_a_delivered_replay_names_the_receipt_it_bought).
    s4, _h4, b4 = _post(server, "/api/anchor", {"hash_hex": HASH_D}, _headers_for(stranger))
    assert s4 == 401, b4
    assert "receipt_id" not in b4 and "receipt_url" not in b4, "payment -> receipt link leaked to a stranger"
    assert _receipts_on_disk_for(tmp_path, HASH_D) == []


def test_private_true_without_a_subscription_is_refused_before_any_charge(server, tmp_path):
    """Review finding 2 (MEDIUM). The private-receipt check needs no
    facilitator and no payment; it must run before settle(), so a request
    that will be refused is never charged."""
    _exhaust_free_tier(server)
    headers = _payment_headers(nonce="n10")
    s, _h, b = _post(server, "/api/anchor", {"hash_hex": HASH_B, "private": True}, headers)
    assert s == 402, b
    assert "private" in json.dumps(b).lower()
    assert _ledger_rows(tmp_path) == [], "charged before the private check refused the request"
    # Nothing was consumed: the same payload without `private` pays once, now.
    s2, _h2, b2 = _post(server, "/api/anchor", {"hash_hex": HASH_B}, headers)
    assert s2 == 200 and b2["x402_settled"] is True, b2
    assert len(_settled_rows(tmp_path)) == 1


@pytest.fixture()
def stranded_server(tmp_path):
    """A data dir seeded as a crash between claim() and delivery leaves it:
    a settled charge row, a standing claim, no delivery row, no receipt."""
    payload = _signed_payload(nonce="n11")
    ident = x402_const.payment_identifier(payload)
    digest = getattr(x402_const, "payment_digest", lambda p: "")(payload)
    request_digest = getattr(x402_const, "request_digest", lambda b: "")({"hash_hex": HASH_E})
    (tmp_path / "x402_ledger.jsonl").write_text(json.dumps({
        "ts": 1, "id": ident, "receipt_id": "", "amount_atomic": "50000",
        "asset": x402_const.USDC_BASE_SEPOLIA, "network": "eip155:84532",
        "tx_hash": "0xmockstranded", "payer": "0xagentPayer0000000000000000000000000001",
        "settled": True, "delivered": False, "reason": "", "digest": digest,
        "request_digest": request_digest}) + "\n")
    (tmp_path / "x402_claimed.jsonl").write_text(json.dumps({
        "id": ident, "receipt_id": "", "claimed_at": 1}) + "\n")
    yield from _srv.server_processes(tmp_path, stub_calendars=True, **X402_ENV)


def test_a_claim_stranded_by_a_crash_is_released_at_boot_and_redeemable(stranded_server, tmp_path):
    """Review finding 3 (MEDIUM). A standing claim with no delivery row and
    no receipt is a payment the customer never got. No anchor is in flight
    at boot, so the server releases such claims, and the identical payload
    then redeems the charge instead of meeting a permanent 401."""
    _exhaust_free_tier(stranded_server)
    headers = _payment_headers(nonce="n11")
    s, h, b = _post(stranded_server, "/api/anchor", {"hash_hex": HASH_E}, headers)
    assert s == 200, b
    assert b["x402_settled"] is True and b["x402_payment_held"] is False
    assert _payment_response(h)["transaction"] == "0xmockstranded", "a second settle() ran"
    assert len(_settled_rows(tmp_path)) == 1


def test_a_lost_settle_answer_is_recorded_and_a_later_used_refusal_says_so(server, tmp_path):
    """Review finding 4 (MEDIUM). When settle() gets no answer the on-chain
    outcome is unknown; that attempt is recorded, and if the chain later
    refuses the same authorization as used, the refusal must not claim
    nothing was charged."""
    _exhaust_free_tier(server)
    headers = _payment_headers(nonce="n12", outcome="unreachable_settle")
    s, _h, b = _post(server, "/api/anchor", {"hash_hex": HASH_B}, headers)
    assert s == 503, b
    attempts = [r for r in _ledger_rows(tmp_path) if not r.get("settled")]
    assert len(attempts) == 1 and "answer" in attempts[0].get("reason", ""), attempts
    assert _settled_rows(tmp_path) == []
    headers2 = _payment_headers(nonce="n12", outcome="settle_fail")
    s2, _h2, b2 = _post(server, "/api/anchor", {"hash_hex": HASH_B}, headers2)
    assert s2 == 402, b2
    assert b2.get("x402_earlier_attempt") is True
    assert "nothing was charged" not in json.dumps(b2).lower()
    assert _receipts_on_disk_for(tmp_path, HASH_B) == []


def test_a_delivered_replay_names_the_receipt_it_bought(server, tmp_path):
    """A response lost after delivery must not leave the customer with only
    a 401: the replay answer names the receipt the payment bought."""
    _exhaust_free_tier(server)
    headers = _payment_headers(nonce="n14")
    s, _h, b = _post(server, "/api/anchor", {"hash_hex": HASH_B}, headers)
    assert s == 200, b
    s2, _h2, b2 = _post(server, "/api/anchor", {"hash_hex": HASH_B}, headers)
    assert s2 == 401, b2
    assert b2.get("receipt_id") == b["receipt_id"]
    # A different request with the same payload is not the one that paid:
    # the signed payload is public on-chain once settled, so it proves
    # nothing about who is asking. No receipt is named to it.
    s3, _h3, b3 = _post(server, "/api/anchor", {"hash_hex": HASH_D}, headers)
    assert s3 == 401, b3
    assert "receipt_id" not in b3 and "receipt_url" not in b3


def test_concurrent_identical_payloads_buy_exactly_one_anchor(server, tmp_path):
    """Review finding 5 (LOW). Pins the atomic claim(): eight identical
    submissions at once must produce exactly one receipt. The sequential
    replay tests are satisfied by the settlement-state pre-check alone;
    this one is not."""
    _exhaust_free_tier(server)
    headers = _payment_headers(nonce="n13")
    def go(_):
        return _post(server, "/api/anchor", {"hash_hex": HASH_E}, headers)[0]
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        statuses = list(pool.map(go, range(8)))
    assert statuses.count(200) == 1, statuses
    assert set(statuses) <= {200, 401, 402}, statuses
    assert len(_receipts_on_disk_for(tmp_path, HASH_E)) == 1


def test_a_settled_payment_whose_ledger_row_cannot_be_written_is_not_a_500(server, tmp_path):
    """Self-review of the round-2 fix. settle() succeeded (money moved) and
    the one row that proves it could not be written. That used to escape as
    a generic 500 with no transaction id in it. Now: 503, no anchor, the
    transaction id in the body, and the server still answering afterwards.

    The ledger is made unwritable but not unreadable with a dangling
    symlink into a directory that does not exist: settlement_state() sees
    no file (unpaid, no read attempted), record_settlement()'s append
    fails with ENOENT. Portable — no chmod games, which file_lock.locked()
    undoes on every open anyway."""
    _exhaust_free_tier(server)
    ledger = tmp_path / "x402_ledger.jsonl"
    assert not ledger.exists()
    ledger.symlink_to(tmp_path / "no-such-dir" / "x402_ledger.jsonl")
    headers = _payment_headers(nonce="n13")
    s, h, b = _post(server, "/api/anchor", {"hash_hex": HASH_B}, headers)
    assert s == 503, b
    assert "settled" in b["error"] and "record" in b["error"]
    assert str(b.get("x402_transaction", "")).startswith("0xmock"), "the customer's proof is the tx id"
    assert "receipt_id" not in b and "receipt_url" not in b
    assert x402_const.PAYMENT_RESPONSE_HEADER not in h
    assert "x402_ledger.jsonl" not in json.dumps(b) and tmp_path.name not in json.dumps(b), \
        "the server's filesystem layout leaked into the body"
    assert _receipts_on_disk_for(tmp_path, HASH_B) == []
    assert _ledger_rows(tmp_path) == []
    # Ledger back (the office fixed its disk). The record is gone for good:
    # the identical payload reads as unpaid, settle() is asked again and the
    # network refuses the spent nonce. That is the stated limit — what must
    # hold is that it is a refusal, not an anchor and not a crash.
    ledger.unlink()
    s2, _h2, b2 = _post(server, "/api/anchor", {"hash_hex": HASH_B}, headers)
    assert s2 == 402, b2
    assert "settlement failed" in b2["error"]
    # One response ago the server said the transfer landed; it must not now
    # say the opposite.
    assert "nothing was charged" not in json.dumps(b2).lower(), b2
    assert b2.get("x402_earlier_attempt") is True, b2
    assert _receipts_on_disk_for(tmp_path, HASH_B) == []


def _chain_observer_payload(victim: dict) -> dict:
    """Exactly what the settling transaction publishes and nothing more.
    The exact-EVM scheme settles with USDC's EIP-3009
    transferWithAuthorization(from, to, value, validAfter, validBefore,
    nonce, signature): every signed field is in the calldata, decoded by any
    block explorer. The decoder's casing and key order are its own; there is
    no `accepted`, no `x402Version` 2, no test-only mock field."""
    inner = victim["payload"]
    auth = inner["authorization"]
    rebuilt = {k: (v.upper() if isinstance(v, str) else v) for k, v in reversed(list(auth.items()))}
    return {"x402Version": 1, "payload": {"signature": inner["signature"].upper(),
                                          "authorization": rebuilt}}


def test_a_chain_observer_cannot_redeem_a_held_payment_or_learn_its_receipt(server, tmp_path):
    """Review of 672be2f (HIGH, reproduced by two finders and by hand). The
    round-2 binding hashed the signature and the authorization, and both are
    public on-chain the moment the payment settles: an observer rebuilt the
    payload from calldata, anchored THEIR hash on the victim's USDC, and the
    victim's own resubmission was then told the observer's receipt. A
    redemption must now be the identical REQUEST that paid (its whole body;
    key order ignored), so the most anyone can make of a rebuilt payload is
    the exact anchor the payer asked for."""
    _exhaust_free_tier(server)
    victim = _signed_payload(nonce="n20", payer="0x3333333333333333333333333333333333333333")
    body = {"hash_hex": HASH_E, "client_label": "victim-label"}
    down = tmp_path / CALENDARS_DOWN
    down.write_text("")
    s, _h, b = _post(server, "/api/anchor", body, _headers_for(victim))
    assert s == 200 and b["x402_payment_held"] is True, b
    down.unlink()
    original_tx = _settled_rows(tmp_path)[0]["tx_hash"]
    held_receipts = sorted(_receipts_on_disk_for(tmp_path, HASH_E))
    observer = _chain_observer_payload(victim)
    # The premise the round-2 binding rested on is false: calldata alone
    # reproduces the digest.
    assert x402_const.payment_digest(observer) == x402_const.payment_digest(victim)
    # (a) the observer's own hash
    s2, h2, b2 = _post(server, "/api/anchor", {"hash_hex": HASH_D}, _headers_for(observer))
    assert s2 == 401, b2
    assert "receipt_id" not in b2 and x402_const.PAYMENT_RESPONSE_HEADER not in h2
    assert _receipts_on_disk_for(tmp_path, HASH_D) == [], "an anchor on the victim's money"
    # (b) the victim's hash, the observer's label / metadata
    for other in ({"hash_hex": HASH_E, "client_label": "attacker"},
                  {"hash_hex": HASH_E, "client_label": "victim-label", "metadata": {"k": "v"}},
                  {"hash_hex": HASH_E}):
        s3, _h3, b3 = _post(server, "/api/anchor", other, _headers_for(observer))
        assert s3 == 401, (other, b3)
        assert "receipt_id" not in b3
    assert sorted(_receipts_on_disk_for(tmp_path, HASH_E)) == held_receipts
    assert len(_settled_rows(tmp_path)) == 1, "no second charge"
    # (d) the victim's identical request, keys reordered, still redeems
    s4, h4, b4 = _post(server, "/api/anchor", {"client_label": "victim-label", "hash_hex": HASH_E},
                       _headers_for(victim))
    assert s4 == 200 and b4["x402_payment_held"] is False, b4
    assert _payment_response(h4)["transaction"] == original_tx
    # (c) delivered: a different request is told nothing about the receipt
    s5, _h5, b5 = _post(server, "/api/anchor", {"hash_hex": HASH_D}, _headers_for(observer))
    assert s5 == 401, b5
    assert "receipt_id" not in b5 and "receipt_url" not in b5, "payment -> receipt link leaked"
    # the identical request that paid is told its receipt
    s6, _h6, b6 = _post(server, "/api/anchor", body, _headers_for(victim))
    assert s6 == 401 and b6.get("receipt_id") == b4["receipt_id"], b6


def test_a_verify_answer_after_an_unanswered_settle_never_asks_for_a_second_payment(server, tmp_path):
    """Review of 672be2f (MEDIUM). After a settle() that got no answer, the
    network's refusal of the identical resubmission can come at verify()
    (a spent nonce, or a wallet the landed transfer drained). That answer
    said "request a fresh challenge"; a verify() that does not answer said
    "nothing was charged". Both must carry the earlier attempt instead."""
    _exhaust_free_tier(server)
    s, _h, b = _post(server, "/api/anchor", {"hash_hex": HASH_B},
                     _payment_headers(nonce="n21", outcome="unreachable_settle"))
    assert s == 503, b
    s2, _h2, b2 = _post(server, "/api/anchor", {"hash_hex": HASH_B},
                        _payment_headers(nonce="n21", outcome="unreachable"))
    assert s2 == 503, b2
    assert b2.get("x402_earlier_attempt") is True, b2
    assert "nothing was charged" not in json.dumps(b2).lower(), b2
    s3, _h3, b3 = _post(server, "/api/anchor", {"hash_hex": HASH_B},
                        _payment_headers(nonce="n21", outcome="invalid"))
    assert s3 == 402, b3
    assert b3.get("x402_earlier_attempt") is True, b3
    assert "fresh challenge" not in json.dumps(b3).lower(), b3
    assert _receipts_on_disk_for(tmp_path, HASH_B) == []
    # A fresh authorization with no history still gets the plain answers.
    s4, _h4, b4 = _post(server, "/api/anchor", {"hash_hex": HASH_B},
                        _payment_headers(nonce="n22", outcome="invalid"))
    assert s4 == 402 and "x402_earlier_attempt" not in b4, b4


def test_an_unreadable_payment_ledger_is_a_503_without_the_servers_paths(server, tmp_path):
    """Review of 672be2f (LOW). The office-side 503s interpolated the raw
    OSError, which carries the data directory's absolute path. A directory
    where the ledger file should be makes every read fail portably."""
    _exhaust_free_tier(server)
    (tmp_path / "x402_ledger.jsonl").mkdir()
    s, _h, b = _post(server, "/api/anchor", {"hash_hex": HASH_B}, _payment_headers(nonce="n23"))
    assert s == 503, b
    text = json.dumps(b)
    assert "x402_ledger.jsonl" not in text and tmp_path.name not in text, b
    # Review of ca45f33 (MEDIUM): this answer comes before any money moves
    # for THIS request, but the office cannot see the payment's history, so
    # it must not claim nothing was ever charged.
    assert "nothing was charged" not in text.lower(), b
    assert "moved no money" in text.lower(), b
    assert _receipts_on_disk_for(tmp_path, HASH_B) == []


def test_an_unwritable_claim_file_after_a_real_charge_is_a_503_and_the_payment_is_held(server, tmp_path):
    """Self-review of 672be2f. claim() mapped a failed READ of the claim
    file to ClaimSetUnavailable but not a failed OPEN, which escaped as an
    unhandled exception — a dropped connection — after settle() had moved
    the money. It is a 503 that says the payment is held, and once the
    file is back the identical request redeems it without a second settle."""
    _exhaust_free_tier(server)
    claim_file = tmp_path / "x402_claimed.jsonl"
    claim_file.mkdir()
    headers = _payment_headers(nonce="n24")
    s, _h, b = _post(server, "/api/anchor", {"hash_hex": HASH_B}, headers)
    assert s == 503, b
    assert "held" in json.dumps(b).lower(), b
    assert "x402_claimed.jsonl" not in json.dumps(b) and tmp_path.name not in json.dumps(b), b
    assert _receipts_on_disk_for(tmp_path, HASH_B) == []
    assert len(_settled_rows(tmp_path)) == 1
    claim_file.rmdir()
    s2, h2, b2 = _post(server, "/api/anchor", {"hash_hex": HASH_B}, headers)
    assert s2 == 200 and b2["x402_settled"] is True, b2
    assert _payment_response(h2)["transaction"] == _settled_rows(tmp_path)[0]["tx_hash"]


@pytest.mark.parametrize("inner", ["a string", ["a", "list"], {"authorization": "a string"},
                                   {"authorization": ["a", "list"]}, {"signature": {"not": "a string"}}])
def test_a_malformed_inner_payload_is_a_400_not_a_dropped_connection(server, tmp_path, inner):
    """Review of 672be2f (noted by the security lens). payment_identifier()
    called .get() on whatever `payload` held; a string there raised
    AttributeError and the client saw its connection dropped."""
    _exhaust_free_tier(server)
    raw = base64.b64encode(json.dumps({"x402Version": 2, "payload": inner}).encode()).decode()
    s, _h, b = _post(server, "/api/anchor", {"hash_hex": HASH_B},
                     {x402_const.PAYMENT_SIGNATURE_HEADER: raw})
    assert s == 400, b
    assert "x402" in b["error"]
    # the server is still there, and the ordinary rail still works
    s2, _h2, b2 = _post(server, "/api/anchor", {"hash_hex": HASH_B}, _payment_headers(nonce="n25"))
    assert s2 == 200, b2


# ── round 4 (review of ca45f33, wf_1f1cbe7e-d74) ─────────────────────────

VICTIM_EMAIL = "victim@example.test"
OTHER_EMAIL = "other@example.test"


def _sha256_text(s: str) -> str:
    import hashlib
    return hashlib.sha256(s.encode()).hexdigest()


def _append_jsonl(path: Path, *rows: dict) -> None:
    with path.open("a") as f:
        for r in rows:
            f.write(json.dumps(r, separators=(",", ":")) + "\n")


def _subscription_row(customer: str, sub: str, email: str, status: str = "active",
                      event: str = "customer.subscription.created") -> dict:
    import time
    return {"ts": "2026-09-01T00:00:00+00:00", "event_type": event,
            "stripe_customer": customer, "stripe_sub": sub, "email": email,
            "status": status, "current_period_end": time.time() + 20 * 86400,
            "cancel_at_period_end": False}


def _cookie(sid: str) -> dict:
    return {"Cookie": f"orpho_sid={sid}"}


@pytest.fixture()
def subscriber_server(tmp_path):
    """Two signed-in subscribers, `sess-victim` and `sess-other`."""
    import time
    _append_jsonl(tmp_path / "auth_sessions.jsonl",
                  {"event": "created", "session_hash": _sha256_text("sess-victim"),
                   "email": VICTIM_EMAIL, "expires_unix": time.time() + 86400},
                  {"event": "created", "session_hash": _sha256_text("sess-other"),
                   "email": OTHER_EMAIL, "expires_unix": time.time() + 86400})
    _append_jsonl(tmp_path / "stripe_customer_emails.jsonl",
                  {"ts": "2026-09-01T00:00:00+00:00", "stripe_customer": "cus_v", "email": VICTIM_EMAIL},
                  {"ts": "2026-09-01T00:00:00+00:00", "stripe_customer": "cus_o", "email": OTHER_EMAIL})
    _append_jsonl(tmp_path / "subscriptions.jsonl",
                  _subscription_row("cus_v", "sub_v", VICTIM_EMAIL),
                  _subscription_row("cus_o", "sub_o", OTHER_EMAIL))
    yield from _srv.server_processes(tmp_path, stub_calendars=True, stub_stripe=True,
                                     STRIPE_SECRET_KEY="", **X402_ENV)


def _hold(base: str, tmp_path: Path, body: dict, payload: dict, headers: dict | None = None) -> dict:
    """Pay with every calendar refusing, so the charge is HELD."""
    down = tmp_path / CALENDARS_DOWN
    down.write_text("")
    s, _h, b = _post(base, "/api/anchor", body, {**_headers_for(payload), **(headers or {})})
    down.unlink()
    assert s == 200 and b["x402_payment_held"] is True, b
    return b


def _receipt_json(tmp_path: Path, rid: str) -> dict:
    return json.loads((tmp_path / "receipts" / rid / "receipt.json").read_text())


def test_plain_verify_answers_speak_only_for_this_request(server, tmp_path):
    """Review of ca45f33 (MEDIUM). With no attempt on record, a verify()
    that does not answer said "nothing was charged" and a refusal said only
    "request a fresh challenge". The office cannot know every earlier call
    (a disk that failed to record one is exactly the case it cannot see), so
    both answers now speak for this request only and point at the wallet."""
    _exhaust_free_tier(server)
    s, _h, b = _post(server, "/api/anchor", {"hash_hex": HASH_B},
                     _payment_headers(nonce="n40", outcome="unreachable"))
    assert s == 503, b
    text = json.dumps(b).lower()
    assert "nothing was charged" not in text, b
    assert "moved no money" in text and "wallet" in text, b
    s2, _h2, b2 = _post(server, "/api/anchor", {"hash_hex": HASH_B},
                        _payment_headers(nonce="n41", outcome="invalid"))
    assert s2 == 402, b2
    text2 = json.dumps(b2).lower()
    assert "moved no money" in text2 and "wallet" in text2, b2
    assert "x402_earlier_attempt" not in b2


def test_a_held_private_payment_stays_its_payers_whoever_redeems_it(subscriber_server, tmp_path):
    """Review of ca45f33 (identity). The body is digest-bound; the session is
    not. A second subscriber presenting the victim's identical private body
    with the payload rebuilt from calldata redeemed the held payment, and the
    paid private receipt was OWNED BY THE REDEEMER: the victim was told its id
    and got a 404 for it. The owner is now the one recorded when the charge
    was made."""
    base = subscriber_server
    victim = _signed_payload(nonce="n42", payer="0x4444444444444444444444444444444444444444")
    body = {"hash_hex": HASH_E, "private": True}
    _hold(base, tmp_path, body, victim, _cookie("sess-victim"))
    s, _h, b = _post(base, "/api/anchor", body,
                     {**_headers_for(_chain_observer_payload(victim)), **_cookie("sess-other")})
    assert s == 200 and b["x402_payment_held"] is False, b
    r1 = b["receipt_id"]
    sv, _ = _srv.get_json(base, f"/api/receipt/{r1}", headers=_cookie("sess-victim"))
    so, _ = _srv.get_json(base, f"/api/receipt/{r1}", headers=_cookie("sess-other"))
    assert sv == 200, "the payer cannot see the private receipt they paid for"
    assert so == 404, "the redeemer owns the payer's private receipt"
    s2, _h2, b2 = _post(base, "/api/anchor", body, {**_headers_for(victim), **_cookie("sess-victim")})
    assert s2 == 401 and b2.get("receipt_id") == r1, b2


def test_a_held_redemption_never_files_the_redeemers_address(subscriber_server, tmp_path):
    """Review of ca45f33 (identity). An anonymous payer's held payment,
    redeemed by a signed-in subscriber with the identical body, persisted the
    REDEEMER's address as the receipt's notify_email: receipt mail, the
    anchor.created webhook and the Bitcoin-pin notice for the payer's anchor
    went to a stranger. On a held redemption only the digest-bound body can
    name an address."""
    base = subscriber_server
    _exhaust_free_tier(base)
    victim = _signed_payload(nonce="n43", payer="0x5555555555555555555555555555555555555555")
    body = {"hash_hex": HASH_E}
    _hold(base, tmp_path, body, victim)
    s, _h, b = _post(base, "/api/anchor", body,
                     {**_headers_for(_chain_observer_payload(victim)), **_cookie("sess-other")})
    assert s == 200 and b["x402_payment_held"] is False, b
    assert "notify_email" not in _receipt_json(tmp_path, b["receipt_id"]), "the redeemer's address was filed"


def test_the_payer_redeeming_their_own_held_payment_keeps_their_account_behaviour(subscriber_server, tmp_path):
    """The honest caller on the same path: a subscriber whose own payment
    was held, redeeming it under their own session, still gets the receipt
    mailed to their account address, exactly as a first-try anchor would."""
    base = subscriber_server
    victim = _signed_payload(nonce="n44", payer="0x6666666666666666666666666666666666666666")
    body = {"hash_hex": HASH_D}
    _hold(base, tmp_path, body, victim, _cookie("sess-victim"))
    s, _h, b = _post(base, "/api/anchor", body, {**_headers_for(victim), **_cookie("sess-victim")})
    assert s == 200 and b["x402_payment_held"] is False, b
    assert _receipt_json(tmp_path, b["receipt_id"]).get("notify_email") == VICTIM_EMAIL


def test_a_held_private_payment_survives_the_subscription_lapsing(subscriber_server, tmp_path):
    """Review of ca45f33 (LOW, a regression against 672be2f). The private
    gate re-checked the CURRENT subscription on a held redemption: once the
    payer's subscription ended, the identical request was refused (402) and
    any other body was foreign (401, "sign a fresh one"), so no request could
    redeem the charge. The charge was privacy-authorised when it was made."""
    base = subscriber_server
    victim = _signed_payload(nonce="n45", payer="0x7777777777777777777777777777777777777777")
    body = {"hash_hex": HASH_D, "private": True}
    _hold(base, tmp_path, body, victim, _cookie("sess-victim"))
    _append_jsonl(tmp_path / "subscriptions.jsonl",
                  _subscription_row("cus_v", "sub_v", VICTIM_EMAIL, status="canceled",
                                    event="customer.subscription.deleted"))
    s, _h, b = _post(base, "/api/anchor", body, {**_headers_for(victim), **_cookie("sess-victim")})
    assert s == 200 and b["x402_payment_held"] is False, b
    sv, _ = _srv.get_json(base, f"/api/receipt/{b['receipt_id']}", headers=_cookie("sess-victim"))
    assert sv == 200
    # A NEW private request from the lapsed account is still refused.
    s2, _h2, b2 = _post(base, "/api/anchor", {"hash_hex": HASH_C, "private": True},
                        {**_payment_headers(nonce="n46"), **_cookie("sess-victim")})
    assert s2 == 402 and "private" in json.dumps(b2).lower(), b2


def test_the_held_200_says_how_to_redeem(server, tmp_path):
    """Review of ca45f33 (LOW). Redemption now needs the identical request;
    the held 200 said only x402_payment_held: true, so a payer who resent
    with any changed field was told "already used — sign a fresh one"."""
    _exhaust_free_tier(server)
    b = _hold(server, tmp_path, {"hash_hex": HASH_B}, _signed_payload(nonce="n47"))
    hint = b.get("x402_hint", "")
    assert "identical request" in hint and "held" in hint, b


_FSYNC_FAULT = '''
import errno, os
_real = os.fsync
def _path_of(fd):
    try:
        import fcntl
        return fcntl.fcntl(fd, fcntl.F_GETPATH, b"\\0" * 1024).split(b"\\0", 1)[0].decode()
    except Exception:
        pass
    try:
        return os.readlink(f"/proc/self/fd/{fd}")
    except OSError:
        return ""
def _fsync(fd):
    dd = os.environ.get("ORPHO_DATA_DIR", "")
    if dd and os.path.exists(os.path.join(dd, "fault_claim_fsync")) \\
            and _path_of(fd).endswith("x402_claimed.jsonl"):
        raise OSError(errno.EIO, "injected claim fsync failure")
    return _real(fd)
os.fsync = _fsync
'''


@pytest.fixture()
def fsync_fault_server(tmp_path):
    """A server whose os.fsync fails on the claim file while
    <data>/fault_claim_fsync exists (macOS F_GETPATH, Linux /proc)."""
    import os
    site = tmp_path / "site"
    site.mkdir()
    (site / "sitecustomize.py").write_text(_FSYNC_FAULT)
    pp = os.pathsep.join(p for p in (str(site), os.environ.get("PYTHONPATH", "")) if p)
    yield from _srv.server_processes(tmp_path, stub_calendars=True, PYTHONPATH=pp, **X402_ENV)


def test_a_claim_whose_fsync_fails_is_released_so_the_payment_stays_held(fsync_fault_server, tmp_path):
    """Review of ca45f33 (LOW). claim() wrote its line, then fsync raised;
    the 503 said "held — resubmit", but the written line stood, so the
    resubmission read as delivered (401 "already used") and the charged
    payment bought nothing until the next boot. The claim is released under
    the same lock before the error propagates."""
    base = fsync_fault_server
    _exhaust_free_tier(base)
    fault = tmp_path / "fault_claim_fsync"
    fault.write_text("")
    headers = _payment_headers(nonce="n48")
    s, _h, b = _post(base, "/api/anchor", {"hash_hex": HASH_B}, headers)
    assert s == 503 and "held" in json.dumps(b).lower(), b
    assert len(_settled_rows(tmp_path)) == 1
    fault.unlink()
    s2, h2, b2 = _post(base, "/api/anchor", {"hash_hex": HASH_B}, headers)
    assert s2 == 200 and b2["x402_payment_held"] is False, b2
    assert _payment_response(h2)["transaction"] == _settled_rows(tmp_path)[0]["tx_hash"], "a second settle ran"


def test_a_failed_claim_write_never_lands_later_and_cannot_release_a_concurrent_claim(tmp_path, monkeypatch):
    """Review of d9d95e9 (LOW, reproduced). claim() wrote its line through the
    buffered file object. When the flush itself failed, the claim line and
    the release row both stayed in the buffer and reached the file at
    close(), which file_lock runs AFTER unlocking: a concurrent identical
    request that claimed in that window was then released by the late
    rows, so a delivered payment read as held and could buy a second anchor.

    Faults BOTH layers a claim write can go through (the buffered file's
    raw writes and os.write) so the test binds the invariant, not one
    implementation: once claim() has failed, nothing of its reaches the
    file afterwards, and the concurrent request's claim stands."""
    import errno
    import io
    import file_lock  # the module x402 locks through (server/ on sys.path)
    monkeypatch.setenv("ORPHO_DATA_DIR", str(tmp_path))
    payload = _signed_payload(nonce="0xrace1")
    body = {"hash_hex": HASH_B}
    ident = x402_const.payment_identifier(payload)
    x402_const.record_settlement(
        ident=ident, receipt_id="", amount_atomic="50000", asset_addr="0xusdc",
        network_id="eip155:84532", tx_hash="0xtx", payer="0x" + "11" * 20, settled=True,
        digest=x402_const.payment_digest(payload), request_digest=x402_const.request_digest(body))
    concurrent = {}
    real_open, real_write = open, os.write

    def second_request_claims():
        if "claimed" not in concurrent:
            concurrent["claimed"] = x402_const.claim(payload)[0]

    class FaultyRaw(io.FileIO):
        calls = 0

        def write(self, b):
            FaultyRaw.calls += 1
            if FaultyRaw.calls <= 2:
                raise OSError(errno.EIO, "injected")
            if FaultyRaw.calls == 3:          # close() flushing after LOCK_UN
                file_lock.open = real_open
                monkeypatch.setattr(os, "write", real_write)
                second_request_claims()
            return super().write(b)

    def faulty_open(path, mode="r", *a, **k):
        if str(path).endswith("x402_claimed.jsonl") and "+" in mode:
            raw = FaultyRaw(str(path), "a+")
            return io.TextIOWrapper(io.BufferedRandom(raw), encoding="utf-8")
        return real_open(path, mode, *a, **k)

    os_write_calls = {"n": 0}

    def faulty_os_write(fd, data):
        os_write_calls["n"] += 1
        if os_write_calls["n"] <= 2:
            raise OSError(errno.EIO, "injected")
        return real_write(fd, data)

    monkeypatch.setattr(file_lock, "open", faulty_open, raising=False)
    monkeypatch.setattr(os, "write", faulty_os_write)
    with pytest.raises(x402_const.ClaimSetUnavailable):
        x402_const.claim(payload)
    file_lock.open = real_open
    monkeypatch.setattr(os, "write", real_write)
    second_request_claims()   # no-op if it already ran inside close()
    assert concurrent["claimed"] is True
    assert x402_const.is_claimed(ident), "a late row from the failed claim released the concurrent one"
    assert x402_const.claim(payload)[0] is False, "a third identical request could claim again"


NOSUB_EMAIL, SUB_EMAIL = "nosub@example.test", "sub@example.test"


def _sha(s: str) -> str:
    import hashlib
    return hashlib.sha256(s.encode()).hexdigest()


def _append_rows(path: Path, *rows):
    with path.open("a") as f:
        for r in rows:
            f.write(json.dumps(r, separators=(",", ":")) + "\n")


@pytest.fixture()
def sessions_server(tmp_path):
    """Two signed-in callers: one with no subscription, one subscribed."""
    import time as _t
    _append_rows(tmp_path / "auth_sessions.jsonl",
                 {"event": "created", "session_hash": _sha("sess-nosub"), "email": NOSUB_EMAIL,
                  "expires_unix": _t.time() + 86400},
                 {"event": "created", "session_hash": _sha("sess-sub"), "email": SUB_EMAIL,
                  "expires_unix": _t.time() + 86400})
    _append_rows(tmp_path / "stripe_customer_emails.jsonl",
                 {"ts": "2026-09-01T00:00:00+00:00", "stripe_customer": "cus_s", "email": SUB_EMAIL})
    _append_rows(tmp_path / "subscriptions.jsonl",
                 {"ts": "2026-09-01T00:00:00+00:00", "event_type": "customer.subscription.created",
                  "stripe_customer": "cus_s", "stripe_sub": "sub_s", "email": SUB_EMAIL,
                  "status": "active", "current_period_end": _t.time() + 20 * 86400,
                  "cancel_at_period_end": False})
    yield from _srv.server_processes(tmp_path, stub_calendars=True, **X402_ENV)


def test_a_non_subscriber_charge_row_links_no_account(sessions_server, tmp_path):
    """Review of d9d95e9 (LOW, reproduced). A signed-in non-subscriber's
    public x402 anchor stored their account id on the charge row, the only
    place linking an account to an on-chain payer address, for an ownership
    use that can never apply to them (a private request is refused at
    charge time). The id is recorded only for a subscriber."""
    _exhaust_free_tier(sessions_server)
    s, _h, b = _post(sessions_server, "/api/anchor", {"hash_hex": HASH_B},
                     {**_payment_headers(nonce="n30"), "Cookie": "orpho_sid=sess-nosub"})
    assert s == 200 and b["x402_settled"] is True, b
    assert all(not r.get("owner_id") for r in _ledger_rows(tmp_path)), "account linked to a non-subscriber's payment"
    # control: a subscriber's charge still records the owner (held private redemption needs it)
    s2, _h2, b2 = _post(sessions_server, "/api/anchor", {"hash_hex": HASH_D},
                        {**_payment_headers(nonce="n31"), "Cookie": "orpho_sid=sess-sub"})
    assert s2 == 200, b2
    rows = [r for r in _ledger_rows(tmp_path) if r.get("id") == x402_const.payment_identifier(
        _signed_payload(nonce="n31"))]
    assert rows and all(r.get("owner_id") for r in rows), rows


@pytest.fixture()
def held_private_fault_server(tmp_path):
    """A held PRIVATE charge with a recorded owner, and a claim file that
    cannot be read (a directory where the file should be)."""
    payload = _signed_payload(nonce="n32")
    body = {"hash_hex": HASH_E, "private": True}
    (tmp_path / "x402_ledger.jsonl").write_text(json.dumps({
        "ts": 1, "id": x402_const.payment_identifier(payload), "receipt_id": "",
        "amount_atomic": "50000", "asset": x402_const.USDC_BASE_SEPOLIA, "network": "eip155:84532",
        "tx_hash": "0xmockheldprivate", "payer": "0xagentPayer0000000000000000000000000001",
        "settled": True, "delivered": False, "reason": "",
        "digest": x402_const.payment_digest(payload),
        "request_digest": x402_const.request_digest(body), "owner_id": "owner-hmac-id"}) + "\n")
    (tmp_path / "x402_claimed.jsonl").mkdir()
    yield from _srv.server_processes(tmp_path, stub_calendars=True, **X402_ENV)


def test_an_unreadable_claim_file_during_a_held_private_redemption_is_a_503_not_the_private_402(
        held_private_fault_server, tmp_path):
    """Review of d9d95e9 (LOW, reproduced). The held-private pre-check turned
    a claim-file read fault into "not a held redemption", so the payer met
    the private-gate 402 ("resend without private"), whose advice changes
    the body and ends at "sign a fresh one": a second payment for a charge
    that was redeemable all along. The fault gets the same 503 the main
    path gives ("resubmit the identical request shortly")."""
    _exhaust_free_tier(held_private_fault_server)
    s, _h, b = _post(held_private_fault_server, "/api/anchor", {"hash_hex": HASH_E, "private": True},
                     _payment_headers(nonce="n32"))
    assert s == 503, b
    assert "settlement state" in b["error"], b
    assert "identical request" in b.get("hint", ""), b
    assert "x402_ledger" not in json.dumps(b) and tmp_path.name not in json.dumps(b)
    assert _receipts_on_disk_for(tmp_path, HASH_E) == []


@pytest.fixture()
def unarmed_server(tmp_path):
    """Production's state today: no pay-to address, no mock. The facilitator
    URL points at a port nothing listens on, so any facilitator call the
    server makes shows up as a 503 instead of reaching the network."""
    yield from _srv.server_processes(tmp_path, stub_calendars=True,
                                     RATE_LIMIT_PER_DAY="1",
                                     ORPHO_X402_FACILITATOR_URL="http://127.0.0.1:9")


def test_an_unarmed_rail_ignores_a_payment_header_and_calls_no_facilitator(unarmed_server, tmp_path):
    """Found live after PR #279 deployed (2026-09-30). The header block ran
    whenever a payment header was present, armed or not: an unarmed
    production built requirements with an empty pay-to and called the
    public facilitator for any request carrying the header, answering it
    with an x402 error instead of what it gave before. The doc's rule is
    that until the rail is armed every path falls through exactly as
    before: the header is ignored, and nothing is called."""
    s, _h, b = _post(unarmed_server, "/api/anchor", {"hash_hex": HASH_A},
                     _payment_headers(nonce="u1"))
    assert s == 200, b                       # the free anchor, as before x402
    assert "x402_settled" not in b
    assert not (tmp_path / "x402_ledger.jsonl").exists()
    assert not (tmp_path / "x402_claimed.jsonl").exists()
    s2, _h2, b2 = _post(unarmed_server, "/api/anchor", {"hash_hex": HASH_B},
                        _payment_headers(nonce="u2"))
    assert s2 == 429, b2                     # past the free tier: the classic 429
    assert "accepts" not in b2               # and no x402 challenge while unarmed


def _health(base: str) -> dict:
    status, raw, _h = _srv.request(base, "/api/health", "GET", None, {})
    assert status == 200
    return json.loads(raw)


def test_health_says_whether_x402_is_armed_and_on_which_network(server, unconfigured_server):
    """Arming is a secret on the server, and secrets cannot be read from
    outside; health is the one observable proof that the rail is (or is not)
    live, and on which network. Testnet first: Base Sepolia."""
    assert _health(server)["x402"] == {"armed": True, "network": "eip155:84532"}
    assert _health(unconfigured_server)["x402"] == {"armed": False, "network": None}


def test_x402_ledgers_never_fall_back_to_the_working_directory(tmp_path, monkeypatch):
    """Without ORPHO_DATA_DIR the charge ledger used to go to the process's
    working directory, which in a container can sit outside the persistent
    volume: a deploy would then lose the record of money taken. It follows
    engine.DATA_DIR's rule instead (production sets the variable)."""
    monkeypatch.delenv("ORPHO_DATA_DIR", raising=False)
    monkeypatch.chdir(tmp_path)
    d = x402_const._data_dir()
    assert d.is_absolute() and tmp_path not in [d, *d.parents], d
    repo = Path(__file__).resolve().parent.parent
    assert d in (repo / "data", repo)


def test_the_agent_docs_say_x402_is_testnet_and_drop_the_five_calendars_claim():
    web = Path(__file__).resolve().parent.parent / "web"
    agents = (web / "docs" / "agents.html").read_text()
    assert "Base Sepolia" in agents and "test network" in agents
    assert "five calendars" not in agents
    llms = (web / "llms.txt").read_text()
    assert "x402" in llms and "TEST network" in llms



def test_our_own_pages_still_get_the_classic_429_past_the_free_tier(server):
    """A browser on orphograph.com marks its fetches Sec-Fetch-Site:
    same-origin. Those keep the classic 429 every page already handles (the
    reference middleware likewise shows browsers a paywall page, not the
    402): arming x402 must not break the site for people. Anyone else, an
    agent or an SDK, gets the x402 challenge."""
    _exhaust_free_tier(server)
    s, h, b = _post(server, "/api/anchor", {"hash_hex": HASH_B}, {"Sec-Fetch-Site": "same-origin"})
    assert s == 429, b
    assert "accepts" not in b and x402_const.PAYMENT_REQUIRED_HEADER not in h
    s2, _h2, b2 = _post(server, "/api/anchor", {"hash_hex": HASH_B})
    assert s2 == 402, b2
