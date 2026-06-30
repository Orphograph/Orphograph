// folder.js — client-side folder hasher.
// The office never receives the file content. Every file is read locally,
// hashed with SHA-256, and combined into an RFC 6962 Merkle tree. Only the
// manifest (paths + per-file digests) and the root cross the counter.
//
// MIT — see /LICENSE.

// ─── Constants (must match server/merkle.py exactly) ────────────────────
const LEAF_PREFIX = new Uint8Array([0x00]);
const INTERNAL_PREFIX = new Uint8Array([0x01]);
const ALGORITHM_TAG = "orphograph-merkle-v1-rfc6962";
const DEFAULT_EXCLUDES = [
  ".DS_Store", "Thumbs.db", "desktop.ini",
  ".git/", "node_modules/", "__pycache__/", ".tmp", ".swp", ".swo",
];

// v1 per-file cap. Files above this are recorded as "oversize_skipped" in
// the receipt summary and excluded from the tree. Documented in the UI.
const MAX_FILE_BYTES = 500 * 1024 * 1024;

// Strict shape check before any receipt id reaches the DOM (XSS guard plus
// shape-validation guard against a malformed server response).
const RECEIPT_ID_RE = /^[A-Za-z0-9_-]{1,64}$/;

// ─── Small helpers ──────────────────────────────────────────────────────
const _hex = (bytes) =>
  [...new Uint8Array(bytes)].map((b) => b.toString(16).padStart(2, "0")).join("");

function _concat(...parts) {
  let total = 0;
  for (const p of parts) total += p.byteLength;
  const out = new Uint8Array(total);
  let off = 0;
  for (const p of parts) {
    out.set(p instanceof Uint8Array ? p : new Uint8Array(p), off);
    off += p.byteLength;
  }
  return out;
}

async function _sha256(bytes) {
  return new Uint8Array(await crypto.subtle.digest("SHA-256", bytes));
}

function _isExcluded(path) {
  // path is a POSIX-style relative path. Match basename and any path
  // segment that equals an excluded directory, plus suffix-based excludes.
  const segments = path.split("/");
  const base = segments[segments.length - 1] || "";
  for (const pat of DEFAULT_EXCLUDES) {
    if (pat.endsWith("/")) {
      const dir = pat.slice(0, -1);
      if (segments.includes(dir)) return true;
    } else if (pat.startsWith(".")) {
      // Treat patterns like ".tmp" / ".swp" as either filename or suffix.
      if (base === pat) return true;
      if (path.endsWith(pat)) return true;
    } else if (base === pat) {
      return true;
    }
  }
  return false;
}

// Compare two Uint8Arrays lexicographically (UTF-8 byte order).
function _byteCompare(a, b) {
  const len = Math.min(a.length, b.length);
  for (let i = 0; i < len; i++) {
    if (a[i] !== b[i]) return a[i] - b[i];
  }
  return a.length - b.length;
}

// ─── File collection ────────────────────────────────────────────────────
// Prefer File System Access API. Fall back to an <input webkitdirectory>
// click for browsers that lack `showDirectoryPicker`.
async function _collectFiles() {
  if (typeof window.showDirectoryPicker === "function") {
    const root = await window.showDirectoryPicker();
    const out = [];
    await _walkDir(root, "", out);
    return out;
  }
  return _collectViaInput();
}

async function _walkDir(dirHandle, prefix, out) {
  for await (const [name, handle] of dirHandle.entries()) {
    const path = prefix ? `${prefix}/${name}` : name;
    if (handle.kind === "directory") {
      if (_isExcluded(path + "/")) continue;
      await _walkDir(handle, path, out);
    } else if (handle.kind === "file") {
      if (_isExcluded(path)) continue;
      try {
        const file = await handle.getFile();
        out.push({ path, file });
      } catch (e) {
        console.warn("[orphograph/folder] skip unreadable file", path, e);
      }
    }
  }
}

