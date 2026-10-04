"""An address with an uppercase letter outside A-Z is refused where it comes in.

auth.email_id is the HMAC of email.lower(), and str.lower() turns U+212A
KELVIN SIGN into "k". EMAIL_RE admits that character, so a sign-in typed with
it in place of the "k" of karl's address (the twin) got karl's account id: its
session listed karl's receipts, private ones included; pack recovery mailed
karl's claim codes to the twin spelling; and one bounce of the twin stopped
mail to karl. Founder decision 2026-10-03: the id stays, and every intake that
takes an address from its caller refuses one that needs_lowercase(), with a
hint to type it in lowercase. Typed in lowercase, the same address is karl's
and works; an ASCII address in any case works exactly as before.

Everything goes through the real handlers of a server process on 127.0.0.1:
calendars and Stripe stubbed, every email recorded instead of sent, and every
connection or name lookup for another host refused and recorded
(tests/_run_server.py). Each refusal sits beside a control that goes through,
so a test that sees nothing cannot pass by seeing nothing.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
import sys
import time
from pathlib import Path

import pytest

import _srv
from conftest import write_fixture_receipt

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "server"))

import auth  # noqa: E402
import resend_webhook  # noqa: E402
from email_fold import LOWERCASE_HINT, fold_email, needs_lowercase  # noqa: E402

KELVIN = "K"  # KELVIN SIGN. str.lower() turns it into ASCII "k".
SECRET = "email-twin-closed-hmac-secret"
WHSEC = "whsec_" + "twin-closed-test"   # a test value, short of a real secret's shape
JSON = {"Content-Type": "application/json"}
REFUSED = {"error": "email_needs_lowercase", "message": LOWERCASE_HINT}
WAIT_SEC = 30

TOKENS = "auth_tokens.jsonl"
SESSIONS = "auth_sessions.jsonl"
WAITLIST = "waitlist.jsonl"
CREDITS = "credit_ledger.jsonl"
STRIPE_CALLS = "stub_stripe_calls.jsonl"
STRIPE_ANSWERS = "stub_stripe_answers.json"
MAIL = "stub_mail_sent.jsonl"
EGRESS = "stub_egress_blocked.jsonl"
GAPS = "recovery_gaps.jsonl"
SUPPRESSED = "resend_suppressed_emails.jsonl"


def _pair(stem: str) -> tuple[str, str]:
    """(karl's address, its twin): the twin has U+212A where karl has "k"."""
    plain = f"k{stem}.{secrets.token_hex(3)}@example.test"
    twin = KELVIN + plain[1:]
    assert twin != plain and twin.lower() == plain, "not the collision this file is about"
    assert needs_lowercase(twin) and not needs_lowercase(plain)
    return plain, twin


def _fresh(stem: str) -> str:
    return f"{stem}.{secrets.token_hex(3)}@example.test"


def _eid(addr: str) -> str:
    return hmac.new(SECRET.encode(), addr.lower().encode(), hashlib.sha256).hexdigest()[:16]


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    d = tmp_path_factory.mktemp("email_twin_closed")
    for base in _srv.server_processes(
            d, stub_calendars=True, stub_stripe=True, capture_mail=True, egress_guard=True,
            ORPHO_HMAC_SECRET=SECRET, STRIPE_SECRET_KEY="sk_test_not_a_real_key",
            STRIPE_PRICE_PACK="price_test_not_real", STRIPE_WEBHOOK_SECRET=WHSEC,
            NOWPAYMENTS_API_KEY="test_dummy_key_not_real", CHECKOUT_RATE_PER_HOUR="1000"):
        yield base, d
    # Nothing in this module may have tried to reach another host except the
    # NOWPayments invoice the lowercase controls ask for (refused, recorded).
    tried = {row["host"] for row in _rows(d / EGRESS)}
    assert tried <= {"api.nowpayments.io"}, tried


# ── reading what the server did ───────────────────────────────────────────

def _rows(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _snap(d: Path, *names: str) -> dict:
    return {n: (d / n).read_bytes() if (d / n).exists() else None for n in names}


def _mails(d: Path) -> list[dict]:
    return [r["payload"] for r in _rows(d / MAIL)]


def _to(mails: list[dict]) -> list[list[str]]:
    return [m["to"] for m in mails]


def _wait_mail(d: Path, addr: str, since: int = 0) -> list[dict]:
    """Every mail to exactly `addr` sent after the first `since`, once one has."""
    deadline = time.time() + WAIT_SEC
    while time.time() < deadline:
        got = [m for m in _mails(d)[since:] if m["to"] == [addr]]
        if got:
            return got
        time.sleep(0.05)
    pytest.fail(f"no mail reached the mailer within {WAIT_SEC}s")


def _post(base: str, path: str, payload: dict, headers: dict | None = None):
    return _srv.post_json(base, path, payload, {**JSON, **(headers or {})}, timeout=30)


def _assert_refused(status: int, body: dict) -> None:
    assert (status, body) == (400, REFUSED), (status, body)


# ── signing in ────────────────────────────────────────────────────────────

def _link_token(mail: dict) -> str:
    m = re.search(r"/a/([A-Za-z0-9_-]{16,64})", mail["text"])
    assert m, "the sign-in email carries no link"
    return m.group(1)


def _redeem(base: str, token: str, method: str = "GET"):
    status, _body, headers = _srv.request(base, "/a/" + token, method, timeout=15)
    cookie = headers.get("Set-Cookie")
    return status, (cookie.split(";", 1)[0] if cookie else None)


def _request_link(base: str, d: Path, addr: str) -> str:
    n0 = len(_mails(d))
    status, body = _post(base, "/api/auth/email-link", {"email": addr})
    assert status == 200 and body.get("ok") is True, (status, body)
    new = _mails(d)[n0:]
    assert _to(new) == [[addr]], ("the link was not mailed to the spelling typed", _to(new))
    return _link_token(new[0])


def _sign_in(base: str, d: Path, addr: str) -> str:
    status, cookie = _redeem(base, _request_link(base, d, addr))
    assert status == 303 and cookie, (status, cookie)
    return cookie


def _vault(base: str, cookie: str):
    status, body = _srv.get_json(base, "/api/me/anchors?limit=200", {"Cookie": cookie})
    if status != 200:
        return status
    return sorted(a["receipt_id"] for a in body["anchors"])


def _me(base: str, cookie: str) -> int:
    return _srv.request(base, "/api/me", headers={"Cookie": cookie})[0]


def _receipts_for(d: Path, plain: str) -> list[str]:
    """A public and a private receipt owned by `plain`'s account id."""
    account = _eid(plain)
    rids = []
    for i, private in enumerate((False, True)):
        rid = f"Twin{account[:8]}{i}xx"
        rd = write_fixture_receipt(d / "receipts", rid)
        rec = json.loads((rd / "receipt.json").read_text())
        rec.update({"source": "sub:" + account, "account_id": account,
                    "private": private, "owner_id": account if private else None})
        (rd / "receipt.json").write_text(json.dumps(rec, indent=2))
        rids.append(rid)
    return sorted(rids)


# ── the rule itself ───────────────────────────────────────────────────────

def test_needs_lowercase_is_exactly_the_characters_lower_changes():
    """1,407 code points outside ASCII change under str.lower() (Unicode
    14.0, Python 3.11); those and only those need lowercase. No ASCII
    character ever does. The count is the control: a rule that answered
    False for everything would find 0."""
    changed = [c for c in map(chr, range(0x80, sys.maxunicode + 1))
               if not 0xD800 <= ord(c) <= 0xDFFF and c != c.lower()]
    assert len(changed) == 1407
    assert all(needs_lowercase(f"a{c}b@example.test") for c in changed)
    flagged = [c for c in map(chr, range(0x80, sys.maxunicode + 1))
               if not 0xD800 <= ord(c) <= 0xDFFF and needs_lowercase(c)]
    assert flagged == changed
    assert not any(needs_lowercase(chr(i)) for i in range(0x80))
    for ascii_address in ("Alice@Example.TEST", "ALICE@EXAMPLE.TEST", " alice@example.test "):
        assert not needs_lowercase(ascii_address)
    assert needs_lowercase(KELVIN + "arl@example.test")
    assert not needs_lowercase("ünal@example.test") and needs_lowercase("Ünal@example.test")
    assert not any(needs_lowercase(v) for v in (None, 7, b"K@x", ["K@x"]))


def test_the_hint_is_plain_text_people_can_read():
    assert "lowercase" in LOWERCASE_HINT and "A to Z" in LOWERCASE_HINT
    assert "—" not in LOWERCASE_HINT and "–" not in LOWERCASE_HINT


# ── sign-in: the door every session comes through ────────────────────────

def test_a_twin_cannot_sign_in_and_so_cannot_list_karls_receipts(server):
    base, d = server
    plain, twin = _pair("arl")
    rids = _receipts_for(d, plain)
    before = _snap(d, TOKENS, SESSIONS)
    n0 = len(_mails(d))
    _assert_refused(*_post(base, "/api/auth/email-link", {"email": twin}))
    _assert_refused(*_post(base, "/api/auth/email-link", {"email": "  " + twin + " "}))
    assert _snap(d, TOKENS, SESSIONS) == before, "a refused sign-in wrote a token or a session"
    assert _mails(d)[n0:] == [], "a refused sign-in sent mail"

    # Control: typed in lowercase it is karl's address, karl's account id,
    # and karl's receipts, private one included.
    karl = _sign_in(base, d, plain)
    assert _vault(base, karl) == rids
    assert _snap(d, TOKENS)[TOKENS] != before[TOKENS], "control: a sign-in writes a token"

    # Shapes that were never addresses keep their neutral answer.
    for junk in ("", KELVIN, KELVIN + "@", "no-at-sign-" + KELVIN, 7):
        status, body = _post(base, "/api/auth/email-link", {"email": junk})
        assert (status, body.get("ok")) == (200, True), (junk, status, body)


def test_an_ascii_address_in_any_case_signs_in_as_before(server):
    base, d = server
    plain = _fresh("kate")
    rids = _receipts_for(d, plain)
    mixed = "K" + plain[1:].replace("example", "Example")
    # The link goes to the spelling typed, as it always has, and the session
    # is the same account as the lowercase one.
    assert _vault(base, _sign_in(base, d, mixed)) == rids
    assert _vault(base, _sign_in(base, d, mixed.upper())) == rids


def test_a_link_issued_to_a_twin_before_the_fix_signs_no_one_in(server):
    """Links live 24 hours, so one issued to a twin spelling before this
    change could still be redeemed after it. It is refused like a used link,
    by HEAD and GET alike, and nothing is spent or minted."""
    base, d = server
    plain, twin = _pair("arlold")
    rids = _receipts_for(d, plain)
    now = time.time()

    def issued(addr: str) -> str:
        token = secrets.token_urlsafe(24)
        with (d / TOKENS).open("a") as f:
            f.write(json.dumps({"ts": "2026-10-03T00:00:00+00:00", "event": "issued",
                                "token_hash": hashlib.sha256(token.encode()).hexdigest(),
                                "email": addr, "expires_unix": now + 3600}) + "\n")
        return token

    old_twin, old_plain = issued(twin), issued(plain)
    sessions = _snap(d, SESSIONS)
    assert _redeem(base, old_twin, "HEAD")[0] == 404
    assert _redeem(base, old_twin) == (404, None)
    assert _snap(d, SESSIONS) == sessions, "a twin's link minted a session"
    # Control: the same kind of row for karl's own spelling signs karl in.
    assert _redeem(base, old_plain, "HEAD")[0] == 303
    status, cookie = _redeem(base, old_plain)
    assert status == 303 and _vault(base, cookie) == rids


def test_logout_all_ends_every_ascii_casing_of_the_account(server):
    base, d = server
    lower = _fresh("alice")
    upper = "A" + lower[1:]
    other = _fresh("bob")
    c_upper, c_lower, c_other = (_sign_in(base, d, a) for a in (upper, lower, other))
    assert [_me(base, c) for c in (c_upper, c_lower, c_other)] == [200, 200, 200]
    status, body = _post(base, "/api/me/logout-all", {}, {"Cookie": c_upper})
    assert (status, body.get("sessions_revoked")) == (200, 2), (status, body)
    assert [_me(base, c) for c in (c_upper, c_lower)] == [401, 401]
    assert _me(base, c_other) == 200, "logout-all ended another account's session"


def test_a_new_link_supersedes_every_ascii_casing_of_the_account(server):
    base, d = server
    lower = _fresh("alina")
    other = _fresh("carol")
    first = _request_link(base, d, lower)
    unrelated = _request_link(base, d, other)
    second = _request_link(base, d, "A" + lower[1:].upper())
    assert _redeem(base, first)[0] == 404, "the older link for the same account still works"
    assert _redeem(base, second)[0] == 303
    assert _redeem(base, unrelated)[0] == 303, "another account's link was superseded"


# ── the waitlist ─────────────────────────────────────────────────────────

def _settle_waitlist(base: str, d: Path) -> str:
    """Confirmation emails go out one at a time, in the order signups were
    answered: once a later signup's email is out, every earlier one has been
    decided, so "no email" is a fact, not a hope."""
    marker = _fresh("settle")
    n0 = len(_mails(d))
    assert _post(base, "/api/waitlist", {"email": marker})[0] == 200
    _wait_mail(d, marker, n0)
    return marker


def test_waitlist_refuses_a_twin_and_takes_karl(server):
    base, d = server
    plain, twin = _pair("arlwait")
    before = _snap(d, WAITLIST)
    n0 = len(_mails(d))
    _assert_refused(*_post(base, "/api/waitlist", {"email": twin, "interest": "personal"}))
    assert _snap(d, WAITLIST) == before, "a refused signup was written"
    marker = _settle_waitlist(base, d)
    assert _to(_mails(d)[n0:]) == [[marker]], "a refused signup was sent a confirmation"

    for addr in (plain, "M" + _fresh("mixed")[1:].upper()):
        n1 = len(_mails(d))
        status, body = _post(base, "/api/waitlist", {"email": addr, "interest": "personal"})
        assert (status, body) == (200, {"ok": True, "message": "On the list."}), (status, body)
        assert any(r.get("email") == addr for r in _rows(d / WAITLIST)), "control was not added"
        _wait_mail(d, addr, n1)

    # Shapes the waitlist never took keep their neutral answer.
    for junk in ("", KELVIN, "Name <" + twin + ">"):
        assert _post(base, "/api/waitlist", {"email": junk}) == (200, {"ok": True}), junk


# ── pack recovery ────────────────────────────────────────────────────────

def _mint(d: Path, code: str, addr: str, n: int) -> None:
    with (d / CREDITS).open("a") as f:
        f.write(json.dumps({"ts": "2026-10-03T00:00:00+00:00", "claim_code": code,
                            "email": addr, "credits_delta": n,
                            "source": "stripe:cs_test_" + secrets.token_hex(6)}) + "\n")


def _recover(base: str, addr: str):
    return _post(base, "/api/pack/recover", {"email": addr})


def _code_mails(d: Path, code: str, since: int) -> list[list[str]]:
    return [m["to"] for m in _mails(d)[since:] if code in m["text"]]


def test_pack_recover_refuses_a_twin_and_sends_nothing(server):
    base, d = server
    plain, twin = _pair("arlpack")
    code = "pk_twinvictim" + secrets.token_hex(4)
    _mint(d, code, plain, 5)
    n0 = len(_mails(d))
    _assert_refused(*_recover(base, twin))
    # Settle: a later recovery's mail is out, and the refused one sent none.
    marker = _fresh("settlepack")
    _mint(d, "pk_settle" + secrets.token_hex(6), marker, 2)
    assert _recover(base, marker)[0] == 200
    _wait_mail(d, marker, n0)
    time.sleep(0.3)
    assert [t for t in _to(_mails(d)[n0:]) if t != [marker]] == [], "a refused recovery sent mail"
    assert _code_mails(d, code, 0) == []


def test_pack_recover_mails_each_code_to_the_address_on_its_own_row(server):
    """The lookup lowercases both sides, so "KARL…" finds karl's code, and so
    would anything else that lowercases onto karl's address. The code goes to
    the address it was bought with, never to the spelling typed."""
    base, d = server
    plain, twin = _pair("arlrows")
    karls, twins = "pk_karlrow" + secrets.token_hex(4), "pk_twinrow" + secrets.token_hex(4)
    _mint(d, karls, plain, 5)
    # A pack bought with the twin spelling (a payment page that took it).
    _mint(d, twins, twin, 3)
    for typed in (plain.upper(), plain):
        n0 = len(_mails(d))
        assert _recover(base, typed) == (200, {"ok": True, "message": (
            "If a pack is associated with that email, we've sent the code(s).")})
        _wait_mail(d, plain, n0)
        _wait_mail(d, twin, n0)
        assert _code_mails(d, karls, n0) == [[plain]], (typed, _code_mails(d, karls, n0))
        assert _code_mails(d, twins, n0) == [[twin]], (typed, _code_mails(d, twins, n0))


# ── payment recovery ─────────────────────────────────────────────────────

def test_payment_recover_refuses_a_twin_before_asking_stripe(server):
    base, d = server
    plain, twin = _pair("arlpay")
    sid = "cs_test_twin" + secrets.token_hex(8)
    code = "pk_payrecover" + secrets.token_hex(4)
    with (d / CREDITS).open("a") as f:
        f.write(json.dumps({"ts": "2026-10-03T00:00:00+00:00", "claim_code": code,
                            "email": plain, "credits_delta": 10, "source": "stripe:" + sid}) + "\n")
    answers = _rows_json(d / STRIPE_ANSWERS)
    answers["/checkout/sessions/" + sid] = {"data": {
        "id": sid, "payment_status": "paid", "mode": "payment",
        "customer_details": {"email": plain}}}
    (d / STRIPE_ANSWERS).write_text(json.dumps(answers))

    before = _snap(d, STRIPE_CALLS, GAPS, CREDITS)
    n0 = len(_mails(d))
    for session in (sid, "np_writer_pack_" + secrets.token_hex(5)):
        _assert_refused(*_post(base, "/api/recover", {"stripe_session_id": session, "email": twin}))
    assert _snap(d, STRIPE_CALLS, GAPS, CREDITS) == before, "a refused recovery asked Stripe or wrote"
    assert _mails(d)[n0:] == []

    for typed in (plain, "K" + plain[1:].upper()):
        calls = len(_rows(d / STRIPE_CALLS))
        n1 = len(_mails(d))
        status, body = _post(base, "/api/recover", {"stripe_session_id": sid, "email": typed})
        assert (status, body.get("mode")) == (200, "payment"), (typed, status, body)
        assert len(_rows(d / STRIPE_CALLS)) == calls + 1
        assert _code_mails(d, code, n1) == [[plain]], (typed, _code_mails(d, code, n1))


def _rows_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {}


# ── checkout, card and crypto ────────────────────────────────────────────

def test_card_checkout_refuses_a_twin_before_any_stripe_call(server):
    base, d = server
    plain, twin = _pair("arlcard")
    before = _snap(d, STRIPE_CALLS)
    _assert_refused(*_post(base, "/api/stripe/checkout", {"plan": "pack", "email": twin}))
    assert _snap(d, STRIPE_CALLS) == before, "a refused checkout reached Stripe"
    for typed in (plain, "K" + plain[1:].upper()):
        calls = len(_rows(d / STRIPE_CALLS))
        status, _body = _post(base, "/api/stripe/checkout", {"plan": "pack", "email": typed})
        assert status == 200, (typed, status)
        sent = _rows(d / STRIPE_CALLS)[calls:]
        assert [(c["path"], c["form"].get("customer_email")) for c in sent] == [
            ("/checkout/sessions", typed)], sent
    # No address at all still opens a checkout, as before.
    assert _post(base, "/api/stripe/checkout", {"plan": "pack"})[0] == 200


def test_crypto_checkout_refuses_a_twin_before_any_invoice(server):
    base, d = server
    plain, twin = _pair("arlcoin")
    before = _snap(d, EGRESS)
    _assert_refused(*_post(base, "/api/nowpayments/create",
                           {"currency": "btc", "plan": "writer_pack", "email": twin}))
    assert _snap(d, EGRESS) == before, "a refused crypto checkout tried to create an invoice"
    # Control: past the gate the invoice is asked for, and the egress guard
    # refuses the connection, so the provider is reported unavailable.
    for typed in (plain, "K" + plain[1:].upper()):
        tried = len(_rows(d / EGRESS))
        status, body = _post(base, "/api/nowpayments/create",
                             {"currency": "btc", "plan": "writer_pack", "email": typed})
        assert (status, body.get("error")) == (503, "Crypto payment provider unavailable."), body
        assert [r["host"] for r in _rows(d / EGRESS)[tried:]][:1] == ["api.nowpayments.io"]


# ── notify_email on a paid anchor ────────────────────────────────────────

def _pack(d: Path) -> str:
    code = "pk_notify" + secrets.token_hex(8)
    _mint(d, code, _fresh("buyer"), 50)
    return code


def _receipt_on_disk(d: Path, rid: str) -> dict:
    return json.loads((d / "receipts" / rid / "receipt.json").read_text())


def _folder(hash_hex: str) -> dict:
    import merkle
    leaf = merkle._leaf_hash("a.txt", bytes.fromhex(hash_hex)).hex()
    return {"algorithm": merkle.ALGORITHM, "version": 1, "root_hex": leaf,
            "leaves": [{"path": "a.txt", "file_sha256_hex": hash_hex,
                        "leaf_hex": leaf, "size_bytes": 1}]}


@pytest.mark.parametrize("route", ["/api/anchor", "/api/anchor_folder"])
def test_a_twin_notify_email_is_ignored_and_the_anchor_still_goes_through(server, route):
    base, d = server
    plain, twin = _pair("arlnote")
    pack = {"X-Pack-Token": _pack(d)}

    def anchor(notify: str, headers: dict):
        h = secrets.token_hex(32)
        body = {"hash_hex": h} if route == "/api/anchor" else _folder(h)
        body["notify_email"] = notify
        n0 = len(_mails(d))
        status, rec = _post(base, route, body, headers)
        assert status == 200 and rec.get("receipt_id"), (status, rec)
        return rec, _receipt_on_disk(d, rec["receipt_id"]), _mails(d)[n0:]

    rec, disk, mails = anchor(twin, pack)
    assert rec.get("notify_email_ignored") == "email_needs_lowercase", rec
    assert "notify_email" not in disk and mails == [], "a twin notify address was kept or mailed"
    for typed in (plain, "K" + plain[1:].upper()):
        rec, disk, mails = anchor(typed, pack)
        assert "notify_email_ignored" not in rec, rec
        assert disk.get("notify_email") == typed and _to(mails) == [[typed]], (typed, _to(mails))
    if route == "/api/anchor":
        # Unpaid, a notify address was never used; nothing new is said.
        rec, disk, mails = anchor(twin, {})
        assert "notify_email_ignored" not in rec and mails == [], rec


# ── bounces and complaints ───────────────────────────────────────────────

@pytest.fixture()
def suppression(server, monkeypatch, tmp_path):
    """record_suppression writes the server's own ledger, as its Resend
    webhook would (do_POST has no route to it yet); the server reads it on
    every send."""
    base, d = server
    monkeypatch.setattr(resend_webhook, "SUPPRESSION_LIST_PATH", d / SUPPRESSED)
    monkeypatch.setattr(resend_webhook, "PROCESSED_EVENTS_PATH", tmp_path / "processed.jsonl")
    return base, d


def _bounce(addr: str) -> None:
    event = {"type": "email.bounced", "data": {"email_id": secrets.token_hex(8), "to": [addr]}}
    assert resend_webhook.handle_event(json.dumps(event).encode()).get("ok") is True


def _link_mailed(base: str, d: Path, addr: str) -> bool:
    n0 = len(_mails(d))
    status, _body = _post(base, "/api/auth/email-link", {"email": addr})
    assert status == 200
    return _to(_mails(d)[n0:]) == [[addr]]


def test_a_twin_bounce_does_not_stop_mail_to_karl(suppression):
    base, d = suppression
    plain, twin = _pair("arlbounce")
    _bounce(twin)
    assert any(r.get("email") == fold_email(twin) for r in _rows(d / SUPPRESSED)), (
        "the bounce was not recorded as the twin spelling")
    assert resend_webhook.is_suppressed(twin) is True
    assert resend_webhook.is_suppressed(plain) is False
    assert _link_mailed(base, d, plain), "one bounce of a lookalike stopped karl's mail"


def test_a_bounce_of_one_ascii_casing_suppresses_every_casing(suppression):
    base, d = suppression
    lower = _fresh("alice")
    _bounce("A" + lower[1:].upper().replace("EXAMPLE.TEST", "Example.Test"))
    assert resend_webhook.is_suppressed(lower) is True
    assert not _link_mailed(base, d, lower), "the bounced mailbox was mailed"
    control = _fresh("dave")
    assert _link_mailed(base, d, control), "control: an address with no bounce is mailed"


def test_rows_written_lowered_before_the_fix_keep_suppressing(suppression):
    """Before 2026-10-03 every row was stored lowered, a twin's bounce
    included. Those rows stay: what they suppress, they keep suppressing."""
    base, d = suppression
    plain, twin = _pair("arllegacy")
    with (d / SUPPRESSED).open("a") as f:
        f.write(json.dumps({"ts": "2026-09-01T00:00:00+00:00", "email": plain,
                            "reason": "email.bounced"}) + "\n")
    for addr in (plain, plain.upper(), twin):
        assert resend_webhook.is_suppressed(addr) is True, addr
    assert not _link_mailed(base, d, "K" + plain[1:]), "a legacy row stopped suppressing"


# ── a gift recipient from the Stripe webhook ─────────────────────────────

def _webhook(base: str, event: dict):
    payload = json.dumps(event).encode()
    ts = int(time.time())
    sig = hmac.new(WHSEC.encode(), f"{ts}.".encode() + payload, hashlib.sha256).hexdigest()
    status, raw, _h = _srv.request(base, "/api/stripe/webhook", "POST", payload,
                                   {**JSON, "Stripe-Signature": f"t={ts},v1={sig}"}, timeout=30)
    return status, json.loads(raw)


def _gift_event(buyer: str, gift_to: str) -> dict:
    return {"id": "evt_test_" + secrets.token_hex(8), "type": "checkout.session.completed",
            "data": {"object": {"id": "cs_test_" + secrets.token_hex(10), "mode": "payment",
                                "payment_status": "paid", "customer_email": buyer,
                                "customer": "cus_test_" + secrets.token_hex(4),
                                "metadata": {"gift_to_email": gift_to, "gift_message": ""}}}}


def test_a_twin_gift_recipient_is_not_mailed_and_the_buyer_gets_the_pack(server):
    """No page of ours sets gift_to_email (web/gift.js, which built it, is
    loaded by no page), so such an address is metadata written by hand. The
    pack goes to the buyer, as for a malformed recipient, and the log names
    no part of it."""
    base, d = server
    buyer = _fresh("giver")
    plain, twin = _pair("imgift")
    n0 = len(_mails(d))
    status, result = _webhook(base, _gift_event(buyer, twin))
    assert (status, result.get("claim_code_minted"), result.get("gift")) == (200, True, False), result
    assert _to(_mails(d)[n0:]) == [[buyer]], _to(_mails(d)[n0:])
    rows = [r for r in _rows(d / CREDITS) if r.get("source", "").endswith(result["session_id"])]
    assert [r["email"] for r in rows] == [buyer], rows
    log = _srv._LOG_BY_BASE[base].read_text(encoding="utf-8", errors="replace")
    assert "gift_to_email that needs lowercase" in log
    for fragment in (twin, twin.split("@")[0], auth.mask_email(twin), plain.split("@")[0][1:]):
        assert fragment not in log, "the log carries the refused recipient"

    # Control: an ordinary recipient is gifted, and only the recipient mailed.
    n1 = len(_mails(d))
    status, result = _webhook(base, _gift_event(buyer, plain))
    assert (status, result.get("gift")) == (200, True), result
    assert _to(_mails(d)[n1:]) == [[plain]]


# ── rows written before the fix (attack pass on PR #286) ─────────────────

def _append_row(path: Path, row: dict) -> None:
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row) + "\n")


