// certificate.js — renders /certificate/<id> for folder (dataset) receipts.
// Fetches /api/verify_folder/<id> ({receipt, manifest}) and renders a
// provenance certificate: summary, scope, license/log documents, the full
// file manifest, and an in-browser Merkle inclusion verifier.
// CSP-safe: no inline scripts, no innerHTML; same-origin endpoints only.
// MIT — see /LICENSE.

// ─── Merkle constants (must match server/merkle.py + folder.js exactly) ──
const LEAF_PREFIX = new Uint8Array([0x00]);
const INTERNAL_PREFIX = new Uint8Array([0x01]);
const VERIFIER_URL = "/verify/";

const CALENDAR_HOSTS = {
  "a": "https://a.pool.opentimestamps.org",
  "b": "https://b.pool.opentimestamps.org",
  "alice": "https://alice.btc.calendar.opentimestamps.org",
  "finney": "https://finney.calendar.eternitywall.com",
  "btc": "https://btc.calendar.catallaxy.com",
};

// ─── tiny DOM + crypto helpers ───────────────────────────────────────────
function el(tag, attrs, ...children) {
  const node = document.createElement(tag);
  if (attrs) {
    for (const [k, v] of Object.entries(attrs)) {
      if (k === "className") node.className = v;
      else if (k === "textContent") node.textContent = v;
      else node.setAttribute(k, v);
    }
  }
  for (const c of children) {
    if (c == null) continue;
    node.appendChild(typeof c === "string" ? document.createTextNode(c) : c);
  }
  return node;
}
function $(sel) { return document.querySelector(sel); }

const _hex = (bytes) =>
  [...new Uint8Array(bytes)].map((b) => b.toString(16).padStart(2, "0")).join("");

function _concat(...parts) {
  let total = 0;
  for (const p of parts) total += p.byteLength;
  const out = new Uint8Array(total);
  let off = 0;
  for (const p of parts) { out.set(p instanceof Uint8Array ? p : new Uint8Array(p), off); off += p.byteLength; }
  return out;
}
async function _sha256(bytes) { return new Uint8Array(await crypto.subtle.digest("SHA-256", bytes)); }
function _bytesFromHex(hex) {
  if (typeof hex !== "string" || hex.length % 2 !== 0) return null;
  const out = new Uint8Array(hex.length / 2);
  for (let i = 0; i < out.length; i++) {
    const b = parseInt(hex.substr(i * 2, 2), 16);
    if (Number.isNaN(b)) return null;
    out[i] = b;
  }
  return out;
}

// Recompute the Merkle root in-browser from a file's leaf + inclusion proof.
// Mirrors server/merkle.py verify_inclusion: leaf = SHA-256(0x00||path||0x00||
// file_sha256); each step "L" => H(0x01||sibling||current), "R" => H(0x01||
// current||sibling). Returns the recomputed root hex (caller compares to root).
async function recomputeRoot(path, fileSha256Hex, proof) {
  const fileDigest = _bytesFromHex(fileSha256Hex);
  if (!fileDigest || fileDigest.length !== 32) throw new Error("bad file digest");
  let current = await _sha256(_concat(
    LEAF_PREFIX, new TextEncoder().encode(path), new Uint8Array([0x00]), fileDigest));
  for (const step of proof) {
    const dir = step[0], sib = _bytesFromHex(step[1]);
    if ((dir !== "L" && dir !== "R") || !sib || sib.length !== 32) {
      throw new Error("malformed proof step");
    }
    current = dir === "L"
      ? await _sha256(_concat(INTERNAL_PREFIX, sib, current))
      : await _sha256(_concat(INTERNAL_PREFIX, current, sib));
  }
  return _hex(current);
}