function _collectViaInput() {
  return new Promise((resolve) => {
    const input = document.createElement("input");
    input.type = "file";
    input.multiple = true;
    // Vendor-prefixed attribute — required for directory selection in
    // Chromium/WebKit-based browsers without showDirectoryPicker.
    input.webkitdirectory = true;
    input.style.display = "none";
    input.addEventListener("change", () => {
      const list = [];
      for (const f of input.files || []) {
        // webkitRelativePath includes the chosen folder's name as the first
        // segment. Strip it so paths are folder-relative, matching the
        // showDirectoryPicker branch.
        const rel = f.webkitRelativePath || f.name;
        const segs = rel.split("/");
        const path = segs.length > 1 ? segs.slice(1).join("/") : segs[0];
        if (_isExcluded(path)) continue;
        list.push({ path, file: f });
      }
      document.body.removeChild(input);
      resolve(list);
    }, { once: true });
    document.body.appendChild(input);
    input.click();
  });
}

// ─── Hashing + tree build ───────────────────────────────────────────────
async function _hashFile(file) {
  // Read into a single ArrayBuffer. v1 caps at MAX_FILE_BYTES — chunked
  // streaming hashing requires a polyfill since crypto.subtle.digest has
  // no streaming API in the browser.
  const buf = await file.arrayBuffer();
  return new Uint8Array(await crypto.subtle.digest("SHA-256", buf));
}

async function _leafFor(path, fileDigest) {
  const pathBytes = new TextEncoder().encode(path);
  // leaf = SHA-256(0x00 || path_utf8 || 0x00 || file_sha256)
  const sep = new Uint8Array([0x00]);
  const material = _concat(LEAF_PREFIX, pathBytes, sep, fileDigest);
  return _sha256(material);
}

async function _buildTree(leaves) {
  // RFC 6962 — odd last node promotes (not duplicates).
  if (leaves.length === 0) {
    // The all-empty hash for an empty tree is SHA-256("") under RFC 6962.
    return _sha256(new Uint8Array(0));
  }
  let level = leaves.slice();
  while (level.length > 1) {
    const next = [];
    for (let i = 0; i < level.length; i += 2) {
      if (i + 1 >= level.length) {
        next.push(level[i]);
        continue;
      }
      const combined = _concat(INTERNAL_PREFIX, level[i], level[i + 1]);
      next.push(await _sha256(combined));
    }
    level = next;
  }
  return level[0];
}

// ─── Server submission ──────────────────────────────────────────────────
async function _submitManifest(manifest) {
  const resp = await fetch("/api/anchor_folder", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(manifest),
  });
  if (!resp.ok) {
    let err = {};
    try { err = await resp.json(); } catch (_) { /* ignore */ }
    throw new Error(err.error || `server returned ${resp.status}`);
  }
  return resp.json();
}

// ─── UI rendering helpers ───────────────────────────────────────────────
function _truncateRoot(hex) {
  if (typeof hex !== "string" || hex.length <= 20) return hex || "";
  return `${hex.slice(0, 12)}...${hex.slice(-8)}`;
}

function _renderProgress(host, payload) {
  if (!host) return;
  host.hidden = false;
  host.replaceChildren();
  const phaseLabels = {
    hashing: "Hashing files locally",
    building_tree: "Building Merkle tree",
    submitting: "Submitting manifest",
  };
  const heading = document.createElement("div");
  heading.className = "folder-progress-heading";
  heading.textContent = phaseLabels[payload.phase] || "Working";
  host.appendChild(heading);

  if (typeof payload.current === "number" && typeof payload.total === "number" && payload.total > 0) {
    const sub = document.createElement("div");
    sub.className = "folder-progress-sub muted small";
    sub.textContent = `${payload.current} of ${payload.total}`;
    host.appendChild(sub);
  }
}

