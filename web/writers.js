// writers.js — Draft-chain workbench for /writers.html
//
// Behavior (observable, not promissory):
//   • Each pasted version is hashed in-browser with SHA-256 + SHA-512 via
//     window.crypto.subtle.
//   • The list of {sha256, sha512, length, timestamp_local} is held in memory
//     and mirrored to localStorage under key `orpho_writer_session:<id>`.
//   • On "Anchor my chain", a Merkle root is built over the per-version
//     sha256 hashes (binary tree; odd leaf duplicated). Only that root
//     (and its sha512 sibling) is POSTed to /api/anchor — the same endpoint
//     the main page uses, no server change.
//   • The receipt + full version list is then saved under
//     `orpho_writer_sessions[<id>]` and offered as a downloadable JSON.
//   • Verify pane: paste/drop a manifest, recompute Merkle root, compare.
//
// Strict CSP: no inline scripts, no external requests. Same-origin
// /api/anchor only. DOM writes use textContent, never innerHTML.

"use strict";

const SESSION_STORAGE_PREFIX = "orpho_writer_session:";
const ALL_SESSIONS_KEY = "orpho_writer_sessions";

const $ = (id) => document.getElementById(id);

// ─── Hex / hashing helpers ──────────────────────────────────────────────
function hexOf(buf) {
  const arr = new Uint8Array(buf);
  let out = "";
  for (let i = 0; i < arr.length; i++) {
    out += arr[i].toString(16).padStart(2, "0");
  }
  return out;
}

function hexToBytes(hex) {
  if (typeof hex !== "string" || hex.length % 2 !== 0) {
    throw new Error("invalid hex");
  }
  const out = new Uint8Array(hex.length / 2);
  for (let i = 0; i < out.length; i++) {
    const b = parseInt(hex.substr(i * 2, 2), 16);
    if (Number.isNaN(b)) throw new Error("invalid hex");
    out[i] = b;
  }
  return out;
}

async function sha256Hex(input) {
  // input: string OR Uint8Array
  const bytes = typeof input === "string"
    ? new TextEncoder().encode(input)
    : input;
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  return hexOf(digest);
}

async function sha512Hex(input) {
  const bytes = typeof input === "string"
    ? new TextEncoder().encode(input)
    : input;
  const digest = await crypto.subtle.digest("SHA-512", bytes);
  return hexOf(digest);
}

// ─── Session id ─────────────────────────────────────────────────────────
function newSessionId() {
  // 16 chars URL-safe (96 bits of entropy is plenty for a client-only id).
  const bytes = new Uint8Array(12);
  crypto.getRandomValues(bytes);
  // base64url
  let b64 = btoa(String.fromCharCode.apply(null, bytes));
  b64 = b64.replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
  return b64.slice(0, 16);
}

// ─── State ──────────────────────────────────────────────────────────────
let state = {
  session_id: null,
  versions: [],   // [{sha256, sha512, length, timestamp_local}]
  anchored: false,
  receipt: null,
  merkle_root: null,
};

function loadOrInitSession() {
  // We don't auto-resume across reloads to avoid surprising the user;
  // we just create a fresh in-memory session on page load. Saved sessions
  // remain in localStorage and can be re-loaded by pasting their manifest
  // into the verify pane.
  state.session_id = newSessionId();
  state.versions = [];
  state.anchored = false;
  state.receipt = null;
  state.merkle_root = null;
  persistDraftSession();
  renderSessionMeta();
  renderVersions();
}

function persistDraftSession() {
  try {
    const key = SESSION_STORAGE_PREFIX + state.session_id;
    const payload = {
      session_id: state.session_id,
      versions: state.versions,
      anchored: state.anchored,
      receipt: state.receipt,
      merkle_root: state.merkle_root,
      updated_at: new Date().toISOString(),
    };
    localStorage.setItem(key, JSON.stringify(payload));
  } catch (e) {
    // localStorage may be disabled or full; the in-memory state still works.
  }
}

function saveAnchoredSessionToIndex() {
  try {
    let idx;
    try {
      idx = JSON.parse(localStorage.getItem(ALL_SESSIONS_KEY) || "{}");
      if (!idx || typeof idx !== "object") idx = {};
    } catch { idx = {}; }
    idx[state.session_id] = buildManifest();
    localStorage.setItem(ALL_SESSIONS_KEY, JSON.stringify(idx));
  } catch (e) {
    // best-effort; manifest download still works.
  }
}

