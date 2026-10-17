"""Recovering a gifted Pack sends its code to the recipient only.

Found in the review of PR #290: /api/recover looked up the code minted for a
paid card session and mailed it to the BUYER, also when the session was a
gift. A gift's code is the recipient's bearer credential (its ledger row
names the recipient), so the buyer could take back a gift they had given.
Founder decision 2026-10-17: the code goes to the recipient's stored address
only; the buyer's mail says a gift was sent to a masked recipient address and
carries no code. A gift row with no recipient address sends the code to
nobody. The buyer's own pack recovery is unchanged, and the answer to the
request is the same, and as fast, whether the session was a gift or not.

Everything goes through the real handler of a server process on 127.0.0.1:
Stripe stubbed, every email recorded instead of sent, and every connection
or name lookup for another host refused and recorded (tests/_run_server.py).
"""
from __future__ import annotations

import json
import secrets
import sys
import time
from pathlib import Path

import pytest

import _srv

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "server"))

import auth  # noqa: E402

JSON = {"Content-Type": "application/json"}
WAIT_SEC = 30
CREDITS = "credit_ledger.jsonl"
STRIPE_ANSWERS = "stub_stripe_answers.json"
MAIL = "stub_mail_sent.jsonl"
MAIL_DOWN = "stub_mail_down"
EGRESS = "stub_egress_blocked.jsonl"


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    d = tmp_path_factory.mktemp("gift_recover")
    for base in _srv.server_processes(
            d, stub_calendars=True, stub_stripe=True, capture_mail=True, egress_guard=True,
            ORPHO_HMAC_SECRET="gift-recover-hmac-secret",
            STRIPE_SECRET_KEY="sk_test_not_a_real_key"):
        yield base, d
    # Nothing in this module may have tried to reach another host.
    assert not _rows(d / EGRESS), _rows(d / EGRESS)


