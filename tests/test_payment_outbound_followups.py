"""Payment/outbound follow-ups that need the app in this process (2026-09-26).

The companion module, tests/test_payment_and_outbound_hardening.py, drives
server subprocesses through tests/_srv.py. These two cannot: a Lightning
invoice from the mock backend is paid with lightning.mock_pay(), which only
reaches invoices held in the same process, and the pack-recover resend is
a function whose collaborators are replaced here. So this module starts its
own loopback server and does not import _srv.

  F3. /api/pack/recover's resend ran every code inside one try once it moved
      onto its own thread, so one failing code stopped the rest (master had
      a try per send).
  F4. The Lightning invoice budget was spent on every quote and never given
      back, so an agent that paid every invoice was cut to 20 purchases and
      then one per 3 minutes. A paid, spent invoice now refunds its token.

Nothing leaves the machine: calendars are stubbed at engine._submit, the
Lightning backend is the in-process mock, mail is inert (no RESEND_API_KEY)
and every proxy variable points at a closed loopback port.
"""
from __future__ import annotations

import http.client
import json
import socket
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "server"))
from conftest import PENDING_BODY  # noqa: E402

# Modules that read the environment at import time: dropped before the
# fresh import and put back afterwards, as tests/test_lightning_l402.py does.
_POLLUTED = (
    "app", "engine", "auth", "rate_limit", "credits", "stats",
    "health", "subscriptions", "teams", "stripe_webhook",
    "mailer", "api_keys", "affiliate", "newsletter", "waitlist",
    "blog", "unsubscribe", "gdpr", "public_config",
    "receipt_export", "btc_price", "btc_payments", "stripe_api",
    "og_svg", "qrcode_svg", "badge_svg", "analytics",
    "support_tools", "onboarding", "referrals", "file_lock",
    "merkle", "lightning", "webhooks",
)

INVOICE_BUDGET = 20  # app.LN_INVOICE_CAPACITY; asserted below, not assumed


def _closed_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def fresh_app(tmp_path, monkeypatch):
    """`app` imported fresh against an empty data dir: one free anchor per
    client per day, mock Lightning, no mail, no route out."""
    proxy = f"http://127.0.0.1:{_closed_port()}"
    env = {
        "ORPHO_DATA_DIR": str(tmp_path), "HOST": "127.0.0.1", "PORT": "0",
        "ORPHO_COOKIE_SECURE": "0", "RATE_LIMIT_PER_DAY": "1",
        "ORPHO_LN_BACKEND": "mock", "ORPHO_LN_ALLOW_MOCK": "1",
        "HTTPS_PROXY": proxy, "https_proxy": proxy, "HTTP_PROXY": proxy,
        "http_proxy": proxy, "NO_PROXY": "127.0.0.1,localhost",
        "no_proxy": "127.0.0.1,localhost",
    }
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    for key in ("RESEND_API_KEY", "STRIPE_SECRET_KEY", "ORPHO_TRUST_PROXY_HEADERS",
                "ORPHO_LN_PRICE_SATS"):
        monkeypatch.delenv(key, raising=False)
    saved = {m: sys.modules[m] for m in _POLLUTED if m in sys.modules}
    for m in _POLLUTED:
        sys.modules.pop(m, None)
    try:
        import app
        import engine
        import lightning
        monkeypatch.setattr(engine, "_submit", lambda cal, h: (True, PENDING_BODY))
        # Retired in production (lightning.LIGHTNING_RETIRED, 2026-09-28);
        # these tests cover the armed code, on this fresh module copy only.
        monkeypatch.setattr(lightning, "LIGHTNING_RETIRED", False)
        yield app
    finally:
        for m in _POLLUTED:
            sys.modules.pop(m, None)
        sys.modules.update(saved)


