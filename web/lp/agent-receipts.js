// agent-receipts.js — the demand instrument for /lp/agent-receipts.
//
// Why this file exists. The page shipped 2026-07-16 with four CTAs, all of
// them pointing at /integrations (documentation). It had no form, no input,
// no mailto and no checkout link, so it could not record interest in either
// direction: `demand_instrument_check` reported NO INSTRUMENT for 33 days
// and the page returned UNKNOWN forever. UNKNOWN is not the same as
// no-demand, which is exactly why the absence mattered.
//
// It posts to the existing /api/waitlist endpoint (rate-limited per IP,
// body-size capped, email-validated server-side, and deliberately neutral in
// its response so it cannot be used to enumerate addresses). The `interest`
// value is "agent_receipts", which is registered in waitlist.ALLOWED_INTERESTS
// — an unregistered value is silently rewritten to "other", which would make
// this page's leads indistinguishable from every other source.
//
// Pattern mirrors web/checkout-cta.js's notify forms rather than inventing a
// second one. No inline script: the site's CSP forbids it.
(function () {
  "use strict";

  // Mirrors server/app.py EMAIL_RE exactly.
  var EMAIL_RE = /^[^@\s,]{1,64}@[^@\s,]{1,255}$/;

  var form = document.getElementById("lp-notify-form");
  if (!form) return;

  // Reveal the form only now that the script is running. It ships hidden so a
  // JS-less browser cannot submit it: with no action/method a submit is a GET
  // to this page, which would put the address in the URL and in the server
  // log. Same hidden-then-reveal contract as the notify forms in pricing.html.
  form.hidden = false;

  var input = document.getElementById("lp-notify-email");
  var btn = document.getElementById("lp-notify-submit");
  var msg = document.getElementById("lp-notify-msg");
  var done = document.getElementById("lp-notify-done");

  function say(text) { if (msg) msg.textContent = text; }

  // Funnel visibility. lp-cta.js only fires for `a.cta-btn`, so a <button>
  // submit matches nothing and this instrument would otherwise be invisible
  // to /api/founder/funnel. FAILURES are tracked too: a submit that never
  // lands is the one event a demand meter must not silently drop.
  function track(name) {
    try {
      if (window.orphoEvent) window.orphoEvent(name);
    } catch (e) { /* analytics must never break the capture */ }
  }

  form.addEventListener("submit", function (ev) {
    ev.preventDefault();
    var email = input ? input.value.trim() : "";
    // This MUST mirror the server's EMAIL_RE. /api/waitlist answers a neutral
    // 200 for an address it rejects -- deliberately, so it cannot be used to
    // enumerate -- and does NOT store it. A looser client check therefore
    // shows "On the list" to someone who was never added, and the instrument
    // silently under-counts the very signal it exists to measure. An
    // under-count reads as no-demand, which is the specific wrong conclusion
    // this whole page was built to avoid.
    if (!EMAIL_RE.test(email)) {
      say("That does not look like an email address we can write to.");
      return;
    }
    if (btn) btn.disabled = true;
    say("Adding you…");
    fetch("/api/waitlist", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "same-origin",
      body: JSON.stringify({ email: email, interest: "agent_receipts" })
    }).then(function (r) {
      if (r.ok) {
        track("lp_notify_submit");
        form.hidden = true;
        if (done) done.hidden = false;
        say("");
      } else if (r.status === 429) {
        track("lp_notify_error");
        say("Too many attempts from this address. Try again shortly.");
        if (btn) btn.disabled = false;
      } else {
        say("That did not go through. Try again in a moment.");
        if (btn) btn.disabled = false;
      }
    }).catch(function () {
      track("lp_notify_error");
      say("Network error. Try again in a moment.");
      if (btn) btn.disabled = false;
    });
  });
})();
