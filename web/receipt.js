// receipt.js — renders /r/<id> by fetching /api/verify/<id> and populating the card.
// CSP-safe: no inline scripts; runs only against same-origin endpoints.

const VERIFIER_URL = "/verify-js";

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

// Calendar short-name → the upstream CALENDAR that server reaches. Mirrors
// server/engine.py CALENDAR_UPSTREAM. a.pool and b.pool are aggregators that
// feed alice and bob, so five servers reach four distinct calendars across
// three operators — and three server acknowledgements can be as few as two
// calendars. Both numbers are shown; neither is asked to mean the other.
const CALENDAR_UPSTREAM = {
  "a": "alice",
  "b": "bob",
  "alice": "alice",
  "finney": "finney",
  "btc": "catallaxy",
};

// Distinct calendars we submit to. One meaning everywhere: the denominator
// is what we aim at, never what one receipt happened to reach — a receipt on
// two calendars must read "2 of 4", never a full-looking "2 of 2".
const CALENDARS_DISTINCT_TOTAL = 4;
const CALENDARS_SUBMITTED_TOTAL = 5;

// Own-property lookup only. A plain object inherits `constructor`,
// `toString`, `__proto__` and friends, so a file literally named
// "constructor.ots" resolved to a truthy function and was counted as a
// calendar (and, in calendarUrlFromFile, reached `new URL(aFunction)`).
function lookup(map, key) {
  return Object.hasOwn(map, key) ? map[key] : null;
}

function calendarUrlFromFile(filename, hashHex) {
  // filename example: "alice.ots" → look up "alice" in CALENDAR_HOSTS
  const stem = String(filename).replace(/\.ots$/i, "");
  const host = lookup(CALENDAR_HOSTS, stem);
  if (!host || !hashHex) return null;
  return `${host}/timestamp/${hashHex}`;
}

function distinctFromFiles(files) {
  const seen = {};
  let n = 0;
  (files || []).forEach((f) => {
    const up = lookup(CALENDAR_UPSTREAM, String(f).replace(/\.ots$/i, ""));
    if (up && !Object.hasOwn(seen, up)) { seen[up] = true; n += 1; }
  });
  return n;
}

// The four numbers the page renders, each with the right denominator.
// Server counts prefer the payload and fall back to deriving from `checks`,
// so a page served by an older build still renders truthfully.
//
// "stamped"/"valid" is a file fact: the proof parses and carries this hash.
// "confirmed" is a BITCOIN fact and a strictly smaller set — today a.pool and
// alice sat pending past 24h while bob, finney and catallaxy were already in
// a block, so wording the two alike would have claimed a confirmation that
// had not happened.
function calendarCounts(rec) {
  const checks = Array.isArray(rec.checks) ? rec.checks : [];
  const okFiles = checks.filter((c) => c && c.ok).map((c) => c.file);
  const num = (v, fallback) => (typeof v === "number" ? v : fallback);
  return {
    serversOk: num(rec.calendars_ok, okFiles.length),
    serversTotal: num(rec.calendars_submitted_total, CALENDARS_SUBMITTED_TOTAL),
    distinctOk: num(rec.calendars_distinct_ok, distinctFromFiles(okFiles)),
    distinctTotal: num(rec.calendars_distinct_total, CALENDARS_DISTINCT_TOTAL),
    // Confirmed counts come from the server, which reads the proof bytes.
    // Absent them the page says nothing about confirmation rather than
    // guessing from file validity.
    serversPinned: num(rec.calendars_pinned_ok, null),
    distinctPinned: num(rec.calendars_distinct_pinned, null),
  };
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
  hideRecordSections();
}

// Hide everything that only makes sense when a record exists (facts strip,
// honesty box, check-yourself, technical details, share block).
function hideRecordSections() {
  for (const node of document.querySelectorAll(".needs-record")) node.hidden = true;
}

function setVerdict(headline, sub, checked) {
  const h = $("#verdict-headline");
  const s = $("#verdict-sub");
  const c = $("#verdict-checked");
  if (h) h.textContent = headline;
  if (s) s.textContent = sub || "";
  if (c) c.textContent = checked || "";
}

