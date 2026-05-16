// popup.js — credential save UI for Orphograph extension.

const $ = (id) => document.getElementById(id);

async function load() {
  const s = await chrome.storage.local.get(["packToken", "apiKey"]);
  if (s.packToken) $("pack").value = s.packToken;
  if (s.apiKey) $("api").value = s.apiKey;
}

async function save() {
  const packToken = ($("pack").value || "").trim();
  const apiKey = ($("api").value || "").trim();
  await chrome.storage.local.set({ packToken, apiKey });
  const status = $("status");
  status.hidden = false;
  status.textContent = "Saved locally — never sent to Orphograph.";
  setTimeout(() => {
    status.hidden = true;
  }, 2500);
}

$("save").addEventListener("click", save);
load();
