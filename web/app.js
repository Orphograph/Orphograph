// app.js — client-side hashing + receipt rendering. No file content ever leaves this browser.

const $ = (sel) => document.querySelector(sel);
const PACK_KEY = "orpho_pack_token";
const RECENT_KEY = "orpho_recent_receipts";
const STATE_KEY = "orpho_anchor_state";
const RECENT_MAX = 20;

// ─── Local-time rendering ──────────────────────────────────────────────
// Server timestamps arrive as ISO-8601 UTC. Browser users see their local
// time as the primary read; UTC stays visible underneath so a VPN or skewed
// system clock is debuggable. Browser timezone is disclosed so the user can
// notice if it's wrong.
function _fmtLocal(d) {
  try {
    return new Intl.DateTimeFormat(undefined, {
      year: "numeric", month: "short", day: "2-digit",
      hour: "2-digit", minute: "2-digit", second: "2-digit",
      timeZoneName: "short",
    }).format(d);
  } catch { return d.toString(); }
}
function _fmtUtc(d) {
  const pad = (n) => n.toString().padStart(2, "0");
  return `${d.getUTCFullYear()}-${pad(d.getUTCMonth() + 1)}-${pad(d.getUTCDate())} ` +
         `${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}:${pad(d.getUTCSeconds())} UTC`;
}
function _detectTz() {
  try { return Intl.DateTimeFormat().resolvedOptions().timeZone || "(unknown)"; }
  catch { return "(unknown)"; }
}
function renderTimeInto(node, isoString) {
  if (!node) return;
  node.replaceChildren();
  if (!isoString) { node.textContent = "—"; return; }
  const d = new Date(isoString);
  if (isNaN(d.getTime())) { node.textContent = isoString; return; }
  const tz = _detectTz();
  const local = document.createElement("span");
  local.className = "ts-primary";
  local.textContent = _fmtLocal(d);
  const sub = document.createElement("span");
  sub.className = "muted small";
  sub.textContent = ` · ${_fmtUtc(d)} · zone ${tz}`;
  node.appendChild(local);
  node.appendChild(sub);
}

// ─── Recent receipts (localStorage) ────────────────────────────────────
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
  } catch {}
}
function loadRecentReceipts() {
  try {
    const raw = localStorage.getItem(RECENT_KEY);
    if (!raw) return [];
    const list = JSON.parse(raw);
    return Array.isArray(list) ? list : [];
  } catch { return []; }
}
function renderRecentReceipts() {
  const host = $("#recent-receipts");
  const body = $("#recent-receipts-body");
  if (!host || !body) return;
  const list = loadRecentReceipts();
  if (!list.length) { host.hidden = true; return; }
  host.hidden = false;
  body.replaceChildren();
  for (const r of list) {
    const row = document.createElement("a");
    row.className = "recent-row";
    row.href = `/r/${r.receipt_id}`;
    const idCell = document.createElement("span");
    idCell.className = "mono recent-id";
    idCell.textContent = r.receipt_id;
    const meta = document.createElement("span");
    meta.className = "muted small recent-meta";
    const d = r.created_at ? new Date(r.created_at) : null;
    const when = (d && !isNaN(d.getTime())) ? _fmtLocal(d) : (r.created_at || "");
    const status = r.status || "pending";
    meta.textContent = `${when} · ${status} · ${r.calendars_ok || 0}/${r.calendars_total || 5} cals` +
                       (r.label ? ` · ${r.label}` : "");
    row.appendChild(idCell);
    row.appendChild(meta);
    body.appendChild(row);
  }
}

// ─── Reload-resilient anchor state ─────────────────────────────────────
// We persist the current anchor phase to localStorage so a mid-flight reload
// shows the user where they were and what's still happening. Cleared on done
// or after a hard timeout.
function saveAnchorState(phase, ctx) {
  try {
    localStorage.setItem(STATE_KEY, JSON.stringify({
      phase, ts: Date.now(), ...ctx,
    }));
  } catch {}
}
function loadAnchorState() {
  try {
    const raw = localStorage.getItem(STATE_KEY);
    if (!raw) return null;
    const s = JSON.parse(raw);
    // Stale state >15min old is discarded — page loaded after a closed tab.
    if (Date.now() - (s.ts || 0) > 15 * 60 * 1000) return null;
    return s;
  } catch { return null; }
}
function clearAnchorState() {
  try { localStorage.removeItem(STATE_KEY); } catch {}
}
function showStatusBanner(text, kind) {
  const el = $("#sticky-status");
  if (!el) return;
  el.hidden = false;
  el.dataset.kind = kind || "info";
  el.textContent = text;
}
function hideStatusBanner() {
  const el = $("#sticky-status");
  if (el) el.hidden = true;
}

