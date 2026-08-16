// status-badge.js — the small live/degraded pill in the header nav.
// Fires one fetch against /api/health on page load and flips the badge state.
// Self-contained: no external assets, no third-party libraries, no logging.
//
// The CSP allows only `script-src 'self'` so this file is loaded with
// `<script src="/status-badge.js" defer>` — inline scripts are blocked by
// design.
(function () {
  "use strict";
  var badge = document.getElementById("live-status-badge");
  if (!badge) return;
  var label = badge.querySelector(".label");
  var done = false;

  function set(state, text) {
    if (done) return;
    done = true;
    badge.setAttribute("data-state", state);
    if (label) label.textContent = text;
  }

  // One retry before declaring "degraded". The endpoint answers in ~1.4s
  // (measured 2026-08-16), so a first-attempt failure is far more often a
  // dropped packet, a sleeping radio, or a mid-navigation abort than a real
  // outage — and this badge is a ONE-SHOT: whatever it says on page load
  // stands until reload. Branding the office degraded on one lost round-trip
  // is a false alarm on the exact surface meant to signal trust. A real
  // outage fails both attempts and still shows within ~11s.
  function attempt(remaining) {
    var controller = "AbortController" in window ? new AbortController() : null;
    var timer = setTimeout(function () {
      if (controller) {
        try { controller.abort(); } catch (e) { /* nothing to do */ }
      }
      fail(remaining);
    }, 8000);

    var opts = { cache: "no-store" };
    if (controller) opts.signal = controller.signal;

    fetch("/api/health", opts)
      .then(function (r) {
        clearTimeout(timer);
        if (r && r.status === 200) set("live", "live");
        else fail(remaining);
      })
      .catch(function () {
        clearTimeout(timer);
        fail(remaining);
      });
  }

  function fail(remaining) {
    if (done) return;
    if (remaining > 0) {
      setTimeout(function () { attempt(remaining - 1); }, 1500);
    } else {
      set("degraded", "degraded");
    }
  }

  attempt(1);
})();
