// statusbar.js — top-of-page status strip showing signed-in state, plan,
// usage counters, and days-left on subscription. Mounts on every page that
// includes this script. Self-contained: no dependencies on app.js or other
// modules so a page can include it standalone.
//
// Renders nothing for anonymous visitors. For signed-in users renders a
// one-line strip directly under the header.

(function () {
  const STYLE_ID = "orpho-statusbar-style";
  const STRIP_ID = "orpho-statusbar";

  function injectStyles() {
    if (document.getElementById(STYLE_ID)) return;
    const style = document.createElement("style");
    style.id = STYLE_ID;
    // !important on layout/chrome so a stray page-level rule cannot
    // collapse the pills back into a continuous run of text (the
    // 2026-05-18 "muddled status page" regression).
    // Margin-right on each child instead of flex `gap` so older Safari
    // builds without flex-gap support still space pills correctly.
    style.textContent = `
#${STRIP_ID} {
  position: sticky !important; top: 0 !important; z-index: 50 !important;
  background: #efe6d2 !important;
  border-bottom: 1px solid #a89e80 !important;
  font-family: Georgia, 'Times New Roman', serif !important;
  font-size: 13px !important; color: #2a2a2a !important;
  padding: 10px 16px !important;
  display: flex !important; align-items: center !important;
  flex-wrap: wrap !important;
  line-height: 1.4 !important;
}
#${STRIP_ID} > * { margin-right: 12px !important; margin-bottom: 0 !important; }
#${STRIP_ID} > *:last-child { margin-right: 0 !important; }
#${STRIP_ID} .ob-pill {
  display: inline-block !important;
  padding: 4px 11px !important;
  border-radius: 999px !important;
  background: #ffffff !important;
  border: 1px solid #a89e80 !important;
  color: #2a2a2a !important;
  font-size: 12px !important;
  line-height: 1.4 !important;
  white-space: nowrap !important;
}
#${STRIP_ID} .ob-pill.ob-plan {
  background: #1a1a1a !important; color: #f5efe0 !important;
  border-color: #1a1a1a !important;
  font-weight: 600 !important; letter-spacing: 0.02em !important;
}
#${STRIP_ID} .ob-pill.ob-warn {
  background: #fff3cd !important; border-color: #d4a82a !important;
  color: #5a4a00 !important;
}
#${STRIP_ID} .ob-email {
  color: #555 !important;
  font-family: -apple-system, BlinkMacSystemFont, 'Helvetica Neue', Menlo, monospace !important;
  font-size: 12px !important;
  white-space: nowrap !important;
}
#${STRIP_ID} .ob-spacer { flex: 1 1 auto !important; margin-right: 0 !important; }
#${STRIP_ID} a.ob-link {
  color: #2a5d4a !important;
  text-decoration: underline !important;
  font-size: 12px !important;
  padding: 4px 0 !important;
  white-space: nowrap !important;
}
#${STRIP_ID} button.ob-signout {
  background: #ffffff !important;
  border: 1px solid #a89e80 !important;
  color: #2a2a2a !important;
  padding: 4px 11px !important;
  border-radius: 999px !important;
  cursor: pointer !important;
  font-family: inherit !important;
  font-size: 12px !important;
  line-height: 1.4 !important;
  white-space: nowrap !important;
}
#${STRIP_ID} button.ob-signout:hover { background: #1a1a1a !important; color: #f5efe0 !important; border-color: #1a1a1a !important; }
@media (max-width: 560px) {
  #${STRIP_ID} { font-size: 12px !important; padding: 8px 12px !important; }
  #${STRIP_ID} > * { margin-right: 8px !important; }
  #${STRIP_ID} .ob-email { display: none !important; }
}
    `;
    document.head.appendChild(style);
  }

  function mountStrip(data) {
    let strip = document.getElementById(STRIP_ID);
    if (!strip) {
      strip = document.createElement("div");
      strip.id = STRIP_ID;
      strip.setAttribute("role", "status");
      strip.setAttribute("aria-live", "polite");
      // Insert just after <header> if present, else as first child of <body>.
      const header = document.querySelector("body > header");
      if (header && header.parentNode) {
        header.parentNode.insertBefore(strip, header.nextSibling);
      } else {
        document.body.insertBefore(strip, document.body.firstChild);
      }
    }
    while (strip.firstChild) strip.removeChild(strip.firstChild);

    // Plan pill.
    const plan = data.plan || (data.subscription_active ? "Standing Order" : null);
    if (plan) {
      const p = document.createElement("span");
      p.className = "ob-pill ob-plan";
      p.textContent = plan + " · active";
      strip.appendChild(p);
    } else if (data.signed_in) {
      const p = document.createElement("span");
      p.className = "ob-pill";
      p.textContent = "Free tier · 3/day";
      strip.appendChild(p);
    }

    // Anchor count.
    if (typeof data.anchor_count === "number") {
      const a = document.createElement("span");
      a.className = "ob-pill";
      const noun = data.anchor_count === 1 ? "anchor" : "anchors";
      a.textContent = `${data.anchor_count} ${noun} on this plan`;
      strip.appendChild(a);
    }

    // Days remaining (subscription only).
    if (typeof data.days_remaining === "number") {
      const d = document.createElement("span");
      const lowDays = data.days_remaining <= 5;
      d.className = "ob-pill" + (lowDays ? " ob-warn" : "");
      d.textContent = `renews in ${data.days_remaining}d`;
      strip.appendChild(d);
    }

    // Email (truncated on mobile via CSS).
    if (data.email) {
      const e = document.createElement("span");
      e.className = "ob-email";
      e.textContent = data.email;
      strip.appendChild(e);
    }

    // Spacer.
    const spacer = document.createElement("span");
    spacer.className = "ob-spacer";
    strip.appendChild(spacer);

    // Account link.
    if (window.location.pathname !== "/account") {
      const link = document.createElement("a");
      link.className = "ob-link";
      link.href = "/account";
      link.textContent = "account →";
      strip.appendChild(link);
    }

    // Sign-out.
    const signout = document.createElement("button");
    signout.type = "button";
    signout.className = "ob-signout";
    signout.textContent = "sign out";
    signout.addEventListener("click", async () => {
      try {
        await fetch("/api/auth/signout", { method: "POST", credentials: "same-origin" });
      } catch (_) { /* fall through to reload */ }
      // Critical: clear cached identity BEFORE the reload so a fresh
      // visitor (or different account) does not see the prior session's
      // strip flash on the next render. 2026-05-18 lesson: stale auth
      // state caused "still signed in" UX after a failed link redemption.
      clearCache();
      window.location.href = "/";
    });
    strip.appendChild(signout);
  }

  const CACHE_KEY = "orpho_me_cache_v1";
  const CACHE_TTL_MS = 30_000;

  function readCache() {
    try {
      const raw = sessionStorage.getItem(CACHE_KEY);
      if (!raw) return null;
      const obj = JSON.parse(raw);
      if (!obj || typeof obj.ts !== "number") return null;
      if (Date.now() - obj.ts > CACHE_TTL_MS) return null;
      return obj.data || null;
    } catch (_) { return null; }
  }

  function writeCache(data) {
    try {
      sessionStorage.setItem(CACHE_KEY, JSON.stringify({ ts: Date.now(), data }));
    } catch (_) { /* sessionStorage may be unavailable in private mode */ }
  }

  function clearCache() {
    try { sessionStorage.removeItem(CACHE_KEY); } catch (_) {}
  }

  async function init() {
    // Render from cache first so navigation feels instant; revalidate in
    // the background. /api/me scans every receipt directory to count
    // anchors which is O(n) on receipts and produced ~2.5s tail-latency
    // probes during 2026-05-18 deploy. Cache cuts that to ~0ms for the
    // common case (multiple page navigations in the same session).
    const cached = readCache();
    if (cached && cached.email) {
      injectStyles();
      mountStrip(cached);
    }
    try {
      const r = await fetch("/api/me", { credentials: "same-origin" });
      if (r.status === 401) { clearCache(); return; }
      if (!r.ok) return;
      const data = await r.json();
      if (!data || !data.email) { clearCache(); return; }
      writeCache(data);
      // Skip a redundant re-mount if the cached render is still
      // visually accurate (same email + same anchor_count + same
      // days_remaining). Avoids the strip flickering on every nav.
      if (cached &&
          cached.email === data.email &&
          cached.anchor_count === data.anchor_count &&
          cached.days_remaining === data.days_remaining) {
        return;
      }
      injectStyles();
      mountStrip(data);
    } catch (e) {
      // Fail silently — strip is a progressive enhancement
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }

  // Live updates: app.js dispatches orpho:anchor-success after a
  // successful anchor so the counter ticks immediately. Without this
  // the strip would show stale "0 anchors" for up to 30s after a
  // brand-new subscriber's first anchor — exactly the kind of
  // confused-state UX that surfaced earlier today.
  window.addEventListener("orpho:anchor-success", () => {
    clearCache();
    init();
  });
})();