function _renderReceipt(host, record, files, oversizeSkipped) {
  if (!host) return;
  host.hidden = false;
  host.replaceChildren();

  // ── Receipt identifier (strict shape check) ─────────────────────────
  const id = String(record.receipt_id || "");
  if (!RECEIPT_ID_RE.test(id)) {
    const warn = document.createElement("p");
    warn.className = "folder-receipt-warn";
    warn.textContent = "The office returned a response that could not be validated. Please refresh and try again.";
    host.appendChild(warn);
    return;
  }

  const card = document.createElement("div");
  card.className = "folder-receipt-card";

  const title = document.createElement("h3");
  title.className = "folder-receipt-title";
  title.textContent = "A folder receipt has been issued";
  card.appendChild(title);

  const idRow = document.createElement("p");
  idRow.className = "folder-receipt-id";
  const idLabel = document.createElement("span");
  idLabel.className = "muted small";
  idLabel.textContent = "Receipt identifier: ";
  const idVal = document.createElement("code");
  idVal.textContent = id;
  idRow.appendChild(idLabel);
  idRow.appendChild(idVal);
  card.appendChild(idRow);

  const rootRow = document.createElement("p");
  rootRow.className = "folder-receipt-root";
  const rootLabel = document.createElement("span");
  rootLabel.className = "muted small";
  rootLabel.textContent = "Merkle root: ";
  const rootVal = document.createElement("code");
  rootVal.textContent = _truncateRoot(String(record.root_hex || ""));
  rootVal.title = String(record.root_hex || "");
  rootRow.appendChild(rootLabel);
  rootRow.appendChild(rootVal);
  card.appendChild(rootRow);

  const summary = document.createElement("p");
  summary.className = "folder-receipt-summary muted";
  const fileCount = Array.isArray(files) ? files.length : 0;
  const parts = [`${fileCount} file${fileCount === 1 ? "" : "s"} recorded`];
  if (oversizeSkipped > 0) {
    parts.push(`${oversizeSkipped} oversize skipped`);
  }
  summary.textContent = parts.join(" · ");
  card.appendChild(summary);

  const eta = document.createElement("p");
  eta.className = "folder-receipt-eta muted small";
  eta.textContent = "Bitcoin commitment expected within ≈1 hour.";
  card.appendChild(eta);

  // Link to the hosted provenance certificate — a shareable, print-to-PDF
  // page that resolves this receipt + its manifest from any device.
  const viewRow = document.createElement("p");
  viewRow.className = "folder-receipt-view";
  const viewLink = document.createElement("a");
  viewLink.href = "/certificate/" + encodeURIComponent(id);
  viewLink.className = "folder-receipt-view-link cta-btn-outline";
  viewLink.textContent = "View the full provenance certificate →";
  viewRow.appendChild(viewLink);
  card.appendChild(viewRow);

  // ── Per-file table ──────────────────────────────────────────────────
  const table = document.createElement("table");
  table.className = "folder-receipt-table";
  const thead = document.createElement("thead");
  const trh = document.createElement("tr");
  for (const label of ["Path", "SHA-256", "Proof"]) {
    const th = document.createElement("th");
    th.textContent = label;
    trh.appendChild(th);
  }
  thead.appendChild(trh);
  table.appendChild(thead);

  const tbody = document.createElement("tbody");
  for (let i = 0; i < files.length; i++) {
    const entry = files[i];
    const tr = document.createElement("tr");

    const tdPath = document.createElement("td");
    tdPath.className = "folder-receipt-path mono";
    tdPath.textContent = entry.path;
    tr.appendChild(tdPath);

    const tdHash = document.createElement("td");
    tdHash.className = "folder-receipt-hash mono small";
    tdHash.textContent = _truncateRoot(entry.file_sha256);
    tdHash.title = entry.file_sha256;
    tr.appendChild(tdHash);

    const tdBtn = document.createElement("td");
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "folder-receipt-proof-btn";
    btn.textContent = "Download inclusion proof";
    btn.addEventListener("click", () => _downloadInclusionProof(id, entry.path, btn));
    tdBtn.appendChild(btn);
    tr.appendChild(tdBtn);

    tbody.appendChild(tr);
  }
  table.appendChild(tbody);
  card.appendChild(table);

  host.appendChild(card);
}

