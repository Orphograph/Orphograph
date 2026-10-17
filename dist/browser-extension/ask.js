// ask.js — asks before a receipt is labelled with the file name.
// The default (focused, first) answer is no label; closing the window
// anchors nothing.

const askId = location.hash.slice(1);

function answer(value) {
  chrome.runtime.sendMessage({ type: "orpho-ask-answer", askId, answer: value });
  window.close();
}

async function load() {
  const key = `ask:${askId}`;
  const stored = await chrome.storage.session.get(key);
  const pending = stored[key];
  if (!pending) {
    window.close();
    return;
  }
  document.getElementById("name").textContent = pending.label;
}

document.getElementById("nolabel").addEventListener("click", () => answer("nolabel"));
document.getElementById("label").addEventListener("click", () => answer("label"));
document.getElementById("cancel").addEventListener("click", () => answer("cancel"));
load();
