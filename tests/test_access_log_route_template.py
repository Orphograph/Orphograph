"""Two access-log items, driven through a real server process.

@log-bearer-startswith: the bearer rule matched `/a/` ANYWHERE in the folded
path, so `/blog/a/x` was logged as [redacted-path]. A bearer route is now one
the folded path STARTS with; a token-shaped run after a bearer route anywhere
else is still redacted.

@log-route-template: a receipt id opens a private receipt and an order id
reads its payment state; the log carried both. Id-bearing routes are now
logged as their template (`/r/{id}`)."""
from __future__ import annotations

import pytest

import _srv


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    data_dir = tmp_path_factory.mktemp("log_template")
    for base in _srv.server_processes(data_dir, stub_calendars=True):
        yield base, data_dir


def _log(data_dir):
    logs = list(data_dir.glob("server-*.log"))
    assert len(logs) == 1, logs
    return logs[0].read_text(errors="replace")


def test_a_path_that_only_contains_a_bearer_route_is_not_over_redacted(server):
    base, data_dir = server
    _srv.raw_request(base, "/blog/a/x")
    _srv.raw_request(base, "/docs/api/pack/balance/help")
    text = _log(data_dir)
    assert '"GET /blog/a/x HTTP/1.1"' in text
    assert '"GET /docs/api/pack/balance/help HTTP/1.1"' in text


def test_a_token_after_a_bearer_route_mid_path_is_still_redacted(server):
    base, data_dir = server
    token = "TokenMidPathCanary0123456789abcd"
    _srv.raw_request(base, f"/x/a/{token}")
    _srv.raw_request(base, f"/login?next=/x/a/{token}")
    text = _log(data_dir)
    assert token not in text
    assert '"GET [redacted-path] HTTP/1.1"' in text


@pytest.mark.parametrize("target,template", [
    ("/r/ReceiptIdCanary01", "/r/{id}"),
    ("/api/receipt/ReceiptIdCanary02", "/api/receipt/{id}"),
    ("/api/verify/ReceiptIdCanary03", "/api/verify/{id}"),
    ("/api/badge/ReceiptIdCanary04.svg", "/api/badge/{id}"),
    ("/api/nowpayments/order/OrderIdCanary05", "/api/nowpayments/order/{id}"),
    ("/certificate/ReceiptIdCanary06", "/certificate/{id}"),
    ("/login?next=/r/ReceiptIdCanary07", "/login?next=/r/{id}"),
    ("/R/ReceiptIdCanary08", "/r/{id}"),
    ("/r/x/a/ReceiptIdCanary09", "/r/{id}"),
])
def test_an_id_route_is_logged_as_its_template(server, target, template):
    base, data_dir = server
    _srv.raw_request(base, target)
    text = _log(data_dir)
    canary = target.rsplit("/", 1)[1].split(".")[0]
    assert canary not in text, f"{canary} reached the access log"
    assert f'"GET {template} HTTP/1.1"' in text


def test_ordinary_paths_are_unchanged(server):
    base, data_dir = server
    _srv.raw_request(base, "/blog/why-receipts")
    _srv.raw_request(base, "/r/")
    text = _log(data_dir)
    assert '"GET /blog/why-receipts HTTP/1.1"' in text
    assert '"GET /r/ HTTP/1.1"' in text
