// widget.js — Orphograph live-verifying badge widget.
//
// Drop-in usage on any third-party page:
//   <script src="https://orphograph.com/widget.js"
//           data-receipt="ZlZXKD4CGxkOzx-T"
//           async></script>
//
// Renders a small, cream-and-serif pill right after the <script> tag.
// The pill shows the seal, the truncated receipt id, and a status —
// PENDING by default, upgraded to VERIFIED if /api/verify/<id> answers
// 200 with a sufficient calendar count. Clicking the pill opens the
// canonical receipt page at /r/<id>.
//
// Defensive design:
//   - Receipt id is matched against /^[A-Za-z0-9_-]{1,64}$/ BEFORE the
//     widget inserts anything. Crafted ids are rejected silently.
//   - All textual content originates from the validated id or static
//     strings; the DOM is built with createElement and textContent.
//     The widget NEVER writes innerHTML.
//   - The verification fetch is best-effort. Any non-200, network
//     failure, or CORS rejection leaves the badge in the PENDING state
//     and the receipt page remains the authoritative instrument.
//   - No cookies, no analytics beacon, no third-party calls.
//   - Stdlib JavaScript only. No framework, no build step.

(function () {
  "use strict";

  var ID_RE = /^[A-Za-z0-9_-]{1,64}$/;
  var DEFAULT_ORIGIN = "https://orphograph.com";

  // ── Locate self ──────────────────────────────────────────────────────
  var thisScript = document.currentScript;
  if (!thisScript) {
    var all = document.getElementsByTagName("script");
    for (var i = all.length - 1; i >= 0; i--) {
      if (all[i].src && all[i].src.indexOf("/widget.js") !== -1) {
        thisScript = all[i];
        break;
      }
    }
  }
  if (!thisScript) return;

  // ── Validate receipt id ──────────────────────────────────────────────
  var receiptId = (thisScript.getAttribute("data-receipt") || "").trim();
  if (!ID_RE.test(receiptId)) {
    if (window.console && window.console.warn) {
      window.console.warn("orphograph widget: missing or invalid data-receipt");
    }
    return;
  }

  // ── Resolve origin from the script src so staging mirrors work ───────
  var origin = DEFAULT_ORIGIN;
  try {
    var srcUrl = new URL(thisScript.src);
    origin = srcUrl.origin;
  } catch (_e) { /* keep default */ }

  var receiptUrl = origin + "/r/" + encodeURIComponent(receiptId);
  var verifyUrl  = origin + "/api/verify/" + encodeURIComponent(receiptId);
  var sealUrl    = origin + "/seal.png";

  // ── Truncate the id for display: first 6 + ellipsis + last 4 ─────────
  function shorten(id) {
    if (id.length <= 12) return id;
    return id.slice(0, 6) + "…" + id.slice(-4);
  }

  // ── Build the DOM ────────────────────────────────────────────────────
  var wrapper = document.createElement("a");
  wrapper.href = receiptUrl;
  wrapper.target = "_blank";
  wrapper.rel = "noopener";
  wrapper.setAttribute("aria-label",
    "Verified on Bitcoin via Orphograph. View the receipt.");
  wrapper.style.cssText = [
    "display: inline-flex",
    "align-items: center",
    "gap: 8px",
    "padding: 6px 12px",
    "border: 1px solid #d9cfb6",
    "border-radius: 999px",
    "background: #fbf7ea",
    "color: #14110d",
    "font: 500 13px/1.3 'EB Garamond', 'Iowan Old Style', Georgia, serif",
    "text-decoration: none",
    "white-space: nowrap",
    "max-width: 100%",
    "vertical-align: middle",
    "box-shadow: 0 1px 2px rgba(60,40,20,0.06)"
  ].join("; ");

  var seal = document.createElement("img");
  seal.src = sealUrl;
  seal.alt = "";
  seal.setAttribute("aria-hidden", "true");
  seal.width = 18;
  seal.height = 18;
  seal.style.cssText = "width:18px;height:18px;flex:0 0 auto;";
  wrapper.appendChild(seal);

  var label = document.createElement("span");
  label.textContent = "Verified on Bitcoin via Orphograph";
  label.style.cssText = "font-family:'EB Garamond',Georgia,serif;";
  wrapper.appendChild(label);

  var sep = document.createElement("span");
  sep.textContent = "·";
  sep.style.cssText = "color:#948a76;";
  wrapper.appendChild(sep);

  var idEl = document.createElement("span");
  idEl.textContent = shorten(receiptId);
  idEl.style.cssText = "font-family:'JetBrains Mono','SF Mono',Menlo,monospace;font-size:11px;color:#6b6354;";
  wrapper.appendChild(idEl);

  var pill = document.createElement("span");
  pill.textContent = "PENDING";
  pill.style.cssText = [
    "margin-left: 4px",
    "padding: 2px 8px",
    "border-radius: 999px",
    "background: #f1ead4",
    "color: #6b6354",
    "font: 600 10px/1 -apple-system, 'Inter', system-ui, sans-serif",
    "letter-spacing: 0.08em",
    "text-transform: uppercase"
  ].join("; ");
  wrapper.appendChild(pill);

  // ── Insert directly after the script tag ─────────────────────────────
  var parent = thisScript.parentNode || document.body;
  if (thisScript.nextSibling) {
    parent.insertBefore(wrapper, thisScript.nextSibling);
  } else {
    parent.appendChild(wrapper);
  }

  // ── Live verification (best-effort, CORS-permitting) ─────────────────
  // If /api/verify returns 200 and reports five calendar attestations,
  // upgrade the pill to VERIFIED. Any error path leaves PENDING in place;
  // the receipt page at /r/<id> remains the authoritative instrument.
  try {
    if (typeof fetch === "function") {
      fetch(verifyUrl, { method: "GET", credentials: "omit", mode: "cors" })
        .then(function (resp) {
          if (!resp || !resp.ok) return null;
          return resp.json();
        })
        .then(function (body) {
          if (!body || typeof body !== "object") return;
          if (body.found !== true) return;
          var ok = typeof body.calendars_ok === "number" ? body.calendars_ok : 0;
          if (ok >= 1) {
            pill.textContent = "VERIFIED";
            pill.style.background = "#e6efe5";
            pill.style.color = "#3a6a4c";
          }
        })
        .catch(function () { /* graceful: leave as PENDING */ });
    }
  } catch (_e) { /* graceful */ }
})();
