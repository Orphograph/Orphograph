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
import time
import urllib.error
import urllib.parse
import urllib.request

STRIPE_SECRET_KEY = os.environ.get("STRIPE_SECRET_KEY", "")
HTTP_TIMEOUT = 10
STRIPE_BASE = "https://api.stripe.com/v1"


def is_configured() -> bool:
    return bool(STRIPE_SECRET_KEY)


# charges_enabled() cache — /api/config is hit on every homepage load, so the
# account lookup must not become a per-request Stripe round-trip. Stale-if-error:
# a transient API failure serves the last known answer instead of flapping the
# card buttons.
_ACCOUNT_CACHE: dict = {"ts": 0.0, "enabled": None}
ACCOUNT_CACHE_TTL_SEC = 600


def charges_enabled() -> bool | None:
    """Whether the Stripe account can currently make live charges.

    The account can be fully configured (valid key, prices, links) and still
    unable to charge — details_submitted with charges_enabled=false means
    Stripe is reviewing or restricting the account (observed live 2026-07-09:
    every card checkout died with "Your account cannot currently make live
    charges"). Card CTAs must not render while that is the state.

    Returns True/False from Stripe (cached ACCOUNT_CACHE_TTL_SEC), or None when
    unknown (key unset, or the lookup has never succeeded). Callers should
    treat anything but True as "do not offer card checkout".
    """
    if not STRIPE_SECRET_KEY:
        return None
    now = time.time()
    if _ACCOUNT_CACHE["enabled"] is not None and now - _ACCOUNT_CACHE["ts"] < ACCOUNT_CACHE_TTL_SEC:
        return _ACCOUNT_CACHE["enabled"]
    res = _request("GET", "/account")
    if res.get("ok"):
        _ACCOUNT_CACHE["ts"] = now
        _ACCOUNT_CACHE["enabled"] = bool((res.get("data") or {}).get("charges_enabled"))
    return _ACCOUNT_CACHE["enabled"]


def _categorize_http_error(code: int) -> tuple[str, bool, bool]:
    """Map Stripe HTTP status → (category, retryable, operator_alert).

    Categories let callers map upstream Stripe errors to specific customer
    messages instead of a generic 502.

    - auth_error    → 401/403: STRIPE_SECRET_KEY rotated / revoked. STOP THE LINE.
    - card_declined → 402: actionable by the buyer (retry with different card)
    - invalid_request → 400: our bug (bad price ID, missing param). Log + 502.
    - rate_limited  → 429: bounce; client may retry with Retry-After
    - stripe_outage → 5xx: Stripe is having issues. Retryable. Surface friendly msg.
    - http_error    → anything else 4xx
    """
    if code in (401, 403):
        return ("auth_error", False, True)
    if code == 402:
        return ("card_declined", False, False)
    if code == 429:
        return ("rate_limited", True, False)
    if 500 <= code < 600:
        return ("stripe_outage", True, False)
    if code == 400:
        return ("invalid_request", False, False)
    if 400 <= code < 500:
        return ("http_error", False, False)
    return ("http_error", False, False)


def _request(method: str, path: str, form: dict | None = None) -> dict:
    if not STRIPE_SECRET_KEY:
        return {
            "ok": False,
            "category": "not_configured",
            "error": "Stripe API not configured (STRIPE_SECRET_KEY unset)",
        }
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
        try:
            err = json.loads(body)
            stripe_msg = err.get("error", {}).get("message") or ""
            stripe_code = err.get("error", {}).get("code") or ""
            decline_code = err.get("error", {}).get("decline_code") or ""
        except (json.JSONDecodeError, ValueError):
            stripe_msg = ""
            stripe_code = ""
            decline_code = ""
        category, retryable, operator_alert = _categorize_http_error(e.code)
        # Auth errors page the operator — STRIPE_SECRET_KEY may be rotated/revoked.
        if operator_alert:
            sys.stderr.write(
                f"[stripe_api] ALERT: auth failure ({e.code}) — STRIPE_SECRET_KEY may be invalid. path={path}\n"
            )
        else:
            sys.stderr.write(
                f"[stripe_api] HTTP {e.code} ({category}) path={path} body={body[:200]}\n"
            )
        # Customer-facing message — never leak our internal Stripe error verbatim
        # for auth/server errors. Card-declined we DO want the buyer to see.
        if category == "auth_error":
            customer_msg = "Payment system misconfigured. We've been notified."
        elif category == "card_declined":
            customer_msg = stripe_msg or "Your card was declined. Try a different card."
        elif category == "rate_limited":
            customer_msg = "Too many requests. Try again in a minute."
        elif category == "stripe_outage":
            customer_msg = "Stripe is having issues — try again in a minute, or pay via Bitcoin."
        elif category == "invalid_request":
            customer_msg = stripe_msg or "Request rejected by Stripe (invalid parameters)."
        else:
            customer_msg = stripe_msg or f"Payment error ({e.code})."
        return {
            "ok": False,
            "category": category,
            "error": customer_msg,
            "status": e.code,
            "retryable": retryable,
            "operator_alert": operator_alert,
            "stripe_code": stripe_code,
            "decline_code": decline_code,
        }
    except urllib.error.URLError as e:
        # DNS failure, connection-refused, TLS handshake — Stripe unreachable
        sys.stderr.write(
            f"[stripe_api] URLError path={path} reason={getattr(e, 'reason', e)}\n"
        )
        return {
            "ok": False,
            "category": "network_error",
            "error": "Could not reach payment provider — please retry.",
            "retryable": True,
        }
    except TimeoutError:
        sys.stderr.write(f"[stripe_api] timeout path={path}\n")
        return {
            "ok": False,
            "category": "timeout",
            "error": "Payment provider timed out — please retry.",
            "retryable": True,
        }
    except OSError as e:
        sys.stderr.write(f"[stripe_api] OSError path={path} {type(e).__name__}: {e}\n")
        return {
            "ok": False,
            "category": "network_error",
            "error": "Network error reaching payment provider.",
            "retryable": True,
        }


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
    metadata: dict | None = None,
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
    }
    # Gated automatic-tax: enabled only when STRIPE_AUTOMATIC_TAX=1.
    # Defaults OFF because Stripe Tax must be registered in every buyer
    # jurisdiction; without that, non-US buyers hit tax_calculation_failed
    # and see our generic 502. Enable once tax registrations are in place
    # for the buyer countries you sell into.
    if os.environ.get("STRIPE_AUTOMATIC_TAX", "0") == "1":
        form["automatic_tax[enabled]"] = "true"
        form["billing_address_collection"] = "auto"
    if customer_email:
        form["customer_email"] = customer_email
    if client_reference_id:
        form["client_reference_id"] = client_reference_id
    # Optional checkout-session metadata (e.g. credit_count for multi-size
    # packs). Round-trips to the webhook on data.object.metadata. Only str
    # values; skip empties so we don't send blank keys.
    if metadata:
        for k, v in metadata.items():
            if v is not None and str(v) != "":
                form[f"metadata[{k}]"] = str(v)
    # For one-time Pack purchases, collect a phone+email and persist as
    # a Customer object so the buyer can come back later. For subs this
    # already happens automatically.
    if mode == "payment":
        form["customer_creation"] = "if_required"

    return _request("POST", "/checkout/sessions", form=form)
