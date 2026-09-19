// buy.js — post-Stripe-Checkout confirmation for /buy.
//
// WHAT THIS IS, AND WHY THE NAME IS CONFUSING. Until 2026-09-19 this file and
// web/buy.html served TWO URLs on one document:
//
//   /buy?stripe_session=cs_…   the card buyer's confirmation page, which is
//                              what server/app.py builds as Stripe's
//                              success_url, reached from the homepage CTA
//   /buy/<btc_order_id>        a per-order page on the direct on-chain BTC
//                              rail, served by a startswith("/buy/") route
//
// The BTC rail was retired. Only the SECOND of those was ever part of it: the
// `/buy/` prefix answers 410 Gone, and every BTC branch is gone from this
// file — no order polling, no address, no sat amount, no wallet deep link.
//
// Deleting the whole file took the card confirmation with it and left paying
// customers on a 410. Restored deliberately, card-only.
//
// textContent only — strict CSP, no innerHTML.

"use strict";

function $(sel) { return document.querySelector(sel); }

// Stripe appends query strings, never path segments:
//   ?stripe_session=cs_…&status=success   on success
//   ?stripe=canceled                      on cancel
function stripeSessionFromUrl() {
  return new URLSearchParams(location.search).get("stripe_session") || "";
}

function stripeWasCanceled() {
  return new URLSearchParams(location.search).get("stripe") === "canceled";
}

function showError(message) {
  const loading = $("#loading");
  if (loading) loading.hidden = true;
  const err = $("#error");
  if (err) err.hidden = false;
  const msg = $("#error-message");
  if (msg) msg.textContent = message;
}

async function showStripeConfirmation(sessionId) {
  const loading = $("#loading");
  if (loading) loading.hidden = true;
  const settledEl = $("#settled");
  if (!settledEl) return;
  settledEl.hidden = false;

  // THE conversion beacon. /api/founder/funnel derives checkout_to_paid and
  // visible_to_paid from this event, and this is its only emitter — deleting
  // this file silently zeroed both rates. See
  // tests/test_funnel_event_whitelist.py.
  if (typeof window !== "undefined" && typeof window.orphoEvent === "function") {
    try { window.orphoEvent("checkout_returned_success"); } catch (e) { /* no-op */ }
  }

  let mode = "";
  let customerEmail = "";
  let paymentStatus = "";
  try {
    const r = await fetch("/api/stripe/session?id=" + encodeURIComponent(sessionId));
    if (r.ok) {
      const j = await r.json();
      mode = (j && j.mode) || "";
      customerEmail = (j && j.customer_email) || "";
      paymentStatus = (j && j.payment_status) || "";
    }
  } catch (e) { /* fall back to the generic copy below */ }

  const h = settledEl.querySelector("h1, h2");
  const p = settledEl.querySelector("p");
  const a = $("#next-link");

  if (paymentStatus && paymentStatus !== "paid") {
    if (h) h.textContent = "Payment pending.";
    if (p) p.textContent =
      "Stripe reports this session is " + paymentStatus + ". " +
      "If you completed payment, your access will arrive shortly — confirmation is recorded automatically once Stripe reports the charge. Check back in a few minutes.";
    if (a) { a.textContent = ""; a.removeAttribute("href"); }
    return;
  }

  if (mode === "subscription") {
    if (h) h.textContent = "Subscription active.";
    if (p) {
      while (p.firstChild) p.removeChild(p.firstChild);
      p.appendChild(document.createTextNode(
        "Your subscription is active. There is no claim code — sign in with your email to start anchoring on your plan. "
      ));
      if (customerEmail) {
        p.appendChild(document.createTextNode("Use "));
        const code = document.createElement("code");
        code.textContent = customerEmail;
        p.appendChild(code);
        p.appendChild(document.createTextNode(" on the sign-in page. "));
      } else {
        p.appendChild(document.createTextNode(
          "Use the same email you paid with on the sign-in page. "
        ));
      }
      p.appendChild(document.createTextNode(
        "A welcome email is on its way; if you don't see it, check spam, then proceed to sign-in below."
      ));
    }
    if (a) {
      a.textContent = "Sign in to your account →";
      a.href = "/signin";
    }
    return;
  }

  // Default: one-time Pack purchase.
  if (h) h.textContent = "Payment received.";
  if (p) p.textContent =
    "Your Pack code has been emailed. If it doesn't arrive within a few minutes, check spam — then reply to the welcome email or head to your account.";
  if (a) {
    a.textContent = "Open your account →";
    a.href = "/account";
  }
}

function showStripeCanceled() {
  const loading = $("#loading");
  if (loading) loading.hidden = true;
  showError(
    "Checkout was canceled. Your card was not charged. " +
    "Head back to the home page to try again."
  );
}

async function main() {
  if (stripeWasCanceled()) { showStripeCanceled(); return; }
  const ssid = stripeSessionFromUrl();
  if (ssid) { await showStripeConfirmation(ssid); return; }
  // No Stripe parameters at all: someone opened /buy directly. There is no
  // order to look up any more, so say so instead of spinning on "loading…".
  showError("Nothing to confirm here. Pick a plan on the pricing page to buy.");
}

main();
