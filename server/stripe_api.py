#!/usr/bin/env python3
"""stripe_api.py — outbound Stripe API helper for subscription actions.

Used for cancel + reactivate flows where the user wants to change
their Stripe subscription state from inside our app rather than
the Stripe Customer Portal.

Stdlib only. POSTs to the Stripe REST API with our secret key.
Inert if STRIPE_SECRET_KEY is unset — every call returns a clear
"not configured" error that the caller surfaces to the user.

Public API:
    cancel_at_period_end(subscription_id) -> dict
    reactivate(subscription_id) -> dict
    create_checkout_session(price_id, mode, success_url, cancel_url, ...) -> dict
    is_configured() -> bool
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

STRIPE_SECRET_KEY = os.environ.get("STRIPE_SECRET_KEY", "")
HTTP_TIMEOUT = 10
STRIPE_BASE = "https://api.stripe.com/v1"


def is_configured() -> bool:
    return bool(STRIPE_SECRET_KEY)


def _request(method: str, path: str, form: dict | None = None) -> dict:
    if not STRIPE_SECRET_KEY:
        return {"ok": False, "error": "Stripe API not configured (STRIPE_SECRET_KEY unset)"}
    data = urllib.parse.urlencode(form or {}).encode("utf-8") if form else None
    req = urllib.request.Request(
        STRIPE_BASE + path,
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {STRIPE_SECRET_KEY}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
            body = resp.read().decode("utf-8")
            return {"ok": True, "data": json.loads(body) if body else {}}
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace") if hasattr(e, "read") else ""
        sys.stderr.write(f"[stripe_api] HTTP {e.code}: {body[:300]}\n")
        try:
            err = json.loads(body)
            msg = err.get("error", {}).get("message", str(e))
        except (json.JSONDecodeError, ValueError):
            msg = f"HTTP {e.code}"
        return {"ok": False, "error": msg, "status": e.code}
    except (urllib.error.URLError, OSError) as e:
        sys.stderr.write(f"[stripe_api] {type(e).__name__}: {e}\n")
        return {"ok": False, "error": f"{type(e).__name__}"}


def cancel_at_period_end(subscription_id: str) -> dict:
    """Mark the subscription to cancel at the end of the current period.

    The user keeps access until the period rolls over; no immediate
    revocation. Matches the gentle-cancel UX customers expect.
    """
    if not subscription_id:
        return {"ok": False, "error": "missing subscription id"}
    return _request("POST", f"/subscriptions/{subscription_id}",
                    form={"cancel_at_period_end": "true"})


def reactivate(subscription_id: str) -> dict:
    """Clear the cancel_at_period_end flag so the subscription continues."""
    if not subscription_id:
        return {"ok": False, "error": "missing subscription id"}
    return _request("POST", f"/subscriptions/{subscription_id}",
                    form={"cancel_at_period_end": "false"})


def create_checkout_session(
    *,
    price_id: str,
    mode: str,
    success_url: str,
    cancel_url: str,
    customer_email: str = "",
    client_reference_id: str = "",
) -> dict:
    """Create a Stripe Checkout Session.

    mode: "payment" for one-time (Pack), "subscription" for recurring (Standing Order).

    Returns {"ok": True, "data": {"id": "cs_…", "url": "https://checkout.stripe.com/…"}}
    on success, or {"ok": False, "error": "…"} on failure.

    The url field is the hosted Checkout page we redirect the buyer to. The
    session id is included in the webhook payload's data.object.id when the
    customer completes payment, which is how server/stripe_webhook.py
    correlates the inbound event back to this purchase.
    """
    if not price_id:
        return {"ok": False, "error": "missing price id"}
    if mode not in ("payment", "subscription"):
        return {"ok": False, "error": "mode must be 'payment' or 'subscription'"}

    # Stripe API uses repeated-form-field syntax for nested arrays.
    form: dict[str, str] = {
        "mode": mode,
        "success_url": success_url,
        "cancel_url": cancel_url,
        "line_items[0][price]": price_id,
        "line_items[0][quantity]": "1",
        # Auto-tax keeps us out of the "you owe back-tax" trap. Enabled
        # only if Stripe Tax is set up on the account; harmless otherwise.
        "automatic_tax[enabled]": "true",
        # Let customers update their billing address — required when
        # automatic_tax is on (Stripe needs a destination to compute tax).
        "billing_address_collection": "auto",
    }
    if customer_email:
        form["customer_email"] = customer_email
    if client_reference_id:
        form["client_reference_id"] = client_reference_id
    # For one-time Pack purchases, collect a phone+email and persist as
    # a Customer object so the buyer can come back later. For subs this
    # already happens automatically.
    if mode == "payment":
        form["customer_creation"] = "if_required"

    return _request("POST", "/checkout/sessions", form=form)
