"""Payment and outbound hardening, reproduced 2026-09-26 against origin/master.

Eight LOW defects, each driven through the real route where the route is the
thing that was wrong:

  1. A referral bonus row carried the BUYER's email on the gift RECIPIENT's
     claim code, so /api/pack/recover mailed the recipient's bearer code to
     the buyer.
  2. /api/pack/recover sent its mail before answering, so response time said
     whether the address owned a pack.
  3. Stripe's invalid_request text reached the caller verbatim.
  4. Cancel/reactivate made one Stripe call per request with no limit.
  5. The receipt privacy toggle read "private" with bool(), so the STRING
     "false" made a receipt private.
  6. Webhook registration echoed the resolved private address and the
     resolver's error text.
  7. The webhook address check was a denylist that let the CGNAT shared
     address space (100.64.0.0/10) through.
  8. Lightning: every quote, and every over-limit free anchor, minted a new
     upstream invoice with no limit; a failed invoice echoed the backend's
     error text.

Nothing here leaves the machine. Calendars are stubbed; every server gets
HTTP(S)_PROXY pointed at a closed loopback port, so a Stripe or mail call dies
locally and says so in the server log (defect 4 counts those lines). The
Lightning servers use the in-process mock backend, or LNbits pointed at a
closed loopback port. DNS failures are simulated with a patched getaddrinfo,
never a real lookup.
"""
from __future__ import annotations

import hashlib
import hmac
import io
import json
import re
import socket
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

import _srv

HMAC_SECRET = "payment-outbound-hardening"
# No whsec_ prefix: the signature check does not need one, and a whsec_-shaped
# value trips tests/test_no_secrets_in_tracked_files.py.
WEBHOOK_SECRET = "payment-outbound-webhook-test-secret"

OWNER = "owner@owner.test"          # privacy toggle
HOOKS = "hooks@hooks.test"          # webhook registration
EMAILS = [OWNER, HOOKS]
SUBSCRIBER = "subscriber@sub.test"  # cancel / reactivate, on its own server

# Defect 1: a third party's earlier pack is the referrer.
REFERRER_CODE = "pk_REFERRERabcdefghijk"
REF_CODE = "ref_" + REFERRER_CODE[3:15]
BUYER = "giftbuyer@buyer.test"            # masked in the log as g***@buyer.test
RECIPIENT = "recipient@recipient.test"    # masked as r***@recipient.test
GIFT_SESSION = "cs_test_giftWithReferral000000001"

# Defect 2: an address that owns a pack.
HIT = "hit@hit.test"
HIT_CODE = "pk_HITHITHITHITHIT01"

PRIVACY_RID = "PrivStrictBool01"


def _account(email: str) -> str:
    return hmac.new(HMAC_SECRET.encode(), email.encode(), hashlib.sha256).hexdigest()[:16]


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))


def _seed_accounts(data: Path, emails: list[str]) -> None:
    _write_jsonl(data / "auth_sessions.jsonl", [dict(
        event="created", session_hash=hashlib.sha256(f"session-{i}".encode()).hexdigest(),
        email=e, expires_unix=time.time() + 3600) for i, e in enumerate(emails)])
    _write_jsonl(data / "subscriptions.jsonl", [dict(
        email=e, status="active", stripe_sub=f"sub_hardening{i}") for i, e in enumerate(emails)])


def _as(email: str, emails: list[str] = EMAILS) -> dict:
    return {"Cookie": f"orpho_sid=session-{emails.index(email)}",
            "Content-Type": "application/json"}


def _no_egress(closed_port: int) -> dict:
    """Every urllib call from the server goes to a port nothing listens on.
    Loopback stays direct so an LNbits URL on 127.0.0.1 is reached as given."""
    proxy = f"http://127.0.0.1:{closed_port}"
    return {"HTTPS_PROXY": proxy, "https_proxy": proxy,
            "HTTP_PROXY": proxy, "http_proxy": proxy,
            "NO_PROXY": "127.0.0.1,localhost", "no_proxy": "127.0.0.1,localhost"}


def _log(data: Path) -> str:
    return "".join(p.read_text(errors="replace") for p in sorted(data.glob("server-*.log")))


def _wait_for(data: Path, since: int, needle: str, timeout: float = 10.0) -> str:
    """Log text after `since` once it contains `needle` (or at the deadline)."""
    deadline = time.time() + timeout
    text = ""
    while time.time() < deadline:
        text = _log(data)[since:]
        if needle in text:
            return text
        time.sleep(0.05)
    return text


def _post(base: str, path: str, body, headers: dict | None = None):
    h = {"Content-Type": "application/json", **(headers or {})}
    raw = body if isinstance(body, bytes) else json.dumps(body).encode()
    return _srv.request(base, path, "POST", raw, h, timeout=15)