class _Server:
    def __init__(self, app):
        from http.server import ThreadingHTTPServer
        import engine
        import lightning
        self.app, self.engine, self.lightning = app, engine, lightning
        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
        self.base = f"http://127.0.0.1:{self._httpd.server_address[1]}"
        threading.Thread(target=self._httpd.serve_forever, daemon=True).start()

    def close(self):
        self._httpd.shutdown()
        self._httpd.server_close()

    def post(self, path, body, headers=None):
        req = urllib.request.Request(
            self.base + path, data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json", **(headers or {})})
        # The loopback server is reached directly, whatever the proxy env says.
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            with opener.open(req, timeout=20) as resp:
                return resp.status, dict(resp.headers), json.loads(resp.read())
        except urllib.error.HTTPError as e:
            return e.code, dict(e.headers), json.loads(e.read())

    def quote(self):
        return self.post("/api/ln/quote", {})

    def paid_credential(self) -> str:
        status, _h, q = self.quote()
        assert status == 200, q
        preimage = self.lightning.mock_pay(
            self.lightning.parse_macaroon(q["macaroon"])["payment_hash"])
        assert preimage
        return f"L402 {q['macaroon']}:{preimage}"

    def anchor(self, digest, credential=None):
        headers = {"Authorization": credential} if credential else None
        return self.post("/api/anchor", {"hash_hex": digest}, headers)

    def quotes_until_refused(self, limit=60) -> int:
        """How many quotes this client gets before the first 429."""
        for n in range(limit):
            status, headers, body = self.quote()
            if status == 429:
                assert int(headers.get("Retry-After", "0")) > 0, headers
                return n
            assert status == 200, body
        raise AssertionError(f"no 429 in {limit} quotes")


@pytest.fixture
def ln_server(fresh_app):
    assert fresh_app.LN_INVOICE_CAPACITY == INVOICE_BUDGET
    assert fresh_app.lightning.configured()
    server = _Server(fresh_app)
    try:
        yield server
    finally:
        server.close()


def _digest(i: int) -> str:
    return f"{i:064x}"


# ── F3. one failing code does not stop the others ──────────────────────────

def test_a_send_that_raises_does_not_stop_the_next_code(fresh_app, monkeypatch, capsys):
    app = fresh_app
    sent = []

    def send(addr, code, remaining):
        if code == "pk_FIRSTcode000001":
            raise http.client.IncompleteRead(b"")
        sent.append((addr, code, remaining))
        return True

    monkeypatch.setattr(app.credits, "find_claim_codes_by_email",
                        lambda _addr: ["pk_FIRSTcode000001", "pk_SECONDcode00001"])
    monkeypatch.setattr(app.credits, "balance", lambda _code: 5)
    monkeypatch.setattr(app.mailer, "send_pack_claim_email", send)
    app._pack_recover_resend("owner@recover.test")
    assert sent == [("owner@recover.test", "pk_SECONDcode00001", 5)]
    err = capsys.readouterr().err
    assert "[pack-recover] resend of one code failed: IncompleteRead" in err, err
    for secret in ("recover.test", "pk_FIRSTcode000001", "pk_SECONDcode00001"):
        assert secret not in err, err


def test_a_balance_that_will_not_read_does_not_stop_the_next_code(fresh_app, monkeypatch,
                                                                   capsys):
    """credits.balance raises on a row whose delta will not parse; that is
    one code's problem, not every code's."""
    app = fresh_app
    sent = []

    def balance(code):
        if code == "pk_CORRUPTcode0001":
            raise ValueError("credits_delta is not a whole number")
        return 3

    monkeypatch.setattr(app.credits, "find_claim_codes_by_email",
                        lambda _addr: ["pk_CORRUPTcode0001", "pk_HEALTHYcode0001"])
    monkeypatch.setattr(app.credits, "balance", balance)
    monkeypatch.setattr(app.mailer, "send_pack_claim_email",
                        lambda addr, code, n: sent.append(code) or True)
    app._pack_recover_resend("owner@recover.test")
    assert sent == ["pk_HEALTHYcode0001"]
    assert "[pack-recover] resend of one code failed: ValueError" in capsys.readouterr().err


def test_a_lookup_that_fails_is_logged_once_without_the_address(fresh_app, monkeypatch,
                                                                capsys):
    app = fresh_app

    def lookup(_addr):
        raise PermissionError("ledger unreadable")

    monkeypatch.setattr(app.credits, "find_claim_codes_by_email", lookup)
    app._pack_recover_resend("owner@recover.test")
    err = capsys.readouterr().err
    assert err.count("[pack-recover]") == 1, err
    assert "[pack-recover] resend failed: PermissionError" in err, err
    assert "recover.test" not in err, err


# ── F4. a paid invoice gives its token back ────────────────────────────────

def test_an_agent_that_pays_every_invoice_is_never_cut_off(ln_server):
    rounds = INVOICE_BUDGET + 5
    for i in range(rounds):
        status, _h, body = ln_server.anchor(_digest(i), ln_server.paid_credential())
        assert status == 200, (i, body)
        assert body["calendars_ok"] > 0
    # Every paid invoice gave its token back, so the whole budget is still
    # there for unpaid ones, and no more than that.
    assert ln_server.quotes_until_refused() == INVOICE_BUDGET


def test_a_free_anchor_does_not_spend_the_invoice_budget(ln_server):
    status, _h, body = ln_server.anchor(_digest(1))
    assert status == 200, body  # the one free anchor
    assert ln_server.quotes_until_refused() == INVOICE_BUDGET
    # Past the invoice budget an over-limit anchor is not challenged: it gets
    # the plain 429, with no invoice in it.
    status, headers, body = ln_server.anchor(_digest(2))
    assert status == 429, body
    assert body["error"] == "rate limit exceeded", body
    assert "WWW-Authenticate" not in headers and "invoice" not in body, (headers, body)


def test_replaying_a_spent_credential_gives_nothing_back(ln_server):
    for _ in range(5):
        assert ln_server.quote()[0] == 200  # five unpaid invoices: 15 left
    credential = ln_server.paid_credential()  # 14 left
    assert ln_server.anchor(_digest(1), credential)[0] == 200  # paid: 15 again
    for i in range(5):
        status, _h, body = ln_server.anchor(_digest(10 + i), credential)
        assert status == 401 and "already spent" in body["error"], body
    assert ln_server.quotes_until_refused() == INVOICE_BUDGET - 5


def test_a_released_credential_refunds_once_when_it_is_finally_spent(ln_server,
                                                                     monkeypatch):
    """A 0-calendar anchor releases the claim so the agent can retry. The
    retry is when the payment is spent, and the only time a token comes
    back; refunding at the claim would return one per attempt."""
    for _ in range(5):
        assert ln_server.quote()[0] == 200  # 15 left
    credential = ln_server.paid_credential()  # 14 left
    monkeypatch.setattr(ln_server.engine, "_submit", lambda cal, h: (False, "stubbed outage"))
    status, _h, body = ln_server.anchor(_digest(1), credential)
    assert status == 200 and body["calendars_ok"] == 0, body
    monkeypatch.setattr(ln_server.engine, "_submit", lambda cal, h: (True, PENDING_BODY))
    status, _h, body = ln_server.anchor(_digest(2), credential)
    assert status == 200 and body["calendars_ok"] > 0, body  # spent: 15 again
    assert ln_server.quotes_until_refused() == INVOICE_BUDGET - 5


# ── rate_limit.TokenBucket.refund ──────────────────────────────────────────

def test_refund_gives_back_what_was_spent_and_never_more_than_capacity():
    from rate_limit import TokenBucket
    bucket = TokenBucket(3, 1 / 3600.0)
    for _ in range(3):
        assert bucket.check("k")[0] is True
    assert bucket.check("k")[0] is False
    bucket.refund("k")
    assert bucket.check("k")[0] is True
    assert bucket.check("k")[0] is False
    bucket.refund("k", 10)
    assert bucket.peek("k") == pytest.approx(3.0)
    # check() and peek() clamp what they read, so a stored value above
    # capacity would not show through them; it would through save(), which
    # writes the stored value to the snapshot. The stored value is the
    # contract.
    assert bucket._buckets["k"][0] <= bucket.capacity
    assert [bucket.check("k")[0] for _ in range(4)] == [True, True, True, False]


def test_refund_for_a_key_with_no_bucket_changes_nothing():
    from rate_limit import TokenBucket
    bucket = TokenBucket(3, 1 / 3600.0)
    bucket.refund("never-seen")
    assert "never-seen" not in bucket._buckets
    assert bucket.peek("never-seen") == 3.0


def test_refund_holds_the_bucket_lock():
    """check() updates a bucket under self._lock; a refund outside it could
    overwrite a concurrent check's update and hand out a token twice."""
    from rate_limit import TokenBucket

    class RecordingLock:
        def __init__(self):
            self._inner = threading.Lock()
            self.entered = 0

        def __enter__(self):
            self._inner.acquire()
            self.entered += 1
            return self

        def __exit__(self, *_exc):
            self._inner.release()
            return False

    bucket = TokenBucket(3, 1 / 3600.0)
    bucket.check("k")
    bucket._lock = RecordingLock()
    bucket.refund("k")
    assert bucket._lock.entered == 1