def _rows(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _mails(d: Path) -> list[dict]:
    return [r["payload"] for r in _rows(d / MAIL)]


def _fresh(stem: str) -> str:
    return f"{stem}.{secrets.token_hex(3)}@example.test"


def _paid_session(d: Path, buyer: str, gift_message: str = "") -> str:
    sid = "cs_test_giftrecover" + secrets.token_hex(6)
    path = d / STRIPE_ANSWERS
    answers = json.loads(path.read_text()) if path.exists() else {}
    meta = {"gift_message": gift_message} if gift_message else {}
    answers["/checkout/sessions/" + sid] = {"data": {
        "id": sid, "payment_status": "paid", "mode": "payment",
        "customer_details": {"email": buyer}, "metadata": meta}}
    path.write_text(json.dumps(answers))
    return sid


def _mint(d: Path, code: str, holder: str, source: str) -> None:
    with (d / CREDITS).open("a") as f:
        f.write(json.dumps({"ts": "2026-10-17T00:00:00+00:00", "claim_code": code,
                            "email": holder, "credits_delta": 10, "source": source}) + "\n")


def _recover(base: str, sid: str, email: str):
    return _srv.post_json(base, "/api/recover", {"stripe_session_id": sid, "email": email},
                          JSON, timeout=30)


def _wait_mail_to(d: Path, addr: str, since: int) -> list[dict]:
    deadline = time.time() + WAIT_SEC
    while time.time() < deadline:
        got = [m for m in _mails(d)[since:] if m["to"] == [addr]]
        if got:
            return got
        time.sleep(0.05)
    pytest.fail(f"no mail to {auth.mask_email(addr)} within {WAIT_SEC}s")


def _settle(base: str, d: Path) -> None:
    """Run one own-pack recovery to the end, so every mail started before it
    has had the same time to go out."""
    buyer = _fresh("settle")
    sid = _paid_session(d, buyer)
    _mint(d, "pk_settle" + secrets.token_hex(6), buyer, "stripe:" + sid)
    n = len(_mails(d))
    assert _recover(base, sid, buyer)[0] == 200
    _wait_mail_to(d, buyer, n)


def _with_code(mails: list[dict], code: str) -> list[list[str]]:
    return [m["to"] for m in mails if code in m["text"] or code in m.get("html", "")]


def test_a_gift_code_is_recovered_to_the_recipient_and_never_to_the_buyer(server):
    base, d = server
    buyer, recipient = _fresh("giftbuyer"), _fresh("giftrecipient")
    sid = _paid_session(d, buyer, gift_message="happy birthday")
    code = "pk_giftcode" + secrets.token_hex(6)
    _mint(d, code, recipient, "stripe-gift:" + sid)
    n0 = len(_mails(d))
    status, body = _recover(base, sid, buyer)
    assert (status, body.get("mode")) == (200, "payment"), (status, body)
    to_recipient = _wait_mail_to(d, recipient, n0)
    notice = _wait_mail_to(d, buyer, n0)
    _settle(base, d)
    sent = _mails(d)[n0:]
    # The code went to the recipient, and to nobody else.
    assert _with_code(sent, code) == [[recipient]], _with_code(sent, code)
    assert "happy birthday" in to_recipient[0]["text"]
    # The buyer was told, with the recipient masked, and got no code.
    assert len(notice) == 1, notice
    text = notice[0]["text"] + notice[0].get("html", "")
    assert code not in text
    assert auth.mask_email(recipient) in text, text
    assert recipient not in text


def test_a_gift_without_a_recipient_address_sends_the_code_to_nobody(server):
    base, d = server
    buyer = _fresh("orphanbuyer")
    sid = _paid_session(d, buyer)
    code = "pk_orphangift" + secrets.token_hex(6)
    _mint(d, code, "", "stripe-gift:" + sid)
    n0 = len(_mails(d))
    status, body = _recover(base, sid, buyer)
    assert (status, body.get("mode")) == (200, "payment"), (status, body)
    notice = _wait_mail_to(d, buyer, n0)
    _settle(base, d)
    sent = _mails(d)[n0:]
    assert _with_code(sent, code) == [], _with_code(sent, code)
    assert len(notice) == 1 and code not in notice[0]["text"] + notice[0].get("html", "")
    assert "hello@orphograph.com" in notice[0]["text"]
    # Logged, without the code.
    deadline = time.time() + WAIT_SEC
    while True:   # the log line is written just after the notice is recorded
        logs = "".join(p.read_text(errors="replace") for p in d.glob("server-*.log"))
        if "recipient=none" in logs or time.time() > deadline:
            break
        time.sleep(0.05)
    assert "[recover] gift" in logs and "recipient=none" in logs, logs[-2000:]
    assert code not in logs


def test_control_own_pack_recovery_still_mails_the_buyer_the_code(server):
    base, d = server
    buyer = _fresh("ownbuyer")
    sid = _paid_session(d, buyer)
    code = "pk_owncode" + secrets.token_hex(6)
    _mint(d, code, buyer, "stripe:" + sid)
    n0 = len(_mails(d))
    status, body = _recover(base, sid, buyer)
    assert (status, body.get("mode")) == (200, "payment"), (status, body)
    _wait_mail_to(d, buyer, n0)
    _settle(base, d)
    sent = [m for m in _mails(d)[n0:] if m["to"] == [buyer]]
    assert _with_code(sent, code) == [[buyer]] and len(sent) == 1, sent


def test_the_answer_is_the_same_and_does_not_wait_for_mail_gift_or_not(server):
    base, d = server
    answers, took = [], []
    down = d / MAIL_DOWN
    down.touch()   # every send now retries for seconds before giving up
    try:
        for gift in (True, False, True, False):
            buyer = _fresh("timingbuyer")
            sid = _paid_session(d, buyer)
            code = "pk_timing" + secrets.token_hex(6)
            if gift:
                _mint(d, code, _fresh("timingrecipient"), "stripe-gift:" + sid)
            else:
                _mint(d, code, buyer, "stripe:" + sid)
            t0 = time.monotonic()
            answers.append(_recover(base, sid, buyer))
            took.append(time.monotonic() - t0)
    finally:
        down.unlink()
    assert all(a == answers[0] for a in answers), answers
    assert answers[0][0] == 200
    # A send that is down takes at least 3 s (1 s + 2 s of retry backoff).
    assert max(took) < 2.0, took


def test_the_pages_say_a_gift_is_recovered_to_the_recipient_only():
    web = ROOT / "web"
    recover = (web / "recover.html").read_text()
    assert "What about a Pack bought as a gift?" in recover
    assert "never to the buyer" in recover and "without the code" in recover
    gift = (web / "gift.html").read_text()
    assert "recovered to the recipient only" in gift and "notice without it" in gift
