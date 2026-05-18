// crypto.js — drives /pay/crypto.html.
//
// Posts {currency, plan, email} to /api/nowpayments/create and redirects
// the buyer to the NOWPayments-hosted invoice on success. textContent
// only — strict-CSP friendly. No inline scripts, no innerHTML.

(function () {
  "use strict";

  function $(sel) { return document.querySelector(sel); }

  var form = $("#crypto-form");
  var coinSel = $("#coin");
  var emailInput = $("#email");
  var submitBtn = $("#pay-submit");
  var msgEl = $("#pay-msg");
  var packPick = $("#pack-pick");
  if (!form || !coinSel || !submitBtn || !packPick) return;

  var currentPlan = "writer_pack";

  // Pack toggle (writer_pack / pack_50). Buttons inside #pack-pick use
  // data-plan attributes; clicking one becomes the active selection.
  var packButtons = packPick.querySelectorAll("button[data-plan]");
  packButtons.forEach(function (btn) {
    btn.addEventListener("click", function () {
      var plan = btn.getAttribute("data-plan");
      if (!plan) return;
      currentPlan = plan;
      packButtons.forEach(function (b) { b.classList.remove("active"); });
      btn.classList.add("active");
    });
  });

  function setMsg(text, isError) {
    msgEl.textContent = text || "";
    if (isError) {
      msgEl.classList.add("error");
    } else {
      msgEl.classList.remove("error");
    }
  }

  function lockUI(locked) {
    submitBtn.disabled = !!locked;
    coinSel.disabled = !!locked;
    emailInput.disabled = !!locked;
    packButtons.forEach(function (b) { b.disabled = !!locked; });
  }

  form.addEventListener("submit", function (ev) {
    ev.preventDefault();

    var coin = (coinSel.value || "").trim().toLowerCase();
    var email = (emailInput.value || "").trim();

    if (!coin) {
      setMsg("Pick a coin to continue.", true);
      return;
    }
    if (email && email.indexOf("@") === -1) {
      setMsg("That email looks off. Leave it blank if you don't have one handy.", true);
      return;
    }

    lockUI(true);
    setMsg("Creating invoice…", false);

    var body = JSON.stringify({
      currency: coin,
      plan: currentPlan,
      email: email,
    });

    fetch("/api/nowpayments/create", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: body,
      credentials: "same-origin",
    }).then(function (r) {
      return r.json().then(function (data) { return { ok: r.ok, status: r.status, data: data }; })
        .catch(function () { return { ok: r.ok, status: r.status, data: {} }; });
    }).then(function (res) {
      var data = res.data || {};
      if (res.ok && data.url) {
        setMsg("Redirecting to NOWPayments…", false);
        // Use assign so the back-button still works.
        window.location.assign(data.url);
        return;
      }
      if (res.status === 503) {
        setMsg(
          data.error ||
          "Crypto checkout isn't enabled right now. Try the card option, or pay in BTC.",
          true
        );
      } else if (res.status === 400) {
        setMsg(data.error || "Couldn't create the invoice — please check the coin and plan.", true);
      } else {
        setMsg(data.error || "Payment provider didn't respond. Try again in a moment.", true);
      }
      lockUI(false);
    }).catch(function () {
      setMsg("Network error. Try again.", true);
      lockUI(false);
    });
  });
})();
