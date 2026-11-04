"""test_x402_stripe_record.py — settled x402 payments booked in Stripe.

With ORPHO_X402_STRIPE_RECORD=1 the pay-to address is a Stripe crypto
deposit address, and each settled x402 payment is recorded in Stripe as a
PaymentIntent (crypto, transaction_verification). Off by default.

Driven through the real server (tests/_srv.py): mock facilitator
(ORPHO_X402_BACKEND=mock + ORPHO_X402_ALLOW_MOCK=1), stubbed calendars,
stubbed Stripe (every call lands in <data dir>/stub_stripe_calls.jsonl, and
while <data dir>/stub_stripe_down exists the REAL stripe_api._request runs
and fails as unconfigured), and the egress guard, so nothing here can reach
a network. Recording runs on a background thread, so the tests wait for the
recorded call (or for a later "barrier" payment's call) instead of sleeping.
"""
from __future__ import annotations

import base64
import importlib
import json
import sys
import time
from pathlib import Path

import pytest

import _srv

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "server"))
import stripe_api  # noqa: E402
import x402  # noqa: E402

# A valid all-lowercase address (no checksum to fail): the deposit address
# and the pay-to are the same one, as the docs tell the operator to set them.
DEPOSIT = "0x00000000000000000000000000000000000000be"
PREVIEW_VERSION = "2026-05-27.preview"
PI_PATH = "/payment_intents"
HASH_FREE = "c1" * 32

BASE_ENV = {
    "ORPHO_X402_BACKEND": "mock",
    "ORPHO_X402_ALLOW_MOCK": "1",
    "ORPHO_X402_PAY_TO_ADDRESS": DEPOSIT,
    "ORPHO_X402_PRICE_CENTS": "5",
    "RATE_LIMIT_PER_DAY": "1",
    "ORPHO_HEALTH_CACHE_SEC": "0",
    # Never a key from the caller's shell: a down stub must fail as
    # unconfigured, the failure the product itself produces.
    "STRIPE_SECRET_KEY": "",
}
RECORD_ENV = {**BASE_ENV, "ORPHO_X402_STRIPE_RECORD": "1",
              "ORPHO_X402_STRIPE_DEPOSIT_ADDRESS": DEPOSIT}
SERVER_KW = {"stub_calendars": True, "stub_stripe": True, "egress_guard": True}


@pytest.fixture()
def recording_server(tmp_path):
    yield from _srv.server_processes(tmp_path, **SERVER_KW, **RECORD_ENV)


@pytest.fixture()
def off_server(tmp_path):
    yield from _srv.server_processes(tmp_path, **SERVER_KW, **BASE_ENV)


def _payment_headers(nonce: str) -> dict:
    payload = {
        "x402Version": 2,
        "accepted": {"scheme": "exact", "network": "eip155:84532",
                     "asset": x402.USDC_BASE_SEPOLIA, "amount": "50000",
                     "payTo": DEPOSIT, "maxTimeoutSeconds": 60, "extra": {}},
        "payload": {
            "signature": "0x" + "ab" * 65,
            "authorization": {"from": "0xagentPayer0000000000000000000000000001",
                              "to": DEPOSIT, "value": "50000", "validAfter": "0",
                              "validBefore": "9999999999", "nonce": nonce},
            "mock_outcome": "ok",
        },
    }
    raw = base64.b64encode(json.dumps(payload).encode()).decode()
    return {x402.PAYMENT_SIGNATURE_HEADER: raw}


def _post(base: str, body: dict, headers: dict | None = None):
    h = {"Content-Type": "application/json", **(headers or {})}
    status, raw, resp_headers = _srv.request(base, "/api/anchor", "POST",
                                             json.dumps(body).encode(), h)
    return status, resp_headers, json.loads(raw)


def _pay(base: str, nonce: str, hash_hex: str):
    status, headers, body = _post(base, {"hash_hex": hash_hex}, _payment_headers(nonce))
    assert status == 200, body
    assert body["x402_settled"] is True, body
    tx = json.loads(base64.b64decode(headers[x402.PAYMENT_RESPONSE_HEADER]))["transaction"]
    return body, tx


def _pi_calls(data_dir: Path) -> list[dict]:
    path = data_dir / "stub_stripe_calls.jsonl"
    if not path.exists():
        return []
    rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    return [r for r in rows if r["path"] == PI_PATH]