// ─── time + bytes formatting ─────────────────────────────────────────────
function _fmtLocal(d) {
  try {
    return new Intl.DateTimeFormat(undefined, {
      year: "numeric", month: "short", day: "2-digit",
      hour: "2-digit", minute: "2-digit", second: "2-digit", timeZoneName: "short",
    }).format(d);
  } catch { return d.toString(); }
}
function _fmtUtc(d) {
  const pad = (n) => n.toString().padStart(2, "0");
  return `${d.getUTCFullYear()}-${pad(d.getUTCMonth() + 1)}-${pad(d.getUTCDate())} ` +
         `${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}:${pad(d.getUTCSeconds())} UTC`;
}
function _detectTz() {
  try { return Intl.DateTimeFormat().resolvedOptions().timeZone || "(unknown)"; }
  catch { return "(unknown)"; }
}
function renderTimeInto(node, isoString) {
  if (!node) return;
  node.replaceChildren();
  if (!isoString) { node.textContent = "—"; return; }
  const d = new Date(isoString);
  if (isNaN(d.getTime())) { node.textContent = isoString; return; }
  node.appendChild(el("span", { className: "ts-primary", textContent: _fmtLocal(d) }));
  node.appendChild(el("span", { className: "muted small" }, " · "));
  node.appendChild(el("span", { className: "ts-secondary muted small", textContent: _fmtUtc(d) }));
}
function renderTimePairInto(node, label, isoString) {
  if (!node) return;
  node.replaceChildren();
  if (!isoString) { node.textContent = `${label} —`; return; }
  const d = new Date(isoString);
  if (isNaN(d.getTime())) { node.textContent = `${label} ${isoString}`; return; }
  const wrap = el("span", { className: "ts-pair" });
  wrap.appendChild(document.createTextNode(`${label} `));
  wrap.appendChild(el("span", { className: "ts-primary", textContent: _fmtLocal(d) }));
  wrap.appendChild(el("br"));
  const sub = el("span", { className: "muted small ts-sub" });
  sub.appendChild(document.createTextNode(`${_fmtUtc(d)} · your browser reports ${_detectTz()}`));
  wrap.appendChild(sub);
  node.appendChild(wrap);
}
function _fmtBytes(n) {
  n = Number(n) || 0;
  if (n < 1024) return `${n} B`;
  const units = ["KB", "MB", "GB", "TB"];
  let v = n / 1024, i = 0;
  while (v >= 1024 && i < units.length - 1) { v /= 1024; i++; }
  return `${v.toFixed(v < 10 ? 1 : 0)} ${units[i]}`;
}
function _shortHash(hex) {
  return typeof hex === "string" && hex.length > 20 ? `${hex.slice(0, 10)}…${hex.slice(-6)}` : (hex || "—");
}

// ─── explorer grid (shared shape with receipt.js) ────────────────────────
function calendarUrlFromFile(filename, hashHex) {
  const host = CALENDAR_HOSTS[(filename || "").replace(/\.ots$/i, "")];
  return host && hashHex ? `${host}/timestamp/${hashHex}` : null;
}
function renderExplorerGrid(rec) {
  const grid = $("#explorer-grid");
  if (!grid) return;
  grid.replaceChildren();
  for (const check of rec.checks || []) {
    const url = calendarUrlFromFile(check.file, rec.hash_hex);
    if (!url) continue;
    const card = el("div", { className: "explorer-card" });
    const stem = check.file.replace(/\.ots$/i, "");
    card.appendChild(el("h3", { textContent: `${stem} calendar` }));
    card.appendChild(el("p", { className: "muted small", textContent: `Pull the proof directly from ${new URL(url).hostname}.` }));
    card.appendChild(el("a", { href: url, target: "_blank", rel: "noopener noreferrer", className: "btn-link" }, "Open proof →"));
    const status = el("p", { className: check.ok ? "ok small" : "bad small" });
    status.textContent = check.ok ? "✓ local OTS file valid" : "✗ local OTS file failed";
    card.appendChild(status);
    grid.appendChild(card);
  }
  const btcCard = el("div", { className: "explorer-card explorer-card-btc" });
  btcCard.appendChild(el("h3", { textContent: "Bitcoin chain" }));
  if (rec.btc_pinned_at) {
    btcCard.appendChild(el("p", { className: "muted small", textContent: `Root anchored to Bitcoin at ${rec.btc_pinned_at}. The .ots file contains the block height and Merkle path.` }));
    const links = el("div", { className: "explorer-link-row" });
    links.appendChild(el("a", { href: "https://mempool.space/", target: "_blank", rel: "noopener noreferrer", className: "btn-link" }, "mempool.space →"));
    links.appendChild(el("a", { href: "https://blockstream.info/", target: "_blank", rel: "noopener noreferrer", className: "btn-link" }, "blockstream.info →"));
    btcCard.appendChild(links);
  } else {
    btcCard.appendChild(el("p", { className: "muted small", textContent: "Pending — Bitcoin block-pinning happens within ~1 hour of anchoring. Once pinned, this links to the block and transaction committing the Merkle root." }));
  }
  grid.appendChild(btcCard);
  const otsCard = el("div", { className: "explorer-card" });
  otsCard.appendChild(el("h3", { textContent: "How verification works" }));
  otsCard.appendChild(el("p", { className: "muted small", textContent: "Read the OpenTimestamps protocol — same one Bitcoin Core developers use. Public spec, no proprietary format." }));
  otsCard.appendChild(el("a", { href: "https://opentimestamps.org/", target: "_blank", rel: "noopener noreferrer", className: "btn-link" }, "opentimestamps.org →"));
  grid.appendChild(otsCard);
}

