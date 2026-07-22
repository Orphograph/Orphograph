// lp-cta.js — records CTA clicks AND a once-per-load page_view on landing
// pages via the funnel beacon. Depends on event.js (window.orphoEvent). Same
// privacy posture: the beacon carries only {event, page}; which button was
// clicked is not recorded. event.js sends page=location.pathname, so a
// page_view fired here self-attributes to the LP's own path (e.g.
// /lp/agent-receipts) — distinct from the homepage's own tracker.
(function () {
  "use strict";
  document.addEventListener("click", function (e) {
    var el = e.target && e.target.closest ? e.target.closest("a.cta-btn") : null;
    if (!el) return;
    if (typeof window.orphoEvent === "function") window.orphoEvent("lp_cta_clicked");
  });
  // Fire exactly one durable page_view per load. The idempotent flag guards
  // against a double-load of this script (e.g. duplicated <script> tags).
  if (!window.__orphoLpPageView) {
    window.__orphoLpPageView = true;
    if (typeof window.orphoEvent === "function") window.orphoEvent("page_view");
  }
})();
