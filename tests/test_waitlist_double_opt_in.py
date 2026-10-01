"""The waitlist asks before anyone is added to the mailing list (double opt-in).

Founder decision 2026-09-28: wire it. Until then POST /api/waitlist only
appended a ledger row, nothing called newsletter.send_confirmation_email, and
/api/waitlist/confirm answered 404. Now:

  * a signup gets one confirmation email: at most one per address per 24
    hours, none for an address on the suppression list or already confirmed,
    and the HTTP answer is the same in every case, so it tells no one whether
    an address is on the list;
  * the link in the email opens a page with one button and writes nothing,
    because mail scanners fetch links with GET;
  * the button POSTs, and only that writes the confirmed row; pressing it
    twice writes one.

Drives real servers. --capture-mail (tests/_run_server.py) records what the
mailer would hand to Resend, so these tests follow the link out of the email a
person would actually get.
"""
from __future__ import annotations

import ast
import base64
import hashlib
import html
import json
import re
import sys
import time
import uuid
from pathlib import Path
from urllib.parse import quote, urlsplit

import pytest

import _srv

REPO_ROOT = Path(__file__).resolve().parent.parent
CONFIRM = "/api/waitlist/confirm"
JSON = {"Content-Type": "application/json"}
# What a browser sends when the page's button is pressed: the form has no
# fields, so the body is empty and the token stays in the action's query.
FORM = {"Content-Type": "application/x-www-form-urlencoded", "Accept": "text/html"}
MARKUP = "<img src=x onerror=alert(1)>"
WAIT_SEC = 30


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    d = tmp_path_factory.mktemp("waitlist_confirm")
    for base in _srv.server_processes(d, stub_calendars=True, capture_mail=True):
        yield base, d


@pytest.fixture(scope="module")
def short_server(tmp_path_factory):
    """Links expire after one second, and the ledger already holds rows from
    before double opt-in existed."""
    d = tmp_path_factory.mktemp("waitlist_confirm_short")
    (d / "waitlist.jsonl").write_text(
        '{"ts":"2026-09-01T00:00:00+00:00","email":"legacy-one@example.test","interest":"personal"}\n'
        '{"ts":"2026-09-02T00:00:00+00:00","email":"legacy-two@example.test","interest":"capture"}\n')
    for base in _srv.server_processes(d, stub_calendars=True, capture_mail=True,
                                      ORPHO_NEWSLETTER_CONFIRM_TTL_SEC="1"):
        yield base, d


def _signup(base: str, email: str, interest: str = "personal"):
    body = json.dumps({"email": email, "interest": interest}).encode()
    return _srv.request(base, "/api/waitlist", method="POST", body=body,
                        headers=JSON, timeout=15)


def _mails(data_dir: Path, to: str) -> list[dict]:
    """Every send the mailer attempted to `to` (ASCII case folded)."""
    p = data_dir / "stub_mail_sent.jsonl"
    if not p.exists():
        return []
    rows = [json.loads(line) for line in p.read_text().splitlines() if line.strip()]
    return [r for r in rows if [a.lower() for a in r["payload"]["to"]] == [to.lower()]]


def _wait_for_mail(data_dir: Path, to: str, count: int = 1) -> list[dict]:
    deadline = time.time() + WAIT_SEC
    while time.time() < deadline:
        got = _mails(data_dir, to)
        if len(got) >= count:
            return got
        time.sleep(0.05)
    pytest.fail(f"no confirmation email reached the mailer within {WAIT_SEC}s")


def _settle(base: str, data_dir: Path) -> None:
    """Confirmation emails are decided one at a time, in the order the signups
    were answered. Once a fresh address's email is out, every signup answered
    before it has been decided, so "no email" can be asserted, not hoped."""
    marker = f"settle-{uuid.uuid4().hex[:12]}@example.test"
    _signup(base, marker)
    _wait_for_mail(data_dir, marker)


def _link(mail: dict) -> str:
    m = re.search(r"https?://\S+/api/waitlist/confirm\?token=\S+", mail["payload"]["text"])
    assert m, "the email carries no confirm link"
    return m.group(0)


def _path(mail: dict) -> str:
    parts = urlsplit(_link(mail))
    return f"{parts.path}?{parts.query}"


def _token(mail: dict) -> str:
    return _path(mail).split("?token=", 1)[1]