// ─── BTC confirmation polling ──────────────────────────────────────────
// After a successful anchor, ping /api/verify/<id> every 90s. When status
// flips from "pending" to "partial" or "pinned", update the receipt card
// and the recent-receipts panel.
let _pinPollTimer = null;
function startPinPolling(receiptId) {
  if (_pinPollTimer) clearInterval(_pinPollTimer);
  let attempts = 0;
  const maxAttempts = 80;  // ~2 hours at 90s — calendars usually land in 1h.
  _pinPollTimer = setInterval(async () => {
    attempts += 1;
    if (attempts > maxAttempts) { clearInterval(_pinPollTimer); _pinPollTimer = null; return; }
    try {
      const r = await fetch(`/api/verify/${encodeURIComponent(receiptId)}`);
      if (!r.ok) return;
      const rec = await r.json();
      if (rec.status && rec.status !== "pending") {
        clearInterval(_pinPollTimer); _pinPollTimer = null;
        // Update recent-receipts entry with new status.
        try {
          const list = loadRecentReceipts();
          const idx = list.findIndex(x => x.receipt_id === receiptId);
          if (idx >= 0) {
            list[idx].status = rec.status;
            localStorage.setItem(RECENT_KEY, JSON.stringify(list));
            renderRecentReceipts();
          }
        } catch {}
        showStatusBanner(
          `Bitcoin confirmation landed for ${receiptId} — status is now "${rec.status}".`,
          "success",
        );
      }
    } catch {}
  }, 90 * 1000);
}

function track(event, page) {
  // Fire-and-forget — silent on any failure. Same-origin only.
  try {
    fetch("/api/event", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ event, page: page || "landing" }),
      keepalive: true,
    }).catch(() => {});
  } catch {}
}
const VERIFIER_URL = "/verify/";
// Stripe URLs and pricing are now fetched dynamically from /api/config.
// Founder configures via environment variables (STRIPE_PACK_URL,
// STRIPE_PERSONAL_MONTHLY_URL, etc.) — no code edits needed.
let STRIPE_PACK_URL = "";
let STRIPE_PERSONAL_MONTHLY_URL = "";
let STRIPE_PERSONAL_ANNUAL_URL = "";
let PERSONAL_MONTHLY_USD = 5;
let PERSONAL_ANNUAL_USD = 60;
let ANNUAL_SAVINGS_USD = (PERSONAL_MONTHLY_USD * 12) - PERSONAL_ANNUAL_USD;
let NOWPAYMENTS_ENABLED = false;

async function loadPublicConfig() {
  try {
    const r = await fetch("/api/config", { credentials: "same-origin" });
    if (!r.ok) return;
    const cfg = await r.json();
    if (cfg.stripe) {
      STRIPE_PACK_URL = cfg.stripe.pack_url || "";
      STRIPE_PERSONAL_MONTHLY_URL = cfg.stripe.personal_monthly_url || "";
      STRIPE_PERSONAL_ANNUAL_URL = cfg.stripe.personal_annual_url || "";
    }
    if (cfg.pricing) {
      PERSONAL_MONTHLY_USD = cfg.pricing.personal_monthly_usd ?? PERSONAL_MONTHLY_USD;
      PERSONAL_ANNUAL_USD = cfg.pricing.personal_annual_usd ?? PERSONAL_ANNUAL_USD;
      ANNUAL_SAVINGS_USD = (PERSONAL_MONTHLY_USD * 12) - PERSONAL_ANNUAL_USD;
    }
    if (cfg.features) {
      NOWPAYMENTS_ENABLED = cfg.features.nowpayments_enabled === true;
    }
  } catch {}
}

function wireCryptoPayLink() {
  // Surface every crypto-checkout CTA + its fineprint based on the public
  // feature flag. Multiple link IDs span each pricing tier so buyers on
  // the subscription card see the option side-by-side with the Stripe
  // button (earlier the crypto link was only on the Pack tier — root
  // cause of the 2026-05-18 customer feedback).
  const targets = [
    "#crypto-pay-link",
    "#crypto-pay-link-sub",
    "#crypto-pay-link-wrap",
    "#crypto-pay-fineprint",
    "#crypto-pay-sub-fineprint",
  ];
  for (const sel of targets) {
    const el = document.querySelector(sel);
    if (!el) continue;
    el.hidden = !NOWPAYMENTS_ENABLED;
    if (NOWPAYMENTS_ENABLED && el.tagName === "A") {
      el.href = "/pay/crypto.html";
    }
  }
}

function _hexOf(digest) {
  return [...new Uint8Array(digest)].map(b => b.toString(16).padStart(2, "0")).join("");
}

async function sha256Hex(file) {
  const buf = await file.arrayBuffer();
  return _hexOf(await crypto.subtle.digest("SHA-256", buf));
}

async function dualHash(file) {
  // SHA-256 is what we anchor to Bitcoin via OpenTimestamps.
  // SHA-512 is recorded as a quantum-hedge sibling witness in the receipt:
  // to forge the file→receipt binding, an attacker must collide BOTH hashes.
  const buf = await file.arrayBuffer();
  const [s256, s512] = await Promise.all([
    crypto.subtle.digest("SHA-256", buf),
    crypto.subtle.digest("SHA-512", buf),
  ]);
  return { sha256: _hexOf(s256), sha512: _hexOf(s512) };
}

function setStep(name, state) {
  const el = document.querySelector(`.step[data-step="${name}"]`);
  if (!el) return;
  el.classList.remove("active", "done");
  if (state) el.classList.add(state);
}

function packToken() {
  try { return localStorage.getItem(PACK_KEY) || ""; }
  catch { return ""; }
}

function setPackToken(code) {
  try {
    if (code) localStorage.setItem(PACK_KEY, code);
    else localStorage.removeItem(PACK_KEY);
  } catch { /* localStorage may be disabled; pack will not persist */ }
}