def _legacy_session(d: Path, addr: str) -> str:
    """A session row exactly as sign-in wrote one before the fix."""
    sid = "sess-" + secrets.token_hex(12)
    _append_row(d / "auth_sessions.jsonl", {
        "event": "created", "session_hash": hashlib.sha256(sid.encode()).hexdigest(),
        "email": addr, "expires_unix": time.time() + 3600})
    return "orpho_sid=" + sid


def test_a_twin_session_made_before_the_fix_ends_now(server):
    # It used to keep working until its 30-day expiry, and for everything keyed
    # by email_id it was karl's account: his receipts, private ones included.
    base, d = server
    plain, twin = _pair("sess")
    karls = _receipts_for(d, plain)
    twin_cookie, plain_cookie = _legacy_session(d, twin), _legacy_session(d, plain)
    assert _me(base, twin_cookie) == 401
    assert _vault(base, twin_cookie) == 401
    assert _me(base, plain_cookie) == 200                       # control
    assert _vault(base, plain_cookie) == karls


def test_an_api_key_issued_to_a_twin_before_the_fix_is_dead(server):
    base, d = server
    plain, twin = _pair("key")
    _receipts_for(d, plain)
    keys = {}
    for addr in (plain, twin):
        key = "orpho_" + secrets.token_urlsafe(24)
        _append_row(d / "api_keys.jsonl", {
            "ts": "2026-09-25T00:00:00+00:00", "event": "issued",
            "key_hash": hashlib.sha256(key.encode()).hexdigest(), "key_prefix": key[:14], "email": addr})
        keys[addr] = key
    # The vault answers a key only for an active subscriber.
    _append_row(d / "subscriptions.jsonl", {"email": plain, "status": "active", "stripe_sub": "sub_" + secrets.token_hex(6)})
    status = lambda k: _srv.request(base, "/api/me/anchors", headers={"X-Orpho-Api-Key": k})[0]
    assert status(keys[twin]) == 401
    assert status(keys[plain]) == 200                           # control


