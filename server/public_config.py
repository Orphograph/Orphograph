#!/usr/bin/env python3
"""public_config.py — public-safe config served to frontend.

Returns Stripe Payment Link URLs and feature flags that are safe to expose
publicly. Founder sets these via environment variables; the frontend fetches
them on page load.

Public API:
    snapshot() -> dict
"""
from __future__ import annotations

import os
import sys


def _int_env(name: str, default: int) -> int:
    """Parse an integer env var, falling back to `default` on bad input.

    Crashing /api/config on every request because PACK_PRICE_USD got "$7"
    pasted in is a worse failure than serving the default; we log loudly
    instead so the founder sees the misconfig.
    """
    raw = os.environ.get(name, "")
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        sys.stderr.write(
            f"[public_config] WARNING {name}={raw!r} is not an integer; "
            f"falling back to default {default}\n"
        )
        return default


def snapshot() -> dict:
    """Return public-safe config for the frontend.

    Includes:
      - Stripe Payment Link URLs (Pack, Personal monthly/annual)
      - Feature toggle states (maintenance, checkout disabled, etc.)
      - Pricing display values

    Excludes:
      - Stripe secret keys
      - Webhook secrets
      - Founder tokens
      - Any PII or server-side state
    """
    return {
        "stripe": {
            "pack_url": os.environ.get("STRIPE_PACK_URL", "").strip(),
            "personal_monthly_url": os.environ.get("STRIPE_PERSONAL_MONTHLY_URL", "").strip(),
            "personal_annual_url": os.environ.get("STRIPE_PERSONAL_ANNUAL_URL", "").strip(),
            "creator_monthly_url": os.environ.get("STRIPE_CREATOR_MONTHLY_URL", "").strip(),
        },
        "pricing": {
            # Canonical entry SKU: Writer Pack — 10 anchors — $19 (founder-confirmed
            # 2026-05-25). Default was $7 (stale "Pack of Ten" pricing) and silently
            # contradicted the live homepage CTA. See config_warnings().
            "pack_usd": _int_env("PACK_PRICE_USD", 19),
            "pack_credits": _int_env("PACK_CREDIT_COUNT", 10),
            # Standing Order (unlimited monthly) — $9/mo canonical (blogs + mcp
            # README + records). Default was $5 (stale). Annual left at 60 pending
            # a founder decision — see config_warnings()/CHECKOUT_GO_LIVE.md.
            "personal_monthly_usd": _int_env("PERSONAL_MONTHLY_USD", 9),
            "personal_annual_usd": _int_env("PERSONAL_ANNUAL_USD", 60),
            "creator_monthly_usd": _int_env("CREATOR_MONTHLY_USD", 19),
        },
        "toggles": {
            "checkout_disabled": os.environ.get("ORPHO_DISABLE_CHECKOUT", "0") == "1",
            "anchoring_disabled": os.environ.get("ORPHO_DISABLE_ANCHORING", "0") == "1",
            "maintenance_mode": os.environ.get("ORPHO_MAINTENANCE_MODE", "0") == "1",
        },
        "features": {
            "btc_payments": bool(os.environ.get("BTC_PAYMENTS_ENABLED", "")),
            "creator_tier_live": bool(os.environ.get("CREATOR_TIER_LIVE", "")),
            "private_receipts": True,  # always available to subscribers
            "receipt_vault": True,     # always available to subscribers
            "nowpayments_enabled": bool(os.environ.get("NOWPAYMENTS_API_KEY", "").strip()),
        },
    }


def is_live_stripe_url(url: str) -> bool:
    """True if `url` looks like a real, clickable Stripe payment/checkout link.

    The motivating failure (2026-05-30): STRIPE_PACK_URL was set to the literal
    placeholder ``https://buy.stripe.com/...`` in production. The old emptiness
    check (`if not pack_url`) passed it, so /api/health reported
    ``checkout.ready: true`` while every card buy button led to a dead Stripe
    page. A non-empty placeholder is worse than an empty value because it
    silently looks configured. This validates the *shape* of a live link:
    a known Stripe host plus a plausible link code (>=8 alphanumeric chars,
    no ``...``).
    """
    url = (url or "").strip()
    if not url:
        return False
    hosts = (
        "https://buy.stripe.com/",
        "https://checkout.stripe.com/",
        "https://pay.stripe.com/",
    )
    if not url.startswith(hosts):
        return False
    after_host = url.split("//", 1)[-1].split("/", 1)
    if len(after_host) < 2:
        return False
    path_only = after_host[1].split("?", 1)[0].split("#", 1)[0]
    # A real link has a path segment that looks like a Stripe code: >=8 chars,
    # alphanumeric (allowing _ and -), and not the '...' placeholder. Scan all
    # segments so both buy.stripe.com/<code> and checkout.stripe.com/c/pay/cs_...
    # validate while 'https://buy.stripe.com/...' (or empty path) does not.
    for seg in path_only.split("/"):
        if seg and "..." not in seg and len(seg) >= 8 \
                and seg.replace("_", "").replace("-", "").isalnum():
            return True
    return False


def config_warnings(cfg: dict | None = None) -> list[str]:
    """Return human-readable problems with the current public config.

    The motivating failure: checkout was *enabled* in production while every
    Stripe Payment Link URL was empty — so the buy buttons led nowhere and no
    one could pay, silently. This makes that (and similar) misconfigs loud.
    Surface in /api/health and assert in tests so dead checkout can't ship.
    """
    cfg = cfg or snapshot()
    warnings: list[str] = []

    checkout_live = not cfg["toggles"]["checkout_disabled"]
    pack_url = cfg["stripe"]["pack_url"]
    if checkout_live and not pack_url:
        warnings.append(
            "checkout is ENABLED but STRIPE_PACK_URL is empty — the Pack buy "
            "button leads nowhere; no one can purchase. Set STRIPE_PACK_URL or "
            "set ORPHO_DISABLE_CHECKOUT=1 until the link exists."
        )
    elif checkout_live and not is_live_stripe_url(pack_url):
        # Non-empty but not a real link (e.g. the placeholder
        # 'https://buy.stripe.com/...'). The empty check above misses this, so a
        # dead button could ship looking configured. Flag it loudly.
        warnings.append(
            "STRIPE_PACK_URL is set but is not a valid Stripe payment link "
            f"({pack_url!r}) — the Pack buy button leads nowhere; no one can "
            "purchase by card. Set the real Stripe Payment Link URL or set "
            "ORPHO_DISABLE_CHECKOUT=1 until it exists."
        )

    # Standing Order ($9/mo) is a real offered tier. An empty URL means "not
    # offered" (no warning), but a non-empty placeholder is the same dead-button
    # trap as the Pack — flag it.
    monthly_url = cfg["stripe"].get("personal_monthly_url", "")
    if checkout_live and monthly_url and not is_live_stripe_url(monthly_url):
        warnings.append(
            "STRIPE_PERSONAL_MONTHLY_URL is set but is not a valid Stripe "
            f"payment link ({monthly_url!r}) — the Standing Order buy button "
            "leads nowhere. Set the real Payment Link URL or clear it."
        )

    # Note on crypto: checkout is served by NOWPayments (multi-coin, incl.
    # BTC). The native exact-amount BTC flow (BTC_PAYMENTS_ENABLED) is an
    # OPTIONAL, redundant secondary path. NOWPayments-on + native-BTC-off is
    # a complete, healthy crypto config — NOT "half-wired" — so it earns no
    # warning. Card checkout (Stripe) is independent of both. We therefore
    # do not warn on crypto-flag combinations here; absence of crypto is a
    # valid card-only configuration.

    return warnings
