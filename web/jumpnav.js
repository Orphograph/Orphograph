// jumpnav.js — sticky in-page navigation for the homepage.
//
// Two jobs, both progressive enhancements over plain anchor links:
//   1. Highlight the section currently in view (IntersectionObserver).
//   2. Keep the anchor-scroll offset (--stick-offset) equal to the REAL
//      rendered height of the two stacked sticky bars (header + jump-nav),
//      so a jumped-to heading is never hidden behind them.
//
// Loaded as a self-hosted <script src> so it satisfies the strict CSP
// (script-src 'self'). With JS disabled the nav is still a row of ordinary
// anchor links and the CSS fallback offset keeps headings clear of the bars.
(function () {
  "use strict";
  if (typeof document === "undefined") return;
  var nav = document.getElementById("jumpnav");
  if (!nav) return;
  var header = document.querySelector("header.nav");
  var links = Array.prototype.slice.call(nav.querySelectorAll("a[data-jump]"));
  if (!links.length) return;

  var sections = [];
  var linkFor = {};
  links.forEach(function (a) {
    var href = a.getAttribute("href") || "";
    if (href.charAt(0) !== "#") return;
    var el = document.getElementById(href.slice(1));
    if (!el) return;
    linkFor[el.id] = a;
    sections.push(el);
  });
  if (!sections.length) return;

  // Publish the true combined bar height so the CSS scroll-margin lands
  // exactly, whatever the viewport or wrapping. Falls back to a hardcoded
  // calc() when this script never runs.
  function syncOffset() {
    var h = (header ? header.offsetHeight : 0) + nav.offsetHeight;
    document.documentElement.style.setProperty("--stick-offset", h + "px");
    return h;
  }
  var offset = syncOffset();
  window.addEventListener("resize", function () { offset = syncOffset(); }, { passive: true });

  if (typeof IntersectionObserver !== "function") return; // links still work

  function setActive(id) {
    links.forEach(function (a) {
      var on = linkFor[id] === a;
      if (on) {
        a.classList.add("active");
        a.setAttribute("aria-current", "location");
      } else {
        a.classList.remove("active");
        a.removeAttribute("aria-current");
      }
    });
  }

  var visible = {};
  var io = new IntersectionObserver(function (entries) {
    entries.forEach(function (e) {
      if (e.isIntersecting) visible[e.target.id] = true;
      else delete visible[e.target.id];
    });
    // First section (document order) that is currently intersecting the band
    // just below the sticky bars is the one we are "in".
    for (var i = 0; i < sections.length; i++) {
      if (visible[sections[i].id]) { setActive(sections[i].id); return; }
    }
  }, { rootMargin: "-" + (offset + 1) + "px 0px -55% 0px", threshold: 0 });

  sections.forEach(function (s) { io.observe(s); });
})();
