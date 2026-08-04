// anchor-output.js — anchor an AI agent's output from the browser.
//
// The text is hashed HERE with WebCrypto (SHA-256 + SHA-512 sibling) and
// only the digests are POSTed to /api/anchor — the output itself never
// leaves the page. Optional C2PA manifest hash rides the existing
// c2pa_manifest_hash field; optional claim rides `attestation`.
// Self-contained, no dependencies (CSP: script-src 'self').
(function () {
  "use strict";

  var PACK_KEY = "orpho_pack_token"; // same key app.js uses

  function $(sel) { return document.querySelector(sel); }

  function hexOf(digest) {
    return Array.prototype.map.call(new Uint8Array(digest), function (b) {
      return b.toString(16).padStart(2, "0");
    }).join("");
  }

  function status(msg, isError) {
    var el = $("#ao-status");
    if (!el) return;
    el.hidden = false;
    el.textContent = msg;
    el.classList.toggle("ao-error", !!isError);
  }

  function packToken() {
    try { return localStorage.getItem(PACK_KEY) || ""; } catch (e) { return ""; }
  }

  var textEl = $("#ao-text");
  var countEl = $("#ao-count");
  if (textEl && countEl) {
    textEl.addEventListener("input", function () {
      countEl.textContent = String(textEl.value.length);
    });
  }

  var form = $("#ao-form");
  if (!form) return;
  form.addEventListener("submit", function (ev) {
    ev.preventDefault();
    var text = textEl ? textEl.value : "";
    if (!text) return;

    var c2pa = ($("#ao-c2pa") ? $("#ao-c2pa").value : "").trim().toLowerCase();
    if (c2pa && !/^[0-9a-f]{64}$/.test(c2pa)) {
      status("C2PA manifest hash must be 64 hex characters (a SHA-256), or leave it empty.", true);
      return;
    }

    var btn = $("#ao-submit");
    if (btn) btn.disabled = true;
    status("Fingerprinting in your browser…", false);

    var data = new TextEncoder().encode(text);
    Promise.all([
      crypto.subtle.digest("SHA-256", data),
      crypto.subtle.digest("SHA-512", data),
    ]).then(function (digests) {
      var body = {
        hash_hex: hexOf(digests[0]),
        sha512_hex: hexOf(digests[1]),
        client_label: ($("#ao-label") ? $("#ao-label").value : "").trim().slice(0, 120),
      };
      if (c2pa) body.c2pa_manifest_hash = c2pa;
      var claim = ($("#ao-claim") ? $("#ao-claim").value : "").trim();
      if (claim) {
        body.attestation = {
          claim: claim.slice(0, 500),
          signed_at: new Date().toISOString(),
        };
      }
      var headers = { "Content-Type": "application/json" };
      var token = packToken();
      if (token) headers["X-Pack-Token"] = token;

      status("Submitting fingerprint to OpenTimestamps calendars…", false);
      return fetch("/api/anchor", {
        method: "POST",
        headers: headers,
        body: JSON.stringify(body),
      }).then(function (resp) {
        return resp.json().catch(function () { return {}; }).then(function (j) {
          return { ok: resp.ok, statusCode: resp.status, json: j };
        });
      }).then(function (r) {
        if (!r.ok) {
          if (r.statusCode === 429) {
            var sec = r.json.retry_after_seconds || 60;
            status("Rate limit reached. Try again in " + sec + "s, or use a Pack to skip the limit.", true);
          } else {
            status("Anchor failed: " + (r.json.error || ("HTTP " + r.statusCode)), true);
          }
          return;
        }
        var rec = r.json;
        var box = $("#ao-receipt");
        if (box) {
          box.hidden = false;
          $("#ao-r-hash").textContent = rec.hash_hex || body.hash_hex;
          var link = $("#ao-r-link");
          if (link && rec.receipt_id) link.href = "/r/" + encodeURIComponent(rec.receipt_id);
        }
        status("Anchored. " + (rec.calendars_ok || 0) + "/" +
               (rec.calendars_total || 5) + " calendars accepted the fingerprint.", false);
      });
    }).catch(function (e) {
      status("Could not fingerprint locally: " + e, true);
    }).finally(function () {
      if (btn) btn.disabled = false;
    });
  });
})();