def _wait_for(predicate, what: str, timeout: float = 15.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.05)
    raise AssertionError(f"timed out waiting for {what}")


def _pending(data_dir: Path) -> list[dict]:
    path = data_dir / "x402_stripe_pending.jsonl"
    if not path.exists():
        return []
    ids = {}
    for line in path.read_text().splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        ids[row["id"]] = row
    return list(ids.values())


def _health_x402(base: str) -> dict:
    return _srv.ok_json(*_srv.get_json(base, "/api/health", timeout=15))["x402"]


def _assert_pi_call(call: dict, *, tx: str, ident: str) -> None:
    form, headers = call["form"], call["headers"]
    assert call["method"] == "POST"
    assert form["amount"] == "5", form                     # 50000 atomic USDC = 5 cents
    assert form["currency"] == "usd"
    assert form["confirm"] == "true"
    assert form["payment_method_data[type]"] == "crypto"
    assert form["allowed_payment_method_types[0]"] == "crypto"
    assert form["payment_method_options[crypto][mode]"] == "transaction_verification"
    opts = "payment_method_options[crypto][transaction_verification_options]"
    assert form[f"{opts}[network]"] == "base"
    assert form[f"{opts}[transaction_hash]"] == tx
    assert headers["Stripe-Version"] == PREVIEW_VERSION
    assert headers["Idempotency-Key"] == ident
    assert "Authorization" not in headers, "the key must never be in the recorded extras"


# ── through the real server ──────────────────────────────────────────────

def test_a_paid_anchor_is_recorded_once_as_a_payment_intent(recording_server, tmp_path):
    _post(recording_server, {"hash_hex": HASH_FREE})            # the free anchor
    assert _pi_calls(tmp_path) == [], "a free anchor is not a payment"
    _body, tx = _pay(recording_server, "s1", "a1" * 32)
    calls = _wait_for(lambda: _pi_calls(tmp_path), "the PaymentIntent call")
    assert len(calls) == 1, calls
    _assert_pi_call(calls[0], tx=tx, ident="nonce:s1")
    _wait_for(lambda: not _pending(tmp_path), "the pending row to clear")
    h = _health_x402(recording_server)
    assert h["stripe_record"] is True and h["stripe_pending"] == 0, h
    assert h["armed"] is True and h["config_problems"] == 0, h
    assert not (tmp_path / "stub_egress_blocked.jsonl").exists(), "something tried to leave"


def test_recording_off_sends_nothing_to_stripe(off_server, tmp_path):
    _post(off_server, {"hash_hex": HASH_FREE})
    _pay(off_server, "s2", "a2" * 32)
    # The pending row is written synchronously before any recording thread
    # starts, so its absence right after the 200 is exact, not a race.
    assert not (tmp_path / "x402_stripe_pending.jsonl").exists()
    time.sleep(0.5)
    assert _pi_calls(tmp_path) == []
    h = _health_x402(off_server)
    assert h["stripe_record"] is False and h["stripe_pending"] == 0, h


def test_an_identical_replay_records_nothing_more(recording_server, tmp_path):
    _post(recording_server, {"hash_hex": HASH_FREE})
    headers = _payment_headers("s3")
    s, _h, first = _post(recording_server, {"hash_hex": "a3" * 32}, headers)
    assert s == 200, first
    _wait_for(lambda: _pi_calls(tmp_path), "the first PaymentIntent call")
    s2, _h2, replay = _post(recording_server, {"hash_hex": "a3" * 32}, headers)
    assert s2 == 200 and replay.get("x402_replay") is True, replay
    # Barrier: a second, different payment. Its call arriving means any
    # call the replay could have started has had its chance too.
    _pay(recording_server, "s3b", "a4" * 32)
    calls = _wait_for(lambda: len(_pi_calls(tmp_path)) >= 2 and _pi_calls(tmp_path),
                      "the barrier payment's call")
    keys = [c["headers"]["Idempotency-Key"] for c in calls]
    assert sorted(keys) == ["nonce:s3", "nonce:s3b"], keys


