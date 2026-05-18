// receipt.js — renders /r/<id> by fetching /api/verify/<id> and populating the card.
// CSP-safe: no inline scripts; runs only against same-origin endpoints.

const VERIFIER_URL = "/verify/";

// Calendar short-name → public hostname. Mirrors server/engine.py CALENDARS list.
// Used to build per-calendar /timestamp/<hash> URLs so anyone can pull the
// proof from the calendar directly, not from us.
const CALENDAR_HOSTS = {
  "a": "https://a.pool.opentimestamps.org",
  "b": "https://b.pool.opentimestamps.org",
  "alice": "https://alice.btc.calendar.opentimestamps.org",
  "finney": "https://finney.calendar.eternitywall.com",
  "btc": "https://btc.calendar.catallaxy.com",
};

function calendarUrlFromFile(filename, hashHex) {
  // filename example: "alice.ots" → look up "alice" in CALENDAR_HOSTS
  const stem = filename.replace(/\.ots$/i, "");
  const host = CALENDAR_HOSTS[stem];
  if (!host || !hashHex) return null;
  return `${host}/timestamp/${hashHex}`;
}

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

function _fmtLocal(d) {
  try {
    return new Intl.DateTimeFormat(undefined, {
      year: "numeric", month: "short", day: "2-digit",
      hour: "2-digit", minute: "2-digit", second: "2-digit",
      timeZoneName: "short",
    }).format(d);
  } catch {
    return d.toString();
  }
}

function _fmtUtc(d) {
  // Server timestamps are ISO-8601 in UTC; show them with explicit "UTC" suffix.
  const pad = (n) => n.toString().padStart(2, "0");
  return `${d.getUTCFullYear()}-${pad(d.getUTCMonth() + 1)}-${pad(d.getUTCDate())} ` +
         `${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}:${pad(d.getUTCSeconds())} UTC`;
}

function _detectTz() {
  try { return Intl.DateTimeFormat().resolvedOptions().timeZone || "(unknown)"; }
  catch { return "(unknown)"; }
}

function renderTimeInto(node, isoString) {
  // Render a single timestamp inline: local-time primary, UTC secondary, with
  // a toggle so the user can flip them if VPN or system clock is suspect.
  if (!node) return;
  node.replaceChildren();
  if (!isoString) { node.textContent = "—"; return; }
  const d = new Date(isoString);
  if (isNaN(d.getTime())) { node.textContent = isoString; return; }
  const span = el("span", { className: "ts" });
  const primary = el("span", { className: "ts-primary", textContent: _fmtLocal(d) });
  const sep = el("span", { className: "muted small" }, " · ");
  const secondary = el("span", { className: "ts-secondary muted small", textContent: _fmtUtc(d) });
  span.appendChild(primary);
  span.appendChild(sep);
  span.appendChild(secondary);
  node.appendChild(span);
}

function renderTimePairInto(node, label, isoString) {
  // Used in headers: "Anchored Tue 17 May 2026, 7:21:38 PM AST · 2026-05-17 23:21:38 UTC"
  // followed by a small zone disclosure: "your browser reports: America/Puerto_Rico".
  if (!node) return;
  node.replaceChildren();
  if (!isoString) { node.textContent = `${label} —`; return; }
  const d = new Date(isoString);
  if (isNaN(d.getTime())) { node.textContent = `${label} ${isoString}`; return; }
  const tz = _detectTz();
  const wrap = el("span", { className: "ts-pair" });
  wrap.appendChild(document.createTextNode(`${label} `));
  wrap.appendChild(el("span", { className: "ts-primary", textContent: _fmtLocal(d) }));
  wrap.appendChild(el("br"));
  const sub = el("span", { className: "muted small ts-sub" });
  sub.appendChild(document.createTextNode(`${_fmtUtc(d)} · your browser reports ${tz}`));
  wrap.appendChild(sub);
  node.appendChild(wrap);
}

