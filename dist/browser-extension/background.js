// background.js — Orphograph browser extension service worker (MV3)
//
// Listens for right-click on images/links/pages. When triggered:
//   1. Fetches the target as a Blob (browser request, not extension upload)
//   2. Computes SHA-256 client-side via WebCrypto
//   3. POSTs only the hash to https://orphograph.com/api/anchor
//   4. Shows a notification with the receipt link
//
// The file itself is never transmitted to Orphograph — only the 32-byte
// SHA-256 digest. Same privacy invariant as the web app.

const API_BASE = "https://orphograph.com";
const CONTEXT_MENU_ID = "orphograph-anchor";
// The file name is a label stored with the receipt. It is never sent
// unless the user is asked and says yes: the plain entry sends no label,
// and the labelled entry opens ask.html, whose default answer is no label.
const CONTEXT_MENU_LABEL_ID = "orphograph-anchor-label";

chrome.runtime.onInstalled.addListener(() => {
  chrome.contextMenus.create({
    id: CONTEXT_MENU_ID,
    title: "Anchor with Orphograph (hash only, file stays local)",
    contexts: ["image", "link", "video", "audio"],
  });
  chrome.contextMenus.create({
    id: CONTEXT_MENU_LABEL_ID,
    title: "Anchor with Orphograph and a file-name label (asks first)",
    contexts: ["image", "link", "video", "audio"],
  });
});

// The last path segment of the URL, without query or fragment (those carry
// tokens and session ids), decoded, at most 200 characters; "" when none.
function proposedLabel(target) {
  let name = "";
  try {
    name = new URL(target).pathname.split("/").pop() || "";
  } catch {
    return "";
  }
  try {
    name = decodeURIComponent(name);
  } catch {
    // keep the raw segment
  }
  return name.trim().slice(0, 200);
}

async function sha256Hex(buf) {
  const digest = await crypto.subtle.digest("SHA-256", buf);
  return [...new Uint8Array(digest)]
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");
}

async function sha512Hex(buf) {
  const digest = await crypto.subtle.digest("SHA-512", buf);
  return [...new Uint8Array(digest)]
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");
}

async function fetchAndHash(url) {
  const r = await fetch(url, { credentials: "omit", mode: "cors" });
  if (!r.ok) throw new Error(`fetch failed: ${r.status}`);
  const buf = await r.arrayBuffer();
  const [s256, s512] = await Promise.all([sha256Hex(buf), sha512Hex(buf)]);
  return { sha256: s256, sha512: s512, size: buf.byteLength };
}

async function anchorHash({ sha256, sha512, clientLabel, packToken, apiKey }) {
  const headers = { "Content-Type": "application/json" };
  if (packToken) headers["X-Pack-Token"] = packToken;
  if (apiKey) headers["X-Orpho-Api-Key"] = apiKey;
  const r = await fetch(`${API_BASE}/api/anchor`, {
    method: "POST",
    headers,
    credentials: "omit",
    body: JSON.stringify({
      hash_hex: sha256,
      sha512_hex: sha512,
      client_label: clientLabel || null,
    }),
  });
  const txt = await r.text();
  let body;
  try {
    body = JSON.parse(txt);
  } catch {
    body = { error: "unexpected response" };
  }
  if (!r.ok) throw new Error(body.error || `anchor failed: ${r.status}`);
  return body;
}

function notify(title, message, receiptId) {
  chrome.notifications.create(
    {
      type: "basic",
      iconUrl: "icons/icon-128.png",
      title,
      message,
      isClickable: !!receiptId,
    },
    (id) => {
      if (receiptId) {
        chrome.notifications.onClicked.addListener(function handler(nid) {
          if (nid === id) {
            chrome.tabs.create({ url: `${API_BASE}/r/${receiptId}` });
            chrome.notifications.onClicked.removeListener(handler);
          }
        });
      }
    },
  );
}

async function anchorTarget(target, clientLabel) {
  notify("Orphograph", "Hashing file locally…");
  try {
    const { sha256, sha512, size } = await fetchAndHash(target);
    const settings = await chrome.storage.local.get(["packToken", "apiKey"]);
    const result = await anchorHash({
      sha256,
      sha512,
      clientLabel: clientLabel || null,
      packToken: settings.packToken,
      apiKey: settings.apiKey,
    });
    const remaining = result.pack_remaining;
    notify(
      "Anchored to Bitcoin",
      `Receipt ${result.receipt_id} created (${size} bytes hashed).${
        typeof remaining === "number" ? ` Pack credits left: ${remaining}.` : ""
      }`,
      result.receipt_id,
    );
  } catch (e) {
    notify("Orphograph error", String(e.message || e));
  }
}

chrome.contextMenus.onClicked.addListener(async (info, tab) => {
  if (info.menuItemId !== CONTEXT_MENU_ID && info.menuItemId !== CONTEXT_MENU_LABEL_ID) return;
  const target = info.srcUrl || info.linkUrl || info.pageUrl;
  if (!target) {
    notify("Orphograph", "No file to anchor on this element.");
    return;
  }
  const label = proposedLabel(target);
  if (info.menuItemId === CONTEXT_MENU_ID || !label) {
    await anchorTarget(target, null);
    return;
  }
  // Ask before the name goes anywhere. The question waits in session
  // storage (the worker may sleep while the window is open).
  const askId = crypto.randomUUID();
  await chrome.storage.session.set({ [`ask:${askId}`]: { target, label } });
  await chrome.windows.create({
    url: `ask.html#${askId}`,
    type: "popup",
    width: 420,
    height: 260,
  });
});

chrome.runtime.onMessage.addListener((msg, sender) => {
  if (!msg || msg.type !== "orpho-ask-answer" || typeof msg.askId !== "string") return;
  // Only the extension's own ask page answers.
  if (sender.id !== chrome.runtime.id) return;
  const key = `ask:${msg.askId}`;
  (async () => {
    const stored = await chrome.storage.session.get(key);
    const pending = stored[key];
    if (!pending) return;
    await chrome.storage.session.remove(key);
    if (msg.answer === "cancel") return;
    await anchorTarget(pending.target, msg.answer === "label" ? pending.label : null);
  })();
});