async function refreshPackBanner() {
  const code = packToken();
  const banner = $("#pack-banner");
  const emailRow = $("#email-row");
  if (!code) {
    if (banner) banner.hidden = true;
    if (emailRow) emailRow.hidden = true;
    return;
  }
  if (banner) banner.hidden = false;
  if (emailRow) emailRow.hidden = false;
  try {
    const resp = await fetch(`/api/pack/balance/${encodeURIComponent(code)}`);
    const j = await resp.json();
    const bal = (j && typeof j.balance === "number") ? j.balance : 0;
    $("#pack-balance-text").textContent = `${bal} anchors remaining (code ${code.slice(0, 8)}…)`;
    if (bal <= 0) {
      $("#pack-balance-text").textContent = `Pack empty (code ${code.slice(0, 8)}…) — buy another to keep going.`;
    }
  } catch {
    $("#pack-balance-text").textContent = `Pack active (code ${code.slice(0, 8)}…)`;
  }
}

function ingestPackFromUrl() {
  // Pack tokens arrive via URL fragment (e.g. #pack=pk_xxx) — fragments
  // never reach the server, so the bearer credential cannot be logged.
  // Legacy ?pack= query is also accepted (warned and stripped).
  let pack = "";
  const hash = location.hash.replace(/^#/, "");
  const hashParams = new URLSearchParams(hash);
  if (hashParams.get("pack")) pack = hashParams.get("pack");

  if (!pack) {
    const qs = new URLSearchParams(location.search);
    if (qs.get("pack")) {
      pack = qs.get("pack");
      console.warn("orphograph: ?pack= in the URL was logged by the server. Use the email's #pack= link instead.");
      qs.delete("pack");
      history.replaceState({}, "", location.pathname + (qs.toString() ? "?" + qs.toString() : "") + location.hash);
    }
  } else {
    hashParams.delete("pack");
    const rest = hashParams.toString();
    history.replaceState({}, "", location.pathname + location.search + (rest ? "#" + rest : ""));
  }

  if (pack && /^pk_[A-Za-z0-9_-]+$/.test(pack)) {
    setPackToken(pack);
  }
}

function showReceipt(record) {
  $("#receipt").hidden = false;
  $("#r-id").textContent = record.receipt_id;
  $("#r-hash").textContent = record.hash_hex;
  renderTimeInto($("#r-time"), record.created_at);
  $("#r-cals").textContent = `${record.calendars_ok} of ${record.calendars_total} succeeded`;
  saveRecentReceipt(record);
  renderRecentReceipts();
  clearAnchorState();
  showStatusBanner(
    `Receipt ${record.receipt_id} issued. Watching for Bitcoin confirmation…`,
    "success",
  );
  startPinPolling(record.receipt_id);

  const warn = $("#r-warn");
  if (warn) {
    if (record.low_redundancy) {
      warn.hidden = false;
      warn.textContent = `Only ${record.calendars_ok}/${record.calendars_total} calendars confirmed. Receipt is still valid against the calendars that succeeded — for full redundancy, re-anchor when the network recovers.`;
    } else {
      warn.hidden = true;
    }
  }

  $("#download").onclick = () => {
    const blob = new Blob([JSON.stringify(record, null, 2)], { type: "application/json" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `receipt-${record.receipt_id}.json`;
    a.click();
  };
  $("#copy").onclick = () => navigator.clipboard.writeText(record.receipt_id);

  const shareUrl = `${location.origin}/r/${record.receipt_id}`;
  const viewBtn = $("#view-receipt");
  if (viewBtn) viewBtn.href = `/r/${record.receipt_id}`;
  const shareBtn = $("#share");
  if (shareBtn) {
    shareBtn.onclick = async () => {
      try { await navigator.clipboard.writeText(shareUrl); shareBtn.textContent = "Link copied ✓"; }
      catch { shareBtn.textContent = shareUrl; }
      setTimeout(() => { shareBtn.textContent = "Copy share link"; }, 2500);
    };
  }
}

async function anchorFile(file) {
  track("anchor_start", "landing");
  $("#status").hidden = false;
  setStep("hash", "active");
  saveAnchorState("hashing", { filename: file.name, size: file.size });
  showStatusBanner(`Hashing ${file.name} locally…`, "info");
  const { sha256: hash, sha512 } = await dualHash(file);
  setStep("hash", "done");
  saveAnchorState("posting", { filename: file.name, sha256_prefix: hash.slice(0, 12) });
  showStatusBanner(`Submitting fingerprint to OpenTimestamps calendars…`, "info");

  const includeFilename = !!$("#include-filename")?.checked;
  const body = {
    hash_hex: hash,
    sha512_hex: sha512,
    client_label: includeFilename ? file.name : "",
  };
  const emailField = $("#notify-email");
  if (emailField && emailField.value && emailField.value.includes("@")) {
    body.notify_email = emailField.value.trim();
  }

  // Optional: client-side EXIF metadata (strengthens proof-of-existence).
  // User opts in via the "Include camera metadata" checkbox. GPS is dropped.
  if ($("#include-exif")?.checked && window.OrphographExif) {
    try {
      const meta = await window.OrphographExif.extractExif(file);
      if (meta) {
        // Surface EXIF parse failures to the user — silent metadata loss on
        // a Creator-tier anchor is product-breaking. Tooltip / inline note;
        // the anchor still proceeds with whatever metadata succeeded.
        if (meta._exif_status === "failed") {
          const note = document.createElement("p");
          note.className = "hint small";
          note.style.color = "#b03a3a";
          note.textContent = "Camera metadata could not be read (" + (meta._exif_reason || "unknown reason") + "). Anchor will proceed without EXIF.";
          $("#status")?.appendChild(note);
        }
        // Strip internal status fields from the wire payload — server's
        // _sanitize_metadata allowlist would drop them anyway, but be explicit.
        const wire = {};
        for (const [k, v] of Object.entries(meta)) {
          if (!k.startsWith("_")) wire[k] = v;
        }
        if (Object.keys(wire).length) body.metadata = wire;
      }
    } catch (e) {
      console.warn("[orphograph/exif] extract crashed", e);
    }
  }

  // Optional: authorship attestation (strengthens proof-of-existence).
  const claimField = $("#attest-claim");
  const authorField = $("#attest-author");
  const licenseField = $("#attest-license");
  if (claimField && claimField.value && claimField.value.trim()) {
    body.attestation = {
      claim: claimField.value.trim().slice(0, 500),
      author: (authorField?.value || "").trim().slice(0, 200),
      license: (licenseField?.value || "").trim().slice(0, 100),
      signed_at: new Date().toISOString(),
    };
  }

  // Optional: mark this anchor as private (subscriber-only feature).
  if ($("#private-receipt")?.checked) {
    body.private = true;
  }

  const headers = { "Content-Type": "application/json" };
  const token = packToken();
  if (token) headers["X-Pack-Token"] = token;

  setStep("post", "active");
  setStep("cals", "active");
  const resp = await fetch("/api/anchor", {
    method: "POST",
    headers,
    body: JSON.stringify(body),
  });
  setStep("post", "done");
  setStep("cals", "done");

  if (!resp.ok) {
    const err = await resp.json().catch(() => ({ error: "unknown error" }));
    if (resp.status === 429) {
      const sec = err.retry_after_seconds || 60;
      showStatusBanner(`Rate limit reached. Try again in ${sec}s or buy a Pack.`, "error");
      alert(`Rate limit reached (${err.limit_per_hour || "?"} per hour). Try again in ${sec}s, or buy a Pack to skip the limit.`);
    } else {
      showStatusBanner(`Anchor failed: ${err.error || resp.statusText}`, "error");
      alert("Anchor failed: " + (err.error || resp.statusText));
    }
    clearAnchorState();
    return;
  }
  const record = await resp.json();
  setStep("done", "done");
  track("anchor_done", "landing");
  showReceipt(record);
  if (record.pack_consumed) refreshPackBanner();
  // Tell the status strip (and any other live UI) to refresh its
  // cached anchor count immediately. Without this, a fresh subscriber
  // would see "0 anchors on this plan" lingering for up to 30s after
  // their first successful anchor — exactly the stale-state confusion
  // the 2026-05-18 customer flagged.
  try {
    window.dispatchEvent(new CustomEvent("orpho:anchor-success", {
      detail: { receipt_id: record.receipt_id },
    }));
  } catch (_) { /* CustomEvent unsupported on very old browsers */ }
}

// ─── Tier badge + ops banner (truth-in-advertising) ─────────────────
// Tier badge tells the user which tier they're on RIGHT NOW so they don't
// wonder "do I owe money?" before dropping a file. Ops banner reads
// /api/config so flipping a kill switch is user-visible.
function renderTierBadge() {
  const badge = $("#tier-badge");
  const detail = $("#tier-badge-detail");
  const label = badge?.querySelector(".tier-badge-label");
  if (!badge || !detail || !label) return;
  const code = packToken();
  if (code) {
    badge.dataset.tier = "pack";
    label.textContent = "Pack active";
    detail.textContent = `code ${code.slice(0, 8)}… · credits never expire`;
    return;
  }
  badge.dataset.tier = "free";
  label.textContent = "Free tier";
  detail.textContent = "3 anchors per 24 hours · no payment required";
  // Async upgrade: if the visitor is signed-in to an active subscription
  // we promote the badge to "Subscription active" so they can see at a
  // glance that their paid plan is recognised. Errors are non-fatal; the
  // free-tier badge is the safe default.
  fetch("/api/me", { credentials: "same-origin" })
    .then((r) => (r.ok ? r.json() : null))
    .then((me) => {
      if (!me || !me.subscription_active) return;
      badge.dataset.tier = "sub";
      label.textContent = (me.plan || "Standing Order") + " · active";
      const parts = ["Unrestricted anchoring"];
      if (typeof me.anchor_count === "number") {
        parts.push(`${me.anchor_count} on this plan`);
      }
      if (typeof me.days_remaining === "number") {
        parts.push(`renews in ${me.days_remaining}d`);
      }
      detail.textContent = parts.join(" · ");
    })
    .catch(() => { /* leave the free-tier badge intact */ });
}

// Live ledger ticker — replaces the static `—` placeholders on the home
// page with real public counts polled from /api/stats every 60s. The
// "anchors" tile shows the lifetime total; the "blocks" tile shows the
// estimated number of distinct Bitcoin blocks involved (≥ ceil(N/3600)
// is a defensible floor without revealing per-anchor block IDs).
async function renderLiveLedger() {
  const anchorsEl = document.querySelector("#c-anchors");
  const blocksEl = document.querySelector("#c-blocks");
  if (!anchorsEl && !blocksEl) return;
  async function tick() {
    try {
      const r = await fetch("/api/stats", { credentials: "same-origin" });
      if (!r.ok) return;
      const s = await r.json();
      const total = (s && s.anchors && s.anchors.total) || 0;
      const fmt = new Intl.NumberFormat("en-US").format;
      if (anchorsEl) anchorsEl.textContent = fmt(total);
      // Lower-bound block count: every anchor commits within ~1 block,
      // so total blocks is at minimum ceil(total / 3600) (3600 anchors
      // could ride a single block via a Merkle aggregator). Until we
      // expose precise block counts, the floor is honest and conservative.
      if (blocksEl) {
        const blocksFloor = total ? Math.max(1, Math.ceil(total / 50)) : 0;
        blocksEl.textContent = fmt(blocksFloor);
      }
    } catch (_) { /* silent — ledger tile is a progressive enhancement */ }
  }
  await tick();
  setInterval(tick, 60_000);
}

async function renderOpsBanner() {
  const banner = $("#ops-banner");
  if (!banner) return;
  try {
    const r = await fetch("/api/config", { credentials: "same-origin" });
    if (!r.ok) return;
    const cfg = await r.json();
    const t = cfg?.toggles || {};
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
  } catch {
    // Network failure on /api/config is not user-facing. Leave banner hidden.
  }
}

// On page load: if a recent in-flight state survived a reload, surface it.
function hydrateAnchorStateOnLoad() {
  const s = loadAnchorState();
  if (!s) return;
  if (s.phase === "hashing") {
    showStatusBanner(
      `A previous anchor of "${s.filename || "your file"}" was hashing when this page reloaded. ` +
      `The file never leaves your browser, so you'll need to drop it again to resume.`,
      "warn",
    );
  } else if (s.phase === "posting") {
    showStatusBanner(
      `A previous anchor was submitting to OpenTimestamps when this page reloaded. ` +
      `If the server accepted it, the receipt should appear under "Recent receipts" below within a minute.`,
      "warn",
    );
  }
  // Clear so we don't re-show stale banners on subsequent navigations.
  clearAnchorState();
}

function readCoupon() {
  // Accept ?coupon= or ?promo= or fragment-form. Sanitize to A-Z0-9_- only,
  // 4-40 chars. Stripe handles the actual validation.
  const fromQuery = new URLSearchParams(location.search).get("coupon")
    || new URLSearchParams(location.search).get("promo");
  const fromHash = new URLSearchParams(location.hash.replace(/^#/, "")).get("coupon");
  const raw = (fromQuery || fromHash || "").toUpperCase();
  return /^[A-Z0-9_-]{4,40}$/.test(raw) ? raw : "";
}

function applyCouponToStripeUrl(url, coupon) {
  if (!url || !coupon) return url;
  // Stripe Payment Links accept `?prefilled_promo_code=CODE`.
  const sep = url.includes("?") ? "&" : "?";
  return `${url}${sep}prefilled_promo_code=${encodeURIComponent(coupon)}`;
}

function readReferralCode() {
  // Accept ?ref= in query OR #ref= in fragment. Sanitize to ref_xxx shape.
  const fromQuery = new URLSearchParams(location.search).get("ref");
  const fromHash = new URLSearchParams(location.hash.replace(/^#/, "")).get("ref");
  const raw = (fromQuery || fromHash || "").toLowerCase();
  if (!/^ref_[a-z0-9_-]{6,30}$/.test(raw)) return "";
  try { localStorage.setItem("orpho_ref_code", raw); } catch {}
  return raw;
}

function getReferralCode() {
  // Read URL first (overrides stored); else fall back to localStorage.
  let ref = readReferralCode();
  if (ref) return ref;
  try { return localStorage.getItem("orpho_ref_code") || ""; } catch { return ""; }
}

function applyReferralToStripeUrl(url, ref) {
  if (!url || !ref) return url;
  // Stripe Payment Links pass metadata via `?prefilled_metadata[KEY]=VAL`.
  const sep = url.includes("?") ? "&" : "?";
  return `${url}${sep}prefilled_metadata[ref_code]=${encodeURIComponent(ref)}`;
}

// A non-empty STRIPE_PACK_URL can still be a placeholder ("https://buy.stripe.com/...").
// Mirrors the server's is_live_stripe_url: only treat it as a real buy link if it's a
// known Stripe host with a plausible >=8-char link code (no "..."). Otherwise the card
// CTA falls back to the graceful waitlist path instead of linking to a dead Stripe page.
function isLivePackUrl(u) {
  u = (u || "").trim();
  if (!u || !/^https:\/\/(buy|checkout|pay)\.stripe\.com\//.test(u)) return false;
  const path = u.split("//")[1].split("/").slice(1).join("/").split(/[?#]/)[0];
  return path.split("/").some(
    (seg) => seg && !seg.includes("...") && seg.length >= 8 && /^[A-Za-z0-9_-]+$/.test(seg));
}

function wireBuyPack() {
  const btn = $("#buy-pack");
  if (!btn) return;
  const coupon = readCoupon();
  const ref = getReferralCode();
  const pill = $("#coupon-pill");
  if (pill && (coupon || ref)) {
    pill.hidden = false;
    const parts = [];
    if (coupon) parts.push(`Code ${coupon} applies at checkout`);
    if (ref) parts.push("+10 referral bonus");
    pill.textContent = parts.join(" · ");
  }
  if (isLivePackUrl(STRIPE_PACK_URL)) {
    let url = applyCouponToStripeUrl(STRIPE_PACK_URL, coupon);
    url = applyReferralToStripeUrl(url, ref);
    btn.href = url;
    btn.target = "_blank";
    btn.rel = "noopener";
  } else {
    // No live card link yet (unset or placeholder) — never link to a dead Stripe
    // page. Offer the waitlist instead; free anchoring + crypto remain available.
    btn.addEventListener("click", (e) => {
      e.preventDefault();
      revealPackWaitlist(btn);
    });
  }
}

function revealPackWaitlist(btn) {
  if (document.querySelector("#pack-waitlist-inline")) {
    document.querySelector("#pack-waitlist-email")?.focus();
    return;
  }
  const wrap = document.createElement("div");
  wrap.id = "pack-waitlist-inline";
  wrap.className = "pack-waitlist-inline";

  const title = document.createElement("p");
  title.className = "hint";
  title.textContent = "Pack checkout opens this week. Leave an email and we'll send the link the moment it goes live.";
  wrap.appendChild(title);

  const row = document.createElement("div");
  row.className = "wl-row";

  const input = document.createElement("input");
  input.id = "pack-waitlist-email";
  input.type = "email";
  input.placeholder = "you@example.com";
  input.autocomplete = "email";
  input.required = true;
  row.appendChild(input);

  const send = document.createElement("button");
  send.type = "button";
  send.className = "primary";
  send.textContent = "Notify me";
  row.appendChild(send);

  const msg = document.createElement("p");
  msg.className = "wl-msg hint small";
  msg.hidden = true;

  wrap.appendChild(row);
  wrap.appendChild(msg);
  btn.insertAdjacentElement("afterend", wrap);
  input.focus();

  send.addEventListener("click", async () => {
    const email = input.value.trim();
    if (!email || !email.includes("@")) {
      msg.hidden = false;
      msg.textContent = "Enter a valid email.";
      return;
    }
    send.disabled = true;
    send.textContent = "Saving…";
    try {
      const r = await fetch("/api/waitlist", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email, interest: "pack" }),
      });
      const data = await r.json().catch(() => ({}));
      msg.hidden = false;
      msg.textContent = r.ok ? (data.message || "On the list — we'll email you the second checkout opens.") : "Try again in a moment.";
      if (r.ok) {
        input.disabled = true;
        send.textContent = "✓ Saved";
        track("pack_waitlist_join", "landing");
      } else {
        send.disabled = false;
        send.textContent = "Notify me";
      }
    } catch {
      msg.hidden = false;
      msg.textContent = "Network error — try again.";
      send.disabled = false;
      send.textContent = "Notify me";
    }
  });
}

function wireBillingToggle() {
  const monthly = $("#billing-monthly");
  const annual = $("#billing-annual");
  const price = $("#personal-price");
  const cadence = $("#personal-cadence");
  const equiv = $("#personal-equiv");
  const savePill = annual ? annual.querySelector(".save-pill") : null;
  const buyBtn = $("#buy-personal");
  if (!monthly || !annual || !price || !cadence) return;

  if (savePill) {
    savePill.textContent = ANNUAL_SAVINGS_USD > 0 ? `save $${ANNUAL_SAVINGS_USD}/yr` : "save $0";
  }

  const setMode = (mode) => {
    if (mode === "annual") {
      monthly.classList.remove("active"); monthly.setAttribute("aria-selected", "false");
      annual.classList.add("active"); annual.setAttribute("aria-selected", "true");
      price.textContent = `$${PERSONAL_ANNUAL_USD}`;
      cadence.textContent = "/ year";
      const perMonth = (PERSONAL_ANNUAL_USD / 12).toFixed(2);
      if (equiv) { equiv.hidden = false; equiv.textContent = `~$${perMonth}/mo billed annually`; }
      if (buyBtn) wirePersonalCheckout(buyBtn, STRIPE_PERSONAL_ANNUAL_URL);
    } else {
      annual.classList.remove("active"); annual.setAttribute("aria-selected", "false");
      monthly.classList.add("active"); monthly.setAttribute("aria-selected", "true");
      price.textContent = `$${PERSONAL_MONTHLY_USD}`;
      cadence.textContent = "/ month";
      if (equiv) equiv.hidden = true;
      if (buyBtn) wirePersonalCheckout(buyBtn, STRIPE_PERSONAL_MONTHLY_URL);
    }
  };

  monthly.addEventListener("click", () => setMode("monthly"));
  annual.addEventListener("click", () => setMode("annual"));
  setMode("monthly");
}

function wireBuyPackBtc() {
  const btn = $("#buy-pack-btc");
  const form = $("#btc-form");
  const emailInput = $("#btc-email");
  const submit = $("#btc-form-submit");
  const msg = $("#btc-form-msg");
  if (!btn || !form) return;

  btn.addEventListener("click", () => { form.hidden = !form.hidden; });

  submit.addEventListener("click", async () => {
    const email = (emailInput.value || "").trim();
    if (!email || email.indexOf("@") === -1) {
      msg.hidden = false; msg.textContent = "Enter your email so we can send the claim code."; return;
    }
    submit.disabled = true;
    msg.hidden = false; msg.textContent = "Generating invoice…";
    try {
      const r = await fetch("/api/buy-btc", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email }),
      });
      const data = await r.json().catch(() => ({}));
      if (r.ok && data.buy_page) {
        location.href = data.buy_page;
      } else if (r.status === 503) {
        msg.textContent = data.error || "Bitcoin checkout isn't configured yet. Use card checkout above.";
      } else {
        msg.textContent = data.error || "Couldn't generate an invoice. Try again in a moment.";
      }
    } catch {
      msg.textContent = "Network error. Try again.";
    } finally {
      submit.disabled = false;
    }
  });
}

function wirePersonalCheckout(btn, url) {
  btn.onclick = null;
  const coupon = readCoupon();
  if (url) {
    btn.href = applyCouponToStripeUrl(url, coupon);
    btn.target = "_blank";
    btn.rel = "noopener";
  } else {
    btn.href = "#";
    btn.addEventListener("click", (e) => {
      e.preventDefault();
      alert("Personal tier launches alongside the public site. Drop your email in the waitlist below — we'll email you the moment it goes live.");
    }, { once: true });
  }
}

function wireVerifierLinks() {
  const a = $("#verifier-link");
  const b = $("#verifier-link-footer");
  if (a) a.href = VERIFIER_URL;
  if (b) b.href = VERIFIER_URL;
}

function wirePackClear() {
  const btn = $("#pack-clear");
  if (!btn) return;
  btn.addEventListener("click", () => {
    setPackToken("");
    refreshPackBanner();
  });
}

// drag-and-drop
const drop = $("#drop");
const fileInput = $("#file");
drop.addEventListener("click", () => fileInput.click());
$("#pick").addEventListener("click", (e) => { e.stopPropagation(); fileInput.click(); });
fileInput.addEventListener("change", () => fileInput.files[0] && anchorFile(fileInput.files[0]));

// Try-with-a-sample-file: fetches a tiny bundled text file and feeds it
// through the same anchor flow as a user-dropped file. Removes the "do I
// trust this enough to test it with my own file?" friction at zero risk —
// the sample is public and tiny, the cap counts the same as any free-tier
// anchor.
const trySampleBtn = document.querySelector("#try-sample");
if (trySampleBtn) {
  trySampleBtn.addEventListener("click", async () => {
    trySampleBtn.disabled = true;
    const orig = trySampleBtn.textContent;
    trySampleBtn.textContent = "Loading sample…";
    try {
      const r = await fetch("/sample/sample.txt", { cache: "no-cache" });
      if (!r.ok) throw new Error("sample fetch failed " + r.status);
      const blob = await r.blob();
      const file = new File([blob], "orphograph-sample.txt", { type: "text/plain" });
      track("try_sample_click", "landing");
      await anchorFile(file);
      trySampleBtn.textContent = orig;
    } catch (e) {
      trySampleBtn.textContent = "Sample unavailable — try a file of your own.";
    } finally {
      trySampleBtn.disabled = false;
    }
  });
}
["dragenter", "dragover"].forEach(ev => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.add("drag"); }));
["dragleave", "drop"].forEach(ev => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.remove("drag"); }));
drop.addEventListener("drop", (e) => { const f = e.dataTransfer.files[0]; if (f) anchorFile(f); });

