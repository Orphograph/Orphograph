// signin.js — POST email to /api/auth/email-link, show neutral confirmation.

const form = document.getElementById("signin-form");
const msg = document.getElementById("signin-msg");
const btn = document.getElementById("submit-btn");

form.addEventListener("submit", async (e) => {
  e.preventDefault();
  const email = document.getElementById("email").value.trim();
  btn.disabled = true;
  btn.textContent = "Sending…";
  try {
    const r = await fetch("/api/auth/email-link", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email }),
    });
    const data = await r.json();
    msg.hidden = false;
    msg.textContent = data.message || "Check your inbox.";
  } catch {
    msg.hidden = false;
    msg.textContent = "Network error. Try again in a moment.";
  } finally {
    btn.disabled = false;
    btn.textContent = "Email me a link";
  }
});
