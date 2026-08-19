// pay-btc.js — Bitcoin payment page logic.
// - Fetches live BTC/USD from the same-origin /api/btc/price proxy
//   (server-side multi-oracle cache; no third-party hosts in the browser).
// - Builds a BIP-21 `bitcoin:` deep link from the address pinned in this
//   file, so the wallet app receives the destination and amount without a
//   request leaving the page. The strict CSP (img-src 'self', connect-src
//   'self') holds with zero exceptions.
// - Routes "I paid" submissions to /api/btc/claim.

(function () {
  "use strict";
  const ADDR = "bc1qclvjjmwmr294rydv4x0dc787nx9jd8j4ny4jaz";
  let currentUsd = 19;
  let currentSats = null;
  let btcUsd = null;

  const copyBtn = document.getElementById("copy-addr");
  if (copyBtn) {
    copyBtn.addEventListener("click", () => {
      navigator.clipboard.writeText(ADDR).then(() => {
        const orig = copyBtn.textContent;
        copyBtn.textContent = "Copied";
        setTimeout(() => (copyBtn.textContent = orig), 1400);
      });
    });
  }

  document.querySelectorAll("#pack-pick button").forEach((btn) => {
    btn.addEventListener("click", () => {
      document.querySelectorAll("#pack-pick button").forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      currentUsd = parseFloat(btn.dataset.usd);
      document.getElementById("pay-usd").textContent = "$" + currentUsd;
      updateBtcAmount();
    });
  });

  async function fetchBtcUsd() {
    try {
      const r = await fetch("/api/btc/price");
      if (!r.ok) return null;
      const j = await r.json();
      return j.usd || null;
    } catch (e) { return null; }
  }

  function updateBtcAmount() {
    if (!btcUsd) {
      document.getElementById("pay-btc").textContent = "loading…";
      return;
    }
    const btc = currentUsd / btcUsd;
    currentSats = Math.round(btc * 1e8);
    document.getElementById("pay-btc").textContent = btc.toFixed(8);
    renderWalletLink();
  }

  function renderWalletLink() {
    const link = document.getElementById("wallet-link");
    if (!link) return;
    // BIP-21 URI built client-side from the address pinned in this file.
    // Nothing leaves the page: the wallet app reads the href, no request
    // is made.
    //
    // The amount is OPTIONAL in BIP-21 on purpose. /api/btc/price can be
    // down (it answers 503, and that path is pinned by a test), and the
    // amountless URI still hands the wallet the right destination. Wiring
    // this only on the success path would leave a button-styled link
    // pointing at "#" exactly when the price feed fails, which is the one
    // state where a stranded visitor cannot recover.
    if (!currentSats) {
      link.href = "bitcoin:" + ADDR;
      link.textContent = "Open in your Bitcoin wallet";
      return;
    }
    const btc = (currentSats / 1e8).toFixed(8);
    link.href = "bitcoin:" + ADDR + "?amount=" + btc + "&label=Orphograph%20Pack";
    link.textContent = "Open in your Bitcoin wallet, " + btc + " BTC";
  }

  function setMsg(text) {
    const msg = document.getElementById("claim-msg");
    msg.textContent = text;
  }

  function setMsgWithMailtoFallback(email, txid, note, statusLabel) {
    const msg = document.getElementById("claim-msg");
    msg.textContent = "";
    const span = document.createElement("span");
    span.textContent = statusLabel + " — please email ";
    const a = document.createElement("a");
    a.textContent = "hello@orphograph.com";
    const subject = encodeURIComponent("BTC payment");
    const body = encodeURIComponent(
      "Email: " + email + "\nTXID: " + txid + "\nPack: $" + currentUsd + "\nNote: " + note
    );
    a.href = "mailto:hello@orphograph.com?subject=" + subject + "&body=" + body;
    const tail = document.createElement("span");
    tail.textContent = " with your TXID and we'll send the claim code manually.";
    msg.appendChild(span);
    msg.appendChild(a);
    msg.appendChild(tail);
  }

  const form = document.getElementById("btc-claim-form");
  if (form) {
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      const email = document.getElementById("claim-email").value.trim();
      const txid  = document.getElementById("claim-txid").value.trim();
      const note  = document.getElementById("claim-note").value.trim();
      if (!email.includes("@") || !/^[0-9a-fA-F]{64}$/.test(txid)) {
        setMsg("Please enter a valid email and a 64-hex Bitcoin transaction ID.");
        return;
      }
      setMsg("Submitting…");
      try {
        const r = await fetch("/api/btc/claim", {
          method:  "POST",
          headers: { "Content-Type": "application/json" },
          body:    JSON.stringify({
            email,
            txid,
            note,
            pack_size: parseInt(document.querySelector("#pack-pick button.active").dataset.pack, 10),
            usd: currentUsd,
            btc_amount: currentSats ? currentSats / 1e8 : null,
            btc_address: ADDR,
          }),
        });
        if (r.ok) {
          setMsg("✓ Got it. We're verifying the payment on-chain. Watch your inbox — you'll get the claim code within ~1 hour.");
          form.querySelector("button[type=submit]").disabled = true;
        } else if (r.status === 404) {
          setMsgWithMailtoFallback(email, txid, note, "Backend not yet wired");
        } else {
          setMsgWithMailtoFallback(email, txid, note, "Server error " + r.status);
        }
      } catch (err) {
        setMsgWithMailtoFallback(email, txid, note, "Network error: " + (err.message || err));
      }
    });
  }

  (async () => {
    // Wire the wallet hand-off BEFORE the price request. updateBtcAmount()
    // early-returns while btcUsd is null, so leaving this to that path would
    // strand the link on its placeholder href for as long as the price feed
    // is down. The amountless BIP-21 URI is correct on its own; the amount
    // is an upgrade, not a precondition.
    renderWalletLink();
    btcUsd = await fetchBtcUsd();
    updateBtcAmount();
    setInterval(async () => {
      const p = await fetchBtcUsd();
      if (p) { btcUsd = p; updateBtcAmount(); }
    }, 60000);
  })();
})();