// ─── categorise leaves by path (mirrors provenance.py _categorise) ───────
function categorise(leaves) {
  const b = { data: [], licenses: [], log: [], other: [] };
  for (const leaf of leaves) {
    const path = leaf.path;
    if (path == null) { b.other.push(leaf); continue; }
    const head = (path.split("/", 1)[0] || "").toLowerCase();
    const name = (path.split("/").pop() || "").toLowerCase();
    if (head === "data") b.data.push(leaf);
    else if (head === "licenses" || head === "license" || head === "consent") b.licenses.push(leaf);
    else if (head === "provenance" || name.startsWith("acquisition_log") ||
             name === "acquisition.log" || name === "provenance.json") b.log.push(leaf);
    else b.other.push(leaf);
  }
  return b;
}

function showError(msg) {
  const e = $("#err");
  e.textContent = msg;
  e.hidden = false;
  $("#card").hidden = true;
}
function escapePath(id) { return encodeURIComponent(id); }
function ridFromUrl() {
  const m = location.pathname.match(/^\/certificate\/([A-Za-z0-9_-]{1,64})\/?$/);
  return m ? m[1] : "";
}

// ─── document lists (licenses / acquisition log) ─────────────────────────
function renderDocList(node, leaves) {
  node.replaceChildren();
  for (const leaf of leaves) {
    const li = el("li", { className: "doc-item" });
    li.appendChild(el("span", { className: "doc-path mono", textContent: leaf.path }));
    li.appendChild(el("span", { className: "doc-hash mono muted small", title: leaf.file_sha256_hex, textContent: _shortHash(leaf.file_sha256_hex) }));
    li.appendChild(el("span", { className: "doc-size muted small", textContent: _fmtBytes(leaf.size_bytes) }));
    node.appendChild(li);
  }
}

// ─── manifest table ──────────────────────────────────────────────────────
function renderManifest(leaves, redacted) {
  const body = $("#manifest-body");
  body.replaceChildren();
  leaves.forEach((leaf, i) => {
    const tr = el("tr");
    tr.appendChild(el("td", { className: "num muted", textContent: String(leaf.index != null ? leaf.index : i) }));
    if (redacted || leaf.path == null) {
      tr.appendChild(el("td", { className: "muted small", textContent: "[path withheld — owner only]" }));
    } else {
      const pathCell = el("td", { className: "mono manifest-path", textContent: leaf.path });
      pathCell.setAttribute("role", "button");
      pathCell.setAttribute("tabindex", "0");
      pathCell.title = "Click to verify this file's inclusion";
      pathCell.addEventListener("click", () => runInclusion(leaf.path));
      pathCell.addEventListener("keydown", (ev) => { if (ev.key === "Enter") runInclusion(leaf.path); });
      tr.appendChild(pathCell);
    }
    tr.appendChild(el("td", { className: "mono muted small", title: leaf.file_sha256_hex, textContent: _shortHash(leaf.file_sha256_hex) }));
    tr.appendChild(el("td", { className: "num muted small", textContent: _fmtBytes(leaf.size_bytes) }));
    body.appendChild(tr);
  });
}

// ─── in-browser inclusion verifier ───────────────────────────────────────
let CERT_RID = "";
let CERT_ROOT = "";
let CERT_LEAVES = [];   // manifest leaves: {path?, file_sha256_hex, leaf_hex, size_bytes, index?}

// Fetch a path’s inclusion proof and recompute the root in-browser. Returns a
// plain result object; rendering is the caller’s job (shared by the path tool
// and the drag-drop checker).
async function fetchAndVerifyInclusion(path) {
  let resp;
  try {
    resp = await fetch(`/api/inclusion_proof?receipt_id=${escapePath(CERT_RID)}&path=${encodeURIComponent(path)}`);
  } catch (e) { return { ok: false, path, error: `Network error: ${e}` }; }
  if (!resp.ok) {
    return { ok: false, path, status: resp.status,
             error: resp.status === 404 ? `No such path in this set: ${path}` : `Server returned ${resp.status}.` };
  }
  const ip = await resp.json();
  const proof = (ip.proof || []).map((s) => [s[0], s[1]]);
  let recomputed;
  try { recomputed = await recomputeRoot(ip.path, ip.file_sha256_hex, proof); }
  catch (e) { return { ok: false, path: ip.path, error: `Could not recompute root: ${e.message}` }; }
  const matches = recomputed === CERT_ROOT && recomputed === (ip.root_hex || CERT_ROOT);
  return { ok: matches, path: ip.path, file_sha256_hex: ip.file_sha256_hex,
           recomputed, proofLen: proof.length };
}

