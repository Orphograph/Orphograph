"""Client-shaped input must get an HTTP answer, never a dropped connection.

Each case below used to raise out of its handler. http.server then printed a
traceback and closed the socket with zero bytes, so the client saw a dropped
connection instead of a status, and the difference was itself a signal:

- A non-ASCII X-Orpho-Founder header made hmac.compare_digest raise
  TypeError on every founder route, before the failure budget was charged.
  Real founder routes dropped the connection; any other /api/founder/* path
  answered 404. A prober could list the founder surface, without limit.
- A Stripe id with a non-ASCII "alphanumeric" (str.isalnum accepts é, ٣)
  passed the shape check and made the HTTP client raise building the URL.
- Valid JSON of the wrong shape ([] or {"email": 1}), or JSON nested past the
  parser's recursion limit, raised on `.get` / `.strip()` / json.loads.

Two more defects from the same route sweep ride here: a failing Stripe account
lookup was repeated on every /api/config load, and every /pay/crypto* request
with a forged arm cookie appended to the A/B ledger on the data volume even
with the experiment off.
"""
from __future__ import annotations

import json
import subprocess
import time
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

import _srv

REPO_ROOT = Path(__file__).resolve().parent.parent
FOUNDER_TOKEN = "founder-token-for-tests-0123456789"
FOUNDER_ROUTES = (
    "/api/founder/payout-status",
    "/api/founder/metrics",
    "/api/founder/customer?email=someone%40example.test",
    "/api/founder/admin/toggles",
    "/api/founder/morning-summary",
    "/api/founder/funnel",
)
# Not a bot (the A/B split never assigns bots) and not a browser string.
CLIENT_UA = "orpho-route-test/1.0"


def _raw(base: str, path: str, header: str = "") -> bytes:
    """One GET carrying a header urllib would refuse to send (a byte >= 0x80)."""
    return _srv.raw_request(base, path, headers=header)


def _status_of(raw: bytes) -> int:
    assert raw, "the server closed the connection without answering"
    return int(raw.split(b" ", 2)[1])


def _log(base: str) -> str:
    return Path(_srv._LOG_BY_BASE[base]).read_text(errors="replace")


