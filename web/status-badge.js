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

  var controller = "AbortController" in window ? new AbortController() : null;
  var timer = setTimeout(function () {
    if (controller) {
      try { controller.abort(); } catch (e) { /* nothing to do */ }
    }
    set("degraded", "degraded");
  }, 8000);

  var opts = { cache: "no-store" };
  if (controller) opts.signal = controller.signal;

  fetch("/api/health", opts)
    .then(function (r) {
      clearTimeout(timer);
      if (r && r.status === 200) set("live", "live");
      else set("degraded", "degraded");
    })
    .catch(function () {
      clearTimeout(timer);
      set("degraded", "degraded");
    });
})();
