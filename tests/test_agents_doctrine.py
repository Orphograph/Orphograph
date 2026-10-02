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


def test_agent_payment_is_described_as_not_open_and_carries_no_price():
    sec = _section()
    assert "It is not open" in sec, "no real-money pay-per-anchor counter is open; the page must say so"
    # The Lightning rail this point used to name was retired on 2026-09-28.
    assert "Lightning" not in sec, "the doctrine still names the retired Lightning rail"
    for token in ("sats", "$", "USD"):
        assert token not in sec.split("<strong>One tool", 1)[0], (
            f"the payment point must not imply a price ({token!r}) before the fee "
            "schedule publishes one")


def test_doctrine_names_no_competitor_and_promises_no_detection():
    sec = _section().lower()
    for banned in ("court", "admissib", "ai-detect", "authorship", "ownership"):
        assert banned not in sec


def test_bitcoin_claim_requires_attestation_and_fulfillment_is_qualified():
    html = PAGE.read_text(encoding="utf-8")
    assert "Once Bitcoin has attested it" in _section()
    assert "fulfillment is not yet open" in html
    assert "Pending receipts" in html