function _detailDl(rows) {
  const dl = el("dl", { className: "inclusion-detail mono small" });
  for (const [k, v] of rows) { dl.appendChild(el("dt", {}, k)); dl.appendChild(el("dd", { textContent: v })); }
  return dl;
}

function renderInclusion(node, res) {
  node.hidden = false;
  if (res.error) {
    node.className = "inclusion-result bad";
    node.replaceChildren(el("p", { textContent: res.error }));
    return;
  }
  node.className = `inclusion-result ${res.ok ? "ok" : "bad"}`;
  node.replaceChildren();
  node.appendChild(el("p", { className: "inclusion-verdict" },
    res.ok ? `✓ ‘${res.path}’ is committed to the certified root.`
           : `✗ ‘${res.path}’ did NOT verify against the certified root.`));
  node.appendChild(_detailDl([
    ["File SHA-256", res.file_sha256_hex],
    ["Recomputed root", res.recomputed],
    ["Certified root", CERT_ROOT],
    ["Proof length", `${res.proofLen} sibling hash(es)`],
  ]));
  if (res.ok) node.appendChild(el("p", { className: "muted small" },
    "For a full check, re-hash your local copy of this file and confirm its SHA-256 equals the value above."));
}

async function runInclusion(path) {
  const out = $("#inclusion-result");
  const input = $("#inclusion-path");
  if (input && path) input.value = path;
  const target = (path || (input && input.value) || "").trim();
  if (!target) { out.hidden = false; out.className = "inclusion-result bad"; out.replaceChildren(el("p", { textContent: "Enter a file path first." })); return; }
  out.hidden = false; out.className = "inclusion-result pending";
  out.replaceChildren(el("p", { className: "muted small", textContent: `Fetching inclusion proof for ${target}…` }));
  renderInclusion(out, await fetchAndVerifyInclusion(target));
}

// ─── drag-and-drop "is this file in the set?" checker ────────────────────
// Hashes the dropped file locally and matches its SHA-256 against the manifest
// leaves. The file never leaves the browser.
async function hashFileHex(file) {
  const buf = await file.arrayBuffer();
  return _hex(await _sha256(new Uint8Array(buf)));
}

async function checkDroppedFile(file) {
  const out = $("#dropcheck-result");
  out.hidden = false;
  out.className = "inclusion-result pending";
  out.replaceChildren(el("p", { className: "muted small", textContent: `Hashing “${file.name}” locally — nothing is uploaded…` }));
  let hex;
  try { hex = (await hashFileHex(file)).toLowerCase(); }
  catch (e) { out.className = "inclusion-result bad"; out.replaceChildren(el("p", { textContent: `Could not read file: ${e}` })); return; }
  const idx = CERT_LEAVES.findIndex((l) => (l.file_sha256_hex || "").toLowerCase() === hex);
  if (idx === -1) {
    out.className = "inclusion-result bad";
    out.replaceChildren(
      el("p", { className: "inclusion-verdict", textContent: `✗ “${file.name}” is NOT in this certified set.` }),
      el("p", { className: "muted small", textContent: "Its exact bytes match no file in the manifest. (A renamed copy with identical contents still matches; an edited file will not.)" }),
      _detailDl([["Your file SHA-256", hex]]));
    return;
  }
  const leaf = CERT_LEAVES[idx];
  if (leaf.path != null) {
    // Path known (owner view): verify the full Merkle proof to the root.
    out.className = "inclusion-result pending";
    out.replaceChildren(el("p", { className: "muted small", textContent: `“${file.name}” matches ‘${leaf.path}’ — verifying Merkle proof…` }));
    const res = await fetchAndVerifyInclusion(leaf.path);
    renderInclusion(out, res);
    if (res.ok) out.insertBefore(
      el("p", { className: "muted small", textContent: `Matched by content: “${file.name}” → ‘${leaf.path}’.` }),
      out.firstChild.nextSibling);
  } else {
    // Paths redacted: the fingerprint match alone is a strong result.
    out.className = "inclusion-result ok";
    out.replaceChildren(
      el("p", { className: "inclusion-verdict", textContent: `✓ “${file.name}” matches a certified file in this set.` }),
      el("p", { className: "muted small", textContent: `Its fingerprint equals leaf #${leaf.index != null ? leaf.index : idx} in the manifest. The owner withheld paths, so the full Merkle proof isn’t shown — but the fingerprint match alone proves your exact file is one of the certified items.` }),
      _detailDl([["Matched SHA-256", hex]]));
  }
}

