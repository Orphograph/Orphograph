// v2.js — wires the v2 mockup homepage to the live /api endpoints.
// Drop zone: client-side SHA-256 + SHA-512 via WebCrypto, then POST to /api/anchor.
// Stats strip: pulls live counts from /api/stats.
// No external dependencies. All DOM builds via createElement/textContent — no innerHTML.

(function () {
  "use strict";

  const $ = (id) => document.getElementById(id);

  // ── localStorage keys for recent-receipts + anchor-state ──────────
  const RECENT_KEY = "orpho_recent_receipts";
  const STATE_KEY = "orpho_anchor_state";
  const RECENT_MAX = 20;

  // ── Local-time rendering helpers ──────────────────────────────────
  // Server timestamps arrive as ISO-8601 UTC. Browser users see their
  // local time as primary; UTC stays visible underneath so a VPN or
  // skewed system clock is debuggable. Browser timezone is disclosed so
  // the user can notice if it's wrong.
  function _fmtLocal(d) {
    try {
      return new Intl.DateTimeFormat(undefined, {
        year: "numeric", month: "short", day: "2-digit",
        hour: "2-digit", minute: "2-digit", second: "2-digit",
        timeZoneName: "short",
      }).format(d);
    } catch (e) { return d.toString(); }
  }
  function _fmtUtc(d) {
    const pad = (n) => n.toString().padStart(2, "0");
    return d.getUTCFullYear() + "-" + pad(d.getUTCMonth() + 1) + "-" + pad(d.getUTCDate()) + " " +
           pad(d.getUTCHours()) + ":" + pad(d.getUTCMinutes()) + ":" + pad(d.getUTCSeconds()) + " UTC";
  }
  function _detectTz() {
    try { return Intl.DateTimeFormat().resolvedOptions().timeZone || "(unknown)"; }
    catch (e) { return "(unknown)"; }
  }
  function renderTimeInto(node, isoString) {
    if (!node) return;
    while (node.firstChild) node.removeChild(node.firstChild);
    if (!isoString) { node.textContent = "—"; return; }
    const d = new Date(isoString);
    if (isNaN(d.getTime())) { node.textContent = isoString; return; }
    const tz = _detectTz();
    const local = document.createElement("span");
    local.className = "ts-primary";
    local.textContent = _fmtLocal(d);
    const sub = document.createElement("span");
    sub.className = "muted small";
    sub.textContent = " · " + _fmtUtc(d) + " · zone " + tz;
    node.appendChild(local);
    node.appendChild(sub);
  }

  // ── Recent receipts (localStorage) ────────────────────────────────
  function loadRecentReceipts() {
    try {
      const raw = localStorage.getItem(RECENT_KEY);
      if (!raw) return [];
      const list = JSON.parse(raw);
      return Array.isArray(list) ? list : [];
    } catch (e) { return []; }
  }
  function saveRecentReceipt(record) {
    try {
      const list = loadRecentReceipts();
      // Skip if same receipt is already at the head.
      if (list.length && list[0].receipt_id === record.receipt_id) return;
      list.unshift({
        receipt_id: record.receipt_id,
        sha256_prefix: (record.hash_hex || "").slice(0, 12),
        label: record.client_label || "",
        created_at: record.created_at,
        calendars_ok: record.calendars_ok,
        calendars_total: record.calendars_total,
        status: record.status || "pending",
      });
      localStorage.setItem(RECENT_KEY, JSON.stringify(list.slice(0, RECENT_MAX)));
    } catch (e) {}
  }
  function renderRecentReceipts() {
    const host = document.getElementById("recent-receipts");
    const body = document.getElementById("recent-receipts-body");
    if (!host || !body) return;
    const list = loadRecentReceipts();
    if (!list.length) { host.hidden = true; return; }
    host.hidden = false;
    while (body.firstChild) body.removeChild(body.firstChild);
    for (const r of list) {
      const row = document.createElement("a");
      row.className = "recent-row";
      row.href = "/r/" + r.receipt_id;
      const idCell = document.createElement("span");
      idCell.className = "mono recent-id";
      idCell.textContent = r.receipt_id;
      const meta = document.createElement("span");
      meta.className = "muted small recent-meta";
      const d = r.created_at ? new Date(r.created_at) : null;
      const when = (d && !isNaN(d.getTime())) ? _fmtLocal(d) : (r.created_at || "");
      const st = r.status || "pending";
      meta.textContent = when + " · " + st + " · " +
        (r.calendars_ok || 0) + "/" + (r.calendars_total || 5) + " cals" +
        (r.label ? " · " + r.label : "");
      row.appendChild(idCell);
      row.appendChild(meta);
      body.appendChild(row);
    }
  }

  // ── Reload-resilient anchor state ─────────────────────────────────
  // We persist the current anchor phase to localStorage so a mid-flight
  // reload shows the user where they were and what's still happening.
  // Cleared on done or after a hard timeout.
  function saveAnchorState(phase, ctx) {
    try {
      const payload = { phase: phase, ts: Date.now() };
      if (ctx) {
        for (const k in ctx) {
          if (Object.prototype.hasOwnProperty.call(ctx, k)) payload[k] = ctx[k];
        }
      }
      localStorage.setItem(STATE_KEY, JSON.stringify(payload));
    } catch (e) {}
  }
  function loadAnchorState() {
    try {
      const raw = localStorage.getItem(STATE_KEY);
      if (!raw) return null;
      const s = JSON.parse(raw);
      // Stale state >15min old is discarded.
      if (Date.now() - (s.ts || 0) > 15 * 60 * 1000) return null;
      return s;
    } catch (e) { return null; }
  }
  function clearAnchorState() {
    try { localStorage.removeItem(STATE_KEY); } catch (e) {}
  }

  // ── Sticky status banner ──────────────────────────────────────────
  function showStatusBanner(text, kind) {
    const el = document.getElementById("sticky-status");
    if (!el) return;
    el.hidden = false;
    el.dataset.kind = kind || "info";
    el.textContent = text;
  }
  function hideStatusBanner() {
    const el = document.getElementById("sticky-status");
    if (el) el.hidden = true;
  }

  // ── BTC confirmation polling ──────────────────────────────────────
  // After a successful anchor, ping /api/verify/<id> every 90s. When
  // status flips from "pending" to "partial" or "pinned", update the
  // recent-receipts panel and notify via the sticky banner.
  let _pinPollTimer = null;
  function startPinPolling(receiptId) {
    if (_pinPollTimer) clearInterval(_pinPollTimer);
    let attempts = 0;
    const maxAttempts = 80; // ~2 hours at 90s — calendars usually land in 1h.
    _pinPollTimer = setInterval(async () => {
      attempts += 1;
      if (attempts > maxAttempts) {
        clearInterval(_pinPollTimer); _pinPollTimer = null; return;
      }
      try {
        const r = await fetch("/api/verify/" + encodeURIComponent(receiptId));
        if (!r.ok) return;
        const rec = await r.json();
        if (rec.status && rec.status !== "pending") {
          clearInterval(_pinPollTimer); _pinPollTimer = null;
          // Update recent-receipts entry with new status.
          try {
            const list = loadRecentReceipts();
            const idx = list.findIndex((x) => x.receipt_id === receiptId);
            if (idx >= 0) {
              list[idx].status = rec.status;
              localStorage.setItem(RECENT_KEY, JSON.stringify(list));
              renderRecentReceipts();
            }
          } catch (e) {}
          showStatusBanner(
            "Bitcoin confirmation landed for " + receiptId + " — status is now \"" + rec.status + "\".",
            "success"
          );
        }
      } catch (e) {}
    }, 90 * 1000);
  }

  // ── Ops banner (kill-switch surface) ──────────────────────────────
  async function renderOpsBanner() {
    const banner = document.getElementById("ops-banner");
    if (!banner) return;
    try {
      const r = await fetch("/api/config", { credentials: "same-origin" });
      if (!r.ok) return;
      const cfg = await r.json();
      const t = (cfg && cfg.toggles) || {};
      if (t.maintenance_mode) {
        banner.hidden = false;
        banner.dataset.kind = "error";
        banner.textContent = "Maintenance mode is on. New anchors and checkout are paused. Existing receipts continue to verify against Bitcoin without us.";
        return;
      }
      if (t.anchoring_disabled && t.checkout_disabled) {
        banner.hidden = false;
        banner.dataset.kind = "warn";
        banner.textContent = "Anchoring and checkout are paused right now. Existing receipts are unaffected.";
        return;
      }
      if (t.anchoring_disabled) {
        banner.hidden = false;
        banner.dataset.kind = "warn";
        banner.textContent = "Anchoring is paused right now. Existing receipts are unaffected; we'll be back shortly.";
        return;
      }
      if (t.checkout_disabled) {
        banner.hidden = false;
        banner.dataset.kind = "warn";
        banner.textContent = "Checkout is paused right now. Free-tier anchoring still works.";
        return;
      }
      banner.hidden = true;
    } catch (e) {
      // Network failure on /api/config is not user-facing.
    }
  }

  // ── Hydrate in-flight anchor state on page load ───────────────────
  function hydrateAnchorStateOnLoad() {
    const s = loadAnchorState();
    if (!s) return;
    if (s.phase === "hashing") {
      showStatusBanner(
        "A previous anchor of \"" + (s.filename || "your file") + "\" was hashing when this page reloaded. " +
        "The file never leaves your browser, so you'll need to drop it again to resume.",
        "warn"
      );
    } else if (s.phase === "posting") {
      showStatusBanner(
        "A previous anchor was submitting to OpenTimestamps when this page reloaded. " +
        "If the server accepted it, the receipt should appear under \"Recent receipts\" below within a minute.",
        "warn"
      );
    }
    // Clear so we don't re-show stale banners on subsequent navigations.
    clearAnchorState();
  }

  // ── Pack-token storage + tier badge ───────────────────────────────
  // Persisted in localStorage so a Pack code survives reload. Hash
  // fragment (#pack=pk_…) is consumed once on load; query string
  // (?pack=…) is migrated and stripped so the bearer cred isn't logged.
  const PACK_KEY = "orph_pack_token";
  const PACK_RE = /^pk_[A-Za-z0-9_-]+$/;

  function packToken() {
    try { return localStorage.getItem(PACK_KEY) || ""; }
    catch (e) { return ""; }
  }
  function setPackToken(code) {
    try {
      if (code) localStorage.setItem(PACK_KEY, code);
      else localStorage.removeItem(PACK_KEY);
    } catch (e) { /* localStorage disabled — pack will not persist */ }
  }
  function ingestPackFromUrl() {
    let pack = "";
    const hash = (location.hash || "").replace(/^#/, "");
    const hashParams = new URLSearchParams(hash);
    if (hashParams.get("pack")) pack = hashParams.get("pack");
    if (!pack) {
      const qs = new URLSearchParams(location.search);
      if (qs.get("pack")) {
        pack = qs.get("pack");
        qs.delete("pack");
        history.replaceState({}, "", location.pathname +
          (qs.toString() ? "?" + qs.toString() : "") + location.hash);
      }
    } else {
      hashParams.delete("pack");
      const rest = hashParams.toString();
      history.replaceState({}, "", location.pathname + location.search +
        (rest ? "#" + rest : ""));
    }
    if (pack && PACK_RE.test(pack)) setPackToken(pack);
  }

  async function renderTierBadge() {
    const badge = $("tier-badge");
    if (!badge) return;
    const label = badge.querySelector(".tier-badge-label");
    const detail = $("tier-badge-detail");
    const linkBtn = $("tier-badge-link");
    const clearBtn = $("tier-badge-clear");
    const explainer = $("tier-explainer");
    const code = packToken();
    if (code) {
      badge.dataset.tier = "pack";
      if (label) label.textContent = "Pack active";
      if (detail) detail.textContent = "code " + code.slice(0, 8) + "… · credits never expire";
      if (linkBtn) linkBtn.hidden = true;
      if (clearBtn) clearBtn.hidden = false;
      if (explainer) explainer.hidden = true;
      // best-effort balance refresh
      try {
        const r = await fetch("/api/pack/balance/" + encodeURIComponent(code));
        if (r.ok) {
          const j = await r.json();
          const bal = (j && typeof j.balance === "number") ? j.balance : null;
          if (bal != null && detail) {
            detail.textContent = "code " + code.slice(0, 8) + "… · " + bal + " anchors remaining";
          }
        }
      } catch (e) { /* network failure — keep static copy */ }
      return;
    }
    badge.dataset.tier = "free";
    if (label) label.textContent = "Free tier";
    if (detail) detail.textContent = "3 anchors per 24 hours · no payment required";
    if (linkBtn) linkBtn.hidden = false;
    if (clearBtn) clearBtn.hidden = true;
    if (explainer) explainer.hidden = false;
  }

  function wirePackForm() {
    const linkBtn = $("tier-badge-link");
    const clearBtn = $("tier-badge-clear");
    const form = $("pack-form");
    const input = $("pack-form-input");
    const cancel = $("pack-form-cancel");
    const msg = $("pack-form-msg");

    function showMsg(text, kind) {
      if (!msg) return;
      msg.textContent = text;
      msg.dataset.kind = kind || "";
      msg.hidden = !text;
    }
    function openForm() {
      if (!form) return;
      form.hidden = false;
      showMsg("", "");
      if (input) { input.value = ""; input.focus(); }
    }
    function closeForm() {
      if (!form) return;
      form.hidden = true;
      showMsg("", "");
    }

    if (linkBtn) linkBtn.addEventListener("click", (e) => {
      e.preventDefault();
      openForm();
    });
    if (cancel) cancel.addEventListener("click", (e) => {
      e.preventDefault();
      closeForm();
    });
    if (clearBtn) clearBtn.addEventListener("click", (e) => {
      e.preventDefault();
      setPackToken("");
      renderTierBadge();
    });
    if (form) form.addEventListener("submit", async (e) => {
      e.preventDefault();
      const raw = input ? input.value.trim() : "";
      if (!PACK_RE.test(raw)) {
        showMsg("That doesn't look like a Pack code. Codes start with pk_ followed by letters, digits, dashes, or underscores.", "error");
        return;
      }
      showMsg("Checking…", "");
      try {
        const r = await fetch("/api/pack/balance/" + encodeURIComponent(raw));
        if (!r.ok) {
          showMsg("Code not found. Check the email we sent when you bought the Pack.", "error");
          return;
        }
        const j = await r.json();
        const bal = (j && typeof j.balance === "number") ? j.balance : null;
        if (bal == null) {
          showMsg("Could not read the balance for that code.", "error");
          return;
        }
        setPackToken(raw);
        showMsg("Pack applied · " + bal + " anchors remaining.", "success");
        await renderTierBadge();
        // collapse the form after a short pause so the success state is legible
        setTimeout(closeForm, 900);
      } catch (err) {
        showMsg("Network error checking that code. Try again.", "error");
      }
    });
  }

  function fmtNum(n) {
    if (n == null) return "—";
    return Number(n).toLocaleString("en-US");
  }

  // ── Live stats strip ─────────────────────────────────────────────
  fetch("/api/stats")
    .then((r) => r.json())
    .then((j) => {
      // /api/stats nests counts under `anchors` (anchors.total,
      // anchors.last_anchor_at). The old flat keys never matched, so the
      // "most recent receipt" clock silently froze at the hardcoded data-utc.
      const a = j.anchors || {};
      if (a.total != null && $("c-anchors")) {
        $("c-anchors").textContent = fmtNum(a.total);
      }
      if (j.bitcoin_blocks_anchored != null && $("c-blocks")) {
        $("c-blocks").textContent = fmtNum(j.bitcoin_blocks_anchored);
      }
      if (a.last_anchor_at) {
        const tz = $("latest-tz-block");
        if (tz) {
          tz.dataset.utc = a.last_anchor_at;
          while (tz.firstChild) tz.removeChild(tz.firstChild);
          if (window.Orphograph && window.Orphograph.renderTimezones) {
            window.Orphograph.renderTimezones();
          }
        }
      }
    })
    .catch(() => {});

  // ── Drop zone helpers ────────────────────────────────────────────
  const drop = $("drop");
  const input = $("drop-input");
  const btn = $("drop-btn");
  const status = $("status");

  function clearStatus() {
    if (!status) return;
    while (status.firstChild) status.removeChild(status.firstChild);
  }

  function setStatusClass(klass) {
    if (!status) return;
    status.className = "status-line show" + (klass ? " " + klass : "");
  }

  function makeLine(label, value, klass) {
    const div = document.createElement("div");
    if (klass) div.className = klass;
    if (label) {
      const s = document.createElement("strong");
      s.textContent = label;
      div.appendChild(s);
      div.appendChild(document.createTextNode(" "));
    }
    if (value != null) div.appendChild(document.createTextNode(value));
    return div;
  }

  function makeField(text) {
    const d = document.createElement("div");
    d.className = "field";
    d.textContent = text;
    return d;
  }

  function setStatusSimple(strongText, restText, klass) {
    clearStatus();
    setStatusClass(klass);
    if (!status) return;
    const top = document.createElement("div");
    const s = document.createElement("strong");
    s.textContent = strongText;
    top.appendChild(s);
    if (restText) top.appendChild(document.createTextNode(" " + restText));
    status.appendChild(top);
  }

  async function hashFile(file, alg) {
    const buf = await file.arrayBuffer();
    const digest = await crypto.subtle.digest(alg, buf);
    const bytes = new Uint8Array(digest);
    return Array.from(bytes).map((b) => b.toString(16).padStart(2, "0")).join("");
  }

  async function anchorFile(file) {
    if (!file) return;
    if (file.size > 500 * 1024 * 1024) {
      setStatusSimple(
        "File too large.",
        "Maximum 500 MB. The fingerprint is computed locally, but very large files exhaust browser memory.",
        "error"
      );
      return;
    }

    setStatusSimple(
      "Reading file locally.",
      "The file is not being uploaded — only its fingerprint will leave your machine.",
      ""
    );
    saveAnchorState("hashing", { filename: file.name, size: file.size });
    showStatusBanner("Hashing " + file.name + " locally…", "info");

    let sha256, sha512;
    try {
      [sha256, sha512] = await Promise.all([
        hashFile(file, "SHA-256"),
        hashFile(file, "SHA-512"),
      ]);
    } catch (e) {
      setStatusSimple("Could not read the file.", String(e && e.message ? e.message : e), "error");
      showStatusBanner("Anchor failed: could not read the file.", "error");
      clearAnchorState();
      return;
    }

    setStatusSimple("Fingerprint computed.", "Submitting to five OpenTimestamps calendars…", "");
    status.appendChild(makeField("SHA-256 · " + sha256));
    saveAnchorState("posting", { filename: file.name, sha256_prefix: sha256.slice(0, 12) });
    showStatusBanner("Submitting fingerprint to OpenTimestamps calendars…", "info");

    try {
      const r = await fetch("/api/anchor", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          hash_hex: sha256,
          sha512_hex: sha512,
          client_label: "v2-homepage",
        }),
      });
      if (!r.ok) {
        if (r.status === 429) {
          // Free daily limit hit — this user just showed intent. Offer the paid
          // path instead of a dead error. (Crypto checkout works today.)
          setStatusSimple(
            "You've used today's free anchors.",
            "A Writer Pack is 10 anchors for $19 — credits never expire, and you can pay with crypto in under a minute.",
            "info"
          );
          const cta = document.createElement("a");
          cta.href = "/pay/crypto.html?plan=writer_pack";
          cta.className = "cta";
          cta.textContent = "Get a Writer Pack →";
          cta.style.display = "inline-block";
          cta.style.marginTop = "10px";
          status.appendChild(cta);
          showStatusBanner("Free limit reached — a Writer Pack removes it.", "info");
          clearAnchorState();
          return;
        }
        const txt = await r.text();
        setStatusSimple(
          "The office could not record this submission.",
          "Server returned " + r.status + ". " + txt.slice(0, 200),
          "error"
        );
        showStatusBanner("Anchor failed: server returned " + r.status + ".", "error");
        clearAnchorState();
        return;
      }
      const j = await r.json();
      const ok = j.calendars_ok || 0;
      const total = j.calendars_total || 5;

      setStatusSimple(
        "Instrument issued.",
        "Calendars attesting: " + ok + " / " + total + ".",
        "success"
      );
      if (typeof window !== "undefined" && typeof window.orphoEvent === "function") {
        try { window.orphoEvent("file_anchored"); } catch (e) {}
      }
      status.appendChild(makeField("Receipt · " + j.receipt_id));
      status.appendChild(makeField("SHA-256 · " + sha256));

      const p = document.createElement("p");
      p.style.margin = "10px 0 0";
      p.style.fontSize = "14px";
      const a = document.createElement("a");
      a.href = "/r/" + encodeURIComponent(j.receipt_id);
      a.style.color = "var(--accent)";
      a.textContent = "View receipt → /r/" + j.receipt_id;
      p.appendChild(a);
      p.appendChild(document.createTextNode(
        "  ·  Bitcoin confirmation arrives within ~1 hour."
      ));
      status.appendChild(p);

      // Optional: render created_at in local time if the server provided it.
      if (j.created_at) {
        const tnode = document.createElement("div");
        tnode.className = "field";
        renderTimeInto(tnode, j.created_at);
        status.appendChild(tnode);
      }

      // Persist + surface the receipt so the user has a reload-safe trail.
      const recordForStorage = {
        receipt_id: j.receipt_id,
        hash_hex: sha256,
        client_label: "v2-homepage",
        created_at: j.created_at,
        calendars_ok: ok,
        calendars_total: total,
        status: j.status || "pending",
      };
      saveRecentReceipt(recordForStorage);
      renderRecentReceipts();
      clearAnchorState();
      showStatusBanner(
        "Receipt issued. Watching for Bitcoin confirmation…",
        "success"
      );
      startPinPolling(j.receipt_id);
    } catch (e) {
      setStatusSimple("Network error.", String(e && e.message ? e.message : e), "error");
      showStatusBanner("Anchor failed: " + String(e && e.message ? e.message : e), "error");
      clearAnchorState();
    }
  }

  if (drop) {
    drop.addEventListener("dragover", (e) => {
      e.preventDefault();
      drop.classList.add("dragover");
    });
    drop.addEventListener("dragleave", () => drop.classList.remove("dragover"));
    drop.addEventListener("drop", (e) => {
      e.preventDefault();
      drop.classList.remove("dragover");
      if (e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files[0]) {
        anchorFile(e.dataTransfer.files[0]);
      }
    });
  }
  if (btn) {
    btn.addEventListener("click", (e) => {
      e.preventDefault();
      e.stopPropagation();
      input.click();
    });
  }
  if (input) {
    input.addEventListener("change", () => {
      if (input.files && input.files[0]) anchorFile(input.files[0]);
    });
  }

  // ── Stripe Checkout buttons (data-checkout="pack" | "pro") ─────────
  // Click handler hits /api/stripe/checkout, gets a hosted Checkout URL,
  // then redirects the browser to it. Stripe handles card entry and on
  // success fires the webhook that mints the Pack code / activates sub.
  // Resolve the .checkout-error slot for a given button by walking up to
  // the enclosing .tier card. Returns null if the markup ever changes
  // and the slot is missing — callers degrade silently in that case.
  function findCheckoutErrorSlot(button) {
    if (!button || typeof button.closest !== "function") return null;
    const card = button.closest(".tier");
    if (!card) return null;
    return card.querySelector(".checkout-error");
  }

  // Render an inline failure inside the tier card. Two affordances:
  // "Try again" re-fires startCheckout for the same plan/button, and
  // "Pay with crypto instead" routes to the existing /pay/crypto.html
  // flow that's already wired on the same card. No alert() — the prior
  // implementation blocked the page and offered no recovery path.
  function showCheckoutError(button, plan, message) {
    const slot = findCheckoutErrorSlot(button);
    if (!slot) return;
    // Clear any prior contents before re-rendering — keeps the slot
    // idempotent across repeated failures.
    while (slot.firstChild) slot.removeChild(slot.firstChild);

    const p = document.createElement("p");
    p.textContent = message;
    slot.appendChild(p);

    const actions = document.createElement("div");
    actions.className = "checkout-error-actions";

    const retry = document.createElement("button");
    retry.type = "button";
    retry.textContent = "Try again";
    retry.addEventListener("click", function (ev) {
      ev.preventDefault();
      // Hide the error row on retry so the next attempt starts clean.
      slot.hidden = true;
      while (slot.firstChild) slot.removeChild(slot.firstChild);
      startCheckout(plan, button);
    });
    actions.appendChild(retry);

    const crypto = document.createElement("a");
    crypto.href = "/pay/crypto.html";
    crypto.textContent = "Pay with crypto instead →";
    actions.appendChild(crypto);

    slot.appendChild(actions);
    slot.hidden = false;

    // Best-effort analytics ping. The orphoEvent hook is owned by a
    // sibling task; if absent we no-op rather than throw.
    try {
      if (typeof window !== "undefined" && typeof window.orphoEvent === "function") {
        window.orphoEvent("checkout_error", { plan: plan, message: message });
      }
    } catch (e) {}
  }

  async function startCheckout(plan, button) {
    const originalLabel = button.textContent;
    button.textContent = "Loading…";
    button.style.pointerEvents = "none";
    // Clear any prior error before the new attempt so the UI doesn't
    // show a stale failure under the spinner.
    const priorSlot = findCheckoutErrorSlot(button);
    if (priorSlot) {
      priorSlot.hidden = true;
      while (priorSlot.firstChild) priorSlot.removeChild(priorSlot.firstChild);
    }
    try {
      if (typeof window !== "undefined" && typeof window.orphoEvent === "function") {
        try { window.orphoEvent("checkout_clicked", { plan: plan }); } catch (e) {}
      }
      const r = await fetch("/api/stripe/checkout", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ plan }),
      });
      if (!r.ok) {
        const body = await r.text();
        let msg = "Checkout temporarily unavailable.";
        try {
          const j = JSON.parse(body);
          if (j && j.error) msg = j.error;
        } catch (e) {}
        button.textContent = originalLabel;
        button.style.pointerEvents = "";
        showCheckoutError(button, plan, msg);
        return;
      }
      const j = await r.json();
      if (j && j.url) {
        window.location.href = j.url;
        return;
      }
      button.textContent = originalLabel;
      button.style.pointerEvents = "";
      showCheckoutError(button, plan, "Checkout response missing url.");
    } catch (e) {
      button.textContent = originalLabel;
      button.style.pointerEvents = "";
      showCheckoutError(
        button,
        plan,
        "Network error opening checkout: " + (e && e.message ? e.message : e)
      );
    }
  }
  document.querySelectorAll("[data-checkout]").forEach((btn) => {
    btn.addEventListener("click", (e) => {
      e.preventDefault();
      const plan = btn.dataset.checkout;
      if (plan === "pack" || plan === "pro") startCheckout(plan, btn);
    });
  });

  // ── Tier badge init ────────────────────────────────────────────
  ingestPackFromUrl();
  renderTierBadge();
  wirePackForm();

  // ── Recent receipts, ops banner, anchor-state hydration ─────────
  renderRecentReceipts();
  renderOpsBanner();
  hydrateAnchorStateOnLoad();

  // Expose hideStatusBanner so a future UI affordance (e.g. dismiss
  // button on #sticky-status) can clear the banner. Touching window
  // here keeps the IIFE encapsulated otherwise.
  if (typeof window !== "undefined") {
    window.Orphograph = window.Orphograph || {};
    window.Orphograph.hideStatusBanner = hideStatusBanner;
  }
})();
