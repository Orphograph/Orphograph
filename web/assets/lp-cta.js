// lp-cta.js — records CTA clicks on landing pages via the funnel beacon.
// Depends on event.js (window.orphoEvent). Same privacy posture: the beacon
// carries only {event, page}; which button was clicked is not recorded.
(function () {
  "use strict";
  document.addEventListener("click", function (e) {
    var el = e.target && e.target.closest ? e.target.closest("a.cta-btn") : null;
    if (!el) return;
    if (typeof window.orphoEvent === "function") window.orphoEvent("lp_cta_clicked");
  });
})();