@pytest.fixture(scope="module")
def main_server(tmp_path_factory):
    data = tmp_path_factory.mktemp("payment_outbound_main")
    _seed_accounts(data, EMAILS)
    _write_jsonl(data / "credit_ledger.jsonl", [
        {"ts": "2026-09-01T00:00:00+00:00", "claim_code": REFERRER_CODE,
         "email": "referrer@referrer.test", "credits_delta": 10,
         "source": "stripe:cs_test_referrerSession00000001"},
        {"ts": "2026-09-01T00:00:00+00:00", "claim_code": HIT_CODE,
         "email": HIT, "credits_delta": 10, "source": "stripe:cs_test_hitSession00000000001"},
    ])
    rd = data / "receipts" / PRIVACY_RID
    rd.mkdir(parents=True)
    (rd / "receipt.json").write_text(json.dumps({
        "receipt_id": PRIVACY_RID, "created_at": "2026-09-23T00:00:00Z",
        "hash_hex": "ab" * 32, "source": "session", "private": False,
        "account_id": _account(OWNER), "calendars_ok": 0, "calendars_total": 5}))
    closed = _srv.reserve_ports(1)[0]
    for base in _srv.server_processes(
            data, stub_calendars=True, ORPHO_HMAC_SECRET=HMAC_SECRET,
            STRIPE_WEBHOOK_SECRET=WEBHOOK_SECRET, **_no_egress(closed)):
        yield base, data


# ── 1. referral bonus row must not name the gift buyer ─────────────────────

def _signed_webhook(base: str, event: dict):
    payload = json.dumps(event).encode()
    t = str(int(time.time()))
    sig = hmac.new(WEBHOOK_SECRET.encode(), f"{t}.".encode() + payload,
                   hashlib.sha256).hexdigest()
    return _post(base, "/api/stripe/webhook", payload,
                 {"Stripe-Signature": f"t={t},v1={sig}"})


def test_gift_with_referral_does_not_let_the_buyer_recover_the_recipients_code(main_server):
    base, data = main_server
    event = {"id": "evt_gift_and_referral", "type": "checkout.session.completed",
             "data": {"object": {
                 "id": GIFT_SESSION, "object": "checkout.session", "mode": "payment",
                 "payment_status": "paid", "customer_email": BUYER,
                 "customer_details": {"email": BUYER},
                 "payment_intent": "pi_gift_and_referral",
                 "metadata": {"gift_to_email": RECIPIENT, "ref_code": REF_CODE}}}}
    status, body, _ = _signed_webhook(base, event)
    assert status == 200, body
    result = json.loads(body)
    assert result.get("gift") is True and (result.get("referral") or {}).get("ok") is True, result

    rows = [json.loads(line) for line in (data / "credit_ledger.jsonl").read_text().splitlines()]
    gift_code = next(r["claim_code"] for r in rows if r["source"] == f"stripe-gift:{GIFT_SESSION}")
    bonus = [r for r in rows if r["claim_code"] == gift_code
             and r["source"].startswith("referral_bonus:")]
    assert bonus, "the referral bonus was not credited at all; the test proves nothing"
    # THE DEFECT: the bonus row on the recipient's code carried the buyer's
    # address, and rows carrying an address are what recovery mails codes by.
    named_for_buyer = [r for r in rows if r["claim_code"] == gift_code
                       and (r.get("email") or "").lower() == BUYER]
    assert not named_for_buyer, named_for_buyer

    # And through the route: the buyer asks, the recipient asks. The
    # recipient's own resend proves the thread ran; the buyer gets nothing.
    since = len(_log(data))
    for who in (BUYER, RECIPIENT):
        status, body, _ = _post(base, "/api/pack/recover", {"email": who})
        assert status == 200, body
    text = _wait_for(data, since, "would send to=r***@recipient.test")
    assert "would send to=r***@recipient.test" in text, text[-2000:]
    time.sleep(0.3)
    text = _log(data)[since:]
    assert "would send to=g***@buyer.test" not in text, text[-2000:]


# ── 2. /api/pack/recover answers before any mail I/O ────────────────────────

def test_pack_recover_answers_before_the_resend_starts(main_server):
    """The access-log line is written when the response starts. It must come
    BEFORE the resend: a send ahead of the answer is what made a hit slower
    than a miss. Log order is deterministic where timing is not."""
    base, data = main_server
    since = len(_log(data))
    status, body, _ = _post(base, "/api/pack/recover", {"email": HIT})
    assert status == 200, body
    text = _wait_for(data, since, "would send to=h***@hit.test")
    sent_at = text.find("would send to=h***@hit.test")
    answered_at = text.find('"POST /api/pack/recover HTTP/1.1" 200')
    assert sent_at >= 0, f"no resend for an address that owns a pack:\n{text[-2000:]}"
    assert answered_at >= 0, f"no access-log line for the request:\n{text[-2000:]}"
    assert answered_at < sent_at, (
        "the resend ran before the answer was written, so response time "
        f"depends on whether the address owns a pack:\n{text[-2000:]}")


