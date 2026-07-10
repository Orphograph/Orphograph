// checkout-cta.js — light up the homepage CARD CTAs from the live Stripe
// Payment Links exposed by /api/config. The crypto CTAs are static links and
// need no JS; this only reveals the card option when Stripe is configured, and
// hides it otherwise so a returning visitor never sees a dead "Pay with card"
// button. Self-contained, no dependencies (CSP: script-src 'self').
//
// Wires:
//   #buy-pack     -> cfg.stripe.pack_url             (Writer Pack, one-time $19)
//   #buy-pack50   -> cfg.stripe.pack50_url           (Pack of Fifty, one-time $29)
//   #buy-personal -> cfg.stripe.personal_monthly_url (Standing Order, $9/mo)
(function () {
  "use strict";

  function wire(id, url) {
    var a = document.getElementById(id);
    if (!a) return;
    if (url) {
      a.href = url;
      a.target = "_blank";
      a.rel = "noopener";
      a.hidden = false;
    } else {
      // No Stripe URL configured for this SKU → keep the card button hidden;
      // the crypto CTA beside it stays fully functional.
      a.hidden = true;
    }
  }

  function apply(cfg) {
    var s = (cfg && cfg.stripe) || {};
    // A configured URL is not enough: the Stripe ACCOUNT must be able to
    // charge (card_charges_enabled). A restricted account with live links
    // would otherwise collect card details and fail at pay time.
    var ok = s.card_charges_enabled === true;
    wire("buy-pack", ok ? (s.pack_url || "") : "");
    wire("buy-pack50", ok ? (s.pack50_url || "") : "");
    wire("buy-personal", ok ? (s.personal_monthly_url || "") : "");
  }

  fetch("/api/config", { credentials: "same-origin" })
    .then(function (r) { return r.ok ? r.json() : {}; })
    .then(apply)
    .catch(function () { apply({}); });
})();