def test_a_stripe_failure_keeps_the_receipt_and_the_boot_retry_clears_it(tmp_path):
    (tmp_path / "stub_stripe_down").write_text("")
    for base in _srv.server_processes(tmp_path, **SERVER_KW, **RECORD_ENV):
        _post(base, {"hash_hex": HASH_FREE})
        body, tx = _pay(base, "s4", "a5" * 32)
        # The paid receipt stands, whatever Stripe said.
        assert body["receipt_id"] and body["x402_payment_held"] is False, body
        assert (tmp_path / "receipts" / body["receipt_id"] / "receipt.json").exists()
        first = _wait_for(lambda: _pi_calls(tmp_path), "the failing call")
        assert len(first) == 1
        _assert_pi_call(first[0], tx=tx, ident="nonce:s4")
        log_text = lambda: "".join(p.read_text() for p in tmp_path.glob("server-*.log"))
        _wait_for(lambda: "stripe record failed (category=not_configured)" in log_text(),
                  "the failure log line")
        assert [r["id"] for r in _pending(tmp_path)] == ["nonce:s4"]
        h = _health_x402(base)
        assert h["stripe_record"] is True and h["stripe_pending"] == 1, h
        # The failure line names the category, never the payment.
        failure = [l for l in log_text().splitlines() if "stripe record failed" in l]
        assert failure and all(tx not in l and "agentPayer" not in l for l in failure), failure
    # Stripe is back; a restart retries with the SAME idempotency key.
    (tmp_path / "stub_stripe_down").unlink()
    for base in _srv.server_processes(tmp_path, **SERVER_KW, **RECORD_ENV):
        calls = _wait_for(lambda: len(_pi_calls(tmp_path)) >= 2 and _pi_calls(tmp_path),
                          "the boot retry's call")
        assert len(calls) == 2, calls
        _assert_pi_call(calls[1], tx=tx, ident="nonce:s4")
        assert calls[1]["form"] == calls[0]["form"]
        _wait_for(lambda: not _pending(tmp_path), "the pending row to clear")
        assert _health_x402(base)["stripe_pending"] == 0


# ── in process: the retry function, the amount rule, the config checks ──

def test_retry_stripe_pending_resends_with_the_same_key_and_clears_on_success(tmp_path, monkeypatch):
    monkeypatch.setenv("ORPHO_DATA_DIR", str(tmp_path))
    sent = []
    answers = iter([{"ok": False, "category": "stripe_outage"}, {"ok": True, "data": {}}])
    # The module x402 will import NOW: other tests in the suite re-import
    # stripe_api, so the copy bound at this file's import may be stale.
    live_stripe_api = importlib.import_module("stripe_api")
    monkeypatch.setattr(live_stripe_api, "_request",
                        lambda m, p, form=None, headers=None: (sent.append((p, form, headers)),
                                                               next(answers))[1])
    x402.stripe_record_settlement(ident="nonce:r1", tx_hash="0xabc", amount_atomic="50000",
                                  network_id=x402.NETWORK_BASE_MAINNET, payer="0xp", wait=True)
    assert x402.stripe_pending_count() == 1
    assert x402.retry_stripe_pending() == (1, 0)
    assert x402.stripe_pending_count() == 0
    assert [h["Idempotency-Key"] for _p, _f, h in sent] == ["nonce:r1", "nonce:r1"]
    assert sent[0][1] == sent[1][1]
    assert x402.retry_stripe_pending() == (0, 0)
    assert len(sent) == 2, "nothing left to resend"


def test_pending_removal_keeps_other_payments(tmp_path, monkeypatch):
    monkeypatch.setenv("ORPHO_DATA_DIR", str(tmp_path))
    for i in ("a", "b", "c"):
        x402.stripe_pending_add({"id": f"nonce:{i}", "tx_hash": "0x1", "amount_atomic": "50000",
                                 "network": x402.NETWORK_BASE_SEPOLIA, "payer": ""})
    x402.stripe_pending_remove("nonce:b")
    assert sorted(r["id"] for r in x402.stripe_pending_rows()) == ["nonce:a", "nonce:c"]


@pytest.mark.parametrize("atomic,cents", [("50000", "5"), ("10000", "1"), (1_230_000, "123")])
def test_amount_in_cents_is_atomic_over_ten_thousand(monkeypatch, atomic, cents):
    sent = []
    monkeypatch.setattr(stripe_api, "_request",
                        lambda m, p, form=None, headers=None: (sent.append(form), {"ok": True})[1])
    res = stripe_api.record_x402_payment(tx_hash="0xabc", amount_atomic=atomic,
                                         network_id=x402.NETWORK_BASE_MAINNET, payer=None,
                                         idempotency_key="nonce:x")
    assert res["ok"] and sent[0]["amount"] == cents