def test_pack_recover_failure_after_the_answer_is_logged_without_the_address(tmp_path):
    """The resend's failure path: the caller already has its 200, so the
    failure can only reach the log, and the address must not."""
    _write_jsonl(tmp_path / "credit_ledger.jsonl", [
        {"ts": "2026-09-01T00:00:00+00:00", "claim_code": HIT_CODE, "email": HIT,
         "credits_delta": 10, "source": "stripe:cs_test_hitSession00000000001"}])
    closed = _srv.reserve_ports(1)[0]
    for base in _srv.server_processes(tmp_path, stub_calendars=True, **_no_egress(closed)):
        ledger = tmp_path / "credit_ledger.jsonl"
        ledger.chmod(0)  # after boot: the lookup itself fails
        try:
            since = len(_log(tmp_path))
            status, body, _ = _post(base, "/api/pack/recover", {"email": HIT})
            assert status == 200 and b"we've sent the code" in body, body
            text = _wait_for(tmp_path, since, "[pack-recover] resend failed")
        finally:
            ledger.chmod(0o600)
        assert "[pack-recover] resend failed: PermissionError" in text, text[-2000:]
        assert "hit.test" not in text


def test_pack_recover_body_is_identical_for_hit_miss_and_garbage(main_server):
    base, _data = main_server
    bodies = {_post(base, "/api/pack/recover", body)[1]
              for body in ({"email": HIT}, {"email": "nobody@nowhere.test"},
                           {"email": "not-an-email"})}
    assert len(bodies) == 1, bodies


# ── 3. Stripe's invalid_request text stays in the log ──────────────────────

def _stripe_error(monkeypatch, status: int, error: dict):
    """Drive the real stripe_api._request with the network edge answering
    `status` and a Stripe-shaped error body. Nothing is sent anywhere."""
    import stripe_api  # the module object the test then calls; see test_stripe_checkout
    monkeypatch.setattr(stripe_api, "STRIPE_SECRET_KEY", "sk_test_unit_only")
    body = json.dumps({"error": error}).encode()

    def answer(req, timeout=None):
        raise urllib.error.HTTPError(req.full_url, status, "stub", {}, io.BytesIO(body))

    monkeypatch.setattr(urllib.request, "urlopen", answer)
    return stripe_api.create_checkout_session(
        price_id="price_1RealConfiguredPackPriceId", mode="payment",
        success_url="https://example.test/s", cancel_url="https://example.test/c",
        customer_email="<b>x</b>@y")


@pytest.mark.parametrize("error", [
    {"type": "invalid_request_error", "code": "resource_missing",
     "param": "line_items[0][price]",
     "message": "No such price: 'price_1RealConfiguredPackPriceId'; a similar object "
                "exists in live mode, but a test mode key was used to make this request."},
    {"type": "invalid_request_error", "code": "email_invalid", "param": "customer_email",
     "message": "Invalid email address: <b>x</b>@y"},
])
def test_invalid_request_gets_a_fixed_message_and_the_detail_is_logged(monkeypatch, capsys, error):
    result = _stripe_error(monkeypatch, 400, error)
    assert result["ok"] is False and result["category"] == "invalid_request"
    assert result["error"] == "Request rejected by Stripe (invalid parameters).", result
    assert error["message"] not in json.dumps(result)
    assert error["message"] in capsys.readouterr().err


def test_other_client_errors_get_a_fixed_message(monkeypatch, capsys):
    result = _stripe_error(monkeypatch, 404, {
        "type": "invalid_request_error", "message": "No such subscription: 'sub_secret123'"})
    assert result["error"] == "Payment error (404).", result
    assert "sub_secret123" in capsys.readouterr().err


def test_card_declined_still_shows_stripes_words(monkeypatch):
    result = _stripe_error(monkeypatch, 402, {
        "type": "card_error", "code": "card_declined",
        "message": "Your card has insufficient funds."})
    assert result["error"] == "Your card has insufficient funds."


# ── 4. cancel / reactivate are rate limited before the Stripe call ─────────

