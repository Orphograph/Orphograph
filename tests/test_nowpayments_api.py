"""test_nowpayments_api.py — outbound NOWPayments REST client.

All network calls mocked. Real NOWPayments endpoints are never hit;
running these tests in an offline environment must still produce a
deterministic result.
"""
from __future__ import annotations

import io
import json
from unittest.mock import patch

import pytest

import nowpayments_api


class _FakeResp:
    """Stand-in for urllib.request.urlopen()'s response."""

    def __init__(self, status: int, body: bytes):
        self.status = status
        self._body = body

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_create_invoice_returns_dict_on_success(monkeypatch):
    """Happy path: a configured client + 200 response yields ok=True with data."""
    monkeypatch.setenv("NOWPAYMENTS_API_KEY", "test-key-abcdef")
    captured: dict = {}

    def fake_urlopen(req, timeout=None):  # noqa: ARG001
        captured["url"] = req.full_url
        captured["method"] = req.get_method()
        captured["headers"] = dict(req.header_items())
        captured["body"] = json.loads(req.data.decode("utf-8")) if req.data else None
        body = json.dumps({
            "id": "inv_42",
            "invoice_url": "https://nowpayments.io/payment/inv_42",
            "price_amount": 19,
            "price_currency": "usd",
            "pay_currency": "usdc",
        }).encode("utf-8")
        return _FakeResp(200, body)

    with patch("nowpayments_api.urllib.request.urlopen", side_effect=fake_urlopen):
        out = nowpayments_api.create_invoice(
            amount_usd=19.0,
            currency="usdc",
            order_id="np_test_123",
            customer_email="buyer@example.com",
        )

    assert out["ok"] is True, out
    assert out["status"] == 200
    assert out["data"]["invoice_url"].startswith("https://nowpayments.io/")
    # Spot-check that the outbound request looks right.
    assert captured["method"] == "POST"
    assert captured["url"].endswith("/invoice")
    assert captured["body"]["price_amount"] == 19
    assert captured["body"]["pay_currency"] == "usdc"
    assert captured["body"]["order_id"] == "np_test_123"
    # api key must be sent, but never logged/returned in our dict.
    headers = {k.lower(): v for k, v in captured["headers"].items()}
    assert headers.get("X-api-key".lower()) == "test-key-abcdef" or \
           headers.get("x-api-key") == "test-key-abcdef"


def test_create_invoice_without_api_key_short_circuits(monkeypatch):
    """No NOWPAYMENTS_API_KEY → never touch the network, return a clean reason."""
    monkeypatch.delenv("NOWPAYMENTS_API_KEY", raising=False)

    def fake_urlopen(req, timeout=None):  # pragma: no cover — must not be called
        raise AssertionError("urlopen must not be called when API key is missing")

    with patch("nowpayments_api.urllib.request.urlopen", side_effect=fake_urlopen):
        out = nowpayments_api.create_invoice(
            amount_usd=19.0,
            currency="usdc",
            order_id="np_no_key",
        )

    assert out == {"ok": False, "reason": "nowpayments_not_configured"}


def test_get_invoice_status_returns_dict_on_success(monkeypatch):
    """GET /payment/<id> returns the parsed body wrapped in our envelope."""
    monkeypatch.setenv("NOWPAYMENTS_API_KEY", "test-key-getter")

    captured: dict = {}

    def fake_urlopen(req, timeout=None):  # noqa: ARG001
        captured["method"] = req.get_method()
        captured["url"] = req.full_url
        body = json.dumps({
            "payment_id": 99,
            "payment_status": "finished",
            "pay_amount": "12.34",
        }).encode("utf-8")
        return _FakeResp(200, body)

    with patch("nowpayments_api.urllib.request.urlopen", side_effect=fake_urlopen):
        out = nowpayments_api.get_invoice_status("inv42abc")

    assert out["ok"] is True
    assert out["data"]["payment_status"] == "finished"
    assert captured["method"] == "GET"
    assert captured["url"].endswith("/payment/inv42abc")
