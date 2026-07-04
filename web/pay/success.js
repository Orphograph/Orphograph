// success.js — externalized from success.html (strict CSP: no inline scripts).
  // Show the order reference from the NOWPayments success_url (?order=...).
  // Safe DOM: textContent only, sanitized to the order-id charset.
  (function () {
    var clean = "";
    try {
      var raw = new URLSearchParams(location.search).get("order") || "";
      clean = raw.replace(/[^A-Za-z0-9_-]/g, "").slice(0, 64);
      if (clean) {
        document.getElementById("ok-order-id").textContent = clean;
        document.getElementById("ok-order").hidden = false;
      }
    } catch (e) { /* no-op */ }

    // Poll the status-only endpoint to see if the order has been credited
    // (i.e. the claim code has been minted + emailed). Status only: the
    // response never carries the code or email. When credited, update the
    // on-page text — but keep the "check your email" guidance, since the
    // code is delivered by email, never shown here. Safe DOM: textContent.
    if (!clean) { return; }
    var tries = 0;
    var MAX_TRIES = 6;        // ~30s total
    var INTERVAL_MS = 5000;   // every 5s

    function markCredited() {
      try {
        var lede = document.getElementById("ok-lede");
        if (lede) {
          lede.textContent =
            "Payment confirmed — your claim code has been emailed. " +
            "It usually arrives within a few minutes (check spam if you " +
            "don't see it).";
        }
      } catch (e) { /* no-op */ }
    }

    function poll() {
      tries += 1;
      fetch("/api/nowpayments/order/" + encodeURIComponent(clean), {
        headers: { "Accept": "application/json" },
      })
        .then(function (r) { return r.ok ? r.json() : null; })
        .then(function (data) {
          if (data && data.credited === true) {
            markCredited();
            return; // stop polling
          }
          if (tries < MAX_TRIES) {
            setTimeout(poll, INTERVAL_MS);
          }
        })
        .catch(function () {
          if (tries < MAX_TRIES) {
            setTimeout(poll, INTERVAL_MS);
          }
        });
    }

    setTimeout(poll, INTERVAL_MS);
  })();