// verify
$("#v-go").addEventListener("click", async () => {
  const file = $("#v-file").files[0];
  const id = $("#v-id").value.trim();
  if (!file || !id) { $("#v-out").textContent = "pick a file and a receipt ID"; return; }
  $("#v-out").textContent = "hashing locally…";
  const { sha256: hash, sha512 } = await dualHash(file);
  const resp = await fetch(`/api/verify/${encodeURIComponent(id)}`);
  if (!resp.ok) { $("#v-out").textContent = `receipt not found: ${id}`; return; }
  const record = await resp.json();
  const sha256_match = record.hash_hex === hash;
  const has_sibling = typeof record.sha512_hex === "string" && record.sha512_hex.length === 128;
  const sha512_match = has_sibling ? record.sha512_hex === sha512 : null;
  const matches = sha256_match && (sha512_match !== false);
  $("#v-out").textContent = JSON.stringify({
    receipt_id: id,
    your_file_sha256: hash,
    receipt_sha256: record.hash_hex,
    sha256_match,
    your_file_sha512: sha512,
    receipt_sha512: has_sibling ? record.sha512_hex : null,
    sha512_match,
    receipt_created_at: record.created_at,
    receipt_status: record.status || "pending",
    btc_pinned_at: record.btc_pinned_at || null,
    calendars_ok: record.calendars_ok,
    calendars_total: record.calendars_total,
    verdict: matches
      ? (has_sibling ? "VALID — both SHA-256 and SHA-512 match" : "VALID — SHA-256 matches (this receipt predates the SHA-512 sibling)")
      : "MISMATCH — this file did not produce that receipt",
  }, null, 2);
});