def test_subscription_changes_are_limited_per_account_before_stripe(tmp_path):
    emails = [SUBSCRIBER]
    _seed_accounts(tmp_path, emails)
    closed = _srv.reserve_ports(1)[0]
    stripe_calls = re.compile(r"\[stripe_api\] URLError path=/subscriptions/")
    for base in _srv.server_processes(tmp_path, stub_calendars=True,
                                      STRIPE_SECRET_KEY="sk_test_unit_only",
                                      **_no_egress(closed)):
        since = len(_log(tmp_path))
        statuses = [_post(base, "/api/me/cancel-subscription", {}, _as(SUBSCRIBER, emails))[0]
                    for _ in range(60)]
        calls = len(stripe_calls.findall(_log(tmp_path)[since:]))
        # Positive control: the Stripe call really is attempted (and dies on
        # the closed proxy port), so a count of 0 cannot pass by accident.
        assert calls > 0, _log(tmp_path)[-2000:]
        assert 429 in statuses, f"no limit in 60 requests; {calls} Stripe calls made"
        first = statuses.index(429)
        assert set(statuses[first:]) == {429}, statuses
        assert calls == first, (calls, statuses)
        # One budget for both routes: alternating cannot double it.
        status, body, headers = _post(base, "/api/me/reactivate-subscription", {},
                                      _as(SUBSCRIBER, emails))
        assert status == 429, body
        assert int(headers.get("Retry-After", "0")) > 0
        assert len(stripe_calls.findall(_log(tmp_path)[since:])) == calls


# ── 5. strict booleans ─────────────────────────────────────────────────────

def _receipt_private(data: Path) -> bool:
    return json.loads((data / "receipts" / PRIVACY_RID / "receipt.json").read_text())["private"]


def _public_status(base: str) -> int:
    return _srv.request(base, f"/api/receipt/{PRIVACY_RID}")[0]


@pytest.mark.parametrize("value", ["false", "0", "no", "true", 0, 1, None, [], {}])
def test_privacy_toggle_refuses_anything_but_json_true_or_false(main_server, value):
    base, data = main_server
    path = f"/api/me/receipt/{PRIVACY_RID}/privacy"
    status, body, _ = _post(base, path, {"private": False}, _as(OWNER))
    assert status == 200 and _receipt_private(data) is False, body
    status, body, _ = _post(base, path, {"private": value}, _as(OWNER))
    assert status == 400, (value, status, body)
    assert _receipt_private(data) is False
    assert _public_status(base) == 200


def test_privacy_toggle_honours_true_false_and_absent(main_server):
    base, data = main_server
    path = f"/api/me/receipt/{PRIVACY_RID}/privacy"
    for body, want in (({"private": True}, True), ({"private": False}, False),
                       ({"private": True}, True), ({}, False)):
        status, raw, _ = _post(base, path, body, _as(OWNER))
        assert status == 200, raw
        assert json.loads(raw)["private"] is want
        assert _receipt_private(data) is want
        assert _public_status(base) == (404 if want else 200)


def test_privacy_toggle_refuses_a_body_that_is_not_an_object(main_server):
    base, data = main_server
    status, raw, _ = _post(base, f"/api/me/receipt/{PRIVACY_RID}/privacy",
                           ["private"], _as(OWNER))
    assert status == 400, raw
    assert _receipt_private(data) is False


def test_anchor_routes_already_refuse_string_booleans(main_server):
    """Regression guard, not a failing-first case: _anchor_input_error has
    type-checked private and paths_public as bool since #267."""
    base, _data = main_server
    status, raw, _ = _post(base, "/api/anchor", {"hash_hex": "cd" * 32, "private": "false"})
    assert status == 400 and b"private must be bool" in raw, raw
    status, raw, _ = _post(base, "/api/anchor_folder", {"paths_public": "false"})
    assert status == 400 and b"paths_public must be bool" in raw, raw


# ── 6 + 7. webhook registration: one code, is_global ───────────────────────
#
# The refusal codes below were non_public_address / dns_error / bad_ip when
# this file was written. Since the follow-up review (2026-09-26) every
# refusal about the address is address_not_allowed to the caller: distinct
# codes against a 200 still said, name by name, whether a name resolved and
# to what kind of address. The specific code stays in the server log.

def _register(base: str, url: str):
    status, raw, _ = _post(base, "/api/me/webhooks", {"url": url}, _as(HOOKS))
    return status, raw


@pytest.mark.parametrize("url, detail", [
    ("https://10.20.30.40/h", "10.20.30.40"),
    ("https://[fdaa:0:1234:a7b:1::2]/h", "fdaa:0:1234:a7b:1::2"),
    ("https://192.168.1.10/h", "192.168.1.10"),
])
def test_refusal_names_no_address(main_server, url, detail):
    base, data = main_server
    since = len(_log(data))
    status, raw = _register(base, url)
    assert status == 400, raw
    assert json.loads(raw) == {"error": "address_not_allowed"}, raw
    assert detail.encode() not in raw
    assert f"refused non_public_address: {detail}" in _wait_for(data, since, detail)


