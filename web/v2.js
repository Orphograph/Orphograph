// v2.js — wires the v2 mockup homepage to the live /api endpoints.
// Drop zone: client-side SHA-256 + SHA-512 via WebCrypto, then POST to /api/anchor.
// Stats strip: pulls live counts from /api/stats.
// No external dependencies. All DOM builds via createElement/textContent — no innerHTML.

(function () {
  "use strict";

  const $ = (id) => document.getElementById(id);

  function fmtNum(n) {
    if (n == null) return "—";
    return Number(n).toLocaleString("en-US");
  }

  // ── Live stats strip ─────────────────────────────────────────────
  fetch("/api/stats")
    .then((r) => r.json())
    .then((j) => {
      if (j.total_anchors != null && $("c-anchors")) {
        $("c-anchors").textContent = fmtNum(j.total_anchors);
      }
      if (j.bitcoin_blocks_anchored != null && $("c-blocks")) {
        $("c-blocks").textContent = fmtNum(j.bitcoin_blocks_anchored);
      }
      if (j.most_recent_anchor_at) {
        const tz = $("latest-tz-block");
        if (tz) {
          tz.dataset.utc = j.most_recent_anchor_at;
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

    let sha256, sha512;
    try {
      [sha256, sha512] = await Promise.all([
        hashFile(file, "SHA-256"),
        hashFile(file, "SHA-512"),
      ]);
    } catch (e) {
      setStatusSimple("Could not read the file.", String(e && e.message ? e.message : e), "error");
      return;
    }

    setStatusSimple("Fingerprint computed.", "Submitting to five OpenTimestamps calendars…", "");
    status.appendChild(makeField("SHA-256 · " + sha256));

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
        const txt = await r.text();
        setStatusSimple(
          "The office could not record this submission.",
          "Server returned " + r.status + ". " + txt.slice(0, 200),
          "error"
        );
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
    } catch (e) {
      setStatusSimple("Network error.", String(e && e.message ? e.message : e), "error");
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
  async function startCheckout(plan, button) {
    const originalLabel = button.textContent;
    button.textContent = "Loading…";
    button.style.pointerEvents = "none";
    try {
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
        alert(msg);
        button.textContent = originalLabel;
        button.style.pointerEvents = "";
        return;
      }
      const j = await r.json();
      if (j && j.url) {
        window.location.href = j.url;
        return;
      }
      alert("Checkout response missing url.");
      button.textContent = originalLabel;
      button.style.pointerEvents = "";
    } catch (e) {
      alert("Network error opening checkout: " + (e && e.message ? e.message : e));
      button.textContent = originalLabel;
      button.style.pointerEvents = "";
    }
  }
  document.querySelectorAll("[data-checkout]").forEach((btn) => {
    btn.addEventListener("click", (e) => {
      e.preventDefault();
      const plan = btn.dataset.checkout;
      if (plan === "pack" || plan === "pro") startCheckout(plan, btn);
    });
  });
})();
