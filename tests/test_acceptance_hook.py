"""Tests for acceptance_hook — the optional /api/verify acceptance resolver.

Verifies the standalone-safe contract: empty by default, populated only when a
resolver is configured, and ALWAYS degrades silently (never raises) so the value
layer can't break the open verify path.
"""
from __future__ import annotations

import sys
import types

import acceptance_hook

_KEYS = {"issuer_profile", "issuer_trusted", "revoked", "disputed"}


def test_default_is_empty_block(monkeypatch):
    monkeypatch.delenv("ORPHO_ACCEPTANCE_RESOLVER", raising=False)
    out = acceptance_hook.resolve("r1", {"issuer": "did:key:z", "profile": "art12"})
    assert set(out) == _KEYS
    assert all(v is None for v in out.values())


def _install_stub(monkeypatch, fn, name="stub_resolver_mod"):
    mod = types.ModuleType(name)
    mod.resolve_acceptance = fn
    monkeypatch.setitem(sys.modules, name, mod)
    monkeypatch.setenv("ORPHO_ACCEPTANCE_RESOLVER", name)


def test_configured_resolver_populates_fields(monkeypatch):
    def fn(receipt_id, issuer_did, profile):
        assert receipt_id == "r1" and issuer_did == "did:key:z" and profile == "art12"
        return {"issuer_profile": profile, "issuer_trusted": True, "revoked": False, "disputed": False}
    _install_stub(monkeypatch, fn)
    out = acceptance_hook.resolve("r1", {"issuer": "did:key:z", "profile": "art12"})
    assert out == {"issuer_profile": "art12", "issuer_trusted": True, "revoked": False, "disputed": False}


def test_resolver_exception_degrades_to_empty(monkeypatch):
    def fn(**kw):
        raise RuntimeError("value layer down")
    _install_stub(monkeypatch, fn)
    assert acceptance_hook.resolve("r1", {}) == acceptance_hook.empty()


def test_partial_return_filled_with_null(monkeypatch):
    def fn(**kw):
        return {"issuer_trusted": True}  # missing the other keys
    _install_stub(monkeypatch, fn)
    out = acceptance_hook.resolve("r1", {})
    assert out["issuer_trusted"] is True
    assert out["revoked"] is None and out["issuer_profile"] is None and out["disputed"] is None


def test_non_dict_return_degrades(monkeypatch):
    def fn(**kw):
        return "not-a-dict"
    _install_stub(monkeypatch, fn)
    assert acceptance_hook.resolve("r1", {}) == acceptance_hook.empty()


def test_missing_resolver_module_degrades(monkeypatch):
    monkeypatch.setenv("ORPHO_ACCEPTANCE_RESOLVER", "no_such_module_xyz_123")
    assert acceptance_hook.resolve("r1", {}) == acceptance_hook.empty()