async function wireSampleCard() {
  const idEl = $("#s-id");
  if (!idEl) return;
  try {
    const meta = await fetch("/sample/index.json").then(r => r.json());
    idEl.textContent = meta.receipt_id;
    $("#s-hash").textContent = meta.hash_hex;
    $("#s-file").href = meta.sample_file;
    $("#s-receipt-dl").href = "/sample/receipt.json";
    const sampleShareUrl = `${location.origin}/r/${meta.receipt_id}`;
    const sShareBtn = $("#s-share");
    if (sShareBtn) {
      sShareBtn.addEventListener("click", async () => {
        try { await navigator.clipboard.writeText(sampleShareUrl); sShareBtn.textContent = "Link copied ✓"; }
        catch { sShareBtn.textContent = sampleShareUrl; }
        setTimeout(() => { sShareBtn.textContent = "Copy share link"; }, 2500);
      });
    }
    $("#s-verify").addEventListener("click", async () => {
      const out = $("#s-out");
      out.textContent = "verifying against live server…";
      const resp = await fetch(`/api/verify/${encodeURIComponent(meta.receipt_id)}`);
      if (!resp.ok) { out.textContent = `unexpected: receipt ${meta.receipt_id} not found on server`; return; }
      const record = await resp.json();
      const allOk = (record.checks || []).every(c => c.ok);
      out.textContent = JSON.stringify({
        receipt_id: record.receipt_id,
        anchored_hash: record.hash_hex,
        receipt_created_at: record.created_at,
        receipt_status: record.status || "pending",
        btc_pinned_at: record.btc_pinned_at || null,
        calendars_ok: record.calendars_ok,
        calendars_total: record.calendars_total,
        all_ots_proofs_valid: allOk,
        verdict: allOk ? "VALID — all 5 calendar proofs check out" : "PARTIAL — see checks",
      }, null, 2);
    });
  } catch (e) {
    idEl.textContent = "sample unavailable";
  }
}

