"""Tests for the loopback HTTP-client acceptance resolver."""
from __future__ import annotations

import io
import json
import urllib.request

import acceptance_resolver_http as h
import pytest


class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _fake_urlopen(payload: bytes):
    def _open(url, timeout=None):
        _open.last_url = url
        return _Resp(payload)
    return _open


def test_unset_url_raises(monkeypatch):
    monkeypatch.delenv("ASN_ACCEPTANCE_URL", raising=False)
    with pytest.raises(Exception):
        h.resolve_acceptance("r1")


def test_returns_dict_and_builds_query(monkeypatch):
    monkeypatch.setenv("ASN_ACCEPTANCE_URL", "http://127.0.0.1:8770")
    payload = json.dumps({"issuer_profile": "art12", "issuer_trusted": True,
                          "revoked": False, "disputed": False}).encode()
    fake = _fake_urlopen(payload)
    monkeypatch.setattr(urllib.request, "urlopen", fake)
    out = h.resolve_acceptance("r1", "did:key:z", "art12")
    assert out["issuer_trusted"] is True
    assert "receipt_id=r1" in fake.last_url and "issuer_did=did" in fake.last_url


def test_non_dict_response_raises(monkeypatch):
    monkeypatch.setenv("ASN_ACCEPTANCE_URL", "http://127.0.0.1:8770")
    monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen(b'"not-a-dict"'))
    with pytest.raises(Exception):
        h.resolve_acceptance("r1")