def _in_data_dir(data_dir: Path, code: str) -> str:
    prog = (
        "import os,sys;"
        f"os.environ['ORPHO_DATA_DIR']={str(data_dir)!r};"
        f"sys.path.insert(0,{str(REPO_ROOT / 'server')!r});" + code
    )
    out = subprocess.run([sys.executable, "-c", prog], capture_output=True,
                         text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return out.stdout.strip()


# ── founder gate ────────────────────────────────────────────────────────────

@pytest.fixture()
def founder_server(tmp_path):
    for base in _srv.server_processes(tmp_path, ORPHO_FOUNDER_TOKEN=FOUNDER_TOKEN):
        yield base


@pytest.mark.parametrize("route", FOUNDER_ROUTES)
def test_non_ascii_founder_header_is_a_plain_404(founder_server, route):
    raw = _raw(founder_server, route, "X-Orpho-Founder: caf\xe9\xff\r\n")
    assert _status_of(raw) == 404
    # The same answer an unknown founder path gives, so nothing is enumerable.
    unknown = _raw(founder_server, "/api/founder/no-such-route",
                   "X-Orpho-Founder: caf\xe9\xff\r\n")
    assert _status_of(unknown) == 404
    assert "Traceback" not in _log(founder_server)


def test_non_ascii_guesses_spend_the_failure_budget(founder_server):
    good = f"X-Orpho-Founder: {FOUNDER_TOKEN}\r\n"
    # Control: the real token opens the route before any guessing.
    assert _status_of(_raw(founder_server, "/api/founder/admin/toggles", good)) == 200
    for _ in range(25):  # FOUNDER_FAIL_CAPACITY is 20
        _raw(founder_server, "/api/founder/admin/toggles", "X-Orpho-Founder: \xe9\r\n")
    # Every non-ASCII probe was charged, so this address is now locked out.
    assert _status_of(_raw(founder_server, "/api/founder/admin/toggles", good)) == 404
    assert "per-IP lockout engaged" in _log(founder_server)


# ── Stripe ids ──────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def stripe_server(tmp_path_factory):
    data_dir = tmp_path_factory.mktemp("robust_stripe")
    # A dummy key so the routes reach their id checks. Every id sent below is
    # rejected before any Stripe call, so nothing leaves the machine.
    # NOWPAYMENTS_API_KEY likewise, so /api/nowpayments/create parses its body.
    for base in _srv.server_processes(data_dir, STRIPE_SECRET_KEY="sk_test_not_a_real_key",
                                      NOWPAYMENTS_API_KEY="np_not_a_real_key"):
        yield base, data_dir


@pytest.mark.parametrize("sid", ["cs_test_%C3%A9", "cs_test_%D9%A3", "cs_live_%EF%BC%A1"])
def test_non_ascii_session_id_is_rejected_not_dropped(stripe_server, sid):
    base, _ = stripe_server
    status, _, _ = _srv.request(base, f"/api/stripe/session?id={sid}",
                                headers={"Accept": "application/json"})
    assert status == 400
    # The route's own check answered, before the lookup budget and before the
    # Stripe client: the client's fallback would log "unsendable path".
    assert "unsendable path" not in _log(base)


@pytest.mark.parametrize("sid", ["cs_test_é", "cs_live_٣abc"])
def test_recover_rejects_non_ascii_session_id(stripe_server, sid):
    base, _ = stripe_server
    body = json.dumps({"stripe_session_id": sid, "email": "a@example.test"}).encode()
    status, _, _ = _srv.request(base, "/api/recover", "POST", body,
                                {"Content-Type": "application/json"})
    assert status == 400


def test_stripe_client_answers_an_unsendable_path(monkeypatch):
    """The last line of defence: an id that slips past a route's check comes
    back as a failed result, not an exception out of the handler."""
    sys.path.insert(0, str(REPO_ROOT / "server"))
    import stripe_api
    monkeypatch.setattr(stripe_api, "STRIPE_SECRET_KEY", "sk_test_not_a_real_key")
    # Closed loopback port: even a regression that got as far as connecting
    # could not reach anything.
    monkeypatch.setattr(stripe_api, "STRIPE_BASE", "http://127.0.0.1:9/v1")
    for bad in ("cs_test_é", "cs_test_a\x01b"):  # UnicodeError, InvalidURL
        res = stripe_api._request("GET", f"/checkout/sessions/{bad}")
        assert res["ok"] is False and res["status"] == 400, bad


# ── webhook signatures: the same compare, the same crash ───────────────────

@pytest.fixture(scope="module")
def webhook_server(tmp_path_factory):
    data_dir = tmp_path_factory.mktemp("robust_webhooks")
    for base in _srv.server_processes(data_dir, STRIPE_WEBHOOK_SECRET="whsec_not_a_real_secret",
                                      NOWPAYMENTS_IPN_SECRET="ipn_not_a_real_secret"):
        yield base


@pytest.mark.parametrize("route,header", [
    ("/api/stripe/webhook", "Stripe-Signature: t=%d,v1=\xe9\xff\r\n"),
    ("/api/nowpayments/webhook", "x-nowpayments-sig: \xe9\xff\r\n"),
])
def test_a_non_ascii_webhook_signature_is_refused_not_dropped(webhook_server, route, header):
    if "%d" in header:
        header = header % int(time.time())
    raw = _srv.raw_request(webhook_server, route, "POST", body=b'{"id":"evt_x","type":"x"}',
                           headers="Content-Type: application/json\r\n" + header)
    assert 400 <= _status_of(raw) < 500
    assert "Traceback" not in _log(webhook_server)


def test_a_deeply_nested_nowpayments_body_is_refused(webhook_server):
    raw = _srv.raw_request(webhook_server, "/api/nowpayments/webhook", "POST",
                           body=b"[" * 2000 + b"]" * 2000,
                           headers="Content-Type: application/json\r\nx-nowpayments-sig: abc\r\n")
    assert 400 <= _status_of(raw) < 500
    assert "Traceback" not in _log(webhook_server)


# ── JSON bodies of the wrong shape ──────────────────────────────────────────

@pytest.fixture(scope="module")
def member(stripe_server):
    """A signed-in subscriber who owns a team: every JSON route is reachable."""
    base, data_dir = stripe_server
    email = "shape-owner@example.test"
    sid = _in_data_dir(data_dir, (
        "import auth, subscriptions, teams, time;"
        f"subscriptions.record_customer_email('cus_shape', {email!r});"
        "subscriptions.record_subscription_event('cus_shape', 'active', time.time() + 86400 * 30, 'sub_shape');"
        f"teams.create_team({email!r}, 'Shape team');"
        f"print(auth.create_session({email!r})[0])"
    ))
    return base, {"Cookie": f"orpho_sid={sid}", "Content-Type": "application/json"}


JSON_ROUTES = (
    "/api/recover",
    "/api/stripe/checkout",
    "/api/me/team/create",
    "/api/me/team/redeem",
    "/api/me/team/remove",
    "/api/me/receipt/ShapeReceipt01/privacy",
    "/api/me/webhooks",
    "/api/me/webhooks/delete",
)
NOT_AN_OBJECT = (b"[]", b'"text"', b"1", b"null", b"[" * 2000 + b"]" * 2000)


@pytest.mark.parametrize("route", JSON_ROUTES)
@pytest.mark.parametrize("body", NOT_AN_OBJECT, ids=["list", "string", "number", "null", "deep"])
def test_a_body_that_is_not_an_object_is_a_400(member, route, body):
    base, headers = member
    status, _, _ = _srv.request(base, route, "POST", body, headers)
    assert status == 400
    assert "Traceback" not in _log(base)


STRING_FIELDS = ("stripe_session_id", "email", "plan", "team_name", "invite_code",
                 "member_email", "url")


@pytest.mark.parametrize("route", JSON_ROUTES)
def test_fields_of_the_wrong_type_are_a_400(member, route):
    """A present field of the wrong type is the client's error, and changes
    nothing: treating it as absent made {"team_name": 1} create "My Team"."""
    base, headers = member
    body = json.dumps({k: 1 for k in STRING_FIELDS}).encode()
    status, _, _ = _srv.request(base, route, "POST", body, headers)
    if route.endswith("/privacy"):
        assert status < 500, status  # reads no string field
    else:
        assert status == 400, status
    assert "Traceback" not in _log(base)


# Routes outside the set above that parse a JSON body. anchor, anchor/batch,
# anchor_folder, event and waitlist are hardened in their own PRs.
MORE_JSON_ROUTES = ("/api/auth/email-link", "/api/pack/recover",
                    "/api/me/refund-request", "/api/nowpayments/create")


@pytest.mark.parametrize("route", MORE_JSON_ROUTES)
@pytest.mark.parametrize("body", [b"[]", b"null", b"[" * 2000 + b"]" * 2000], ids=["list", "null", "deep"])
def test_the_other_json_routes_answer_too(member, route, body):
    base, headers = member
    status, _, _ = _srv.request(base, route, "POST", body, headers)
    if route == "/api/me/refund-request":
        # By design a missing or unreadable body is a request with no reason.
        assert status < 500, status
    else:
        assert status == 400, status
    assert "Traceback" not in _log(base)


# ── /api/config: a failing Stripe lookup is not repeated per request ───────

def test_failed_account_lookup_is_not_repeated_every_call(monkeypatch):
    sys.path.insert(0, str(REPO_ROOT / "server"))
    import stripe_api
    monkeypatch.setattr(stripe_api, "STRIPE_SECRET_KEY", "sk_test_not_a_real_key")
    monkeypatch.setattr(stripe_api, "_ACCOUNT_CACHE", {"ts": 0.0, "enabled": None, "failed": 0.0})
    clock = [1_000_000.0]
    monkeypatch.setattr(stripe_api.time, "time", lambda: clock[0])
    calls = []

    def failing(method, path, form=None):
        calls.append(path)
        return {"ok": False, "status": 401}

    with patch.object(stripe_api, "_request", side_effect=failing):
        for _ in range(8):
            assert stripe_api.charges_enabled() is None
        assert calls == ["/account"], "a failing lookup ran on every call"
        # No answer yet (a fresh process): retry soon, not after a minute.
        clock[0] += stripe_api.ACCOUNT_RETRY_COLD_SEC + 1
        assert stripe_api.charges_enabled() is None
        assert len(calls) == 2, "the cold lookup never retried"

    with patch.object(stripe_api, "_request",
                      return_value={"ok": True, "data": {"charges_enabled": True}}):
        clock[0] += stripe_api.ACCOUNT_RETRY_COLD_SEC + 1
        assert stripe_api.charges_enabled() is True
    # Stale-if-error, and the failure is not retried on every call either.
    clock[0] += stripe_api.ACCOUNT_CACHE_TTL_SEC + 1
    calls.clear()
    with patch.object(stripe_api, "_request", side_effect=failing):
        for _ in range(5):
            assert stripe_api.charges_enabled() is True
        assert calls == ["/account"]
        clock[0] += stripe_api.ACCOUNT_RETRY_COLD_SEC + 1
        assert stripe_api.charges_enabled() is True
        assert calls == ["/account"], "with an answer in hand the wait is the full minute"


def test_concurrent_first_lookups_all_get_the_answer(monkeypatch):
    """A request that arrives while the first lookup after boot is in flight
    must not be told the card rail is off."""
    import threading
    sys.path.insert(0, str(REPO_ROOT / "server"))
    import stripe_api
    monkeypatch.setattr(stripe_api, "STRIPE_SECRET_KEY", "sk_test_not_a_real_key")
    monkeypatch.setattr(stripe_api, "_ACCOUNT_CACHE", {"ts": 0.0, "enabled": None, "failed": 0.0})

    def slow_ok(method, path, form=None):
        time.sleep(0.3)
        return {"ok": True, "data": {"charges_enabled": True}}

    answers = []
    with patch.object(stripe_api, "_request", side_effect=slow_ok):
        threads = [threading.Thread(target=lambda: answers.append(stripe_api.charges_enabled()))
                   for _ in range(3)]
        for t in threads:
            t.start()
            time.sleep(0.05)
        for t in threads:
            t.join()
    assert answers == [True, True, True]


# ── /pay/crypto: the A/B ledger only records the page, only while running ──

def _ab_rows(data_dir: Path) -> list[dict]:
    p = data_dir / "ab_home.jsonl"
    if not p.exists():
        return []
    return [json.loads(x) for x in p.read_text().splitlines() if x.strip()]


def _get(base: str, path: str, ua: str = CLIENT_UA) -> int:
    return _srv.request(base, path, headers={
        "User-Agent": ua, "Cookie": "orpho_ab_home=dark"})[0]


def test_checkout_view_is_not_written_with_the_experiment_off(tmp_path):
    for base in _srv.server_processes(tmp_path, ORPHO_AB_HOME="0"):
        for _ in range(3):
            assert _get(base, "/pay/crypto") == 200
        _get(base, "/pay/cryptoZZZ")
    assert _ab_rows(tmp_path) == []


def test_checkout_view_counts_the_page_only(tmp_path):
    for base in _srv.server_processes(tmp_path, ORPHO_AB_HOME="1.0"):
        assert _get(base, "/pay/crypto") == 200  # control: one real view
        assert _get(base, "/pay/crypto/") == 200  # the page with a slash: a view too
        for path in ("/pay/crypto.css?v=2", "/pay/crypto.js?v=2",
                     "/pay/crypto.html", "/pay/cryptoZZZ"):
            _get(base, path)
        _get(base, "/pay/crypto", ua="Googlebot/2.1")
        rows = [r for r in _ab_rows(tmp_path) if r["event"] == "checkout_view"]
        assert len(rows) == 2, rows
        assert all(r.get("v") == 2 for r in rows)

        # The ledger has a ceiling: once full, it stops growing.
        ledger = tmp_path / "ab_home.jsonl"
        with open(ledger, "r+b") as f:
            f.truncate(16 * 1024 * 1024)  # sparse; no real 16 MB written
        assert _get(base, "/pay/crypto") == 200
        assert ledger.stat().st_size == 16 * 1024 * 1024