@pytest.mark.parametrize("url", [
    "https://100.64.0.1/h", "https://100.127.255.254/h", "https://[::ffff:100.64.0.1]/h",
])
def test_cgnat_shared_address_space_is_refused(main_server, url):
    status, raw = _register(main_server[0], url)
    assert status == 400, raw
    assert json.loads(raw) == {"error": "address_not_allowed"}, raw


@pytest.mark.parametrize("url", ["https://224.0.0.1/h", "https://[64:ff9b::a00:1]/h"])
def test_named_checks_still_refuse_addresses_python_calls_global(main_server, url):
    """224.0.0.1 and 64:ff9b::a00:1 report is_global=True on this interpreter
    and are refused only by is_multicast / is_reserved: the denylist stays."""
    status, raw = _register(main_server[0], url)
    assert status == 400, raw


@pytest.mark.parametrize("url", ["https://1.1.1.1/h", "https://[2606:4700::1111]/h"])
def test_public_addresses_still_register(main_server, url):
    base, _data = main_server
    status, raw = _register(base, url)
    assert status == 200 and json.loads(raw)["ok"] is True, raw
    status, raw, _ = _post(base, "/api/me/webhooks/delete", {"url": url}, _as(HOOKS))
    assert status == 200, raw


def test_dns_failure_returns_the_code_and_logs_the_detail(monkeypatch, capsys):
    import webhooks

    def fail(*_args, **_kwargs):
        raise socket.gaierror(8, "nodename nor servname provided, or not known")

    monkeypatch.setattr(socket, "getaddrinfo", fail)
    assert webhooks._validate_webhook_url("https://missing.example/h") == (
        False, "address_not_allowed")
    assert webhooks._public_addresses("missing.example") == ([], "address_not_allowed")
    err = capsys.readouterr().err
    assert "refused dns_error: missing.example" in err and "nodename nor servname" in err


def test_unparsable_resolver_answer_returns_the_code_and_logs_the_detail(monkeypatch, capsys):
    import webhooks
    monkeypatch.setattr(socket, "getaddrinfo", lambda *_a, **_k: [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("not-an-ip-from-resolver", 0))])
    assert webhooks._validate_webhook_url("https://odd.example/h") == (
        False, "address_not_allowed")
    assert "refused bad_ip: not-an-ip-from-resolver" in capsys.readouterr().err


# ── 8. Lightning invoices ──────────────────────────────────────────────────

def _ln_server(tmp_path, **env):
    # arm_lightning: the rail is retired in production (2026-09-28); these
    # cases cover the armed code a re-arm switches back on. The switch is a
    # flag on the test launcher, which production cannot run (tests/_srv.py).
    closed = _srv.reserve_ports(1)[0]
    return _srv.server_processes(tmp_path, stub_calendars=True, arm_lightning=True,
                                 RATE_LIMIT_PER_DAY="1", **_no_egress(closed), **env)


MOCK_LN = {"ORPHO_LN_BACKEND": "mock", "ORPHO_LN_ALLOW_MOCK": "1"}


def test_quote_invoices_are_limited_per_client(tmp_path):
    for base in _ln_server(tmp_path, **MOCK_LN):
        statuses, headers = [], []
        for _ in range(60):
            status, _raw, h = _post(base, "/api/ln/quote", {})
            statuses.append(status)
            headers.append(h)
        assert statuses[0] == 200, statuses
        assert 429 in statuses, "60 quotes, 60 upstream invoices, no limit"
        first = statuses.index(429)
        assert set(statuses[:first]) == {200} and set(statuses[first:]) == {429}, statuses
        assert int(headers[first].get("Retry-After", "0")) > 0


def test_over_limit_anchor_stops_minting_invoices_past_the_budget(tmp_path):
    for base in _ln_server(tmp_path, **MOCK_LN):
        status, body = _srv.anchor(base, {"hash_hex": "01" * 32})
        assert status == 200, body  # the one free anchor
        statuses, invoices = [], 0
        for i in range(60):
            status, raw, h = _post(base, "/api/anchor",
                                   {"hash_hex": hashlib.sha256(bytes([i])).hexdigest()})
            statuses.append(status)
            invoices += bool(h.get("WWW-Authenticate")) or b"lnmock" in raw
        assert statuses[0] == 402, statuses
        assert 429 in statuses, f"60 over-limit anchors minted {invoices} invoices"
        first = statuses.index(429)
        assert set(statuses[:first]) == {402} and set(statuses[first:]) == {429}, statuses
        assert invoices == first
        # The quote path draws on the same budget.
        assert _post(base, "/api/ln/quote", {})[0] == 429


