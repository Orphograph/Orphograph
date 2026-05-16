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

chrome.runtime.onInstalled.addListener(() => {
  chrome.contextMenus.create({
    id: CONTEXT_MENU_ID,
    title: "Anchor with Orphograph (hash only, file stays local)",
    contexts: ["image", "link", "video", "audio"],
  });
});

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

chrome.contextMenus.onClicked.addListener(async (info, tab) => {
  if (info.menuItemId !== CONTEXT_MENU_ID) return;
  const target = info.srcUrl || info.linkUrl || info.pageUrl;
  if (!target) {
    notify("Orphograph", "No file to anchor on this element.");
    return;
  }
  notify("Orphograph", "Hashing file locally…");
  try {
    const { sha256, sha512, size } = await fetchAndHash(target);
    const settings = await chrome.storage.local.get(["packToken", "apiKey"]);
    const result = await anchorHash({
      sha256,
      sha512,
      clientLabel: target.split("/").pop().slice(0, 200),
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
});
