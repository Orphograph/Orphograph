#!/usr/bin/env python3
"""Agent price: one cent per receipt by default, and a bad price refuses to arm.

Founder, 2026-11-05: make the agent price "competing and accessible". The
per-call notary services in the differentiation ledger charge $0.001–$0.01;
one payment here buys a receipt for a single output or for a whole agent run
(up to 50,000 steps), so the per-step price of a run is far below a cent.

A zero, negative or non-integer ORPHO_X402_PRICE_CENTS would publish a 402
challenge that charges nothing or nonsense; it now lands in config_problems()
and the rail stays off, the same fail-closed rule as the pay-to check.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "server"))

import x402  # noqa: E402

GOOD = {
    "ORPHO_X402_PAY_TO_ADDRESS": "0x000000000000000000000000000000000000BEEF",
    "ORPHO_X402_FACILITATOR_URL": "https://127.0.0.1:9/facilitator",
}


@pytest.fixture
def env(monkeypatch):
    for k in ("ORPHO_X402_PRICE_CENTS", "ORPHO_X402_BACKEND", "ORPHO_X402_NETWORK",
              "ORPHO_X402_ASSET"):
        monkeypatch.delenv(k, raising=False)
    for k, v in GOOD.items():
        monkeypatch.setenv(k, v)
    return monkeypatch


def test_default_is_one_cent(env):
    assert x402.price_cents() == 1
    assert x402.price_atomic() == "10000"          # USDC has 6 decimals
    assert x402.price_problem() is None
    assert x402.config_problems() == []


def test_operator_can_still_set_a_price(env):
    env.setenv("ORPHO_X402_PRICE_CENTS", "5")
    assert x402.price_cents() == 5
    assert x402.price_atomic() == "50000"
    assert x402.config_problems() == []


@pytest.mark.parametrize("bad", ["0", "-5", "abc", "1.5", "0.01"])
def test_bad_price_refuses_to_arm(env, bad):
    env.setenv("ORPHO_X402_PRICE_CENTS", bad)
    probs = x402.config_problems()
    assert any("ORPHO_X402_PRICE_CENTS" in p for p in probs), probs
    assert not x402.configured()
    # the message names the variable, never echoes the value (checked in the
    # forms a value would take: =value or 'value'; a bare "0" also occurs in
    # the variable's own name, ORPHO_X402)
    assert all(f"={bad}" not in p and repr(bad) not in p for p in probs if "PRICE" in p)


def test_blank_price_means_default(env):
    env.setenv("ORPHO_X402_PRICE_CENTS", "  ")
    assert x402.price_cents() == 1
    assert x402.price_problem() is None
