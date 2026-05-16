// badge.js — embeddable "Anchored to Bitcoin" badge.
//
// Usage on a photographer's portfolio page:
//   <script src="https://orphograph.com/badge.js"
//           data-receipt="abc123def456"
//           async></script>
//
// Renders an inline badge near the script tag, linking to
// https://orphograph.com/r/<id>. Verification happens at the
// destination page; the badge is intentionally cheap (one outbound
// link, no extra fetches) so it doesn't hurt the host page's load
// time or privacy posture.
//
// Defensive design:
//   - Same-origin only when loaded from orphograph.com. From any other
//     origin, the script self-bootstraps without fetching anything.
//   - All output is created via DOM methods (no innerHTML).
//   - No cookies, no analytics beacon, no third-party calls.
//   - Receipt id is validated against [A-Za-z0-9_-]{1,64} before any
//     URL construction.

(function () {
  "use strict";

  // Find the current <script> tag so we can read its data-receipt.
  var thisScript = document.currentScript;
  if (!thisScript) {
    // Fallback for legacy loaders — scan all scripts.
    var all = document.getElementsByTagName("script");
    for (var i = all.length - 1; i >= 0; i--) {
      if (all[i].src && all[i].src.indexOf("/badge.js") !== -1) {
        thisScript = all[i];
        break;
      }
    }
  }
  if (!thisScript) return;

  var receiptId = (thisScript.getAttribute("data-receipt") || "").trim();
  if (!/^[A-Za-z0-9_-]{1,64}$/.test(receiptId)) {
    // Invalid or missing receipt id — render nothing rather than
    // pointing visitors at /r/garbage. Console hint helps the
    // photographer fix it.
    if (window.console && window.console.warn) {
      window.console.warn("orphograph badge: missing or invalid data-receipt attribute");
    }
    return;
  }

  // Derive the orphograph origin from this script's src so the badge
  // works equally well from staging or a fork.
  var origin = "https://orphograph.com";
  try {
    var srcUrl = new URL(thisScript.src);
    origin = srcUrl.origin;
  } catch (_e) { /* fall back to the canonical origin */ }

  var receiptUrl = origin + "/r/" + encodeURIComponent(receiptId);

  // Build the badge with DOM APIs only — no string-templated HTML.
  var wrapper = document.createElement("a");
  wrapper.href = receiptUrl;
  wrapper.target = "_blank";
  wrapper.rel = "noopener";
  wrapper.setAttribute("aria-label",
    "View the Bitcoin-anchored proof-of-existence receipt for this image on Orphograph");

  // Inline styles only — no external stylesheet. Keep this scoped to
  // the wrapper to avoid colliding with the host site's CSS.
  wrapper.style.cssText = [
    "display: inline-flex",
    "align-items: center",
    "gap: 6px",
    "padding: 4px 10px",
    "border: 1px solid rgba(91,220,155,0.35)",
    "border-radius: 999px",
    "background: rgba(91,220,155,0.06)",
    "color: #5bdc9b",
    "font: 500 12px/1.2 -apple-system, BlinkMacSystemFont, 'Inter', 'Helvetica Neue', Arial, sans-serif",
    "text-decoration: none",
    "white-space: nowrap",
    "max-width: 100%",
    "vertical-align: middle"
  ].join("; ");

  // Anchor icon, inline SVG.
  var svgNS = "http://www.w3.org/2000/svg";
  var svg = document.createElementNS(svgNS, "svg");
  svg.setAttribute("viewBox", "0 0 32 32");
  svg.setAttribute("width", "14");
  svg.setAttribute("height", "14");
  svg.setAttribute("aria-hidden", "true");
  var g = document.createElementNS(svgNS, "g");
  g.setAttribute("fill", "none");
  g.setAttribute("stroke", "#5bdc9b");
  g.setAttribute("stroke-width", "2.5");
  g.setAttribute("stroke-linecap", "round");
  g.setAttribute("stroke-linejoin", "round");
  function el(tag, attrs) {
    var node = document.createElementNS(svgNS, tag);
    for (var k in attrs) { if (Object.prototype.hasOwnProperty.call(attrs, k)) node.setAttribute(k, attrs[k]); }
    return node;
  }
  g.appendChild(el("line", { x1: "16", y1: "6", x2: "16", y2: "26" }));
  g.appendChild(el("line", { x1: "11", y1: "11", x2: "21", y2: "11" }));
  var arc = el("path", { d: "M8 19 Q 16 28 24 19" });
  g.appendChild(arc);
  g.appendChild(el("circle", { cx: "16", cy: "6.5", r: "1.5" }));
  svg.appendChild(g);
  wrapper.appendChild(svg);

  var label = document.createElement("span");
  label.textContent = "Anchored to Bitcoin · view receipt";
  wrapper.appendChild(label);

  // Insert the badge right after the script tag so it lands where
  // the photographer pasted the snippet.
  if (thisScript.parentNode) {
    if (thisScript.nextSibling) {
      thisScript.parentNode.insertBefore(wrapper, thisScript.nextSibling);
    } else {
      thisScript.parentNode.appendChild(wrapper);
    }
  } else {
    document.body.appendChild(wrapper);
  }
})();
