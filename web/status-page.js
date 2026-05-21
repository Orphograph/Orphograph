// status-page.js — live in-browser probe for /status.html.
// Fetches /api/health once on page load; populates the status-grid pill,
// the HTTP detail, and the round-trip-time field.
// CSP allows only `script-src 'self'`; this file is loaded with
// `<script src="/status-page.js" defer>`.
(function () {
  "use strict";
  var t0 = performance.now();
  var pill = document.getElementById("status-pill");
  var detail = document.getElementById("status-detail");
  var rtt = document.getElementById("status-rtt");
  if (!pill || !detail || !rtt) return;

  fetch("/api/health", { method: "GET", cache: "no-store" })
    .then(function (r) {
      var dt = (performance.now() - t0).toFixed(0);
      if (r.status === 200) {
        pill.className = "status-pill ok";
        pill.textContent = "reachable";
        detail.textContent = "HTTP 200";
      } else {
        pill.className = "status-pill fail";
        pill.textContent = "degraded";
        detail.textContent = "HTTP " + r.status;
      }
      rtt.textContent = dt + " ms";
    })
    .catch(function () {
      pill.className = "status-pill fail";
      pill.textContent = "unreachable";
      detail.textContent = "no response";
      rtt.textContent = "—";
    });
})();