def test_failed_invoice_says_so_without_the_backends_words(tmp_path):
    dead = _srv.reserve_ports(1)[0]
    for base in _ln_server(tmp_path, ORPHO_LN_BACKEND="lnbits",
                           ORPHO_LN_LNBITS_URL=f"http://127.0.0.1:{dead}",
                           ORPHO_LN_LNBITS_KEY="stub-key-not-real"):
        since = len(_log(tmp_path))
        status, raw, _ = _post(base, "/api/ln/quote", {})
        assert status == 503, raw
        assert json.loads(raw)["error"] == "invoice creation failed", raw
        for word in (b"URLError", b"Errno", b"refused", b"127.0.0.1", str(dead).encode()):
            assert word not in raw, (word, raw)
        assert "[ln] invoice creation failed (quote): URLError" in _wait_for(
            tmp_path, since, "[ln] invoice creation failed (quote)")
        # The anchor challenge falls back to the plain 429, and now says why
        # in the log instead of silently.
        assert _srv.anchor(base, {"hash_hex": "02" * 32})[0] == 200
        status, raw, _ = _post(base, "/api/anchor", {"hash_hex": "03" * 32})
        assert status == 429, raw
        assert "[ln] invoice creation failed (anchor challenge)" in _wait_for(
            tmp_path, since, "[ln] invoice creation failed (anchor challenge)")


# ── follow-up review, 2026-09-26 ───────────────────────────────────────────
#
# F1. The referral fix above changed the WRITER only. Bonus rows written
#     before it still carry email=<gift buyer> on the gift RECIPIENT's code,
#     and the readers key credit rows by email.
# F2. support_tools' lookup, keyed by email too, lost a referred buyer's own
#     +10 once the writer stopped naming them.
# F5. The cancel/reactivate budget is per ACCOUNT; nothing proved it did not
#     follow the client address instead.
# F6. Stripe's decoded message went to the log raw, newlines included.
# (F3 and F4 need an in-process server; they live in
# tests/test_payment_outbound_followups.py, which opens its own sockets and
# so does not import _srv.)

LEGACY_GIFT_CODE = "pk_LEGACYGIFTrecip01"   # the gift recipient's code
BUYER_OWN_CODE = "pk_BUYEROWNpack000001"    # the buyer's own pack
LEGACY_BONUS_SOURCE = "referral_bonus:from_ref_REFERRERabcd"


def _legacy_ledger_rows() -> list[dict]:
    """The recipient's gift mint, the bonus row origin/master wrote for it
    (buyer's email, recipient's code), and the buyer's own pack with 3 of 10
    spent. In this order on purpose: recovery sends codes in first-seen
    order, so a server that still honours the bonus row mails the buyer
    "Pack of 20" BEFORE their own "Pack of 7", and waiting for the 7 is
    enough to have seen the 20 if it was sent."""
    return [
        {"ts": "2026-09-01T00:00:00+00:00", "claim_code": LEGACY_GIFT_CODE,
         "email": RECIPIENT, "credits_delta": 10,
         "source": "stripe-gift:cs_test_legacyGift0000000001"},
        {"ts": "2026-09-01T00:00:01+00:00", "claim_code": LEGACY_GIFT_CODE,
         "email": BUYER, "credits_delta": 10, "source": LEGACY_BONUS_SOURCE},
        {"ts": "2026-09-02T00:00:00+00:00", "claim_code": BUYER_OWN_CODE,
         "email": BUYER, "credits_delta": 10, "source": "stripe:cs_test_buyerOwnPack00000001"},
        *({"ts": "2026-09-02T00:01:00+00:00", "claim_code": BUYER_OWN_CODE,
           "email": "", "credits_delta": -1, "source": "anchor"} for _ in range(3)),
    ]