// Verdict banner — the one thing a skeptic must read. Plain language only.
function renderVerdict(rec) {
  const d = rec.created_at ? new Date(rec.created_at) : null;
  const when = d && !isNaN(d.getTime())
    ? new Intl.DateTimeFormat(undefined, { year: "numeric", month: "long", day: "numeric" }).format(d)
    : rec.created_at || "the recorded date";

  if (rec.private) {
    const p = $("#verdict-private");
    if (p) p.hidden = false;
  }

  // The /api/verify endpoint re-checks the stored proofs on every request,
  // so "checked just now" is literally true.
  const checkedLine = "Record re-checked just now, at " + _fmtLocal(new Date()) + ".";

  if (rec.btc_pinned_at) {
    setVerdict(
      `This file existed on or before ${when}.`,
      "Anchored in the Bitcoin blockchain — verifiable by anyone, no account, no trust in Orphograph required. This fingerprint now lives in the most heavily verified public record in human history; it will outlive every company involved, including ours.",
      checkedLine
    );
    document.getElementById("verdict").classList.add("verdict-anchored");
  } else if ((rec.calendars_ok || 0) > 0) {
    setVerdict(
      `Sealed ${when} — awaiting Bitcoin confirmation.`,
      "The seal is in place; the Bitcoin anchor usually lands within hours. Refresh this page later to see it confirmed.",
      checkedLine
    );
    document.getElementById("verdict").classList.add("verdict-pending");
  } else {
    setVerdict(
      `Sealed ${when} — confirmation in progress.`,
      "The record exists but its proofs have not yet been confirmed. Check back shortly.",
      checkedLine
    );
    document.getElementById("verdict").classList.add("verdict-pending");
  }
}

// Facts strip — dates in explicit UTC so two skeptics in two time zones
// read the same numbers.
function renderFacts(rec) {
  const sealed = $("#fact-sealed");
  if (sealed) {
    const d = new Date(rec.created_at);
    sealed.textContent = isNaN(d.getTime()) ? (rec.created_at || "—") : _fmtUtc(d);
  }
  const btc = $("#fact-btc");
  if (btc) {
    if (rec.btc_pinned_at) {
      const d = new Date(rec.btc_pinned_at);
      btc.textContent = "Pinned " + (isNaN(d.getTime()) ? rec.btc_pinned_at : _fmtUtc(d));
    } else {
      btc.textContent = "Pending — usually within hours";
    }
  }
  const cals = $("#fact-cals");
  if (cals) {
    const c = calendarCounts(rec);
    cals.textContent =
      `${c.serversOk} of ${c.serversTotal} servers · ` +
      `${c.distinctOk} of ${c.distinctTotal} calendars`;
  }
  const rid = $("#fact-rid");
  if (rid) rid.textContent = rec.receipt_id || "—";
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
    if (r.status === 404) {
      // Honest verdict, in the verdict slot: nothing here, say so plainly.
      hideRecordSections();
      setVerdict(
        "No record with this id.",
        `Nothing has been anchored under “${rid}”. Check the link you were sent for typos, or ask the sender for the correct one.`,
        ""
      );
      document.getElementById("verdict").classList.add("verdict-missing");
      return;
    }
    if (r.status === 400) return showError(`Invalid receipt id: ${rid}`);
    return showError(`Server returned ${r.status}.`);
  }
  const rec = await r.json();
  // Folder (dataset) anchors render as a provenance certificate, not a
  // single-file receipt. Redirect so shared /r/<id> links to folder
  // anchors land on the right view.
  if (rec.kind === "folder") {
    location.replace("/certificate/" + escapePath(rid));
    return;
  }
  renderVerdict(rec);
  // Owner view: a signed-in visitor gets integration hints, not sales CTAs.
  fetch("/api/me").then((res) => {
    if (!res.ok) return;
    for (const id of ["visitor-cta-what"]) {
      const el = document.getElementById(id);
      if (el) el.hidden = true;
    }
    const hints = document.getElementById("owner-hints");
    if (hints) hints.hidden = false;
  }).catch(() => {});
  renderFacts(rec);
  const dl = document.getElementById("download-zip");
  if (dl) dl.href = "/api/receipt/" + escapePath(rec.receipt_id || rid) + ".zip";
  $("#rid").textContent = rec.receipt_id;
  $("#sha256").textContent = rec.hash_hex;
  $("#sha512").textContent = rec.sha512_hex || "(none — receipt predates SHA-512 sibling)";
  $("#label").textContent = rec.client_label || "(none)";
  // Friendly status copy. The bare word "partial" reads as broken, so it is
  // replaced with counts. Two denominators, because the five calendar servers
  // reach four distinct calendars (a.pool and b.pool are aggregators for alice
  // and bob) and it is the DISTINCT count the durability threshold measures —
  // three server acknowledgements can rest on two calendars under one
  // operator's domain.
  //
  // CONFIRMED is driven by the Bitcoin-pinned counts, never by proof validity:
  // a stamped-but-pending proof is not a confirmation.
  const _rawStatus = rec.status || "pending";
  const _c = calendarCounts(rec);
  const _stamped =
    `${_c.serversOk} of ${_c.serversTotal} calendar servers · ` +
    `${_c.distinctOk} of ${_c.distinctTotal} calendars`;
  const _confirmed = _c.serversPinned === null
    ? null
    : `${_c.serversPinned} of ${_c.serversTotal} calendar servers · ` +
      `${_c.distinctPinned} of ${_c.distinctTotal} calendars`;
  let _friendly;
  if (_rawStatus === "pinned" || _rawStatus === "partial") {
    _friendly = _confirmed === null
      ? `Anchored to Bitcoin · ${_stamped} stamped`
      : `Anchored to Bitcoin · ${_confirmed} confirmed`;
  } else if (_rawStatus === "pending") {
    _friendly = `Pending Bitcoin confirmation · ${_stamped} stamped`;
  } else {
    _friendly = `${_rawStatus} (${_stamped} stamped)`;
  }
  $("#status").textContent = _friendly;
  $("#cals").textContent =
    `${_c.serversOk} of ${_c.serversTotal} OTS proofs valid · ` +
    `${_c.distinctOk} of ${_c.distinctTotal} distinct calendars`;
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
  initShareBlock(rec);

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