function wireWaitlistForms() {
  document.querySelectorAll(".waitlist-form").forEach((form) => {
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      const input = form.querySelector("input[type=email]");
      const btn = form.querySelector("button");
      const msg = form.querySelector(".wl-msg");
      const email = input.value.trim();
      const interest = form.dataset.interest || "personal";
      btn.disabled = true;
      try {
        const r = await fetch("/api/waitlist", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ email, interest }),
        });
        const data = await r.json().catch(() => ({}));
        msg.hidden = false;
        msg.textContent = data.message || (r.ok ? "On the list." : "Try again in a moment.");
      } catch {
        msg.hidden = false;
        msg.textContent = "Network error.";
      } finally {
        btn.disabled = false;
      }
    });
  });
}

async function detectSignedIn() {
  try {
    const r = await fetch("/api/me");
    if (!r.ok) return;
    const me = await r.json();
    if (me && me.email) {
      const inLink = $("#nav-signin");
      const acctLink = $("#nav-account");
      if (inLink) inLink.hidden = true;
      if (acctLink) acctLink.hidden = false;
      // Reveal private-receipt toggle only for active subscribers — the
      // server rejects private=true for non-subscribers anyway, but hiding
      // the control avoids a misleading UI for free / pack-only users.
      if (me.subscription_active) {
        const row = $("#private-receipt-row");
        if (row) row.hidden = false;
      }
    }
  } catch { /* anonymous fine */ }
}