function renderExplorerGrid(rec) {
  const grid = document.querySelector("#explorer-grid");
  if (!grid) return;
  grid.replaceChildren();

  // Card 1..5 — one per OTS calendar.
  for (const check of rec.checks || []) {
    const url = calendarUrlFromFile(check.file, rec.hash_hex);
    if (!url) continue;
    const card = el("div", { className: "explorer-card" });
    const stem = check.file.replace(/\.ots$/i, "");
    card.appendChild(el("h3", { textContent: `${stem} calendar` }));
    card.appendChild(el("p", { className: "muted small", textContent: `Pull the proof directly from ${new URL(url).hostname}.` }));
    const link = el("a", { href: url, target: "_blank", rel: "noopener noreferrer", className: "btn-link" }, "Open proof →");
    card.appendChild(link);
    const status = el("p", { className: check.ok ? "ok small" : "bad small" });
    status.textContent = check.ok ? "✓ local OTS file valid" : "✗ local OTS file failed";
    card.appendChild(status);
    grid.appendChild(card);
  }

  // Card N+1 — Bitcoin block / tx explorer. Two redundant explorers so if one
  // goes dark the proof still resolves.
  const btcCard = el("div", { className: "explorer-card explorer-card-btc" });
  btcCard.appendChild(el("h3", { textContent: "Bitcoin chain" }));
  if (rec.btc_pinned_at) {
    btcCard.appendChild(el("p", { className: "muted small", textContent: `Anchored to Bitcoin at ${rec.btc_pinned_at}. The .ots file contains the block height and Merkle path; download it to see the exact tx.` }));
    // Generic explorer search for the SHA-256. mempool.space + blockstream.info
    // both index BTC; if one site is down the other still works.
    const links = el("div", { className: "explorer-link-row" });
    links.appendChild(el("a", {
      href: `https://mempool.space/`,
      target: "_blank", rel: "noopener noreferrer", className: "btn-link",
    }, "mempool.space →"));
    links.appendChild(el("a", {
      href: `https://blockstream.info/`,
      target: "_blank", rel: "noopener noreferrer", className: "btn-link",
    }, "blockstream.info →"));
    btcCard.appendChild(links);
  } else {
    btcCard.appendChild(el("p", { className: "muted small", textContent: "Pending — Bitcoin block-pinning happens within ~1 hour of anchoring. Once pinned, this card will link directly to the Bitcoin block and transaction containing the Merkle root that commits your hash." }));
  }
  grid.appendChild(btcCard);

  // Card N+2 — OpenTimestamps protocol docs (transparency).
  const otsCard = el("div", { className: "explorer-card" });
  otsCard.appendChild(el("h3", { textContent: "How verification works" }));
  otsCard.appendChild(el("p", { className: "muted small", textContent: "Read the OpenTimestamps protocol — same one Bitcoin Core developers use to timestamp commits. Public spec, no proprietary format." }));
  otsCard.appendChild(el("a", {
    href: "https://opentimestamps.org/",
    target: "_blank", rel: "noopener noreferrer", className: "btn-link",
  }, "opentimestamps.org →"));
  grid.appendChild(otsCard);
}

function $(sel) { return document.querySelector(sel); }

function showError(msg) {
  const el = $("#err");
  el.textContent = msg;
  el.hidden = false;
  $("#card").hidden = true;
}

function escapePath(id) {
  return encodeURIComponent(id);
}

function rid_from_url() {
  // pages live at /r/<id> — extract <id> from pathname.
  const m = location.pathname.match(/^\/r\/([A-Za-z0-9_-]{1,64})\/?$/);
  return m ? m[1] : "";
}