// ─── Merkle root ────────────────────────────────────────────────────────
// Binary tree, pairs hashed with SHA-256, odd leaf duplicated.
// Leaves are the BYTES of the per-version sha256 (32 bytes each).
async function merkleRoot(hexHashes) {
  if (!hexHashes.length) throw new Error("no leaves");
  let level = hexHashes.map(hexToBytes);
  while (level.length > 1) {
    const next = [];
    for (let i = 0; i < level.length; i += 2) {
      const left = level[i];
      const right = (i + 1 < level.length) ? level[i + 1] : level[i];
      const concat = new Uint8Array(left.length + right.length);
      concat.set(left, 0);
      concat.set(right, left.length);
      const digest = await crypto.subtle.digest("SHA-256", concat);
      next.push(new Uint8Array(digest));
    }
    level = next;
  }
  return hexOf(level[0]);
}

// ─── Rendering ──────────────────────────────────────────────────────────
function renderSessionMeta() {
  const idLabel = $("session-id-label");
  if (idLabel) {
    idLabel.textContent = state.session_id
      ? "session " + state.session_id
      : "session not started";
  }
  const count = $("version-count");
  if (count) {
    const n = state.versions.length;
    count.textContent = n + (n === 1 ? " version added" : " versions added");
  }
  const anchorBtn = $("anchor-chain-btn");
  if (anchorBtn) {
    anchorBtn.disabled = state.versions.length === 0 || state.anchored;
    anchorBtn.textContent = state.anchored
      ? "Chain anchored"
      : "Anchor my chain";
  }
}

function renderVersions() {
  const list = $("versions-list");
  const body = $("versions-body");
  if (!list || !body) return;
  while (body.firstChild) body.removeChild(body.firstChild);
  if (!state.versions.length) {
    list.hidden = true;
    return;
  }
  list.hidden = false;
  state.versions.forEach((v, i) => {
    const li = document.createElement("li");

    const idx = document.createElement("span");
    idx.className = "ver-idx";
    idx.textContent = "#" + String(i + 1).padStart(2, "0");

    const hash = document.createElement("span");
    hash.className = "ver-hash";
    hash.textContent = v.sha256.slice(0, 24) + "…";

    const meta = document.createElement("span");
    meta.className = "ver-meta";
    const t = v.timestamp_local
      ? new Date(v.timestamp_local).toLocaleTimeString()
      : "";
    meta.textContent = (v.length + " chars · " + t).trim();

    li.appendChild(idx);
    li.appendChild(hash);
    li.appendChild(meta);
    body.appendChild(li);
  });
}

function setStatus(target, msg, kind) {
  const el = typeof target === "string" ? $(target) : target;
  if (!el) return;
  el.classList.remove("ok", "err");
  if (kind === "ok") el.classList.add("ok");
  if (kind === "err") el.classList.add("err");
  el.textContent = msg || "";
}

// ─── Actions ────────────────────────────────────────────────────────────
async function addVersion() {
  if (state.anchored) {
    setStatus("writers-status",
      "This chain is already anchored. Start a new session to add more versions.",
      "err");
    return;
  }
  const ta = $("draft-input");
  if (!ta) return;
  const text = ta.value;
  if (!text || !text.trim()) {
    setStatus("writers-status", "Paste a version of your draft first.", "err");
    return;
  }
  setStatus("writers-status", "Hashing locally…");
  try {
    const [s256, s512] = await Promise.all([sha256Hex(text), sha512Hex(text)]);
    const rec = {
      sha256: s256,
      sha512: s512,
      length: text.length,
      timestamp_local: new Date().toISOString(),
    };
    state.versions.push(rec);
    persistDraftSession();
    ta.value = "";
    renderSessionMeta();
    renderVersions();
    setStatus("writers-status",
      "Version added. " + state.versions.length + " in chain. Paste the next version when ready.",
      "ok");
  } catch (e) {
    setStatus("writers-status", "Could not hash: " + (e && e.message ? e.message : e), "err");
  }
}

function buildManifest() {
  return {
    format: "orpho-writer-session/v1",
    session_id: state.session_id,
    created_at: state.versions.length ? state.versions[0].timestamp_local : new Date().toISOString(),
    versions: state.versions.map((v) => ({
      sha256: v.sha256,
      sha512: v.sha512,
      length: v.length,
      timestamp_local: v.timestamp_local,
    })),
    merkle_root_sha256: state.merkle_root,
    anchored: state.anchored,
    receipt: state.receipt,
  };
}

