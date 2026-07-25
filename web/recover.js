(function () {
  "use strict";
  var form = document.getElementById("rec-form");
  var sessionEl = document.getElementById("rec-session");
  var emailEl = document.getElementById("rec-email");
  var submitBtn = document.getElementById("rec-submit");
  var resultEl = document.getElementById("rec-result");

  function showResult(kind, text) {
    resultEl.className = kind;
    resultEl.textContent = text;
  }

  form.addEventListener("submit", function (ev) {
    ev.preventDefault();
    var sid = (sessionEl.value || "").trim();
    var email = (emailEl.value || "").trim();
    if (!sid || !email) {
      showResult("err", "Both fields are required.");
      return;
    }
    var isStripe = /^cs_(test|live)_[A-Za-z0-9_]+$/.test(sid);
    var isCrypto = /^np_[A-Za-z0-9_-]{1,61}$/.test(sid);
    if (!isStripe && !isCrypto) {
      showResult("err", "Payment identifier is not in the expected format. A card session begins with cs_live_ or cs_test_; a crypto order begins with np_.");
      return;
    }
    submitBtn.disabled = true;
    showResult("wait", isCrypto ? "Looking up the payment on file…" : "Verifying with the payment processor…");
    fetch("/api/recover", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ stripe_session_id: sid, email: email })
    })
      .then(function (r) { return r.json().then(function (j) { return { status: r.status, body: j }; }); })
      .then(function (res) {
        submitBtn.disabled = false;
        if (res.status === 200 && res.body && res.body.ok) {
          showResult("ok", res.body.message || "The replacement instrument has been issued.");
          sessionEl.value = "";
        } else if (res.status === 202 && res.body && res.body.retryable) {
          showResult("wait", res.body.message || "Payment is on file but fulfillment has not yet completed. Try again in five minutes.");
        } else {
          showResult("err", (res.body && res.body.error) ? res.body.error : "Recovery could not be completed. Verify the session identifier and the email match exactly.");
        }
      })
      .catch(function () {
        submitBtn.disabled = false;
        showResult("err", "Network interruption. Try again in a moment.");
      });
  });
})();