function initDropCheck() {
  const zone = $("#dropcheck"), input = $("#dropcheck-input");
  if (!zone || !input) return;
  const pick = () => input.click();
  zone.addEventListener("click", pick);
  zone.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); pick(); } });
  input.addEventListener("change", () => { if (input.files && input.files[0]) checkDroppedFile(input.files[0]); });
  ["dragenter", "dragover"].forEach((ev) => zone.addEventListener(ev, (e) => { e.preventDefault(); zone.classList.add("dropcheck-over"); }));
  ["dragleave", "dragend"].forEach((ev) => zone.addEventListener(ev, () => zone.classList.remove("dropcheck-over")));
  zone.addEventListener("drop", (e) => {
    e.preventDefault(); zone.classList.remove("dropcheck-over");
    const f = e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files[0];
    if (f) checkDroppedFile(f);
  });
}

// ─── share block ─────────────────────────────────────────────────────────
function initShareBlock(rid) {
  const receiptUrl = window.location.origin + "/certificate/" + encodeURIComponent(rid);
  const urlField = $("#share-url"), copyBtn = $("#copy-link-btn"), nativeBtn = $("#native-share-btn");
  if (!urlField || !copyBtn) return;
  urlField.value = receiptUrl;
  function flash(btn, original) {
    const prior = btn.textContent;
    btn.textContent = "Copied ✓"; btn.disabled = true;
    setTimeout(() => { btn.textContent = original || prior; btn.disabled = false; }, 1600);
  }
  copyBtn.addEventListener("click", async () => {
    try { await navigator.clipboard.writeText(receiptUrl); flash(copyBtn, "Copy link"); }
    catch (_) {
      urlField.select(); urlField.setSelectionRange(0, 99999);
      try { document.execCommand("copy"); flash(copyBtn, "Copy link"); } catch (__) { urlField.focus(); }
    }
  });

  // Embeddable dataset badge — the server renders a folder-aware
  // "dataset · N files · anchored to Bitcoin" SVG that links here.
  const badgeUrl = window.location.origin + "/api/badge/" + encodeURIComponent(rid) + ".svg";
  const badgeImg = $("#badge-preview"), embedTa = $("#embed-code"), copyEmbedBtn = $("#copy-embed-btn");
  if (badgeImg) badgeImg.src = badgeUrl;
  if (embedTa) embedTa.value =
    '<a href="' + receiptUrl + '" rel="noopener" aria-label="Verifiable dataset provenance">\n' +
    '  <img src="' + badgeUrl + '" alt="Dataset anchored to Bitcoin — Orphograph" height="40">\n' +
    '</a>';
  if (copyEmbedBtn && embedTa) {
    copyEmbedBtn.addEventListener("click", async () => {
      try { await navigator.clipboard.writeText(embedTa.value); flash(copyEmbedBtn, "Copy embed code"); }
      catch (_) { embedTa.select(); try { document.execCommand("copy"); flash(copyEmbedBtn, "Copy embed code"); } catch (__) {} }
    });
  }

  if (nativeBtn && "share" in navigator) {
    nativeBtn.hidden = false;
    nativeBtn.addEventListener("click", async () => {
      try { await navigator.share({ title: "Dataset Provenance Certificate " + rid, text: "A Bitcoin-anchored dataset provenance certificate.", url: receiptUrl }); }
      catch (_) { /* dismissed */ }
    });
  }
}

// ─── status copy (shared with receipt.js) ────────────────────────────────
function friendlyStatus(rec) {
  const raw = rec.status || "pending";
  const cok = rec.calendars_ok || 0, ctot = rec.calendars_total || 5;
  if (raw === "pinned") return `Anchored to Bitcoin · all ${ctot} calendars confirmed`;
  if (raw === "partial") return `Anchored to Bitcoin · ${cok} of ${ctot} calendars confirmed`;
  if (raw === "pending") return `Pending Bitcoin confirmation · ${cok} of ${ctot} calendars stamped`;
  return `${raw} (${cok}/${ctot} calendars)`;
}