async function _downloadInclusionProof(receiptId, path, btn) {
  const original = btn.textContent;
  btn.disabled = true;
  btn.textContent = "Fetching proof";
  try {
    const url = `/api/inclusion_proof?receipt_id=${encodeURIComponent(receiptId)}&path=${encodeURIComponent(path)}`;
    const resp = await fetch(url);
    if (!resp.ok) throw new Error(`server returned ${resp.status}`);
    const proof = await resp.json();
    const blob = new Blob([JSON.stringify(proof, null, 2)], { type: "application/json" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    // Path may contain "/"; replace to make a valid filename.
    const safeName = path.replace(/[^A-Za-z0-9._-]+/g, "_").slice(0, 80) || "file";
    a.download = `inclusion-proof-${receiptId}-${safeName}.json`;
    a.click();
    btn.textContent = "Proof downloaded";
  } catch (e) {
    console.warn("[orphograph/folder] inclusion proof fetch failed", e);
    btn.textContent = "Proof unavailable";
  } finally {
    setTimeout(() => {
      btn.disabled = false;
      btn.textContent = original;
    }, 2500);
  }
}

// ─── Public API ────────────────────────────────────────────────────────
export async function anchorFolder({ onProgress } = {}) {
  const progress = typeof onProgress === "function" ? onProgress : () => {};

  let entries;
  try {
    entries = await _collectFiles();
  } catch (e) {
    // User cancelled the picker, or permission denied. Nothing to do.
    console.warn("[orphograph/folder] picker dismissed", e);
    return null;
  }

  if (!entries || entries.length === 0) {
    alert("No files selected");
    return null;
  }

  // ── Phase 1: hash every file ────────────────────────────────────────
  const hashed = [];
  let oversizeSkipped = 0;
  for (let i = 0; i < entries.length; i++) {
    const { path, file } = entries[i];
    progress({ phase: "hashing", current: i, total: entries.length });
    if (typeof file.size === "number" && file.size > MAX_FILE_BYTES) {
      console.warn(`[orphograph/folder] skipping oversize file (${file.size} bytes): ${path}`);
      oversizeSkipped += 1;
      continue;
    }
    const digest = await _hashFile(file);
    hashed.push({
      path,
      file_sha256: _hex(digest),
      digest,
      size: typeof file.size === "number" ? file.size : 0,
    });
  }
  progress({ phase: "hashing", current: entries.length, total: entries.length });

  if (hashed.length === 0) {
    alert("No files selected");
    return null;
  }

  // ── Phase 2: build the tree (sorted by UTF-8 byte order of path) ────
  progress({ phase: "building_tree", current: 0, total: hashed.length });
  const enc = new TextEncoder();
  for (const h of hashed) h._pathBytes = enc.encode(h.path);
  hashed.sort((a, b) => _byteCompare(a._pathBytes, b._pathBytes));

  const leaves = [];
  for (let i = 0; i < hashed.length; i++) {
    const leaf = await _leafFor(hashed[i].path, hashed[i].digest);
    leaves.push(leaf);
    progress({ phase: "building_tree", current: i + 1, total: hashed.length });
  }
  const root = await _buildTree(leaves);
  const root_hex = _hex(root);

  // ── Phase 3: submit the manifest ────────────────────────────────────
  progress({ phase: "submitting", current: 0, total: 1 });
  // Server-format manifest (orphograph-merkle-v1-rfc6962): /api/anchor_folder
  // reconstructs the tree from `leaves` and verifies the root, so we must emit
  // the full leaf records — NOT a {files} summary, which the server rejects
  // with "manifest leaves must be a non-empty list".
  const manifest = {
    algorithm: ALGORITHM_TAG,
    version: 1,
    root_hex,
    leaves: hashed.map((h, i) => ({
      path: h.path,
      file_sha256_hex: h.file_sha256,
      leaf_hex: _hex(leaves[i]),
      size_bytes: h.size,
    })),
  };
  let record;
  try {
    record = await _submitManifest(manifest);
  } catch (e) {
    alert("Folder anchor failed: " + (e && e.message ? e.message : String(e)));
    return null;
  }
  progress({ phase: "submitting", current: 1, total: 1 });

  return {
    record,
    files: manifest.leaves.map((l) => ({ path: l.path, file_sha256: l.file_sha256_hex })),
    root_hex,
    oversize_skipped: oversizeSkipped,
  };
}

export async function verifyFolder(receipt) {
  // Re-hash a folder locally and compare against the receipt's root.
  // The receipt is the JSON returned by /api/anchor_folder or its
  // /api/verify_folder/<id> equivalent. The user is prompted to pick the
  // same folder; the function returns { match, root_hex_recomputed,
  // root_hex_expected, mismatches: [...] }.
  if (!receipt || typeof receipt !== "object") {
    throw new Error("verifyFolder requires the receipt object");
  }
  const expectedRoot = String(receipt.root_hex || "");
  const expectedFiles = Array.isArray(receipt.files) ? receipt.files : [];
  const byPath = new Map();
  for (const f of expectedFiles) {
    if (f && typeof f.path === "string" && typeof f.file_sha256 === "string") {
      byPath.set(f.path, f.file_sha256);
    }
  }

  let entries;
  try {
    entries = await _collectFiles();
  } catch (e) {
    return { match: false, error: "picker_dismissed" };
  }
  if (!entries || entries.length === 0) {
    return { match: false, error: "no_files" };
  }

  const hashed = [];
  const mismatches = [];
  for (const { path, file } of entries) {
    if (typeof file.size === "number" && file.size > MAX_FILE_BYTES) continue;
    const digest = await _hashFile(file);
    const hex = _hex(digest);
    hashed.push({ path, file_sha256: hex, digest });
    const expected = byPath.get(path);
    if (expected && expected !== hex) {
      mismatches.push({ path, expected, actual: hex });
    }
  }

  const enc = new TextEncoder();
  for (const h of hashed) h._pathBytes = enc.encode(h.path);
  hashed.sort((a, b) => _byteCompare(a._pathBytes, b._pathBytes));
  const leaves = [];
  for (const h of hashed) leaves.push(await _leafFor(h.path, h.digest));
  const recomputed = _hex(await _buildTree(leaves));

  return {
    match: recomputed === expectedRoot,
    root_hex_recomputed: recomputed,
    root_hex_expected: expectedRoot,
    file_count: hashed.length,
    mismatches,
  };
}

// ─── Wire the page button (if present) ──────────────────────────────────
function _wireButton() {
  const btn = document.querySelector("#anchor-folder-btn");
  if (!btn) return;
  const progressHost = document.querySelector("#folder-progress");
  const receiptHost = document.querySelector("#folder-receipt");

  btn.addEventListener("click", async () => {
    btn.disabled = true;
    if (receiptHost) receiptHost.hidden = true;
    try {
      const result = await anchorFolder({
        onProgress: (p) => _renderProgress(progressHost, p),
      });
      if (progressHost) progressHost.hidden = true;
      if (result && result.record) {
        _renderReceipt(receiptHost, result.record, result.files, result.oversize_skipped);
      }
    } catch (e) {
      console.warn("[orphograph/folder] anchor failed", e);
      if (progressHost) progressHost.hidden = true;
      alert("Folder anchor failed: " + (e && e.message ? e.message : String(e)));
    } finally {
      btn.disabled = false;
    }
  });
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", _wireButton, { once: true });
} else {
  _wireButton();
}
