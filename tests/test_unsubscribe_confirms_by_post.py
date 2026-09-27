"""An unsubscribe link asks first; only the person's POST unsubscribes.

Founder decision 2026-09-27 (9A). GET on /api/unsubscribe recorded the
suppression, so a mail gateway or link scanner that fetched the link (they
fetch with GET, and some follow every link in a message) unsubscribed the
recipient without them doing anything. Now:

  * GET and HEAD answer a confirmation page with one button and write
    nothing. The page is the same whether or not the address is already
    suppressed, so it tells no one whether its owner unsubscribed.
  * The button POSTs to the same URL (the address stays in the query string;
    the body only says the page sent it) and gets the "Done" page, or a 503
    page when the ledger cannot be written.
  * The RFC 8058 one-click POST that mailbox providers send is unchanged.
"""
from __future__ import annotations

import html
import json
from pathlib import Path
from urllib.parse import quote

import pytest

import _srv

FORM = {"Content-Type": "application/x-www-form-urlencoded", "Accept": "text/html"}


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    d = tmp_path_factory.mktemp("unsub_confirm")
    for base in _srv.server_processes(d):
        yield base, d


def _suppressed(data_dir: Path) -> list[str]:
    p = data_dir / "suppressions.jsonl"
    if not p.exists():
        return []
    return [json.loads(l).get("email") for l in p.read_text().splitlines() if l.strip()]


def test_following_the_link_unsubscribes_no_one(server):
    base, data_dir = server
    email = "scanned@example.test"
    path = "/api/unsubscribe?e=" + quote(email)
    status, body, headers = _srv.request(base, path, timeout=15)
    assert status == 200
    assert email not in _suppressed(data_dir), "a GET recorded an unsubscribe"
    page = body.decode()
    assert '<form method="post"' in page, "the page must offer the POST button"
    assert f'action="{html.escape(path)}"' in page
    assert headers.get("Cache-Control") == "no-store"
    head_status, head_body, head_headers = _srv.request(base, path, method="HEAD", timeout=15)
    assert head_status == 200 and head_body == b""
    assert head_headers.get("Content-Length") == str(len(body))
    assert email not in _suppressed(data_dir)


def test_the_button_unsubscribes_and_says_so(server):
    base, data_dir = server
    email = "clicked@example.test"
    path = "/api/unsubscribe?e=" + quote(email)
    status, body, headers = _srv.request(base, path, method="POST", body=b"via=page",
                                         headers=FORM, timeout=15)
    assert status == 200, body
    assert b"Done" in body and b"<strong>clicked@example.test</strong>" in body
    assert headers.get("Content-Type", "").startswith("text/html")
    assert headers.get("Cache-Control") == "no-store"
    assert _suppressed(data_dir).count(email) == 1


def test_the_page_does_not_say_whether_an_address_already_unsubscribed(server):
    base, _data_dir = server
    known = "/api/unsubscribe?e=" + quote("known@example.test")
    _srv.request(base, known, method="POST", body=b"via=page", headers=FORM, timeout=15)
    fresh = "/api/unsubscribe?e=" + quote("fresh@example.test")
    known_page = _srv.request(base, known, timeout=15)[1].decode()
    fresh_page = _srv.request(base, fresh, timeout=15)[1].decode()
    def blank(page: str, local: str) -> str:
        return page.replace(f"{local}@", "X@").replace(f"{local}%40", "X%40")
    assert blank(known_page, "known") == blank(fresh_page, "fresh")


def test_the_one_click_post_mailbox_providers_send_still_works(server):
    """Control: RFC 8058, unchanged."""
    base, data_dir = server
    email = "one-click@example.test"
    status, raw, headers = _srv.request(
        base, "/api/unsubscribe?e=" + quote(email), method="POST",
        body=b"List-Unsubscribe=One-Click",
        headers={"Content-Type": "application/x-www-form-urlencoded"}, timeout=15)
    assert status == 200 and json.loads(raw) == {"ok": True}
    assert _suppressed(data_dir).count(email) == 1


def test_a_one_click_post_is_recorded_in_a_directory_the_writer_repairs(tmp_path):
    """The ledger's directory exists, is owned by the server and is read-only;
    the writer chmods it and writes. The first writability check on POST
    guessed "unwritable" and answered a mailbox provider 503 for an
    unsubscribe the writer would have recorded (found in review of 81760a5)."""
    import os
    assert os.geteuid() != 0, "run this suite as a non-root user"
    sub = tmp_path / "sub"
    sub.mkdir()
    sub.chmod(0o500)
    ledger = sub / "suppressions.jsonl"
    try:
        for base in _srv.server_processes(tmp_path, ORPHO_SUPPRESSIONS=str(ledger)):
            status, raw, _h = _srv.request(
                base, "/api/unsubscribe?e=" + quote("gmail-user@example.test"), method="POST",
                body=b"List-Unsubscribe=One-Click",
                headers={"Content-Type": "application/x-www-form-urlencoded"}, timeout=15)
            assert status == 200, (status, raw)
            assert "gmail-user@example.test" in ledger.read_text()
    finally:
        sub.chmod(0o700)


def test_a_held_lock_does_not_delay_an_already_unsubscribed_address(tmp_path, monkeypatch):
    """The writability check took the ledger's exclusive lock, and that lock
    waits. A POST for an address that is already suppressed writes nothing,
    and it is the one mailbox providers retry, yet it waited for whoever held
    the lock (reproduced in review: a 4 s hold delayed the answer 4 s)."""
    import fcntl
    import threading
    import unsubscribe

    ledger = tmp_path / "suppressions.jsonl"
    monkeypatch.setattr(unsubscribe, "SUPPRESS_PATH", ledger)
    assert unsubscribe.add("known@example.test") is True
    raised: list[BaseException] = []

    def check() -> None:
        try:
            unsubscribe.ensure_writable()
        except BaseException as e:   # reported below, not lost with the thread
            raised.append(e)

    checker = threading.Thread(target=check, daemon=True)
    holder = ledger.open("a")
    try:
        fcntl.flock(holder.fileno(), fcntl.LOCK_EX)
        with ledger.open("a") as other:
            with pytest.raises(OSError):   # control: the lock is held and binds
                fcntl.flock(other.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        checker.start()
        checker.join(timeout=2)
        waited = checker.is_alive()
    finally:
        fcntl.flock(holder.fileno(), fcntl.LOCK_UN)
        holder.close()
    checker.join(timeout=5)   # a check that waited finishes before tmp_path goes
    assert not waited, "the writability check waited for the ledger lock"
    assert raised == [], raised


def test_looking_at_the_link_touches_nothing_on_disk(tmp_path):
    """GET and HEAD only ask. The writability check creates the ledger and
    chmods its directory, so it belongs to the POST alone: after both
    requests there is no ledger and the directory keeps its mode."""
    sub = tmp_path / "sub"
    sub.mkdir()
    sub.chmod(0o755)
    ledger = sub / "suppressions.jsonl"
    before = oct(sub.stat().st_mode)
    path = "/api/unsubscribe?e=" + quote("only-looking@example.test")
    for base in _srv.server_processes(tmp_path, ORPHO_SUPPRESSIONS=str(ledger)):
        assert _srv.request(base, path, timeout=15)[0] == 200
        assert _srv.request(base, path, method="HEAD", timeout=15)[0] == 200
    assert not ledger.exists(), "looking at the link created the ledger"
    assert oct(sub.stat().st_mode) == before, "looking at the link changed the directory"
