// buy.js — render the BTC payment page and poll for settlement.

const $ = (sel) => document.querySelector(sel);
const POLL_MS = 30_000;

function orderIdFromUrl() {
  const m = location.pathname.match(/^\/buy\/(btc_[A-Za-z0-9_-]{1,32})\/?$/);
  return m ? m[1] : "";
}

function showError(msg) {
  $("#loading").hidden = true;
  $("#order").hidden = true;
  $("#error").hidden = false;
  $("#error-message").textContent = msg;
}

function showSettled(order) {
  $("#loading").hidden = true;
  $("#order").hidden = true;
  $("#settled").hidden = false;
  const tx = order.tx_hash || "";
  const a = $("#tx-link");
  if (tx) {
    a.href = `https://mempool.space/tx/${tx}`;
    a.textContent = tx.slice(0, 12) + "…" + tx.slice(-6);
  } else {
    a.textContent = "(awaiting tx hash)";
  }
}

function renderOrder(order) {
  $("#loading").hidden = true;
  $("#order").hidden = false;
  const sats = order.amount_sats;
  const btc = (sats / 100_000_000).toFixed(8);
  $("#amount-btc").textContent = btc + " BTC";
  $("#amount-sats").textContent = sats.toLocaleString();
  $("#address").textContent = order.address;
  $("#amount-usd").textContent = "≈ $" + (order.usd_amount || 7).toFixed(2) + " USD at order time";
  $("#expires").textContent = order.expires_at;
  const uri = `bitcoin:${order.address}?amount=${btc}&label=Orphograph+Pack`;
  $("#wallet-link").href = uri;
  // Render the BIP-21 payment QR. The SVG is server-rendered (stdlib
  // qrcode_svg.py) and contains ONLY `bitcoin:<addr>?amount=<btc>` —
  // no email, no order id, no label. Loaded via <img> so the browser
  // sandboxes the SVG (no script execution from the response).
  const qr = $("#qr-container");
  if (qr) {
    while (qr.firstChild) qr.removeChild(qr.firstChild);
    const img = document.createElement("img");
    img.alt = "Bitcoin payment QR code";
    img.width = 240;
    img.height = 240;
    img.src = `/api/btc-order/${encodeURIComponent(order.order_id || orderIdFromUrl())}/qr.svg`;
    img.decoding = "async";
    qr.appendChild(img);
  }
  $("#copy-address").addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(order.address);
      $("#copy-address").textContent = "Copied ✓";
      setTimeout(() => { $("#copy-address").textContent = "Copy address"; }, 2500);
    } catch { /* clipboard blocked — user can still select manually */ }
  });
}

async function fetchStatus(orderId) {
  try {
    const r = await fetch(`/api/btc-order/${encodeURIComponent(orderId)}`);
    if (r.status === 404) { showError("Order not found."); return null; }
    if (!r.ok) { return null; }
    return await r.json();
  } catch { return null; }
}

// Stripe success/cancel branch: when buyers land here from Stripe Checkout,
// the URL has ?stripe_session=cs_…&status=success (or ?stripe=canceled).
// We render a confirmation block instead of trying to look up a BTC order.
function stripeSessionFromUrl() {
  return new URLSearchParams(location.search).get("stripe_session") || "";
}
function stripeWasCanceled() {
  return new URLSearchParams(location.search).get("stripe") === "canceled";
}
async function showStripeConfirmation(sessionId) {
  $("#loading").hidden = true;
  $("#order").hidden = true;
  // Reuse the #settled card — it already conveys "transaction complete."
  const settledEl = $("#settled");
  if (settledEl) {
    settledEl.hidden = false;
    // Replace any BTC-specific copy with Stripe-specific success copy.
    const h = settledEl.querySelector("h1, h2");
    if (h) h.textContent = "Payment received.";
    const p = settledEl.querySelector("p");
    if (p) {
      p.textContent =
        "Your Pack code has been emailed. It also lives in your account at /account.html. " +
        "If it does not arrive within a few minutes, check spam, then email hello@orphograph.com.";
    }
    const a = $("#tx-link");
    if (a) {
      a.textContent = "View receipt page →";
      a.href = "/account.html";
    }
  }
  // Best-effort: ask the server to verify the session before showing success.
  // If verification fails (bad id, payment_status != paid), we still show
  // something rather than a broken page — the webhook is the source of truth.
  try {
    const r = await fetch("/api/stripe/session?id=" + encodeURIComponent(sessionId));
    if (r.ok) {
      const j = await r.json();
      if (j && j.payment_status && j.payment_status !== "paid") {
        const p = settledEl && settledEl.querySelector("p");
        if (p) p.textContent =
          "Stripe reports this session is " + j.payment_status + ". " +
          "If you completed payment, your Pack code will arrive shortly — the webhook is final source of truth.";
      }
    }
  } catch (e) { /* keep the optimistic success view */ }
}
function showStripeCanceled() {
  $("#loading").hidden = true;
  $("#order").hidden = true;
  $("#error").hidden = false;
  $("#error-message").textContent =
    "Checkout was canceled. Your card was not charged. " +
    "Head back to the home page to try again.";
}

async function main() {
  // Stripe branch takes priority — it adds query strings, not path segments.
  if (stripeWasCanceled()) { showStripeCanceled(); return; }
  const ssid = stripeSessionFromUrl();
  if (ssid) { await showStripeConfirmation(ssid); return; }

  const orderId = orderIdFromUrl();
  if (!orderId) { showError("Bad URL. Start over from the home page."); return; }
  const order = await fetchStatus(orderId);
  if (!order) return;

  // Hydrate the bulk fields once. Status updates only.
  // We need amount_btc + usd_amount for display — also fetch from order.
  if (!order.usd_amount) {
    // Older response shape; try to recover.
    order.usd_amount = 7;
  }
  renderOrder(order);
  if (order.status === "settled") { showSettled(order); return; }
  if (order.status === "expired") {
    showError("This order expired before payment was detected. Start a new order from the home page.");
    return;
  }
  // Poll for status changes.
  setInterval(async () => {
    const fresh = await fetchStatus(orderId);
    if (!fresh) return;
    if (fresh.status === "settled") { showSettled(fresh); }
    if (fresh.status === "expired") { showError("Order expired."); }
  }, POLL_MS);
}

main();
