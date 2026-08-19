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
  const settledEl = $("#settled");
  if (!settledEl) return;
  settledEl.hidden = false;
  if (typeof window !== "undefined" && typeof window.orphoEvent === "function") {
    try { window.orphoEvent("checkout_returned_success"); } catch (e) {}
  }

  let mode = "";
  let customerEmail = "";
  let paymentStatus = "";
  try {
    const r = await fetch("/api/stripe/session?id=" + encodeURIComponent(sessionId));
    if (r.ok) {
      const j = await r.json();
      mode = (j && j.mode) || "";
      customerEmail = (j && j.customer_email) || "";
      paymentStatus = (j && j.payment_status) || "";
    }
  } catch (e) { /* fall back to generic copy below */ }

  const h = settledEl.querySelector("h1, h2");
  const p = settledEl.querySelector("p");
  const a = $("#tx-link");

  if (paymentStatus && paymentStatus !== "paid") {
    if (h) h.textContent = "Payment pending.";
    if (p) p.textContent =
      "Stripe reports this session is " + paymentStatus + ". " +
      "If you completed payment, your access will arrive shortly — confirmation is recorded automatically once Stripe reports the charge. Check back in a few minutes.";
    if (a) { a.textContent = ""; a.removeAttribute("href"); }
    return;
  }

  if (mode === "subscription") {
    if (h) h.textContent = "Subscription active.";
    if (p) {
      while (p.firstChild) p.removeChild(p.firstChild);
      p.appendChild(document.createTextNode(
        "Your subscription is active. There is no claim code — sign in with your email to start anchoring on your plan. "
      ));
      if (customerEmail) {
        p.appendChild(document.createTextNode("Use "));
        const code = document.createElement("code");
        code.textContent = customerEmail;
        p.appendChild(code);
        p.appendChild(document.createTextNode(" on the sign-in page. "));
      } else {
        p.appendChild(document.createTextNode(
          "Use the same email you paid with on the sign-in page. "
        ));
      }
      p.appendChild(document.createTextNode(
        "A welcome email is on its way; if you don't see it, check spam, then proceed to sign-in below."
      ));
    }
    if (a) {
      a.textContent = "Sign in to your account →";
      a.href = "/signin";
    }
    return;
  }

  // Default: one-time Pack purchase.
  if (h) h.textContent = "Payment received.";
  if (p) p.textContent =
    "Your Pack code has been emailed. If it doesn't arrive within a few minutes, check spam — then reply to the welcome email or head to your account.";
  if (a) {
    a.textContent = "Open your account →";
    a.href = "/account";
  }
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
