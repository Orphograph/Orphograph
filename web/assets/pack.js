/* pack.js — /pack access surface. External per CSP (script-src 'self').
 *
 * Three tasks, no framework, no inline handlers:
 *   1. Enter a pk_ code -> GET /api/pack/balance/{code} -> show remaining.
 *   2. Anchor a file against the loaded pack -> POST /api/anchor with the
 *      X-Pack-Token header (the production pack-spend path).
 *   3. Recover a lost code by email -> POST /api/pack/recover (neutral reply).
 *
 * The claim code lives only in localStorage under the same key the homepage
 * uses, so a pack loaded here is recognised everywhere the token is read.
 */
(function () {
  "use strict";

  var PACK_KEY = "orpho_pack_token";
  var PK_RE = /^pk_[A-Za-z0-9_-]+$/;

  var $ = function (id) { return document.getElementById(id); };

  // ── element handles ──────────────────────────────────────────────
  var codeForm    = $("pk-code-form");
  var codeInput   = $("pk-code");
  var codeSubmit  = $("pk-code-submit");
  var balanceEl   = $("pk-balance");

  var anchorSec   = $("pk-anchor");
  var drop        = $("pk-drop");
  var fileInput   = $("pk-file");
  var anchorStat  = $("pk-anchor-status");
  var receiptBox  = $("pk-receipt");
  var rName       = $("pk-r-name");
  var rHash       = $("pk-r-hash");
  var rTs         = $("pk-r-ts");
  var rLink       = $("pk-r-link");

  var recForm     = $("pk-recover-form");
  var recEmail    = $("pk-email");
  var recSubmit   = $("pk-recover-submit");
  var recResult   = $("pk-recover-result");

  var busy = false;         // guards concurrent anchors
  var activeBalance = 0;    // remaining anchors for the loaded code

  function packToken() {
    try { return localStorage.getItem(PACK_KEY) || ""; } catch (e) { return ""; }
  }
  function setPackToken(code) {
    try {
      if (code) localStorage.setItem(PACK_KEY, code);
      else localStorage.removeItem(PACK_KEY);
    } catch (e) { /* private mode: token simply won't persist */ }
  }

  function show(el, kind, text) {
    el.className = kind;
    el.textContent = text;
    el.style.display = "block";
  }

  // ── 1. balance lookup ────────────────────────────────────────────
  function lookup(code) {
    show(balanceEl, "wait", "Checking…");
    return fetch("/api/pack/balance/" + encodeURIComponent(code))
      .then(function (r) { return r.json().then(function (j) { return { status: r.status, body: j }; }); })
      .then(function (res) {
        if (res.status === 400) {
          activeBalance = 0;
          anchorSec.hidden = true;
          show(balanceEl, "err", "That doesn't look like a valid pack code. A code begins pk_ followed by letters and numbers.");
          return;
        }
        var bal = (res.body && typeof res.body.balance === "number") ? res.body.balance : 0;
        activeBalance = bal;
        setPackToken(code);
        if (bal > 0) {
          show(balanceEl, "ok", bal + (bal === 1 ? " anchor" : " anchors") + " remaining on this pack. It is stored in this browser.");
          anchorSec.hidden = false;
        } else {
          // balance() returns 0 for both a spent pack and an unknown code —
          // the ledger can't tell them apart, so we say so honestly.
          anchorSec.hidden = true;
          show(balanceEl, "err", "This pack has no anchors remaining, or the code isn't recognised. If you expected credits, recover the code below or buy another pack.");
        }
      })
      .catch(function () {
        show(balanceEl, "err", "Network interruption. Try again in a moment.");
      });
  }

  if (codeForm) {
    codeForm.addEventListener("submit", function (ev) {
      ev.preventDefault();
      var code = (codeInput.value || "").trim();
      if (!PK_RE.test(code)) {
        show(balanceEl, "err", "Enter the code exactly as issued — it begins pk_.");
        return;
      }
      codeSubmit.disabled = true;
      lookup(code).then(function () { codeSubmit.disabled = false; });
    });
  }

  // ── 2. anchor against the pack ───────────────────────────────────
  function hexOf(buf) {
    var out = "", v = new Uint8Array(buf), i;
    for (i = 0; i < v.length; i++) out += v[i].toString(16).padStart(2, "0");
    return out;
  }

  function anchorFile(file) {
    if (busy) return;
    var token = packToken();
    if (!token) { show(anchorStat, "err", "Load a pack code first."); return; }
    if (activeBalance <= 0) { show(anchorStat, "err", "This pack has no anchors left."); return; }
    if (!(window.crypto && crypto.subtle)) {
      show(anchorStat, "err", "This browser can't compute SHA-256 in a secure context.");
      return;
    }
    busy = true;
    receiptBox.hidden = true;
    show(anchorStat, "wait", "Fingerprinting locally…");
    file.arrayBuffer()
      .then(function (buf) {
        return Promise.all([
          crypto.subtle.digest("SHA-256", buf),
          crypto.subtle.digest("SHA-512", buf)
        ]);
      })
      .then(function (digests) {
        var sha256 = hexOf(digests[0]), sha512 = hexOf(digests[1]);
        show(anchorStat, "wait", sha256.slice(0, 16) + "… anchoring via OpenTimestamps…");
        return fetch("/api/anchor", {
          method: "POST",
          headers: { "Content-Type": "application/json", "X-Pack-Token": token },
          body: JSON.stringify({ hash_hex: sha256, sha512_hex: sha512, client_label: "pack-page" })
        }).then(function (r) {
          return r.json().catch(function () { return {}; }).then(function (j) {
            return { status: r.status, body: j, sha256: sha256, name: file.name };
          });
        });
      })
      .then(function (res) {
        busy = false;
        if (res.status === 429) {
          show(anchorStat, "err", "This pack is out of anchors — nothing was charged. Buy another pack to continue.");
          lookup(packToken());
          return;
        }
        if (!res || res.status < 200 || res.status >= 300 || !res.body || !res.body.receipt_id) {
          show(anchorStat, "err", "The anchor couldn't be recorded (server said " + (res ? res.status : "?") + "). Your file never left your device; try again.");
          return;
        }
        if (res.body.pack_consumed === false) {
          // Guard: the pack didn't actually pay for this anchor.
          show(anchorStat, "err", "No credit was drawn from this pack — it may be empty. Re-check the balance above.");
          lookup(packToken());
          return;
        }
        // success
        anchorStat.style.display = "none";
        rName.textContent = res.name;
        rHash.textContent = res.sha256;
        var d = new Date();
        rTs.textContent = "sealed " + d.toISOString().slice(0, 16).replace("T", " ") + " UTC · calendars " +
          (res.body.calendars_ok || 0) + "/" + (res.body.calendars_total || 5);
        if (typeof res.body.pack_remaining === "number") {
          activeBalance = res.body.pack_remaining;
          show(balanceEl, "ok", activeBalance + (activeBalance === 1 ? " anchor" : " anchors") + " remaining on this pack.");
          if (activeBalance <= 0) anchorSec.hidden && (anchorSec.hidden = false);
        }
        if (res.body.receipt_id) {
          rLink.href = "/r/" + encodeURIComponent(res.body.receipt_id);
          rLink.hidden = false;
        }
        receiptBox.hidden = false;
      })
      .catch(function () {
        busy = false;
        show(anchorStat, "err", "Network hiccup — nothing was recorded. Your file never left your device; try again.");
      });
  }

  if (drop) {
    drop.addEventListener("click", function () { fileInput.click(); });
    drop.addEventListener("keydown", function (ev) {
      if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); fileInput.click(); }
    });
    ["dragenter", "dragover"].forEach(function (e) {
      drop.addEventListener(e, function (ev) { ev.preventDefault(); drop.classList.add("drag"); });
    });
    ["dragleave", "drop"].forEach(function (e) {
      drop.addEventListener(e, function (ev) { ev.preventDefault(); drop.classList.remove("drag"); });
    });
    drop.addEventListener("drop", function (ev) {
      if (ev.dataTransfer && ev.dataTransfer.files[0]) anchorFile(ev.dataTransfer.files[0]);
    });
  }
  if (fileInput) {
    fileInput.addEventListener("change", function () {
      if (fileInput.files[0]) { anchorFile(fileInput.files[0]); fileInput.value = ""; }
    });
  }
  // a missed drop outside the zone must not navigate the page to the file
  window.addEventListener("dragover", function (ev) { ev.preventDefault(); });
  window.addEventListener("drop", function (ev) { ev.preventDefault(); });

  // ── 3. lost-code recovery ────────────────────────────────────────
  if (recForm) {
    recForm.addEventListener("submit", function (ev) {
      ev.preventDefault();
      var email = (recEmail.value || "").trim();
      if (!email || email.indexOf("@") < 0) {
        show(recResult, "err", "Enter the email address you bought with.");
        return;
      }
      recSubmit.disabled = true;
      show(recResult, "wait", "Submitting…");
      fetch("/api/pack/recover", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email: email })
      })
        .then(function (r) { return r.json().then(function (j) { return { status: r.status, body: j }; }); })
        .then(function (res) {
          recSubmit.disabled = false;
          if (res.status === 429) {
            show(recResult, "wait", "Too many requests — wait a minute and try once more.");
            return;
          }
          // Neutral by design: the same confirmation regardless of whether a
          // pack exists for this address.
          show(recResult, "ok", (res.body && res.body.message) ||
            "If a pack is associated with that email, we've sent the code(s).");
        })
        .catch(function () {
          recSubmit.disabled = false;
          show(recResult, "err", "Network interruption. Try again in a moment.");
        });
    });
  }

  // ── on load: ingest #pack= / ?pack= and auto-fill, else stored code ─
  function ingestFromUrl() {
    var pack = "";
    var hash = (location.hash || "").replace(/^#/, "");
    var hp = new URLSearchParams(hash);
    if (hp.get("pack")) {
      pack = hp.get("pack");
      hp.delete("pack");
      var rest = hp.toString();
      history.replaceState({}, "", location.pathname + location.search + (rest ? "#" + rest : ""));
    } else {
      var qs = new URLSearchParams(location.search);
      if (qs.get("pack")) {
        pack = qs.get("pack");
        qs.delete("pack");
        history.replaceState({}, "", location.pathname + (qs.toString() ? "?" + qs.toString() : "") + location.hash);
      }
    }
    return pack;
  }

  (function init() {
    var fromUrl = ingestFromUrl();
    var code = (fromUrl && PK_RE.test(fromUrl)) ? fromUrl : packToken();
    if (code && PK_RE.test(code)) {
      codeInput.value = code;
      lookup(code);
    }
  })();
})();
