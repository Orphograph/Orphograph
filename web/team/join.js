(async function () {
  const params = new URLSearchParams(location.search);
  const code = (params.get("code") || "").trim();
  const codeEl = document.getElementById("invite-code");
  if (!code) {
    codeEl.textContent = "(no invite code in URL — ask the team owner for a fresh link)";
    return;
  }
  codeEl.textContent = code;

  let me = null;
  try {
    const r = await fetch("/api/me", { credentials: "same-origin" });
    if (r.ok) me = await r.json();
  } catch {}

  if (!me || !me.email) {
    document.getElementById("signed-out").hidden = false;
    return;
  }

  document.getElementById("signed-in").hidden = false;
  document.getElementById("signed-in-email").textContent = "Signed in as " + me.email;
  document.getElementById("redeem-btn").addEventListener("click", async () => {
    const r = await fetch("/api/me/team/redeem", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "same-origin",
      body: JSON.stringify({ invite_code: code }),
    });
    const data = await r.json().catch(() => ({}));
    const result = document.getElementById("join-result");
    result.hidden = false;
    if (data.ok) {
      result.textContent = "Joined! Redirecting to your account…";
      setTimeout(() => { location.href = "/account"; }, 800);
    } else {
      result.textContent = data.error || "Could not redeem invite.";
    }
  });
})();