// Initialises the "Share this proof" block on the receipt page:
// copy-link, native share (mobile), embeddable SVG badge with preview.
// A link ending in #share opens the embed drawer and brings the share block
// into view, so a "share this proof" CTA elsewhere lands on the controls.
function openShareFromHash() {
  if (window.location.hash !== "#share") return;
  const block = document.querySelector(".share-block");
  const details = document.querySelector(".share-block .embed-details");
  if (details) details.open = true;
  if (block && block.scrollIntoView) block.scrollIntoView({ block: "start" });
}
window.addEventListener("hashchange", openShareFromHash);
// All actions are origin-local — no third-party network calls.
function initShareBlock(rec) {
  const rid = rec.receipt_id;
  if (!rid) return;
  const origin = window.location.origin;
  const receiptUrl = origin + "/r/" + encodeURIComponent(rid);
  const badgeUrl = origin + "/api/badge/" + encodeURIComponent(rid) + ".svg";

  const urlField = document.getElementById("share-url");
  const copyBtn = document.getElementById("copy-link-btn");
  const nativeBtn = document.getElementById("native-share-btn");
  const badgeImg = document.getElementById("badge-preview");
  const embedTa = document.getElementById("embed-code");
  const copyEmbedBtn = document.getElementById("copy-embed-btn");
  if (!urlField || !copyBtn) return;

  urlField.value = receiptUrl;
  if (badgeImg) badgeImg.src = badgeUrl;
  if (embedTa) {
    embedTa.value =
      '<a href="' + receiptUrl + '" rel="noopener" aria-label="Verifiable proof of existence">\n' +
      '  <img src="' + badgeUrl + '" alt="Bitcoin-anchored proof of existence" height="32">\n' +
      '</a>';
  }

  function flashCopied(btn, original) {
    const prior = btn.textContent;
    btn.textContent = "Copied ✓";
    btn.disabled = true;
    setTimeout(() => { btn.textContent = original || prior; btn.disabled = false; }, 1600);
  }

  copyBtn.addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(receiptUrl);
      flashCopied(copyBtn, "Copy link");
    } catch (_) {
      urlField.select();
      urlField.setSelectionRange(0, 99999);
      try { document.execCommand("copy"); flashCopied(copyBtn, "Copy link"); } catch (__) {
        urlField.focus();
      }
    }
  });

  if (copyEmbedBtn && embedTa) {
    copyEmbedBtn.addEventListener("click", async () => {
      try {
        await navigator.clipboard.writeText(embedTa.value);
        flashCopied(copyEmbedBtn, "Copy embed code");
      } catch (_) {
        embedTa.select();
        try { document.execCommand("copy"); flashCopied(copyEmbedBtn, "Copy embed code"); } catch (__) { /* noop */ }
      }
    });
  }

  // Web Share API — only useful on mobile / supported desktops.
  if (nativeBtn && "share" in navigator) {
    nativeBtn.hidden = false;
    nativeBtn.addEventListener("click", async () => {
      try {
        await navigator.share({
          title: "Orphograph receipt " + rid,
          text: "A verifiable Bitcoin-anchored proof of existence.",
          url: receiptUrl,
        });
      } catch (_) { /* user dismissed; no-op */ }
    });
  }
  openShareFromHash();
}

main();
