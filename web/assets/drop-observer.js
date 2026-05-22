// drop-observer.js — fires drop_zone_visible once when #drop scrolls in.
// Loaded as a self-hosted <script src> to satisfy CSP `script-src 'self'`.
(function () {
  if (typeof window === "undefined" || typeof IntersectionObserver === "undefined") return;
  var el = document.getElementById("drop");
  if (!el) return;
  var fired = false;
  var io = new IntersectionObserver(function (entries) {
    entries.forEach(function (entry) {
      if (entry.isIntersecting && !fired) {
        fired = true;
        io.disconnect();
        if (typeof window.orphoEvent === "function") {
          try { window.orphoEvent("drop_zone_visible"); } catch (e) {}
        }
      }
    });
  }, { threshold: 0.25 });
  io.observe(el);
})();