def test_legacy_referral_bonus_row_is_not_recovered_or_exported_to_the_buyer(tmp_path):
    _write_jsonl(tmp_path / "credit_ledger.jsonl", _legacy_ledger_rows())
    _write_jsonl(tmp_path / "auth_sessions.jsonl", [dict(
        event="created", session_hash=hashlib.sha256(b"session-0").hexdigest(),
        email=BUYER, expires_unix=time.time() + 3600)])
    closed = _srv.reserve_ports(1)[0]
    for base in _srv.server_processes(tmp_path, stub_calendars=True, **_no_egress(closed)):
        since = len(_log(tmp_path))
        for who in (BUYER, RECIPIENT):
            status, body, _ = _post(base, "/api/pack/recover", {"email": who})
            assert status == 200 and b"we've sent the code" in body, body
        _wait_for(tmp_path, since, "would send to=r***@recipient.test")
        text = _wait_for(tmp_path, since, "Pack of 7 ")
        buyer_sends = [line for line in text.splitlines()
                       if "would send to=g***@buyer.test" in line]
        # The buyer's own pack still recovers (a normal pack is unaffected)...
        assert any("Pack of 7 " in line for line in buyer_sends), text[-2000:]
        # ...and it is the only thing the buyer is sent: the recipient's code
        # (10 + the 10 bonus = "Pack of 20") is not.
        assert len(buyer_sends) == 1, buyer_sends
        # The recipient still gets their own code, bonus included.
        recipient_sends = [line for line in text.splitlines()
                           if "would send to=r***@recipient.test" in line]
        assert len(recipient_sends) == 1 and "Pack of 20 " in recipient_sends[0], text[-2000:]

        status, raw, _ = _srv.request(base, "/api/me/export", "GET", None,
                                      {"Cookie": "orpho_sid=session-0"})
        assert status == 200, raw
        assert LEGACY_GIFT_CODE.encode() not in raw, raw
        rows = json.loads(raw)["items"]["credit_ledger"]
        bonus = [r for r in rows if r.get("source") == LEGACY_BONUS_SOURCE]
        # The row is the buyer's data and stays in their export, code withheld.
        assert len(bonus) == 1 and bonus[0]["claim_code"] == "pk_…", rows
        assert bonus[0]["credits_delta"] == 10 and bonus[0]["email"] == BUYER
        # The buyer's own code is exported as it is.
        assert any(r.get("claim_code") == BUYER_OWN_CODE for r in rows), rows


def test_recovery_skips_only_referral_bonus_rows(tmp_path, monkeypatch):
    """A denylist, not an allowlist: a mint kind this code has never heard of
    still recovers, and so does a row whose source is not even a string."""
    import credits
    monkeypatch.setattr(credits, "LEDGER_PATH", tmp_path / "credit_ledger.jsonl")
    _write_jsonl(credits.LEDGER_PATH, [
        {"claim_code": "pk_futureRailMint01", "email": BUYER, "credits_delta": 10,
         "source": "future-rail:abc"},
        {"claim_code": "pk_cryptoMint000001", "email": BUYER, "credits_delta": 10,
         "source": "nowpayments:inv_1:np_ord_1"},
        {"claim_code": "pk_oddSourceMint001", "email": BUYER, "credits_delta": 10,
         "source": None},
        {"claim_code": "pk_giftForSomeone01", "email": BUYER, "credits_delta": 10,
         "source": LEGACY_BONUS_SOURCE},
        # Only the prefix counts: this is not a bonus row.
        {"claim_code": "pk_notQuiteABonus01", "email": BUYER, "credits_delta": 10,
         "source": "stripe:referral_bonus:x"},
    ])
    assert credits.find_claim_codes_by_email(BUYER.upper()) == [
        "pk_futureRailMint01", "pk_cryptoMint000001", "pk_oddSourceMint001",
        "pk_notQuiteABonus01"]


def test_support_lookup_joins_referral_bonus_rows_by_claim_code(tmp_path, monkeypatch):
    """F2. Bonus rows show under whoever HOLDS the code they credit: the
    new-style row (email="") on the buyer's own code shows under the buyer,
    and a legacy row naming the buyer on a gift code shows under the
    recipient, not the buyer."""
    import credits
    import subscriptions
    import support_tools
    monkeypatch.setattr(credits, "LEDGER_PATH", tmp_path / "credit_ledger.jsonl")
    monkeypatch.setattr(support_tools, "DATA_DIR", tmp_path)
    monkeypatch.setattr(subscriptions, "status_for", lambda _email: None)
    new_bonus = "referral_bonus:from_ref_NEWSTYLEabcd"
    _write_jsonl(credits.LEDGER_PATH, [
        *_legacy_ledger_rows(),
        # What referrals.apply writes since the writer fix, for a buyer who
        # bought their own pack with a referral code.
        {"ts": "2026-09-02T00:00:02+00:00", "claim_code": BUYER_OWN_CODE, "email": "",
         "credits_delta": 10, "source": new_bonus},
        # Someone else's bonus must not join anyone else's lookup.
        {"ts": "2026-09-02T00:00:03+00:00", "claim_code": REFERRER_CODE, "email": "",
         "credits_delta": 10, "source": "referral_bonus:from_ref_SOMEONEELSE1"},
    ])

    claims = support_tools.lookup_customer(BUYER)["pack_claims"]
    assert sorted((c["claim_code"], c["source"]) for c in claims) == sorted([
        (BUYER_OWN_CODE, "stripe:cs_test_buyerOwnPack00000001"),
        (BUYER_OWN_CODE, new_bonus),
    ]), claims

    claims = support_tools.lookup_customer(RECIPIENT)["pack_claims"]
    assert sorted((c["claim_code"], c["source"]) for c in claims) == sorted([
        (LEGACY_GIFT_CODE, "stripe-gift:cs_test_legacyGift0000000001"),
        (LEGACY_GIFT_CODE, LEGACY_BONUS_SOURCE),
    ]), claims


