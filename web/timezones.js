// timezones.js — render a UTC timestamp across UTC, the viewer's local zone,
// a small featured set, and (on demand) every IANA zone the browser knows.
// No dependencies. Stdlib JS only. Used by mockups and (later) /receipt.

(function () {
  "use strict";

  const FEATURED = [
    "UTC",
    "America/New_York",
    "America/Chicago",
    "America/Los_Angeles",
    "America/Puerto_Rico",
    "America/Sao_Paulo",
    "Europe/London",
    "Europe/Paris",
    "Europe/Berlin",
    "Africa/Lagos",
    "Asia/Dubai",
    "Asia/Kolkata",
    "Asia/Shanghai",
    "Asia/Tokyo",
    "Australia/Sydney",
  ];

  function allZones() {
    try {
      if (typeof Intl.supportedValuesOf === "function") {
        return Intl.supportedValuesOf("timeZone");
      }
    } catch (e) {}
    return FEATURED;
  }

  function viewerZone() {
    try {
      return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
    } catch (e) {
      return "UTC";
    }
  }

  function fmt(d, tz) {
    try {
      const f = new Intl.DateTimeFormat("en-CA", {
        timeZone: tz,
        year: "numeric", month: "2-digit", day: "2-digit",
        hour: "2-digit", minute: "2-digit", second: "2-digit",
        hour12: false, timeZoneName: "short",
      });
      // en-CA gives "2026-05-16, 11:34:00 AST" — normalize to one line.
      return f.format(d).replace(",", "");
    } catch (e) {
      return d.toISOString();
    }
  }

  function row(zone, value, klass) {
    const li = document.createElement("li");
    li.className = "tz-row" + (klass ? " " + klass : "");
    const z = document.createElement("span");
    z.className = "tz-zone";
    z.textContent = zone;
    const v = document.createElement("span");
    v.className = "tz-val";
    v.textContent = value;
    li.appendChild(z);
    li.appendChild(v);
    return li;
  }

  function render(container) {
    const utcIso = container.dataset.utc;
    if (!utcIso) return;
    const d = new Date(utcIso);
    if (isNaN(d.getTime())) {
      container.textContent = utcIso;
      return;
    }

    const local = viewerZone();

    // Primary row: UTC + viewer-local side by side
    const primary = document.createElement("ul");
    primary.className = "tz-primary";
    primary.appendChild(row("UTC", fmt(d, "UTC"), "tz-canonical"));
    if (local !== "UTC") {
      primary.appendChild(row(local + " (your local)", fmt(d, local), "tz-local"));
    }
    container.appendChild(primary);

    // Featured (collapsed) — toggle
    const featuredDetails = document.createElement("details");
    featuredDetails.className = "tz-featured-wrap";
    const fs = document.createElement("summary");
    fs.textContent = "Show major time zones";
    featuredDetails.appendChild(fs);
    const featuredList = document.createElement("ul");
    featuredList.className = "tz-featured";
    FEATURED.forEach((z) => {
      if (z === "UTC" || z === local) return;
      featuredList.appendChild(row(z, fmt(d, z)));
    });
    featuredDetails.appendChild(featuredList);
    container.appendChild(featuredDetails);

    // All zones (collapsed, with filter)
    const allDetails = document.createElement("details");
    allDetails.className = "tz-all-wrap";
    const as = document.createElement("summary");
    as.textContent = "Show all time zones";
    allDetails.appendChild(as);

    const filter = document.createElement("input");
    filter.type = "search";
    filter.placeholder = "Filter zones (e.g. Tokyo, Paris, Auckland)";
    filter.className = "tz-filter";
    allDetails.appendChild(filter);

    const allList = document.createElement("ul");
    allList.className = "tz-all";
    const zones = allZones().slice();
    zones.sort();
    zones.forEach((z) => {
      const li = row(z, fmt(d, z));
      allList.appendChild(li);
    });
    allDetails.appendChild(allList);

    filter.addEventListener("input", () => {
      const q = filter.value.trim().toLowerCase();
      [].forEach.call(allList.children, (li) => {
        const t = li.firstChild.textContent.toLowerCase();
        li.style.display = !q || t.indexOf(q) !== -1 ? "" : "none";
      });
    });

    container.appendChild(allDetails);
  }

  function init() {
    document.querySelectorAll("[data-utc]").forEach(render);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }

  window.Orphograph = window.Orphograph || {};
  window.Orphograph.renderTimezones = init;
})();