async function anchorChain() {
  if (state.anchored) return;
  if (!state.versions.length) {
    setStatus("writers-status", "Add at least one version before anchoring.", "err");
    return;
  }
  const btn = $("anchor-chain-btn");
  if (btn) btn.disabled = true;

  setStatus("writers-status", "Computing Merkle root locally…");
  let root, rootSha512;
  try {
    const leaves = state.versions.map((v) => v.sha256);
    root = await merkleRoot(leaves);
    // sibling sha512 binding — same shape the main /api/anchor expects.
    // hash the 32-byte root, not the hex string, to match the wire convention.
    rootSha512 = await sha512Hex(hexToBytes(root));
  } catch (e) {
    setStatus("writers-status", "Could not compute Merkle root: " + (e.message || e), "err");
    if (btn) btn.disabled = false;
    return;
  }
  state.merkle_root = root;

  setStatus("writers-status", "Submitting Merkle root to the calendars…");
  let resp;
  try {
    resp = await fetch("/api/anchor", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        hash_hex: root,
        sha512_hex: rootSha512,
        client_label: "writer-session",
      }),
    });
  } catch (e) {
    setStatus("writers-status", "Network error reaching the office: " + (e.message || e), "err");
    if (btn) btn.disabled = false;
    return;
  }

  if (!resp.ok) {
    let errText = resp.statusText;
    try {
      const j = await resp.json();
      errText = j.error || errText;
      if (resp.status === 429) {
        const sec = j.retry_after_seconds || 60;
        errText = "Free-tier limit reached. Try again in " + sec + "s, or buy a Writer Pack.";
      }
    } catch {}
    setStatus("writers-status", "Anchor failed: " + errText, "err");
    if (btn) btn.disabled = false;
    return;
  }

  let record;
  try {
    record = await resp.json();
  } catch (e) {
    setStatus("writers-status", "Anchor response was not valid JSON.", "err");
    if (btn) btn.disabled = false;
    return;
  }

  state.anchored = true;
  state.receipt = record;
  persistDraftSession();
  saveAnchoredSessionToIndex();
  renderSessionMeta();

  // Receipt UI
  const receiptBox = $("writers-receipt");
  const idLine = $("receipt-id-line");
  const link = $("receipt-link");
  if (receiptBox && idLine && link) {
    const rid = record.id || record.receipt_id || "(unknown id)";
    idLine.textContent = "Receipt ID: " + rid + " · Merkle root: " + root.slice(0, 24) + "…";
    if (record.id) {
      link.href = "/r/" + encodeURIComponent(record.id);
      link.textContent = "Open receipt page (/r/" + record.id + ")";
    } else {
      link.removeAttribute("href");
      link.textContent = "(receipt URL unavailable)";
    }
    receiptBox.hidden = false;
  }

  setStatus("writers-status",
    "Anchored. Merkle root submitted to 5 calendars. Within ~1 hour it will be committed in a Bitcoin block.",
    "ok");
}

