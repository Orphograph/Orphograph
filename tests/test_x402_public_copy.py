"""Public x402 copy states the production network and price (2026-11-05).

The mainnet switch was made with Fly secrets alone, so no code or copy
changed, and four public surfaces kept telling agents x402 ran on "Base
Sepolia, the TEST network: test USDC has no value" while production took real
USDC. An agent reading that would treat the price as play money. This pins the
whole class: every surface that describes x402 to an outside reader.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "server"))
import x402  # noqa: E402

STALE = re.compile(r"base sepolia|test network|testnet|test usdc|84532|not open for real money", re.I)


def _surfaces() -> dict[str, str]:
    spec = json.loads((ROOT / "web/openapi.json").read_text())
    html = re.sub(r"<!--.*?-->", "", (ROOT / "web/docs/agents.html").read_text(), flags=re.S)
    return {
        "openapi /api/anchor": spec["paths"]["/api/anchor"]["post"]["description"],
        "llms.txt x402 entry": next(block for block in (ROOT / "web/llms.txt").read_text().split("\n- ")
                                    if block.startswith("x402 pay-per-anchor")),
        "docs/agents x402 items": " ".join(li for li in re.findall(r"<li>.*?</li>", html, re.S)
                                           if "x402" in li or "pays" in li or "Agents pay" in li),
    }


@pytest.mark.parametrize("name", ["openapi /api/anchor", "llms.txt x402 entry", "docs/agents x402 items"])
def test_public_x402_copy_names_mainnet_and_the_price(name):
    text = _surfaces()[name]
    assert text, name
    assert not STALE.search(text), (name, STALE.search(text).group(0))
    assert re.search(r"mainnet", text, re.I), name
    assert f"{x402.DEFAULT_PRICE_CENTS} cent" in text, name


def test_the_pattern_fires_on_the_old_wording():
    old = "x402 runs on Base Sepolia (eip155:84532), the TEST network: test USDC has no value."
    assert STALE.search(old)
