"""Seam tests for customer outbound webhooks.

These drive storage, registration, URL policy, DNS pinning, wire headers, and
dispatch.  Network operations are replaced at the socket boundary; validation
and request construction remain real.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import socket

import pytest

import webhooks


@pytest.fixture(autouse=True)
def _isolated_ledger(tmp_path, monkeypatch):
    monkeypatch.setattr(webhooks, "WEBHOOKS_LEDGER", tmp_path / "webhooks.jsonl")


def test_registration_listing_deletion_and_secret_redaction(monkeypatch):
    monkeypatch.setattr(webhooks, "_validate_webhook_url", lambda _url: (True, None))
    created = webhooks.register("Owner@Example.test", "https://hooks.example/a")
    assert created["ok"] is True
    assert created["secret"].startswith("orpho_whsec_")

    public = webhooks.list_for_email("owner@example.test")
    assert public == [{
        "url": "https://hooks.example/a",
        "secret_prefix": created["secret"][:10] + "…",
        "created_at": public[0]["created_at"],
    }]
    assert created["secret"] not in json.dumps(public)
    assert webhooks.list_for_email_with_secrets("OWNER@example.test") == [{
        "url": "https://hooks.example/a", "secret": created["secret"],
    }]

    assert webhooks.register("owner@example.test", "https://hooks.example/a") == {
        "ok": False, "reason": "duplicate_url",
    }
    assert webhooks.delete("owner@example.test", "https://hooks.example/a") is True
    assert webhooks.list_for_email("owner@example.test") == []
    assert webhooks.delete("owner@example.test", "https://hooks.example/a") is False


def test_registration_rejections_and_limit(monkeypatch):
    assert webhooks.register("not-an-email", "https://hooks.example/a") == {
        "ok": False, "reason": "bad_email",
    }
    monkeypatch.setattr(
        webhooks, "_validate_webhook_url",
        lambda url: (False, "url_must_be_https") if url.startswith("http:") else (True, None),
    )
    assert webhooks.register("a@b.test", "http://hooks.example/a")["reason"] == "url_must_be_https"
    for index in range(webhooks.MAX_PER_EMAIL):
        assert webhooks.register("a@b.test", f"https://hooks.example/{index}")["ok"]
    assert webhooks.register("a@b.test", "https://hooks.example/overflow") == {
        "ok": False, "reason": "registration_limit",
    }


@pytest.mark.parametrize(("url", "reason"), [
    ("", "bad_url"),
    ("http://example.com/hook", "url_must_be_https"),
    ("https:///hook", "missing_host"),
    ("https://user:pass@example.com/hook", "userinfo_not_allowed"),
    ("https://example.com:bad/hook", "bad_port"),
    ("https://example.com/\\evil", "bad_url"),
    ("https://example.com/a\tb", "bad_url"),
    ("https://localhost/hook", "non_public_address"),
    ("https://service.internal/hook", "non_public_address"),
])
def test_url_policy_rejects_ambiguous_or_internal_shapes(url, reason, monkeypatch):
    monkeypatch.setattr(webhooks, "_is_public_address", lambda _host: (True, None))
    assert webhooks._validate_webhook_url(url) == (False, reason)


def test_url_policy_requires_every_dns_answer_to_be_public(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *_args, **_kwargs: [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("203.0.113.8", 0)),
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 0)),
    ])
    ok, reason = webhooks._validate_webhook_url("https://mixed.example/hook")
    assert ok is False
    assert reason.startswith("non_public_address")


def test_public_addresses_unwraps_literals_and_reports_dns_errors(monkeypatch):
    assert webhooks._public_addresses("8.8.8.8") == (["8.8.8.8"], None)
    assert webhooks._public_addresses("127.0.0.1")[0] == []
    monkeypatch.setattr(socket, "getaddrinfo", lambda *_a, **_k: (_ for _ in ()).throw(socket.gaierror("nope")))
    addresses, reason = webhooks._public_addresses("missing.example")
    assert addresses == []
    assert reason.startswith("dns_error:")


class _Response:
    def __init__(self, status=204, body=b""):
        self.status = status
        self._body = body

    def read(self, _limit):
        return self._body


def test_pinned_connection_uses_validated_ip_but_tls_hostname(monkeypatch):
    calls = {}

    class FakeContext:
        verify_mode = webhooks.ssl.CERT_REQUIRED
        check_hostname = True

        def wrap_socket(self, sock, *, server_hostname):
            calls["wrapped"] = sock
            calls["server_hostname"] = server_hostname
            return "tls-socket"

    monkeypatch.setattr(webhooks.ssl, "create_default_context", FakeContext)
    connection = webhooks._PinnedHTTPSConnection(
        "receiver.example", 443, "93.184.216.34", 5.0,
    )
    connection._create_connection = lambda address, timeout, source: calls.update(
        address=address, timeout=timeout, source=source,
    ) or "tcp-socket"
    connection.connect()

    assert calls["address"] == ("93.184.216.34", 443)
    assert calls["server_hostname"] == "receiver.example"
    assert calls["wrapped"] == "tcp-socket"
    assert connection.sock == "tls-socket"


def test_delivery_pins_validated_ip_and_builds_signed_wire_request(monkeypatch):
    captured = {}

    class FakeConnection:
        def __init__(self, hostname, port, pinned_ip, timeout):
            captured.update(hostname=hostname, port=port, pinned_ip=pinned_ip, timeout=timeout)

        def request(self, method, target, body, headers):
            captured.update(method=method, target=target, body=body, headers=headers)

        def getresponse(self):
            return _Response()

        def close(self):
            captured["closed"] = True

    monkeypatch.setattr(webhooks, "_validate_webhook_url", lambda _url: (True, None))
    monkeypatch.setattr(webhooks, "_public_addresses", lambda _host: (["93.184.216.34"], None))
    monkeypatch.setattr(webhooks, "_PinnedHTTPSConnection", FakeConnection)
    monkeypatch.setattr(webhooks.time, "time", lambda: 1_700_000_000)

    body = b'{"proof":"payload"}'
    webhooks._deliver_one(
        "https://receiver.example:8443/hooks/inbox?tenant=7#ignored",
        "orpho_whsec_test", body, "evt_wire",
    )
    assert captured["hostname"] == "receiver.example"
    assert captured["pinned_ip"] == "93.184.216.34"
    assert captured["port"] == 8443
    assert captured["target"] == "/hooks/inbox?tenant=7"
    assert captured["method"] == "POST"
    assert captured["body"] == body
    expected = hmac.new(
        b"orpho_whsec_test", b"1700000000." + body, hashlib.sha256,
    ).hexdigest()
    assert captured["headers"]["X-Orpho-Signature"] == f"t=1700000000,v1={expected}"
    assert captured["headers"]["X-Orpho-Event-Id"] == "evt_wire"
    assert captured["closed"] is True


def test_delivery_refuses_rebound_private_resolution(monkeypatch, capsys):
    monkeypatch.setattr(webhooks, "_validate_webhook_url", lambda _url: (True, None))
    monkeypatch.setattr(
        webhooks, "_public_addresses", lambda _host: ([], "non_public_address: 127.0.0.1"),
    )
    webhooks._deliver_one("https://rebound.example/hook", "secret", b"{}", "evt_rebind")
    error = capsys.readouterr().err
    assert "delivery refused" in error
    assert "127.0.0.1" in error


def test_delivery_does_not_follow_redirects(monkeypatch, capsys):
    class RedirectConnection:
        def __init__(self, *_args, **_kwargs):
            pass

        def request(self, *_args, **_kwargs):
            pass

        def getresponse(self):
            return _Response(302, b"redirect body")

        def close(self):
            pass

    monkeypatch.setattr(webhooks, "_validate_webhook_url", lambda _url: (True, None))
    monkeypatch.setattr(webhooks, "_public_addresses", lambda _host: (["93.184.216.34"], None))
    monkeypatch.setattr(webhooks, "_PinnedHTTPSConnection", RedirectConnection)
    webhooks._deliver_one("https://receiver.example/hook", "secret", b"{}", "evt_redirect")
    error = capsys.readouterr().err
    assert "HTTP 302" in error
    assert "redirect body" in error


def test_dispatch_builds_one_envelope_per_event_and_starts_each_target(monkeypatch):
    deliveries = []
    monkeypatch.setattr(webhooks, "list_for_email_with_secrets", lambda _email: [
        {"url": "https://one.example/h", "secret": "s1"},
        {"url": "https://two.example/h", "secret": "s2"},
    ])
    monkeypatch.setattr(webhooks, "_deliver_one", lambda *args: deliveries.append(args))
    monkeypatch.setattr(webhooks.secrets, "token_urlsafe", lambda _n: "fixed")
    monkeypatch.setattr(webhooks.time, "time", lambda: 1_700_000_001)

    class ImmediateThread:
        def __init__(self, *, target, args, **_kwargs):
            self.target, self.args = target, args

        def start(self):
            self.target(*self.args)

    monkeypatch.setattr(webhooks.threading, "Thread", ImmediateThread)
    webhooks.dispatch("anchor.created", "owner@example.test", {"receipt_id": "r1"})

    assert len(deliveries) == 2
    envelopes = [json.loads(args[2]) for args in deliveries]
    assert envelopes[0] == envelopes[1] == {
        "id": "evt_fixed",
        "type": "anchor.created",
        "created": 1_700_000_001,
        "data": {"receipt_id": "r1"},
    }
    assert {args[0] for args in deliveries} == {
        "https://one.example/h", "https://two.example/h",
    }


def test_dispatch_rejects_unknown_events_and_bad_inputs(monkeypatch, capsys):
    called = []
    monkeypatch.setattr(webhooks, "list_for_email_with_secrets", lambda email: called.append(email) or [])
    webhooks.dispatch("made.up", "owner@example.test", {})
    webhooks.dispatch("anchor.created", "", {})
    webhooks.dispatch("anchor.created", "owner@example.test", "not-a-dict")
    assert called == []
    assert "unknown event_type" in capsys.readouterr().err
