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
//
// Card-notify fallback (funnel-hygiene): while card_charges_enabled is FALSE
// the card buttons stay hidden and the per-tier notify forms (#notify-pack,
// #notify-pack50, #notify-personal) are revealed instead — a card-only buyer
// can leave an email and be told when card checkout returns. The forms POST
// to the existing /api/waitlist endpoint with the tier encoded as `interest`.
// The moment the flag flips true, the same apply() pass hides the forms and
// reveals the buttons — one mechanism, no divergence.
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

  function wireNotify(id, tier, show) {
    var form = document.getElementById(id);
    if (!form) return;
    form.hidden = !show;
    if (!show || form.getAttribute("data-wired") === "1") return;
    form.setAttribute("data-wired", "1");
    form.addEventListener("submit", function (ev) {
      ev.preventDefault();
      var input = form.querySelector('input[type="email"]');
      var email = input ? input.value.trim() : "";
      if (!email || email.indexOf("@") < 1) return;
      var btn = form.querySelector("button");
      if (btn) btn.disabled = true;
      fetch("/api/waitlist", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "same-origin",
        body: JSON.stringify({ email: email, interest: tier })
      }).then(function (r) {
        if (r.ok) {
          var row = form.querySelector(".card-notify-row");
          var note = form.querySelector(".card-notify-note");
          var done = form.querySelector(".card-notify-done");
          if (row) row.hidden = true;
          if (note) note.hidden = true;
          if (done) done.hidden = false;
        } else if (btn) {
          btn.disabled = false;
        }
      }).catch(function () {
        if (btn) btn.disabled = false;
      });
    });
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
    // Notify fallback: shown ONLY while card charges are off; auto-hidden by
    // the exact same flag that reveals the card buttons.
    wireNotify("notify-pack", "card_pack", !ok);
    wireNotify("notify-pack50", "card_pack50", !ok);
    wireNotify("notify-personal", "card_personal", !ok);
  }

  fetch("/api/config", { credentials: "same-origin" })
    .then(function (r) { return r.ok ? r.json() : {}; })
    .then(apply)
    .catch(function () { apply({}); });
})();