async function main() {
  const rid = rid_from_url();
  if (!rid) return showError("This page expects a URL of the form /r/<receipt-id>.");
  let r;
  try {
    r = await fetch(`/api/verify/${escapePath(rid)}`);
  } catch (e) {
    return showError(`network error: ${e}`);
  }
  if (!r.ok) {
    if (r.status === 404) return showError(`Receipt not found: ${rid}`);
    if (r.status === 400) return showError(`Invalid receipt id: ${rid}`);
    return showError(`Server returned ${r.status}.`);
  }
  const rec = await r.json();
  $("#rid").textContent = rec.receipt_id;
  $("#sha256").textContent = rec.hash_hex;
  $("#sha512").textContent = rec.sha512_hex || "(none — receipt predates SHA-512 sibling)";
  $("#label").textContent = rec.client_label || "(none)";
  // Friendly status copy. "partial" with 3/5 still meets MIN_CALENDARS_OK=3
  // (cryptographically anchored to Bitcoin via 3 independent calendars), but
  // the bare word "partial" reads as broken — replace with clearer text.
  const _rawStatus = rec.status || "pending";
  const _cok = rec.calendars_ok || 0;
  const _ctot = rec.calendars_total || 5;
  let _friendly;
  if (_rawStatus === "pinned") {
    _friendly = `Anchored to Bitcoin · all ${_ctot} calendars confirmed`;
  } else if (_rawStatus === "partial") {
    _friendly = `Anchored to Bitcoin · ${_cok} of ${_ctot} calendars confirmed`;
  } else if (_rawStatus === "pending") {
    _friendly = `Pending Bitcoin confirmation · ${_cok} of ${_ctot} calendars stamped`;
  } else {
    _friendly = `${_rawStatus} (${_cok}/${_ctot} calendars)`;
  }
  $("#status").textContent = _friendly;
  $("#cals").textContent = `${rec.calendars_ok} of ${rec.calendars_total} OTS proofs valid`;
  if (rec.btc_pinned_at) {
    renderTimeInto($("#btc"), rec.btc_pinned_at);
  } else {
    $("#btc").textContent = "pending — block-pinning happens within ~1 hour";
  }
  renderTimePairInto($("#created"), "Anchored", rec.created_at);

  // Attestation (authorship claim, if present)
  if (rec.attestation && rec.attestation.claim) {
    const dt = $("#attestation-label");
    const dd = $("#attestation");
    if (dt && dd) {
      dt.hidden = false;
      dd.hidden = false;
      dd.replaceChildren();
      const claimPara = document.createElement("p");
      claimPara.className = "attest-claim";
      claimPara.textContent = rec.attestation.claim;
      dd.appendChild(claimPara);
      const meta = [];
      if (rec.attestation.author) meta.push(`Author: ${rec.attestation.author}`);
      if (rec.attestation.license) meta.push(`License: ${rec.attestation.license}`);
      if (rec.attestation.signed_at) meta.push(`Signed: ${rec.attestation.signed_at}`);
      if (meta.length) {
        const small = document.createElement("p");
        small.className = "muted small";
        small.textContent = meta.join(" · ");
        dd.appendChild(small);
      }
    }
  }

  // Camera metadata (EXIF, if present)
  if (rec.metadata && Object.keys(rec.metadata).length) {
    const dt = $("#metadata-label");
    const dd = $("#metadata");
    if (dt && dd) {
      dt.hidden = false;
      dd.hidden = false;
      dd.replaceChildren();
      const dlInner = document.createElement("dl");
      dlInner.className = "metadata-inner";
      const friendlyName = {
        filename: "Filename",
        size_bytes: "File size (bytes)",
        mime_type: "MIME type",
        exif_camera_make: "Camera make",
        exif_camera_model: "Camera model",
        exif_camera_serial: "Camera serial",
        exif_lens: "Lens",
        exif_capture_time: "Capture time (camera clock)",
        exif_software: "Software",
        exif_iso: "ISO",
        exif_aperture: "Aperture (f-stop)",
        exif_shutter: "Shutter speed (s)",
        exif_focal_length: "Focal length (mm)",
        image_width: "Width (px)",
        image_height: "Height (px)",
        image_format: "Format",
      };
      for (const [k, v] of Object.entries(rec.metadata)) {
        const dtInner = document.createElement("dt");
        dtInner.textContent = friendlyName[k] || k;
        const ddInner = document.createElement("dd");
        ddInner.textContent = String(v);
        dlInner.appendChild(dtInner);
        dlInner.appendChild(ddInner);
      }
      dd.appendChild(dlInner);
    }
  }

  const list = $("#ots-list");
  list.replaceChildren();
  for (const check of rec.checks || []) {
    const li = document.createElement("li");
    const status = document.createElement("span");
    status.className = check.ok ? "ok" : "bad";
    status.textContent = check.ok ? "✓ valid" : "✗ failed";
    const name = document.createElement("span");
    name.className = "file";
    name.textContent = check.file;
    li.appendChild(status);
    li.appendChild(name);
    list.appendChild(li);
  }

  renderExplorerGrid(rec);

  $("#verifier-url").href = VERIFIER_URL;
  $("#print-btn").addEventListener("click", (e) => { e.preventDefault(); window.print(); });

  // Auto-trigger print dialog if the URL has ?print=1 — used by the
  // headless-Brave / headless-Chromium PDF-export flow documented in
  // deploy/RECEIPT_PDF.md. After ~400ms (giving the explorer grid time
  // to render) we invoke window.print(). In headless mode this writes
  // to --print-to-pdf=<file>; in a normal browser it pops the dialog.
  const params = new URLSearchParams(window.location.search);
  if (params.get("print") === "1") {
    setTimeout(() => { try { window.print(); } catch (_) { /* noop */ } }, 400);
  }
}

main();
