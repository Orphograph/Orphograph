"""/api/unsubscribe must never reflect markup from its `e` parameter (2026-09-19).

The intake address check is a SHAPE check (no `@`, whitespace or comma in either
part); it admits `<svg/onload=alert(1)>@x.co`. The GET handler placed that value
straight into an HTML page. script-src 'self' stopped script execution, not
rendering: a crafted link could show attacker-chosen markup on our origin.

The guard is ENCODING ON OUTPUT, not rejection. An earlier draft of this fix
also refused `<` and `>` at parse time; review caught that an address accepted
at signup could then never unsubscribe (CAN-SPAM, RFC 8058), so unsubscribe
stays exactly as permissive as intake and that is pinned below.

Drives a real server over HTTP: status, headers and body are the bytes a
visitor receives, including the 4xx/5xx bodies a stubbed handler would hide.
"""
from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import quote

import pytest

import _srv

PAYLOADS = (
    "<svg/onload=alert(1)>@x.co",
    "<a/href=//evil.example>re-subscribe</a>@x.co",
    "a<b>@x.co",
)


@pytest.fixture(scope="module")
def data_dir(tmp_path_factory) -> Path:
    return tmp_path_factory.mktemp("unsub")


@pytest.fixture(scope="module")
def server(data_dir):
    yield from _srv.server_processes(data_dir)


@pytest.fixture(scope="module")
def broken_server(tmp_path_factory):
    """Same server, but the suppression ledger is a DIRECTORY: opening it
    raises OSError, which the ledger reports as SuppressionUnavailable."""
    d = tmp_path_factory.mktemp("unsub_broken")
    (d / "suppressions.jsonl").mkdir()
    yield from _srv.server_processes(d)


def _get(base: str, raw: str):
    status, body, headers = _srv.request(base, "/api/unsubscribe?e=" + quote(raw))
    return status, body.decode("utf-8"), headers


def _ledger_emails(data_dir: Path) -> list[str]:
    p = data_dir / "suppressions.jsonl"
    if not p.exists():
        return []
    return [json.loads(l)["email"] for l in p.read_text().splitlines() if l.strip()]


@pytest.mark.parametrize("payload", PAYLOADS)
def test_markup_is_encoded_never_rendered(server, payload):
    import html
    status, body, _ = _get(server, payload)
    assert status == 200, "an address intake accepts must be able to unsubscribe"
    assert payload not in body, "raw markup from the URL reached the page"
    assert html.escape(payload) in body


@pytest.mark.parametrize("payload", PAYLOADS)
def test_unsubscribe_is_as_permissive_as_intake(server, data_dir, payload):
    """THE REVIEW FINDING, pinned: stranding an address is worse than storing
    an ugly one. Whatever the shape check admits must be recorded."""
    _get(server, payload)
    assert payload.lower() in [e.lower() for e in _ledger_emails(data_dir)]


def test_quotes_and_ampersands_come_back_encoded(server):
    status, body, _ = _get(server, "o'brien&\"co\"@example.com")
    assert status == 200
    assert "o'brien&\"co\"@example.com" not in body
    assert "o&#x27;brien&amp;&quot;co&quot;@example.com" in body


def test_refused_value_is_not_reflected_in_the_error_body(server):
    """No `@`, so the shape check refuses it. The 400 body is real here (it
    was an empty string under a stubbed send_error, which made this vacuous)."""
    status, body, _ = _get(server, "<svg/onload=alert(1)>")
    assert status == 400
    assert body, "empty error body: this assertion would prove nothing"
    assert "<svg" not in body


def test_ordinary_address_works_and_is_idempotent(server, data_dir):
    status, body, headers = _get(server, "reader@example.com")
    assert status == 200
    assert "<strong>reader@example.com</strong>" in body
    assert "Confirmed" in body
    assert "reader@example.com" in _ledger_emails(data_dir)
    # The second visit gets the same page: a different one said whether the
    # address had been suppressed before, to anyone who asked.
    status2, body2, headers2 = _get(server, "reader@example.com")
    assert status2 == 200 and body2 == body
    # The page carries the recipient's address, so it is never cached.
    assert headers.get("Cache-Control") == "no-store"
    assert headers2.get("Cache-Control") == "no-store"
    assert "style-src 'self'" in (headers.get("Content-Security-Policy") or "")


def test_page_has_no_inline_style_and_links_the_site_sheets(server):
    _, body, _ = _get(server, "layout@example.com")
    low = body.lower()
    assert "<style" not in low and " style=" not in low
    assert body.count('rel="stylesheet"') >= 3
    assert '<link rel="stylesheet" href="/style.css?v=' in body


def test_unreadable_ledger_answers_503_not_a_dropped_socket(broken_server):
    """Before: the exception escaped, the socket closed, and _srv.request
    raised ServerGone. The visitor could not tell whether it was recorded."""
    status, body, _ = _get(broken_server, "reader@example.com")
    assert status == 503
    assert "Done" not in body, "claimed success while nothing was recorded"
    status_post, raw, _ = _srv.request(
        broken_server, "/api/unsubscribe?e=" + quote("reader@example.com"),
        method="POST", body=b"List-Unsubscribe=One-Click",
        headers={"Content-Type": "application/x-www-form-urlencoded"})
    assert status_post == 503, raw