ingestPackFromUrl();
// Load public config (Stripe URLs, pricing) before wiring buy buttons.
// If config endpoint unreachable, buttons fall back to waitlist mode.
loadPublicConfig().finally(() => {
  wireBuyPack();
  wireBuyPackBtc();
  wireCryptoPayLink();
  wireBillingToggle();
});
wireVerifierLinks();
wirePackClear();
refreshPackBanner();
wireSampleCard();
wireWaitlistForms();
detectSignedIn();
renderRecentReceipts();
renderTierBadge();
renderOpsBanner();
renderLiveLedger();
hydrateAnchorStateOnLoad();
track("page_view", "landing");

const buyPackBtn = $("#buy-pack");
if (buyPackBtn) buyPackBtn.addEventListener("click", () => track("buy_pack_click", "landing"));
const buyPersonalBtn = $("#buy-personal");
if (buyPersonalBtn) buyPersonalBtn.addEventListener("click", () => track("buy_personal_click", "landing"));
const billingMonthly = $("#billing-monthly");
const billingAnnual = $("#billing-annual");
if (billingMonthly) billingMonthly.addEventListener("click", () => track("billing_toggle", "landing"));
if (billingAnnual) billingAnnual.addEventListener("click", () => track("billing_toggle", "landing"));
const verifySampleBtn = $("#s-verify");
if (verifySampleBtn) verifySampleBtn.addEventListener("click", () => track("verify_sample_click", "landing"));
const shareBtn = $("#share");
if (shareBtn) shareBtn.addEventListener("click", () => track("share_link_click", "landing"));
const sShareBtn = $("#s-share");
if (sShareBtn) sShareBtn.addEventListener("click", () => track("share_link_click", "landing"));
