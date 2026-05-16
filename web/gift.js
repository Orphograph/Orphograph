// gift.js — gifting flow. Fetches public Stripe URL from /api/config,
// appends recipient + buyer + message as Stripe Payment Link metadata,
// then redirects to checkout.

const $ = (sel) => document.querySelector(sel);

async function loadConfig() {
  try {
    const r = await fetch("/api/config", { credentials: "same-origin" });
    if (!r.ok) {
      console.warn("[gift] /api/config returned", r.status);
      return { _error: "config endpoint returned HTTP " + r.status };
    }
    return await r.json();
  } catch (e) {
    console.warn("[gift] /api/config fetch failed", e);
    return { _error: "could not reach /api/config: " + (e && e.message || e) };
  }
}

function buildGiftCheckoutUrl(packUrl, fields) {
  // Stripe Payment Links pass metadata via `?prefilled_metadata[KEY]=VALUE`.
  // Each field is URL-encoded. Stripe truncates keys/values at 500 chars on
  // their end; we already cap the message at 500 in the textarea maxlength.
  const sep = packUrl.includes("?") ? "&" : "?";
  const params = [
    `prefilled_metadata[gift_to_email]=${encodeURIComponent(fields.giftToEmail)}`,
    `prefilled_metadata[gift_message]=${encodeURIComponent(fields.message || "")}`,
    `prefilled_email=${encodeURIComponent(fields.buyerEmail)}`,
  ];
  return `${packUrl}${sep}${params.join("&")}`;
}

async function init() {
  const form = $("#gift-form");
  const errEl = $("#gift-error");
  const btn = $("#gift-buy");
  const cfg = await loadConfig();
  const packUrl = (cfg && cfg.stripe && cfg.stripe.pack_url) || "";

  if (!packUrl) {
    btn.disabled = true;
    btn.textContent = "Gifting opens at launch — join the waitlist on /";
    errEl.style.display = "block";
    errEl.textContent = "Stripe checkout is not yet configured. Check back after launch, or email hello@orphograph.com to be notified.";
    return;
  }

  form.addEventListener("submit", (e) => {
    e.preventDefault();
    const to = ($("#gift-to").value || "").trim();
    const from = ($("#gift-from").value || "").trim();
    const message = ($("#gift-message").value || "").trim().slice(0, 500);
    errEl.style.display = "none";
    if (!to.includes("@") || !from.includes("@")) {
      errEl.style.display = "block";
      errEl.textContent = "Both emails must be valid.";
      return;
    }
    if (to.toLowerCase() === from.toLowerCase()) {
      errEl.style.display = "block";
      errEl.textContent = "Recipient and buyer emails are the same. Use the regular Pack purchase on the homepage instead.";
      return;
    }
    const checkoutUrl = buildGiftCheckoutUrl(packUrl, {
      giftToEmail: to,
      buyerEmail: from,
      message,
    });
    window.location.href = checkoutUrl;
  });
}

init();