def _press(base: str, path: str):
    return _srv.request(base, path, method="POST", body=b"", headers=FORM, timeout=15)


def _rows(data_dir: Path) -> list[dict]:
    p = data_dir / "waitlist.jsonl"
    if not p.exists():
        return []
    return [json.loads(line) for line in p.read_text().splitlines() if line.strip()]


def _snapshot(data_dir: Path) -> dict[str, str]:
    """Every file the server keeps except request accounting (the limiter's
    counters and the harness's own server log), as tests/test_head_is_a_safe_method.py
    does."""
    accounting = re.compile(r"^(rate_limit_state\.json|server-\d+\.log)$")
    return {str(p.relative_to(data_dir)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(data_dir.rglob("*"))
            if p.is_file() and not p.name.endswith(".lock") and not accounting.match(p.name)}


def _signed_up(server, email: str, interest: str = "personal") -> dict:
    base, data_dir = server
    status, _body, _headers = _signup(base, email, interest)
    assert status == 200
    return _wait_for_mail(data_dir, email)[0]


def test_a_signup_sends_one_confirmation(server):
    base, data_dir = server
    status, body, headers = _signup(base, "first@example.test")
    assert status == 200 and json.loads(body) == {"ok": True, "message": "On the list."}
    mail = _wait_for_mail(data_dir, "first@example.test")[0]["payload"]
    _settle(base, data_dir)
    assert len(_mails(data_dir, "first@example.test")) == 1
    assert {"name": "category", "value": "newsletter-confirm"} in mail["tags"]
    assert mail["subject"] == "Confirm your Orphograph waitlist spot"
    text = mail["text"]
    assert _path({"payload": mail}).startswith(CONFIRM + "?token=")
    # It says what happens next, in the person's terms.
    assert "button" in text and "24 hours" in text and "ignore this email" in text
    # It carries what every transactional email carries in its footer.
    assert "Privacy:" in text and "Terms:" in text
    # The copy above the shared footer is written without em dashes.
    assert "—" not in text.split("\n—\n", 1)[0] and "—" not in mail["subject"]
    # The token in the link does not spell out whose it is. (Only its body
    # is text; the second segment is a MAC, random bytes.)
    token = _token({"payload": mail})
    body = token.split(".")[0]
    decoded = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
    assert set(decoded) == {"exp", "n"} and "first" not in token
    # Today's ledger row is still written for the signup itself.
    signups = [r for r in _rows(data_dir)
               if r.get("email") == "first@example.test" and "event" not in r]
    assert len(signups) == 1


def test_a_second_signup_inside_a_day_sends_nothing_and_answers_the_same(server):
    base, data_dir = server
    first = _signup(base, "twice@example.test")
    _wait_for_mail(data_dir, "twice@example.test")
    again = _signup(base, "twice@example.test")
    other_case = _signup(base, "Twice@Example.TEST")
    _settle(base, data_dir)
    assert len(_mails(data_dir, "twice@example.test")) == 1, "the form can flood an inbox"
    assert first[0] == again[0] == other_case[0] == 200
    assert first[1] == again[1] == other_case[1]
    assert first[2].get("Content-Type") == again[2].get("Content-Type")
    # Each signup is still recorded; only the email is held back.
    signups = [r for r in _rows(data_dir)
               if r.get("email", "").lower() == "twice@example.test" and "event" not in r]
    assert len(signups) == 3


def test_following_the_link_confirms_no_one(server):
    base, data_dir = server
    mail = _signed_up(server, "scanned@example.test")
    path = _path(mail)
    _settle(base, data_dir)
    before = _snapshot(data_dir)
    status, body, headers = _srv.request(base, path, timeout=15)
    head_status, head_body, head_headers = _srv.request(base, path, method="HEAD", timeout=15)
    assert _snapshot(data_dir) == before, "a GET or HEAD on the confirm link wrote something"
    assert status == head_status == 200
    assert head_body == b""
    for name in ("Content-Type", "Content-Length", "Cache-Control", "Content-Security-Policy"):
        assert headers.get(name) == head_headers.get(name), name
    assert headers.get("Cache-Control") == "no-store"
    page = body.decode()
    assert '<form method="post"' in page, "the page must offer the POST button"
    assert f'action="{html.escape(path)}"' in page
    assert page.count("<button") == 1


def test_the_button_confirms_once_and_answers_the_same_twice(server):
    base, data_dir = server
    email = "pressed@example.test"
    path = _path(_signed_up(server, email))
    _settle(base, data_dir)
    page_before = _srv.request(base, path, timeout=15)[1]
    first = _press(base, path)
    ledger = (data_dir / "waitlist.jsonl").read_bytes()
    second = _press(base, path)
    assert (data_dir / "waitlist.jsonl").read_bytes() == ledger, "a second press wrote again"
    assert first[0] == second[0] == 200, first[1]
    assert first[1] == second[1]
    assert first[2].get("Content-Type", "").startswith("text/html")
    assert first[2].get("Cache-Control") == "no-store"
    confirmed = [r for r in _rows(data_dir)
                 if r.get("event") == "confirmed" and r.get("email") == email]
    assert len(confirmed) == 1
    # The link's page reads the same before and after: it never says who confirmed.
    assert _srv.request(base, path, timeout=15)[1] == page_before
    # A confirmed address is not sent another confirmation.
    _signup(base, email)
    _settle(base, data_dir)
    assert len(_mails(data_dir, email)) == 1


def _refused_everywhere(base: str, data_dir: Path, path: str) -> None:
    before = _snapshot(data_dir)
    get = _srv.request(base, path, timeout=15)
    head = _srv.request(base, path, method="HEAD", timeout=15)
    post = _press(base, path)
    assert _snapshot(data_dir) == before, f"a refused link wrote something: {path}"
    assert get[0] == head[0] == post[0] == 400
    assert head[1] == b"" and head[2].get("Content-Length") == get[2].get("Content-Length")
    for status, body, headers in (get, post):
        page = body.decode()
        assert headers.get("Content-Type", "").startswith("text/html")
        assert "<h1>" in page and "<form" not in page


def test_a_tampered_link_is_refused_and_writes_nothing(server):
    base, data_dir = server
    token = _token(_signed_up(server, "tampered@example.test"))
    _settle(base, data_dir)
    body, sig = token.split(".")
    flipped = body + "." + ("A" if sig[0] != "A" else "B") + sig[1:]
    # base64 decoding skips characters outside its alphabet, so a token with
    # two inserted would decode to the same bytes and still verify.
    padded = token[:4] + "!!" + token[4:]
    for bad in (flipped, padded, token + "AA", token.replace(".", "", 1), ""):
        _refused_everywhere(base, data_dir, CONFIRM + "?token=" + quote(bad))
    _refused_everywhere(base, data_dir, CONFIRM)
    assert not [r for r in _rows(data_dir) if r.get("event") == "confirmed"
                and r.get("email") == "tampered@example.test"]


def test_a_suppressed_address_gets_no_email(server):
    base, data_dir = server
    email = "opted-out@example.test"
    status, _b, _h = _srv.request(base, "/api/unsubscribe?e=" + quote(email), method="POST",
                                  body=b"via=page", headers=FORM, timeout=15)
    assert status == 200
    normal = _signup(base, "not-opted-out@example.test")
    answer = _signup(base, email)
    _settle(base, data_dir)
    assert _mails(data_dir, email) == []
    assert _mails(data_dir, "not-opted-out@example.test"), "control: the other address was mailed"
    assert answer[:2] == normal[:2]


def test_markup_in_the_token_or_the_interest_is_never_reflected(server):
    base, data_dir = server
    mail = _signed_up(server, "markup@example.test", interest=MARKUP)
    token = _token(mail)
    for raw in (MARKUP, "<script>alert(1)</script>", token + MARKUP):
        for method in ("GET", "POST"):
            if method == "GET":
                status, body, _h = _srv.request(base, CONFIRM + "?token=" + quote(raw), timeout=15)
            else:
                status, body, _h = _press(base, CONFIRM + "?token=" + quote(raw))
            assert status == 400
            assert b"onerror" not in body and b"alert(1)" not in body, (method, raw)
    for part in ("text", "html"):
        assert "onerror" not in mail["payload"][part]
    for status, body, _h in (_srv.request(base, _path(mail), timeout=15),
                             _press(base, _path(mail))):
        assert status == 200 and b"onerror" not in body


def test_a_mail_outage_does_not_change_the_answer(server):
    base, data_dir = server
    normal = _signup(base, "mail-up@example.test")
    _wait_for_mail(data_dir, "mail-up@example.test")
    down = data_dir / "stub_mail_down"
    down.touch()
    try:
        answer = _signup(base, "mail-down@example.test")
        attempts = _wait_for_mail(data_dir, "mail-down@example.test")
    finally:
        down.unlink()
    assert attempts[0]["delivered"] is False, "control: the outage was in force"
    assert answer[:2] == normal[:2]


def test_an_expired_link_is_refused_and_writes_nothing(short_server):
    base, data_dir = short_server
    mail = _signed_up(short_server, "expired@example.test")
    time.sleep(2.5)
    _refused_everywhere(base, data_dir, _path(mail))


def test_no_one_already_on_the_list_is_mailed(short_server):
    """Rule 4: no backfill. A server that boots on a ledger with rows from
    before double opt-in sends them nothing."""
    base, data_dir = short_server
    _settle(base, data_dir)
    assert _mails(data_dir, "legacy-one@example.test") == []
    assert _mails(data_dir, "legacy-two@example.test") == []


@pytest.fixture(scope="module")
def limiter_server(tmp_path_factory):
    d = tmp_path_factory.mktemp("waitlist_confirm_limit")
    for base in _srv.server_processes(d, stub_calendars=True, capture_mail=True):
        yield base, d


def test_the_confirm_post_is_rate_limited(limiter_server):
    base, _data_dir = limiter_server
    answers = [_press(base, CONFIRM + "?token=not-a-token")[0] for _ in range(25)]
    assert answers[0] == 400
    assert 429 in answers, "the confirm POST has no limit"
    refused = _press(base, CONFIRM + "?token=not-a-token")
    assert refused[0] == 429 and refused[2].get("Retry-After")
    # The page itself is not rationed: a GET writes and reads nothing.
    assert _srv.request(base, CONFIRM + "?token=not-a-token", timeout=15)[0] == 400


def test_the_confirm_route_is_in_the_route_lists(monkeypatch):
    import importlib.util

    def load(name):
        spec = importlib.util.spec_from_file_location(name, REPO_ROOT / "scripts" / f"{name}.py")
        mod = importlib.util.module_from_spec(spec)
        monkeypatch.setitem(sys.modules, name, mod)
        spec.loader.exec_module(mod)
        return mod

    monkeypatch.syspath_prepend(str(REPO_ROOT / "scripts"))
    es = load("enumerate_surface")
    probes_src = (REPO_ROOT / "scripts" / "all_endpoints_probe.py").read_text()
    tracked = {str(p.relative_to(REPO_ROOT)) for p in (REPO_ROOT / "web").rglob("*") if p.is_file()}
    report = es.routes_report((REPO_ROOT / "server" / "app.py").read_text(), probes_src, tracked)
    assert report["ok"], "the route enumeration failed its own oracles"
    routes = {(e["method"], e["literal"]) for e in report["elements"] if e["kind"] == "eq"}
    assert ("GET", CONFIRM) in routes and ("POST", CONFIRM) in routes
    assert ("GET", CONFIRM) in es._probe_entries(probes_src)
    # The confirm page is not a GET that writes, so the list of known GET
    # writers (each must sit behind the HEAD check) has no entry for it.
    src = (REPO_ROOT / "tests" / "test_head_is_a_safe_method.py").read_text()
    known = next(n.value for n in ast.walk(ast.parse(src)) if isinstance(n, ast.Assign)
                 and any(getattr(t, "id", "") == "_KNOWN_GET_WRITERS" for t in n.targets))
    entries = {tuple(e.value for e in elt.elts) for elt in known.elts}
    ours = {"newsletter", "waitlist", "request_confirmation", "mark_confirmed", "confirm",
            "add_confirmed_contact", "_queue_waitlist_confirmation"}
    assert entries and not [e for e in entries if ours & set(e)], entries


# --- bundle review round 1 -------------------------------------------------------

def _all_mails(data_dir: Path) -> list[dict]:
    p = data_dir / "stub_mail_sent.jsonl"
    return [json.loads(line) for line in p.read_text().splitlines() if line.strip()] if p.exists() else []


def _parsed_rows(data_dir: Path) -> list[dict]:
    """Rows that parse; a torn or glued line is skipped, as the readers do."""
    out = []
    p = data_dir / "waitlist.jsonl"
    for line in (p.read_text().splitlines() if p.exists() else []):
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    return out


def test_confirming_after_unsubscribing_adds_nothing(server):
    """Review round 1 (LOW, reproduced): the button wrote a confirmed row and
    told a person who had since unsubscribed that they were on the list."""
    base, data_dir = server
    email = "confirm-after-unsub@example.test"
    path = _path(_signed_up(server, email))
    _settle(base, data_dir)
    status, _b, _h = _srv.request(base, "/api/unsubscribe?e=" + quote(email), method="POST",
                                  body=b"via=page", headers=FORM, timeout=15)
    assert status == 200
    pressed = _press(base, path)
    assert pressed[0] == 200, pressed[1]
    page = pressed[1].decode()
    assert "unsubscribed" in page.lower() and "mailing list" not in page.lower(), page
    assert not [r for r in _rows(data_dir) if r.get("event") == "confirmed" and r.get("email") == email]


def test_a_display_name_or_quoted_spelling_gets_no_email_and_no_row(server):
    """Review round 1 (LOW, reproduced): "x<victim@...>", "<victim@...>" and
    '"victim"@...' passed EMAIL_RE, were not recognised as the suppressed
    victim, and each was sent a confirmation that the mail provider delivered
    to the victim. Only a bare address is taken; the answer is the one every
    invalid address gets."""
    base, data_dir = server
    victim = "dn-victim@example.test"
    status, _b, _h = _srv.request(base, "/api/unsubscribe?e=" + quote(victim), method="POST",
                                  body=b"via=page", headers=FORM, timeout=15)
    assert status == 200
    invalid = _signup(base, "not-an-address")
    for spelling in ("x<dn-victim@example.test>", "<dn-victim@example.test>",
                     '"dn-victim"@example.test'):
        answer = _signup(base, spelling)
        assert answer[:2] == invalid[:2], (spelling, answer[:2])
    _settle(base, data_dir)
    assert not [m for m in _all_mails(data_dir)
                if any("dn-victim" in a.lower() for a in m["payload"]["to"])], "the victim was mailed"
    assert not [r for r in _parsed_rows(data_dir) if "dn-victim" in str(r.get("email", ""))]


def test_a_forged_link_on_a_fresh_server_creates_no_secret(tmp_path):
    """Review round 1 (LOW): a well-shaped forged token made GET/HEAD read the
    HMAC secret, and reading it CREATES it on a fresh data directory; a GET
    must not write. No token can verify before any was minted."""
    forged = "eyJ4IjoxfQ." + "A" * 43
    for base in _srv.server_processes(tmp_path, stub_calendars=True):
        assert not (tmp_path / ".hmac_secret").exists(), "precondition: a fresh data dir"
        get = _srv.request(base, CONFIRM + "?token=" + forged, timeout=15)
        head = _srv.request(base, CONFIRM + "?token=" + forged, method="HEAD", timeout=15)
        assert get[0] == head[0] == 400
        assert not (tmp_path / ".hmac_secret").exists(), "a GET created the HMAC secret"


def test_a_torn_last_line_does_not_swallow_the_next_signup(tmp_path):
    """Review round 1 (LOW, reproduced): a write that failed part-way leaves a
    line with no newline; the next waitlist row was glued onto it and lost
    while the person was told it worked. Every waitlist writer starts on a
    fresh line, as unsubscribe.add does."""
    (tmp_path / "waitlist.jsonl").write_text(
        '{"ts":"2026-09-01T00:00:00+00:00","email":"before@example.test","interest":"personal"}\n'
        '{"ts":"2026-09-02T00:00:00+00:00","email":"torn@exa')
    email = "after-the-tear@example.test"
    for base in _srv.server_processes(tmp_path, stub_calendars=True, capture_mail=True):
        status, _b, _h = _signup(base, email)
        assert status == 200
        _wait_for_mail(tmp_path, email)
    rows = [r for r in _parsed_rows(tmp_path) if r.get("email") == email]
    assert any(r.get("event") is None for r in rows), "the signup row was glued onto the torn line"
    assert any(r.get("event") == "confirm_sent" for r in rows)