def test_subscription_change_budget_follows_the_account_not_the_address(tmp_path):
    """F5. With the Fly edge header trusted, spend the budget from one client
    address, then come back with the same session from another /24: still
    429, and no Stripe call. Keyed on the address, the second one would have
    a fresh budget."""
    emails = [SUBSCRIBER]
    _seed_accounts(tmp_path, emails)
    closed = _srv.reserve_ports(1)[0]
    stripe_calls = re.compile(r"\[stripe_api\] URLError path=/subscriptions/")
    first_ip, second_ip = "203.0.113.7", "198.51.100.9"
    for base in _srv.server_processes(tmp_path, stub_calendars=True,
                                      STRIPE_SECRET_KEY="sk_test_unit_only",
                                      ORPHO_TRUST_PROXY_HEADERS="1",
                                      RATE_LIMIT_PER_DAY="1",
                                      **_no_egress(closed)):
        # Control: this server really keys limits on Fly-Client-IP, so the
        # two addresses below are two different clients to it. One free
        # anchor per address per day: the second from the first address is
        # refused, the first from the second address is not.
        def anchor_from(ip, digest):
            return _srv.anchor(base, {"hash_hex": digest}, {"Fly-Client-IP": ip})[0]
        assert anchor_from(first_ip, "0a" * 32) == 200
        assert anchor_from(first_ip, "0b" * 32) == 429
        assert anchor_from(second_ip, "0c" * 32) == 200

        since = len(_log(tmp_path))
        session = {**_as(SUBSCRIBER, emails), "Fly-Client-IP": first_ip}
        statuses = [_post(base, "/api/me/cancel-subscription", {}, session)[0]
                    for _ in range(30)]
        assert 429 in statuses, statuses
        calls = len(stripe_calls.findall(_log(tmp_path)[since:]))
        assert calls == statuses.index(429) and calls > 0, (calls, statuses)

        moved = {**_as(SUBSCRIBER, emails), "Fly-Client-IP": second_ip}
        for route in ("/api/me/cancel-subscription", "/api/me/reactivate-subscription"):
            status, body, _ = _post(base, route, {}, moved)
            assert status == 429, (route, status, body)
        assert len(stripe_calls.findall(_log(tmp_path)[since:])) == calls


def test_stripe_message_with_a_newline_stays_on_one_log_line(monkeypatch, capsys):
    """F6. Stripe echoes the caller's input in its message. A newline in that
    input used to reach the log as a real one, so the caller could write a
    line of their own that read like ours. The body is pretty-printed JSON,
    as Stripe sends it, so it has line breaks of its own too."""
    import stripe_api
    monkeypatch.setattr(stripe_api, "STRIPE_SECRET_KEY", "sk_test_unit_only")
    forged = "[stripe_api] ALERT: auth failure (401) - forged by caller"
    email = f"a\n{forged}\r\n@b.test\u2028x"
    body = json.dumps({"error": {"type": "invalid_request_error", "code": "email_invalid",
                                 "message": f"Invalid email address: {email}"}},
                      indent=2).encode()
    assert b"\n" in body

    def answer(req, timeout=None):
        raise urllib.error.HTTPError(req.full_url, 400, "stub", {}, io.BytesIO(body))

    monkeypatch.setattr(urllib.request, "urlopen", answer)
    result = stripe_api.create_checkout_session(
        price_id="price_1RealConfiguredPackPriceId", mode="payment",
        success_url="https://example.test/s", cancel_url="https://example.test/c",
        customer_email=email)
    assert result["error"] == "Request rejected by Stripe (invalid parameters).", result
    err = capsys.readouterr().err
    lines = err.splitlines()
    # Two lines, the HTTP line and the message line, and nothing else.
    assert len(lines) == 2, lines
    assert lines[0].startswith("[stripe_api] HTTP 400 (invalid_request) "), lines
    assert lines[1].startswith("[stripe_api] stripe message (invalid_request, "), lines
    assert not any(line.startswith("[stripe_api] ALERT") for line in lines), lines
    # The message is still all there, escaped.
    assert "Invalid email address: a\\n[stripe_api] ALERT" in lines[1], lines[1]
