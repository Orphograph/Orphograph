// scroll-depth.js — native, cookieless scroll-depth telemetry.
//
// Answers one question for the funnel: "where do visitors stop scrolling?"
// It reuses the existing first-party beacon (window.orphoEvent, defined in
// event.js) and fires "scroll_25" / "scroll_50" / "scroll_75" / "scroll_100"
// exactly ONCE each per page load, the first time each depth threshold is
// crossed. No new network surface, no CSP change, no cookies.
//
// What this script DOES NOT do (by design — same posture as event.js):
//   - no external script, no CDN, no third-party heatmap
//   - no localStorage, no sessionStorage, no cookies
//   - no per-pixel path recording, no session replay, no fingerprinting
//   - no-op entirely if window.orphoEvent is absent (event.js not loaded)
//
// Each threshold routes through window.orphoEvent(name), so the server stores
// only the same {ts, event, page, ip_trunc} row it stores for every other
// funnel event — see _handle_event / FUNNEL_EVENTS in server/app.py.
(function () {
  "use strict";

  // No beacon available → do nothing at all (script is safe to include on any
  // page; it only activates where event.js has already defined orphoEvent).
  if (typeof window === "undefined" || typeof window.orphoEvent !== "function") {
    return;
  }

  // Idempotent guard: each depth fires at most once per page load. Shared on
  // window so a double-included <script> can't double-count.
  var fired = window.__orphoScroll;
  if (!fired) {
    fired = window.__orphoScroll = { 25: false, 50: false, 75: false, 100: false };
  }

  var THRESHOLDS = [25, 50, 75, 100];

  // Emit with a LITERAL event name per threshold. The names appear verbatim so
  // the server-side recurrence guard (tests/test_funnel_event_whitelist.py,
  // which greps client JS for orphoEvent("<name>") literals) can verify each is
  // whitelisted in FUNNEL_EVENTS. A concatenated name would evade that guard.
  function fire(t) {
    if (fired[t]) return;
    fired[t] = true;
    try {
      if (t === 25) window.orphoEvent("scroll_25");
      else if (t === 50) window.orphoEvent("scroll_50");
      else if (t === 75) window.orphoEvent("scroll_75");
      else if (t === 100) window.orphoEvent("scroll_100");
    } catch (e) {
      // best-effort analytics — never let a beacon failure surface
    }
  }

  function currentDepthPct() {
    var doc = document.documentElement;
    var body = document.body || {};
    var scrollTop = window.pageYOffset || doc.scrollTop || body.scrollTop || 0;
    var viewport = window.innerHeight || doc.clientHeight || 0;
    var full = Math.max(
      doc.scrollHeight || 0,
      body.scrollHeight || 0,
      doc.offsetHeight || 0,
      body.offsetHeight || 0
    );
    // Scrollable distance. When the page fits in the viewport there is nothing
    // to scroll — treat it as fully seen (100%) rather than dividing by zero.
    var scrollable = full - viewport;
    if (scrollable <= 0) return 100;
    var pct = ((scrollTop + viewport) / full) * 100;
    if (pct > 100) pct = 100;
    if (pct < 0) pct = 0;
    return pct;
  }

  function evaluate() {
    var pct = currentDepthPct();
    for (var i = 0; i < THRESHOLDS.length; i++) {
      var t = THRESHOLDS[i];
      if (!fired[t] && pct >= t) fire(t);
    }
    // All four fired → stop listening; nothing left to observe.
    if (fired[25] && fired[50] && fired[75] && fired[100]) {
      detach();
    }
  }

  // Throttle: coalesce a burst of scroll events into one measurement per frame
  // (requestAnimationFrame), falling back to a ~250ms timer where rAF is absent.
  var scheduled = false;
  function onScroll() {
    if (scheduled) return;
    scheduled = true;
    var run = function () {
      scheduled = false;
      evaluate();
    };
    if (typeof window.requestAnimationFrame === "function") {
      window.requestAnimationFrame(run);
    } else {
      setTimeout(run, 250);
    }
  }

  function detach() {
    window.removeEventListener("scroll", onScroll);
    window.removeEventListener("resize", onScroll);
  }

  // Passive listeners: never block scrolling.
  window.addEventListener("scroll", onScroll, { passive: true });
  window.addEventListener("resize", onScroll, { passive: true });

  // Evaluate once on load: short pages (nothing to scroll) fire scroll_100
  // immediately; a page already scrolled by an anchor/deep-link is measured too.
  evaluate();
})();
