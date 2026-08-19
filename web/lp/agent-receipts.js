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

  var form = document.getElementById("lp-notify-form");
  if (!form) return;

  var input = document.getElementById("lp-notify-email");
  var btn = document.getElementById("lp-notify-submit");
  var msg = document.getElementById("lp-notify-msg");
  var done = document.getElementById("lp-notify-done");

  function say(text) { if (msg) msg.textContent = text; }

  form.addEventListener("submit", function (ev) {
    ev.preventDefault();
    var email = input ? input.value.trim() : "";
    // Deliberately permissive. The server is the authority on validity and
    // answers neutrally either way; this only catches the obvious typo so a
    // visitor is not left wondering.
    if (!email || email.indexOf("@") < 1) {
      say("That does not look like an email address.");
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
        form.hidden = true;
        if (done) done.hidden = false;
      } else if (r.status === 429) {
        say("Too many attempts from this address. Try again shortly.");
        if (btn) btn.disabled = false;
      } else {
        say("That did not go through. Try again in a moment.");
        if (btn) btn.disabled = false;
      }
    }).catch(function () {
      say("Network error. Try again in a moment.");
      if (btn) btn.disabled = false;
    });
  });
})();
