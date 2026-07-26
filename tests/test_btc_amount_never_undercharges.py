"""Regression: the BTC per-order tag must never reduce the charged amount.

Pinned to the bug fixed 2026-07-26 in server/btc_price.py:sats_for_usd.

The original implementation floored the sat amount to the nearest 10,000 and
replaced the last four digits with the suffix:

    base = (sats // 10000) * 10000
    sats = base + suffix

Because server/app.py supplies `suffix = secrets.randbelow(10000)`, the charge
landed at a uniformly random point in a 10,000-sat band straddling the true
price. At $60k/BTC a $19 order (31,667 sats) could be billed anywhere between
30,000 and 39,999 sats — undercharging by up to $1.00 or overcharging by up to
$5.00, per order, at random.

These tests fail against that implementation and pass against the additive tag.
They are permanent: the invariant is that a disambiguation tag may make an order
cost slightly MORE, never less.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from server import btc_price  # noqa: E402


@pytest.fixture
def fixed_price(monkeypatch):
    """Pin BTC/USD so the arithmetic is deterministic."""
    def _set(price: float):
        monkeypatch.setattr(btc_price, "get_usd_per_btc", lambda: price)
        return price
    return _set


# The live SKU is $19 (server/app.py hardcodes usd_amount = 19.0); the others
# are included because the same helper would serve them once tiers are wired.
SKUS = [19.0, 29.0, 9.0]
PRICES = [20_000.0, 60_000.0, 150_000.0]


@pytest.mark.parametrize("usd", SKUS)
@pytest.mark.parametrize("price", PRICES)
def test_tag_never_undercharges(fixed_price, usd, price):
    """For EVERY suffix in the caller's range, charged >= true price."""
    fixed_price(price)
    true_sats = btc_price.sats_for_usd(usd)
    # server/app.py passes secrets.randbelow(10000) — walk the whole range.
    for suffix in range(0, 10_000):
        charged = btc_price.sats_for_usd(usd, suffix=suffix)
        assert charged >= true_sats, (
            f"UNDERCHARGE: ${usd} at ${price:,.0f}/BTC with suffix={suffix} "
            f"billed {charged} sats vs true {true_sats}"
        )


@pytest.mark.parametrize("usd", SKUS)
@pytest.mark.parametrize("price", PRICES)
def test_tag_overcharge_is_bounded_and_small(fixed_price, usd, price):
    """The tag must stay under a dollar — it is a disambiguator, not a fee."""
    fixed_price(price)
    true_sats = btc_price.sats_for_usd(usd)
    worst = max(btc_price.sats_for_usd(usd, suffix=s) for s in range(0, 10_000))
    overcharge_sats = worst - true_sats
    assert overcharge_sats < btc_price.suffix_modulus_for_price(price)
    overcharge_usd = overcharge_sats / 100_000_000 * price
    # The tag is a disambiguator, not a fee. TAG_MAX_USD is the ceiling; the
    # TAG_MIN_SLOTS clamp can exceed it only at implausible BTC prices.
    assert overcharge_usd <= btc_price.TAG_MAX_USD + 0.01, (
        f"tag costs ${overcharge_usd:.2f} on a ${usd} order at ${price:,.0f}/BTC"
    )


def test_tag_still_disambiguates(fixed_price):
    """The whole point of the tag: distinct suffixes -> distinct amounts."""
    fixed_price(60_000.0)
    mod = btc_price.suffix_modulus_for_price(60_000.0)
    amounts = {btc_price.sats_for_usd(19.0, suffix=s) for s in range(mod)}
    assert len(amounts) == mod
    assert mod >= btc_price.TAG_MIN_SLOTS


def test_suffix_is_reduced_modulo_not_rejected(fixed_price):
    """Callers pass randbelow(10000); out-of-range values must not blow up."""
    fixed_price(60_000.0)
    base = btc_price.sats_for_usd(19.0)
    assert btc_price.sats_for_usd(19.0, suffix=0) == base
    # modulus and 0 collapse to the same tag.
    mod = btc_price.suffix_modulus_for_price(60_000.0)
    assert (btc_price.sats_for_usd(19.0, suffix=mod)
            == btc_price.sats_for_usd(19.0, suffix=0))
    assert btc_price.sats_for_usd(19.0, suffix=999_999) >= base


def test_dust_floor_still_applies(fixed_price):
    """A tiny order still clears the 1000-sat dust floor."""
    fixed_price(60_000.0)
    assert btc_price.sats_for_usd(0.000001) >= 1000


def test_no_price_feed_returns_zero(fixed_price):
    """Unreachable feed must return 0 so the caller rejects the order."""
    fixed_price(0.0)
    assert btc_price.sats_for_usd(19.0) == 0
    assert btc_price.sats_for_usd(19.0, suffix=42) == 0


def test_the_exact_historical_defect(fixed_price):
    """The specific case from the audit, spelled out.

    $19 at $60,000/BTC = 31,667 sats. The old code floored to 30,000 and added
    the suffix, so suffix=0 billed 30,000 — $1.00 short.
    """
    fixed_price(60_000.0)
    assert btc_price.sats_for_usd(19.0) == 31_667
    assert btc_price.sats_for_usd(19.0, suffix=0) == 31_667   # was 30_000
    assert btc_price.sats_for_usd(19.0, suffix=0) != 30_000