def test_a_webhook_registered_by_a_twin_before_the_fix_gets_nothing(tmp_path, monkeypatch):
    # Webhook rows are keyed by email.lower(), so the twin's registration
    # matched karl's anchor events, and registrations never expire.
    import webhooks
    ledger = tmp_path / "webhooks.jsonl"
    monkeypatch.setattr(webhooks, "WEBHOOKS_LEDGER", ledger)
    plain, twin = _pair("hook")
    for addr, url in ((twin, "https://twin.example.test/hook"), (plain, "https://karl.example.test/hook")):
        _append_row(ledger, {"ts": "2026-09-25T00:00:00+00:00", "event": "registered",
                             "email": addr, "url": url, "secret": "orpho_whsec_" + secrets.token_hex(8)})
    assert [h["url"] for h in webhooks.list_for_email(plain)] == ["https://karl.example.test/hook"]
    assert [h["url"] for h in webhooks.list_for_email_with_secrets(plain)] == ["https://karl.example.test/hook"]
    assert webhooks.list_for_email(twin) == [] and webhooks.list_for_email_with_secrets(twin) == []
    assert webhooks.delete(twin, "https://karl.example.test/hook") is False   # cannot delete karl's


def test_a_sign_in_with_a_lone_surrogate_writes_nothing(server):
    # It matched EMAIL_RE, was stored in the token ledger, and then raised in
    # email_id (found by the review of PR #285).
    base, d = server
    before = _snap(d, "auth_tokens.jsonl")
    n0 = len(_mails(d))
    status, body = _post(base, "/api/auth/email-link", {"email": "a\ud800@example.test"})
    assert status == 200 and body.get("ok") is True, (status, body)   # the neutral answer
    assert _snap(d, "auth_tokens.jsonl") == before and len(_mails(d)) == n0
