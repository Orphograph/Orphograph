// event.js — privacy-preserving, cookieless funnel tracker.
//
// Exposes window.orphoEvent(name) which POSTs {event, page} to /api/event.
// Uses sendBeacon (fire-and-forget, survives unload) with a fetch fallback
// for browsers without it. Body is always a fixed two-field JSON object;
// the server rejects any extra keys with 400.
//
// What this script DOES NOT do (by design):
//   - no localStorage, no sessionStorage, no cookies
//   - no User-Agent, screen size, timezone, language sniffing
//   - no canvas / WebGL / font fingerprinting
//   - no retries (failed pings are dropped on the floor)
//   - no batching (each event is a single request)
//
// The server stores only: {ts, event, page, ip_trunc} — see _handle_event
// in server/app.py.
(function () {
  "use strict";
  var ENDPOINT = "/api/event";
  function send(name) {
    if (typeof name !== "string" || !name) return;
    var body = JSON.stringify({ event: name, page: location.pathname || "/" });
    try {
      if (navigator && typeof navigator.sendBeacon === "function") {
        // Blob with JSON MIME so the server's content-type sniff works.
        var blob = new Blob([body], { type: "application/json" });
        if (navigator.sendBeacon(ENDPOINT, blob)) return;
      }
    } catch (e) { /* fall through to fetch */ }
    try {
      fetch(ENDPOINT, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: body,
        keepalive: true,
        credentials: "omit",
        mode: "same-origin",
      }).catch(function () {});
    } catch (e) { /* give up — analytics is best-effort */ }
  }
  window.orphoEvent = send;
})();
