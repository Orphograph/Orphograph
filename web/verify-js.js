(function () {
  "use strict";

  var state = {
    file: null,
    fileSha256: null,
    fileSha512: null,
    receipt: null,
    receiptRaw: ""
  };

  var fileZone = document.getElementById("fileZone");
  var filePicker = document.getElementById("filePicker");
  var fileStatus = document.getElementById("fileStatus");
  var receiptZone = document.getElementById("receiptZone");
  var receiptPicker = document.getElementById("receiptPicker");
  var receiptStatus = document.getElementById("receiptStatus");
  var receiptPaste = document.getElementById("receiptPaste");
  var verifyBtn = document.getElementById("verifyBtn");
  var resetBtn = document.getElementById("resetBtn");
  var resultBox = document.getElementById("result");
  var fetchBtn = document.getElementById("fetchBtn");
  var fetchId = document.getElementById("fetchId");
  var fetchStatus = document.getElementById("fetchStatus");

  function bufToHex(buf) {
    var bytes = new Uint8Array(buf);
    var hex = "";
    for (var i = 0; i < bytes.length; i++) {
      var h = bytes[i].toString(16);
      if (h.length === 1) h = "0" + h;
      hex += h;
    }
    return hex;
  }

  function setText(el, s) {
    while (el.firstChild) el.removeChild(el.firstChild);
    el.appendChild(document.createTextNode(s));
  }

  function fmtBytes(n) {
    if (n < 1024) return n + " bytes";
    if (n < 1024 * 1024) return (n / 1024).toFixed(1) + " KB";
    return (n / (1024 * 1024)).toFixed(2) + " MB";
  }

  function updateVerifyEnabled() {
    verifyBtn.disabled = !(state.file && state.receipt);
  }

  function clearResult() {
    while (resultBox.firstChild) resultBox.removeChild(resultBox.firstChild);
    resultBox.className = "v-result pending";
    var p = document.createElement("p");
    p.className = "v-verdict";
    p.appendChild(document.createTextNode("Awaiting a file and a receipt."));
    resultBox.appendChild(p);
    var p2 = document.createElement("p");
    p2.className = "v-row";
    p2.appendChild(document.createTextNode("Once both are present, the verifier will compute SHA-256 locally and compare it to the fingerprint recorded in the receipt. If the receipt also carries SHA-512, the sibling check is performed as well."));
    resultBox.appendChild(p2);
  }

  function setFile(f) {
    state.file = f;
    state.fileSha256 = null;
    state.fileSha512 = null;
    setText(fileStatus, "Selected: " + f.name + " (" + fmtBytes(f.size) + ")");
    updateVerifyEnabled();
  }

  function parseReceiptText(txt) {
    var t = (txt || "").trim();
    if (!t) return null;
    try {
      var obj = JSON.parse(t);
      if (obj && typeof obj === "object") return obj;
    } catch (e) {
      // not JSON; ignore
    }
    return null;
  }

  function describeReceipt(r) {
    var hash = (r && typeof r.hash_hex === "string") ? r.hash_hex : "";
    var rid = (r && (r.receipt_id || r.id || r.receiptId)) || "";
    var parts = [];
    if (rid) parts.push("receipt_id: " + rid);
    if (hash) parts.push("hash_hex: " + hash.slice(0, 16) + "...");
    return parts.join(" | ");
  }

  function setReceipt(obj, sourceLabel) {
    state.receipt = obj;
    var msg = sourceLabel ? sourceLabel + " parsed." : "Receipt parsed.";
    var desc = describeReceipt(obj);
    setText(receiptStatus, msg + (desc ? "  " + desc : ""));
    updateVerifyEnabled();
  }

  function rejectReceipt(msg) {
    state.receipt = null;
    setText(receiptStatus, msg);
    updateVerifyEnabled();
  }

  // File drop zone
  fileZone.addEventListener("click", function () { filePicker.click(); });
  fileZone.addEventListener("keydown", function (e) {
    if (e.key === "Enter" || e.key === " ") { e.preventDefault(); filePicker.click(); }
  });
  filePicker.addEventListener("change", function () {
    if (filePicker.files && filePicker.files[0]) setFile(filePicker.files[0]);
  });
  ["dragenter", "dragover"].forEach(function (evt) {
    fileZone.addEventListener(evt, function (e) {
      e.preventDefault(); e.stopPropagation();
      fileZone.classList.add("over");
    });
  });
  ["dragleave", "drop"].forEach(function (evt) {
    fileZone.addEventListener(evt, function (e) {
      e.preventDefault(); e.stopPropagation();
      fileZone.classList.remove("over");
    });
  });
  fileZone.addEventListener("drop", function (e) {
    var dt = e.dataTransfer;
    if (dt && dt.files && dt.files[0]) setFile(dt.files[0]);
  });

  // Receipt drop zone (avoid stealing clicks on the textarea/input)
  receiptZone.addEventListener("click", function (e) {
    if (e.target === receiptPaste || e.target.tagName === "TEXTAREA" || e.target.tagName === "INPUT") return;
    receiptPicker.click();
  });
  receiptPicker.addEventListener("change", function () {
    if (receiptPicker.files && receiptPicker.files[0]) {
      var rf = receiptPicker.files[0];
      var reader = new FileReader();
      reader.onload = function () {
        var txt = String(reader.result || "");
        receiptPaste.value = txt;
        var obj = parseReceiptText(txt);
        if (obj) setReceipt(obj, "Receipt file");
        else rejectReceipt("Receipt file did not parse as JSON.");
      };
      reader.onerror = function () { rejectReceipt("Could not read the selected file."); };
      reader.readAsText(rf);
    }
  });
  ["dragenter", "dragover"].forEach(function (evt) {
    receiptZone.addEventListener(evt, function (e) {
      e.preventDefault(); e.stopPropagation();
      receiptZone.classList.add("over");
    });
  });
  ["dragleave", "drop"].forEach(function (evt) {
    receiptZone.addEventListener(evt, function (e) {
      e.preventDefault(); e.stopPropagation();
      receiptZone.classList.remove("over");
    });
  });
  receiptZone.addEventListener("drop", function (e) {
    var dt = e.dataTransfer;
    if (!dt) return;
    if (dt.files && dt.files[0]) {
      var rf = dt.files[0];
      var reader = new FileReader();
      reader.onload = function () {
        var txt = String(reader.result || "");
        receiptPaste.value = txt;
        var obj = parseReceiptText(txt);
        if (obj) setReceipt(obj, "Receipt file");
        else rejectReceipt("Dropped file did not parse as JSON.");
      };
      reader.readAsText(rf);
      return;
    }
    var txt = dt.getData && dt.getData("text");
    if (txt) {
      receiptPaste.value = txt;
      var obj2 = parseReceiptText(txt);
      if (obj2) setReceipt(obj2, "Pasted receipt");
      else rejectReceipt("Dropped text did not parse as JSON.");
    }
  });
  receiptPaste.addEventListener("input", function () {
    var txt = receiptPaste.value;
    if (!txt.trim()) {
      state.receipt = null;
      setText(receiptStatus, "");
      updateVerifyEnabled();
      return;
    }
    var obj = parseReceiptText(txt);
    if (obj) setReceipt(obj, "Pasted receipt");
    else {
      // Maybe the user pasted only a receipt_id; do not reject loudly.
      state.receipt = null;
      setText(receiptStatus, "Pasted text is not JSON. If it is a receipt id, use the optional fetch below.");
      // Pre-fill the fetch field
      var trimmed = txt.trim();
      if (trimmed.length > 0 && trimmed.length < 64 && /^[A-Za-z0-9_\-]+$/.test(trimmed)) {
        fetchId.value = trimmed;
      }
      updateVerifyEnabled();
    }
  });

  // Optional fetch by receipt_id.
  //
  // Endpoint resolution. Served over http(s), the verifier must query the
  // origin that served it: an absolute host baked into the script makes a
  // staging, mirror, localhost or self-hosted copy silently query production.
  // A saved file:// copy has no origin to be relative to — "/api/verify/<id>"
  // there resolves to file:///api/verify/<id>, which is not a degraded request
  // but a nonsense URL the fetch layer rejects outright, so a bare relative URL
  // would make the saved-copy experience worse, not better. That case names the
  // public office explicitly and says so on screen before the request leaves.
  //
  // Nothing else on this page touches the network: the file is hashed locally
  // and compared to the receipt locally. This fetch is the single optional
  // convenience and it runs only on an explicit button press.
  var PUBLIC_OFFICE = "https://orphograph.com";
  var FETCH_TIMEOUT_MS = 15000;

  function resolveVerifyEndpoint(id) {
    var path = "/api/verify/" + encodeURIComponent(id);
    var proto = (window.location && window.location.protocol) || "";
    if (proto === "http:" || proto === "https:") {
      return { url: path, note: "Fetching..." };
    }
    return {
      url: PUBLIC_OFFICE + path,
      note: "This saved copy has no origin of its own; querying the public office at orphograph.com."
    };
  }

  fetchBtn.addEventListener("click", function () {
    var id = (fetchId.value || "").trim();
    if (!id) {
      setText(fetchStatus, "No receipt_id provided.");
      return;
    }
    if (!/^[A-Za-z0-9_\-]{4,64}$/.test(id)) {
      setText(fetchStatus, "Receipt id has an unexpected shape; fetch was not attempted.");
      return;
    }
    var endpoint = resolveVerifyEndpoint(id);
    setText(fetchStatus, endpoint.note);
    if (typeof fetch !== "function") {
      setText(fetchStatus, "This browser cannot fetch. Paste the receipt JSON above; verification itself runs locally and needs no network.");
      return;
    }
    try {
      // No request may hang: without a deadline a stalled connection leaves
      // the status line reading "Fetching..." forever, which is exactly the
      // silent failure this surface must not produce.
      var opts = { method: "GET", credentials: "omit", cache: "no-store" };
      var timer = null;
      if (typeof AbortController === "function") {
        var ctrl = new AbortController();
        opts.signal = ctrl.signal;
        timer = setTimeout(function () { ctrl.abort(); }, FETCH_TIMEOUT_MS);
      }
      var clearTimer = function () { if (timer !== null) { clearTimeout(timer); timer = null; } };
      fetch(endpoint.url, opts)
        .then(function (r) {
          if (!r.ok) throw new Error("HTTP " + r.status);
          return r.text();
        })
        .then(function (txt) {
          clearTimer();
          var obj = parseReceiptText(txt);
          if (!obj) throw new Error("Response was not JSON.");
          receiptPaste.value = txt;
          setReceipt(obj, "Fetched receipt");
          setText(fetchStatus, "Fetched.");
        })
        .catch(function (err) {
          clearTimer();
          var why = err && err.name === "AbortError"
            ? "no response within " + (FETCH_TIMEOUT_MS / 1000) + " seconds"
            : (err && err.message ? err.message : String(err));
          setText(fetchStatus, "The receipt could not be fetched (" + why + "). Paste the receipt JSON above instead; verification itself runs locally and needs no network.");
        });
    } catch (err) {
      setText(fetchStatus, "The receipt could not be fetched in this environment. Paste the receipt JSON above instead; verification itself runs locally and needs no network.");
    }
  });

  // Verification
  function digest(name, buf) {
    return crypto.subtle.digest(name, buf).then(bufToHex);
  }

  function readFileBuf(f) {
    return new Promise(function (resolve, reject) {
      var reader = new FileReader();
      reader.onload = function () { resolve(reader.result); };
      reader.onerror = function () { reject(new Error("Could not read the file.")); };
      reader.readAsArrayBuffer(f);
    });
  }

  function renderResult(out) {
    while (resultBox.firstChild) resultBox.removeChild(resultBox.firstChild);
    resultBox.className = "v-result " + (out.valid ? "valid" : "mismatch");

    var verdict = document.createElement("p");
    verdict.className = "v-verdict " + (out.valid ? "valid" : "mismatch");
    verdict.appendChild(document.createTextNode(out.headline));
    resultBox.appendChild(verdict);

    function addRow(k, v, mono) {
      var p = document.createElement("p");
      p.className = "v-row";
      var ks = document.createElement("span");
      ks.className = "k";
      ks.appendChild(document.createTextNode(k));
      var vs = document.createElement("span");
      if (mono) vs.className = "mono";
      vs.appendChild(document.createTextNode(v));
      p.appendChild(ks);
      p.appendChild(vs);
      resultBox.appendChild(p);
    }

    addRow("File", out.fileName || "(unnamed)");
    addRow("File size", fmtBytes(out.fileSize || 0));
    addRow("Local SHA-256", out.localSha256 || "—", true);
    addRow("Receipt hash_hex", out.receiptSha256 || "(absent)", true);
    addRow("SHA-256 match", out.sha256Match ? "yes" : "no");

    if (out.receiptSha512 || out.localSha512) {
      addRow("Local SHA-512", out.localSha512 || "—", true);
      addRow("Receipt sha512_hex", out.receiptSha512 || "(absent)", true);
      addRow("SHA-512 match", out.sha512Match ? "yes" : (out.receiptSha512 ? "no" : "(not checked)"));
    }

    if (out.receiptId) addRow("receipt_id", out.receiptId, true);
    if (out.anchoredAt) addRow("Receipt asserts", out.anchoredAt);

    var note = document.createElement("p");
    note.className = "v-row";
    note.style.marginTop = "14px";
    note.style.color = "var(--muted-local)";
    note.appendChild(document.createTextNode(out.note));
    resultBox.appendChild(note);
  }

  verifyBtn.addEventListener("click", function () {
    if (!state.file || !state.receipt) return;
    verifyBtn.disabled = true;
    var setBusy = function () {
      while (resultBox.firstChild) resultBox.removeChild(resultBox.firstChild);
      resultBox.className = "v-result pending";
      var p = document.createElement("p");
      p.className = "v-verdict";
      p.appendChild(document.createTextNode("Computing local fingerprints..."));
      resultBox.appendChild(p);
    };
    setBusy();

    var receipt = state.receipt;
    // Canonical fields only, stored value compared AS-IS: the engine
    // (server/engine.py verify_hash_against_receipt) lowercases only the
    // locally computed side and takes the stored hash verbatim — receipts
    // are issued in lowercase hex (engine.py anchor_hash normalises once,
    // at write time). A receipt whose stored hash was tampered to uppercase,
    // or that carries only alias fields, must NOT verify here either
    // (docs/VERIFIER_SPEC.md §3.2–3.3; AUDIT_VERIFIER_DRIFT D1/D5).
    var receiptSha256 = typeof receipt.hash_hex === "string" ? receipt.hash_hex : "";
    var receiptSha512 = typeof receipt.sha512_hex === "string" ? receipt.sha512_hex : "";
    var receiptId = receipt.receipt_id || receipt.id || receipt.receiptId || "";
    var anchoredAt = receipt.bitcoin_block_time || receipt.anchored_at || receipt.attested_at || receipt.issued_at || "";

    readFileBuf(state.file).then(function (buf) {
      var p256 = digest("SHA-256", buf);
      var p512 = receiptSha512 ? digest("SHA-512", buf) : Promise.resolve("");
      return Promise.all([p256, p512]);
    }).then(function (hashes) {
      var localSha256 = (hashes[0] || "").toLowerCase();
      var localSha512 = (hashes[1] || "").toLowerCase();
      state.fileSha256 = localSha256;
      state.fileSha512 = localSha512;

      var sha256Match = !!receiptSha256 && localSha256 === receiptSha256;
      var sha512Match = !!receiptSha512 && localSha512 === receiptSha512;

      var allRequired = sha256Match && (receiptSha512 ? sha512Match : true);
      var valid = !!receiptSha256 && allRequired;

      // Case-tamper diagnosis: a stored hash that matches only after
      // lowercasing is NOT a canonical Orphograph fingerprint — the
      // receipt was edited out-of-band. Still a mismatch; better note.
      var caseTamper =
        (!sha256Match && !!receiptSha256 && receiptSha256.toLowerCase() === localSha256) ||
        (!!receiptSha512 && !sha512Match && receiptSha512.toLowerCase() === localSha512 && sha256Match);

      var headline, note;
      if (!receiptSha256) {
        headline = "Receipt incomplete";
        note = "The supplied receipt has no hash_hex field; nothing can be compared. Confirm that the JSON is an Orphograph receipt.";
      } else if (valid) {
        headline = "Match — the file corresponds to the receipt.";
        note = receiptSha512
          ? "SHA-256 and SHA-512 both match. For full chain-level verification, run the receipt's .ots files through the published Python verifier or any OpenTimestamps client."
          : "SHA-256 matches. The receipt does not carry SHA-512; the single-hash check is sufficient for binding. For chain-level verification, see the .ots files.";
      } else if (caseTamper) {
        headline = "Mismatch — the receipt is not in canonical form.";
        note = "The receipt's stored fingerprint differs from the canonical lowercase hex this office issues. The receipt has been altered or re-typed since issue; it does not verify as-is.";
      } else {
        headline = "Mismatch — the file does not correspond to the receipt.";
        note = "The local fingerprint differs from the fingerprint recorded in the receipt. The file may have been modified, or the receipt belongs to a different file.";
      }

      renderResult({
        valid: valid,
        headline: headline,
        note: note,
        fileName: state.file.name,
        fileSize: state.file.size,
        localSha256: localSha256,
        localSha512: localSha512 || "",
        receiptSha256: receiptSha256,
        receiptSha512: receiptSha512,
        sha256Match: sha256Match,
        sha512Match: sha512Match,
        receiptId: receiptId,
        anchoredAt: anchoredAt
      });

      verifyBtn.disabled = false;
    }).catch(function (err) {
      while (resultBox.firstChild) resultBox.removeChild(resultBox.firstChild);
      resultBox.className = "v-result mismatch";
      var p = document.createElement("p");
      p.className = "v-verdict mismatch";
      p.appendChild(document.createTextNode("The verifier could not complete."));
      resultBox.appendChild(p);
      var p2 = document.createElement("p");
      p2.className = "v-row";
      p2.appendChild(document.createTextNode((err && err.message ? err.message : String(err))));
      resultBox.appendChild(p2);
      verifyBtn.disabled = false;
    });
  });

  resetBtn.addEventListener("click", function () {
    state.file = null;
    state.receipt = null;
    state.fileSha256 = null;
    state.fileSha512 = null;
    filePicker.value = "";
    receiptPicker.value = "";
    receiptPaste.value = "";
    fetchId.value = "";
    setText(fileStatus, "");
    setText(receiptStatus, "");
    setText(fetchStatus, "");
    clearResult();
    updateVerifyEnabled();
  });

  // Feature probe
  if (!window.crypto || !crypto.subtle || !crypto.subtle.digest) {
    setText(fileStatus, "This browser does not expose WebCrypto SubtleCrypto; verification cannot run.");
    verifyBtn.disabled = true;
  }
})();