// ─── main ────────────────────────────────────────────────────────────────
async function main() {
  const rid = ridFromUrl();
  if (!rid) return showError("This page expects a URL of the form /certificate/<receipt-id>.");
  let r;
  try { r = await fetch(`/api/verify_folder/${escapePath(rid)}`); }
  catch (e) { return showError(`network error: ${e}`); }
  if (!r.ok) {
    if (r.status === 404) return showError(`Certificate not found: ${rid}`);
    if (r.status === 400) return showError(`Receipt ${rid} is not a folder anchor — single-file receipts live at /r/${rid}.`);
    return showError(`Server returned ${r.status}.`);
  }
  const data = await r.json();
  const rec = data.receipt || {};
  const manifest = data.manifest || {};
  const leaves = manifest.leaves || [];
  const redacted = !!manifest.paths_redacted;
  CERT_RID = rec.receipt_id || rid;
  CERT_ROOT = (manifest.root_hex || rec.hash_hex || "").toLowerCase();
  CERT_LEAVES = leaves;

  // Header
  $("#dataset-name").textContent = rec.client_label || "(unnamed dataset)";
  renderTimePairInto($("#anchored"), "Anchored", rec.created_at);

  // Summary
  $("#total-files").textContent = String(rec.leaf_count != null ? rec.leaf_count : leaves.length);
  const totalBytes = leaves.reduce((s, l) => s + (Number(l.size_bytes) || 0), 0);
  $("#total-bytes").textContent = redacted ? _fmtBytes(totalBytes) : _fmtBytes(totalBytes);

  // KV
  $("#rid").textContent = CERT_RID;
  $("#root").textContent = CERT_ROOT;
  $("#algo").textContent = manifest.algorithm || "orphograph-merkle-v1-rfc6962";
  $("#status").textContent = friendlyStatus(rec);
  $("#cals").textContent = `${rec.calendars_ok || 0} of ${rec.calendars_total || 5} OTS proofs valid`;
  if (rec.btc_pinned_at) renderTimeInto($("#btc"), rec.btc_pinned_at);
  else $("#btc").textContent = "pending — block-pinning happens within ~1 hour";

  // Categorised documents — only possible when paths are visible (owner view)
  if (!redacted) {
    const buckets = categorise(leaves);
    if (buckets.licenses.length) {
      $("#stat-licenses").hidden = false;
      $("#cat-licenses").textContent = String(buckets.licenses.length);
      $("#docs-section").hidden = false;
      $("#licenses-block").hidden = false;
      renderDocList($("#licenses-list"), buckets.licenses);
    }
    if (buckets.log.length) {
      $("#stat-log").hidden = false;
      $("#cat-log").textContent = String(buckets.log.length);
      $("#docs-section").hidden = false;
      $("#log-block").hidden = false;
      renderDocList($("#log-list"), buckets.log);
    }
  } else {
    const note = $("#redaction-note");
    note.hidden = false;
    note.textContent = manifest.paths_redaction_reason ||
      "File paths are visible only to the receipt owner. Each file's fingerprint is still listed, and anyone who knows a path can verify its inclusion below.";
  }

  // Manifest
  $("#manifest-count").textContent = `(${leaves.length} file${leaves.length === 1 ? "" : "s"})`;
  renderManifest(leaves, redacted);

  // Inclusion verifier (drag-drop checker + by-path proof)
  initDropCheck();
  $("#inclusion-btn").addEventListener("click", () => runInclusion());
  $("#inclusion-path").addEventListener("keydown", (ev) => { if (ev.key === "Enter") { ev.preventDefault(); runInclusion(); } });

  // OTS proofs
  const list = $("#ots-list");
  list.replaceChildren();
  for (const check of rec.checks || []) {
    const li = el("li");
    li.appendChild(el("span", { className: check.ok ? "ok" : "bad", textContent: check.ok ? "✓ valid" : "✗ failed" }));
    li.appendChild(el("span", { className: "file", textContent: check.file }));
    list.appendChild(li);
  }

  renderExplorerGrid(rec);
  initShareBlock(CERT_RID);
  $("#verifier-url").href = VERIFIER_URL;
  $("#print-btn").addEventListener("click", (e) => { e.preventDefault(); window.print(); });

  const params = new URLSearchParams(window.location.search);
  if (params.get("print") === "1") setTimeout(() => { try { window.print(); } catch (_) {} }, 400);
}

main();
