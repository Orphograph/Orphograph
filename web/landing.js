// landing.js — homepage-only enhancements (live counter, sample verify,
// waitlist mini-forms, badge copy). CSP-compliant (no inline script tags).

(function () {
  "use strict";

  // ── Tab navigation ────────────────────────────────────────────────
  const TABS = ["what", "how", "verify", "pricing", "badge", "faq"];

  function activateTab(name) {
    if (!TABS.includes(name)) name = "what";
    document.querySelectorAll(".tab-btn").forEach((b) => {
      const isActive = b.id === "tab-" + name;
      b.setAttribute("aria-selected", isActive ? "true" : "false");
      if (isActive) b.focus({ preventScroll: true });
    });
    document.querySelectorAll(".tab-panel").forEach((p) => {
      p.classList.toggle("active", p.id === "panel-" + name);
    });
    if (location.hash !== "#" + name) {
      history.replaceState(null, "", "#" + name);
    }
  }

  document.querySelectorAll(".tab-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      const name = btn.id.replace(/^tab-/, "");
      activateTab(name);
    });
    btn.addEventListener("keydown", (e) => {
      const buttons = Array.from(document.querySelectorAll(".tab-btn"));
      const i = buttons.indexOf(btn);
      if (e.key === "ArrowRight") { e.preventDefault(); buttons[(i + 1) % buttons.length].click(); }
      else if (e.key === "ArrowLeft")  { e.preventDefault(); buttons[(i - 1 + buttons.length) % buttons.length].click(); }
      else if (e.key === "Home")       { e.preventDefault(); buttons[0].click(); }
      else if (e.key === "End")        { e.preventDefault(); buttons[buttons.length - 1].click(); }
    });
  });

  // Header nav links route through the tabs
  document.querySelectorAll("header nav a[data-tab]").forEach((a) => {
    a.addEventListener("click", (e) => {
      e.preventDefault();
      activateTab(a.dataset.tab);
      window.scrollTo({ top: document.querySelector(".tabs").offsetTop - 60, behavior: "smooth" });
    });
  });

  // Initial route from URL hash
  (function () {
    const fromHash = (location.hash || "").replace(/^#/, "");
    if (TABS.includes(fromHash)) activateTab(fromHash);
    window.addEventListener("hashchange", () => {
      const h = (location.hash || "").replace(/^#/, "");
      if (TABS.includes(h)) activateTab(h);
    });
  })();

  // ── Live counter strip ────────────────────────────────────────────
  (async () => {
    try {
      const r = await fetch("/api/stats", { credentials: "same-origin" });
      if (!r.ok) return;
      const s = await r.json();
      const fmt = (n) => (n || 0).toLocaleString();
      const set = (id, v) => {
        const el = document.getElementById(id);
        if (el) el.textContent = fmt(v);
      };
      set("c-files",         s.total_anchors || s.anchors_total || 0);
      set("c-verifications", s.total_verifications || s.verifications_total || 0);
      set("c-blocks",        s.bitcoin_blocks_referenced || s.unique_blocks || 1);
    } catch (_) {
      // leave placeholder dashes
    }
  })();

  // ── Sample receipt verify button ──────────────────────────────────
  const sVerify = document.getElementById("s-verify");
  if (sVerify) {
    sVerify.addEventListener("click", async () => {
      const out = document.getElementById("s-out");
      if (!out) return;
      out.textContent = "Verifying...";
      try {
        const r = await fetch("/api/verify/ChvTMbYLIACHEHJT");
        const j = await r.json();
        out.textContent = JSON.stringify(j, null, 2);
      } catch (e) {
        out.textContent = "Error: " + (e.message || e);
      }
    });
  }

  // ── Pro billing toggle (Monthly / Annual) ─────────────────────────
  const billingMonthly = document.getElementById("billing-monthly");
  const billingAnnual  = document.getElementById("billing-annual");
  const priceEl        = document.getElementById("personal-price");
  const cadenceEl      = document.getElementById("personal-cadence");
  const equivEl        = document.getElementById("personal-equiv");

  function setBilling(cadence) {
    const monthly = cadence === "monthly";
    if (billingMonthly) {
      billingMonthly.classList.toggle("active", monthly);
      billingMonthly.setAttribute("aria-selected", monthly ? "true" : "false");
    }
    if (billingAnnual) {
      billingAnnual.classList.toggle("active", !monthly);
      billingAnnual.setAttribute("aria-selected", monthly ? "false" : "true");
    }
    if (priceEl)   priceEl.textContent   = monthly ? "$9" : "$90";
    if (cadenceEl) cadenceEl.textContent = monthly ? "/ month" : "/ year";
    if (equivEl) {
      if (monthly) {
        equivEl.hidden = true;
        equivEl.textContent = "";
      } else {
        equivEl.hidden = false;
        equivEl.textContent = "$7.50/mo equivalent — save $18 per year";
      }
    }
  }
  if (billingMonthly) billingMonthly.addEventListener("click", () => setBilling("monthly"));
  if (billingAnnual)  billingAnnual.addEventListener("click",  () => setBilling("annual"));

  // ── Waitlist mini-forms (Pro + Pack tiers) ────────────────────────
  document.querySelectorAll(".waitlist-mini").forEach((f) => {
    f.addEventListener("submit", async (e) => {
      e.preventDefault();
      const emailInput = f.querySelector("input");
      const email      = (emailInput.value || "").trim();
      const interest   = f.dataset.interest || "general";
      const btn        = f.querySelector("button");
      if (!email.includes("@")) return;
      btn.disabled    = true;
      btn.textContent = "Saving…";
      try {
        await fetch("/api/waitlist", {
          method:  "POST",
          headers: { "Content-Type": "application/json" },
          body:    JSON.stringify({ email, interest }),
        });
        btn.textContent = "On the list ✓";
      } catch (_) {
        btn.textContent = "Try again";
        btn.disabled    = false;
      }
    });
  });

  // ── Badge snippet copy button ─────────────────────────────────────
  const copyBtn = document.getElementById("badge-copy");
  if (copyBtn) {
    copyBtn.addEventListener("click", () => {
      const code = document.getElementById("badge-snippet-code");
      if (!code) return;
      navigator.clipboard.writeText((code.textContent || "").trim()).then(() => {
        copyBtn.textContent = "Copied";
        setTimeout(() => (copyBtn.textContent = "Copy"), 1400);
      });
    });
  }

  // ── FAQ badge-link smooth-scroll (replacement for inline onclick) ─
  const faqBadgeLink = document.getElementById("faq-badge-link");
  if (faqBadgeLink) {
    faqBadgeLink.addEventListener("click", (e) => {
      e.preventDefault();
      const target = document.querySelector(".badge-section");
      if (target) target.scrollIntoView({ behavior: "smooth" });
    });
  }
})();
