"""/docs/agents carries the 'sold to machines' doctrine (founder, 2026-09-05)
and stays honest about what is not live."""
from __future__ import annotations

from pathlib import Path

PAGE = Path(__file__).resolve().parent.parent / "web" / "docs" / "agents.html"


def _section():
    html = PAGE.read_text(encoding="utf-8")
    assert 'id="sold-to-machines"' in html
    return html.split('id="sold-to-machines"', 1)[1].split("</section>", 1)[0]


def test_three_doctrine_points_are_present():
    sec = _section()
    assert "Agents pay without an account." in sec
    assert "One tool to stamp, one to look up." in sec
    assert "The proof stays on Bitcoin, not on whoever hosts the marketplace." in sec


def test_agent_payment_names_the_published_price_and_no_retired_rail():
    """Until 2026-11-05 this point had to say "It is not open" and name no
    price "before the fee schedule publishes one". Real payments opened on
    Base mainnet that day and /pricing lists the agent price, so the point
    names it, and it must be the price the code charges."""
    import sys
    sys.path.insert(0, str(PAGE.parents[2] / "server"))
    import x402
    sec = _section()
    assert "It is not open" not in sec
    assert f"{x402.DEFAULT_PRICE_CENTS} cent" in sec
    assert "Lightning" not in sec, "the doctrine still names the retired Lightning rail"
    pricing = (PAGE.parents[1] / "pricing.html").read_text(encoding="utf-8")
    assert f"{x402.DEFAULT_PRICE_CENTS} cent in USDC on Base" in pricing, "the fee schedule must list the agent price"


def test_doctrine_names_no_competitor_and_promises_no_detection():
    sec = _section().lower()
    for banned in ("court", "admissib", "ai-detect", "authorship", "ownership"):
        assert banned not in sec


def test_bitcoin_claim_requires_attestation_and_fulfillment_is_qualified():
    html = PAGE.read_text(encoding="utf-8")
    assert "Once Bitcoin has attested it" in _section()
    assert "fulfillment is not yet open" in html
    assert "Pending receipts" in html