@pytest.mark.parametrize("kwargs", [
    {"amount_atomic": "50001"},                 # not a whole cent
    {"amount_atomic": "9999"},                  # under a cent
    {"amount_atomic": "0"},
    {"amount_atomic": "5e4"},                   # not an integer
    {"network_id": "eip155:1"},                 # no Stripe network for it
    {"tx_hash": ""},
    {"tx_hash": "0xab cd"},
    {"idempotency_key": ""},
])
def test_unrecordable_payments_are_refused_before_any_request(monkeypatch, kwargs):
    sent = []
    monkeypatch.setattr(stripe_api, "_request", lambda *a, **k: sent.append(a) or {"ok": True})
    args = {"tx_hash": "0xabc", "amount_atomic": "50000",
            "network_id": x402.NETWORK_BASE_SEPOLIA, "payer": None,
            "idempotency_key": "nonce:x", **kwargs}
    res = stripe_api.record_x402_payment(**args)
    assert res["ok"] is False and res["category"] == "invalid_input", res
    assert sent == []


def _real_env(monkeypatch, **overrides):
    for k in ("ORPHO_X402_BACKEND", "ORPHO_X402_ALLOW_MOCK", "ORPHO_X402_ASSET",
              "ORPHO_X402_FACILITATOR_URL", "ORPHO_X402_STRIPE_RECORD",
              "ORPHO_X402_STRIPE_DEPOSIT_ADDRESS", "STRIPE_SECRET_KEY"):
        monkeypatch.delenv(k, raising=False)
    env = {"ORPHO_X402_NETWORK": x402.NETWORK_BASE_SEPOLIA,
           "ORPHO_X402_PAY_TO_ADDRESS": DEPOSIT,
           "ORPHO_X402_STRIPE_RECORD": "1",
           "ORPHO_X402_STRIPE_DEPOSIT_ADDRESS": DEPOSIT,
           "STRIPE_SECRET_KEY": "stub-placeholder", **overrides}
    for k, v in env.items():
        if v is None:
            monkeypatch.delenv(k, raising=False)
        else:
            monkeypatch.setenv(k, v)


def test_a_correct_recording_config_has_no_problems(monkeypatch):
    _real_env(monkeypatch)
    assert x402.config_problems() == []
    assert x402.configured() is True
    # The same address in another case is the same address.
    _real_env(monkeypatch, ORPHO_X402_STRIPE_DEPOSIT_ADDRESS=DEPOSIT.upper().replace("0X", "0x"))
    assert x402.config_problems() == []
    # Off: none of the recording rules apply.
    _real_env(monkeypatch, ORPHO_X402_STRIPE_RECORD=None, ORPHO_X402_STRIPE_DEPOSIT_ADDRESS=None,
              STRIPE_SECRET_KEY=None)
    assert x402.config_problems() == []


OTHER = "0x00000000000000000000000000000000000000cd"


@pytest.mark.parametrize("overrides,needle", [
    ({"ORPHO_X402_STRIPE_DEPOSIT_ADDRESS": None}, "ORPHO_X402_STRIPE_DEPOSIT_ADDRESS is unset"),
    ({"ORPHO_X402_STRIPE_DEPOSIT_ADDRESS": OTHER}, "ORPHO_X402_PAY_TO_ADDRESS is not"),
    ({"ORPHO_X402_NETWORK": "eip155:1"}, "is not Base mainnet or Base Sepolia"),
    ({"STRIPE_SECRET_KEY": None}, "STRIPE_SECRET_KEY is unset"),
    ({"ORPHO_X402_STRIPE_RECORD": "yes"}, "must be 1 (on) or 0"),
])
def test_each_recording_misconfiguration_disarms_the_rail(monkeypatch, overrides, needle):
    _real_env(monkeypatch, **overrides)
    problems = x402.config_problems()
    assert any(needle in p for p in problems), problems
    assert x402.configured() is False
    text = " ".join(problems)
    assert DEPOSIT not in text and OTHER not in text and "stub-placeholder" not in text