function downloadManifest() {
  const manifest = buildManifest();
  const blob = new Blob([JSON.stringify(manifest, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = "orphograph-writer-session-" + state.session_id + ".json";
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function resetSession() {
  if (state.versions.length && !state.anchored) {
    const ok = confirm("Discard the current unsaved chain and start over?");
    if (!ok) return;
  }
  const ta = $("draft-input");
  if (ta) ta.value = "";
  const receiptBox = $("writers-receipt");
  if (receiptBox) receiptBox.hidden = true;
  setStatus("writers-status", "");
  loadOrInitSession();
}

// ─── Verify pane ────────────────────────────────────────────────────────
async function verifyManifest(raw) {
  setStatus("verify-status", "Parsing manifest…");
  let m;
  try {
    m = JSON.parse(raw);
  } catch (e) {
    setStatus("verify-status", "Not valid JSON: " + (e.message || e), "err");
    return;
  }
  if (!m || typeof m !== "object") {
    setStatus("verify-status", "Manifest is not an object.", "err");
    return;
  }
  if (!Array.isArray(m.versions) || !m.versions.length) {
    setStatus("verify-status", "Manifest has no versions[] array.", "err");
    return;
  }
  const claimedRoot = (m.merkle_root_sha256 || "").toLowerCase();
  const receiptHash = m.receipt && m.receipt.hash_hex ? String(m.receipt.hash_hex).toLowerCase() : "";

  setStatus("verify-status", "Recomputing Merkle root from " + m.versions.length + " version hashes…");
  let computed;
  try {
    const leaves = m.versions.map((v) => {
      if (!v || typeof v.sha256 !== "string" || v.sha256.length !== 64) {
        throw new Error("a version entry is missing a 64-hex sha256");
      }
      return v.sha256.toLowerCase();
    });
    computed = await merkleRoot(leaves);
  } catch (e) {
    setStatus("verify-status", "Could not recompute root: " + (e.message || e), "err");
    return;
  }

  const matchesClaimed = claimedRoot && claimedRoot === computed;
  const matchesReceipt = receiptHash && receiptHash === computed;

  if (matchesReceipt) {
    setStatus("verify-status",
      "PASS · recomputed root " + computed.slice(0, 24) + "… matches the receipt hash.",
      "ok");
  } else if (matchesClaimed) {
    setStatus("verify-status",
      "PASS (no receipt in manifest) · recomputed root matches the stored merkle_root_sha256.",
      "ok");
  } else {
    const expected = receiptHash || claimedRoot || "(none in manifest)";
    setStatus("verify-status",
      "FAIL · recomputed " + computed.slice(0, 24) + "… expected " + (expected.slice(0, 24) || "") + "…",
      "err");
  }
}

function readFileAsText(file) {
  return new Promise((resolve, reject) => {
    const fr = new FileReader();
    fr.onerror = () => reject(fr.error || new Error("read failed"));
    fr.onload = () => resolve(String(fr.result || ""));
    fr.readAsText(file);
  });
}

// ─── Wire up ────────────────────────────────────────────────────────────
function bind() {
  const addBtn = $("add-version-btn");
  if (addBtn) addBtn.addEventListener("click", addVersion);

  const anchorBtn = $("anchor-chain-btn");
  if (anchorBtn) anchorBtn.addEventListener("click", anchorChain);

  const dlBtn = $("download-manifest-btn");
  if (dlBtn) dlBtn.addEventListener("click", downloadManifest);

  const resetBtn = $("reset-session-btn");
  if (resetBtn) resetBtn.addEventListener("click", resetSession);

  const verifyBtn = $("verify-btn");
  if (verifyBtn) {
    verifyBtn.addEventListener("click", () => {
      const ta = $("verify-input");
      if (!ta || !ta.value.trim()) {
        setStatus("verify-status", "Paste a manifest, or choose a file.", "err");
        return;
      }
      verifyManifest(ta.value);
    });
  }

  const verifyFileBtn = $("verify-file-btn");
  const verifyFileInput = $("verify-file-input");
  if (verifyFileBtn && verifyFileInput) {
    verifyFileBtn.addEventListener("click", () => verifyFileInput.click());
    verifyFileInput.addEventListener("change", async () => {
      const f = verifyFileInput.files && verifyFileInput.files[0];
      if (!f) return;
      try {
        const text = await readFileAsText(f);
        const ta = $("verify-input");
        if (ta) ta.value = text;
        await verifyManifest(text);
      } catch (e) {
        setStatus("verify-status", "Could not read file: " + (e.message || e), "err");
      } finally {
        verifyFileInput.value = "";
      }
    });
  }

  // Drag-and-drop the manifest onto the verify pane.
  const verifyCard = document.querySelector(".verify-card");
  const verifyTA = $("verify-input");
  if (verifyCard && verifyTA) {
    ["dragover", "dragenter"].forEach((ev) => {
      verifyCard.addEventListener(ev, (e) => {
        e.preventDefault();
        verifyCard.classList.add("dragover");
      });
    });
    ["dragleave", "drop"].forEach((ev) => {
      verifyCard.addEventListener(ev, () => verifyCard.classList.remove("dragover"));
    });
    verifyCard.addEventListener("drop", async (e) => {
      e.preventDefault();
      const f = e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files[0];
      if (!f) return;
      try {
        const text = await readFileAsText(f);
        verifyTA.value = text;
        await verifyManifest(text);
      } catch (err) {
        setStatus("verify-status", "Could not read file: " + (err.message || err), "err");
      }
    });
  }
}

document.addEventListener("DOMContentLoaded", () => {
  if (!window.crypto || !window.crypto.subtle) {
    setStatus("writers-status",
      "This browser does not expose the Web Crypto API. Use a recent Firefox, Chrome, Safari, or Edge.",
      "err");
    const addBtn = $("add-version-btn");
    if (addBtn) addBtn.disabled = true;
    return;
  }
  loadOrInitSession();
  bind();
});
