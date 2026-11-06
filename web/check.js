/* Orphograph receiving-side check (2026-11-05).
 *
 * Whoever receives an output (a reply, a report, one step of an agent run)
 * checks it against a receipt in their own browser. The text or file is
 * hashed here and never sent; only the receipt id is used to fetch the
 * public record, and every hash in that record is recomputed, not believed.
 *
 * The rules mirror mcp/orphograph_mcp.py tool_verify_output exactly
 * (tests/test_check_page.py runs both on the same fixtures):
 *   single anchor: sha256(salt || bytes) == anchored hash
 *   run / folder:  leaf = sha256(0x00 || path || 0x00 || digest)
 *                  node = sha256(0x01 || left || right), a lone last node promoted
 *                  the rebuilt root must equal the anchored hash, then the
 *                  digest must sit in a leaf whose path binds it.
 * It shows the output existed by the anchor time. It does not show who
 * produced it or that it is correct.
 */
(function () {
  "use strict";

  var RID_RE = /^[A-Za-z0-9_-]{1,64}$/;
  var HEX_RE = /^[0-9a-f]*$/;

  function subtle() {
    var c = (typeof globalThis !== "undefined" && globalThis.crypto) || null;
    if (!c || !c.subtle) throw new Error("this browser has no Web Crypto");
    return c.subtle;
  }

  function utf8(s) { return new TextEncoder().encode(s); }

  function concat(parts) {
    var n = 0, i, off = 0, out;
    for (i = 0; i < parts.length; i++) n += parts[i].length;
    out = new Uint8Array(n);
    for (i = 0; i < parts.length; i++) { out.set(parts[i], off); off += parts[i].length; }
    return out;
  }

  function toHex(bytes) {
    var s = "", i;
    for (i = 0; i < bytes.length; i++) s += (bytes[i] < 16 ? "0" : "") + bytes[i].toString(16);
    return s;
  }

  function fromHex(hex) {
    if (typeof hex !== "string" || hex.length % 2 || !HEX_RE.test(hex.toLowerCase())) {
      throw new Error("not hex");
    }
    var out = new Uint8Array(hex.length / 2), i;
    for (i = 0; i < out.length; i++) out[i] = parseInt(hex.substr(i * 2, 2), 16);
    return out;
  }

  function sha256(bytes) {
    return subtle().digest("SHA-256", bytes).then(function (buf) { return new Uint8Array(buf); });
  }

  /* #r=<receipt id>&s=<salt hex>. The fragment never reaches a server. */
  function parseFragment(hash) {
    var out = { receipt_id: "", salt_hex: "" };
    (hash || "").replace(/^#/, "").split("&").forEach(function (kv) {
      var i = kv.indexOf("="), k = i < 0 ? kv : kv.slice(0, i), v = i < 0 ? "" : kv.slice(i + 1);
      try { v = decodeURIComponent(v); } catch (e) { v = ""; }
      if (k === "r" && RID_RE.test(v)) out.receipt_id = v;
      if (k === "s" && !saltProblem(v)) out.salt_hex = v;
    });
    return out;
  }

  /* The MCP tool's rule (mcp/orphograph_mcp.py _parse_salt), same message. */
  var SALT_HEX_RE = /^[0-9a-f]{64}$/;
  function saltProblem(saltHex) {
    if (typeof saltHex === "string" && SALT_HEX_RE.test(saltHex)) return null;
    return "salt_hex must be 64 lowercase hex characters (32 bytes)";
  }

  /* Pasting loses bytes a file keeps: a textarea turns CRLF into LF, and a
     copy adds or drops the final newline. These are the only variants tried,
     each named in the verdict, and only for pasted text, never for a file. */
  function pasteVariants(text) {
    var out = [], seen = {};
    function add(label, s) { if (!seen.hasOwnProperty(s)) { seen[s] = 1; out.push({ label: label, text: s }); } }
    var bare = text.replace(/\r?\n$/, "");
    add("", text);
    add("with a final newline added", bare + "\n");
    add("without its final newline", bare);
    add("with Windows line endings (CRLF)", bare.replace(/\r?\n/g, "\r\n"));
    add("with Windows line endings (CRLF) and a final newline", bare.replace(/\r?\n/g, "\r\n") + "\r\n");
    return out;
  }

  function outputDigest(bytes, saltHex) {
    var salt = saltHex ? fromHex(saltHex) : new Uint8Array(0);
    return sha256(concat([salt, bytes])).then(toHex);
  }

  function leafHash(path, digestBytes) {
    return sha256(concat([new Uint8Array([0]), utf8(path), new Uint8Array([0]), digestBytes]));
  }

  function merkleRoot(hashes) {
    var level = hashes.slice();
    function step() {
      if (level.length <= 1) return Promise.resolve(level[0]);
      var jobs = [], i;
      for (i = 0; i < level.length; i += 2) {
        if (i + 1 < level.length) jobs.push(sha256(concat([new Uint8Array([1]), level[i], level[i + 1]])));
        else jobs.push(Promise.resolve(level[i]));
      }
      return Promise.all(jobs).then(function (next) { level = next; return step(); });
    }
    return step();
  }

  /* Rebuild the root from served leaves without trusting their leaf_hex.
     Resolves {root: Uint8Array|null, problems: [string]}. */
  function rebuildRoot(leaves) {
    if (!Array.isArray(leaves) || !leaves.length) {
      return Promise.resolve({ root: null, problems: ["manifest has no leaves"] });
    }
    var problems = [], jobs = [], i;
    for (i = 0; i < leaves.length; i++) {
      var leaf = leaves[i];
      if (!leaf || typeof leaf !== "object") {
        return Promise.resolve({ root: null, problems: ["leaf " + i + " is not an object"] });
      }
      var d = leaf.file_sha256_hex, lh = leaf.leaf_hex, p = leaf.path;
      try {
        if (typeof p === "string" && typeof d === "string") {
          jobs.push((function (idx, served, digest, path) {
            return leafHash(path, digest).then(function (h) {
              if (typeof served === "string" && served.toLowerCase() !== toHex(h)) {
                problems.push("leaf " + idx + ": served leaf_hex does not match its path and digest");
              }
              return h;
            });
          })(i, lh, fromHex(d.toLowerCase()), p));
        } else if (typeof lh === "string") {
          jobs.push(Promise.resolve(fromHex(lh.toLowerCase())));
        } else {
          return Promise.resolve({ root: null,
            problems: ["leaf " + i + " carries neither a path+digest nor a leaf hash"] });
        }
      } catch (e) {
        return Promise.resolve({ root: null, problems: ["leaf " + i + " is not hex"] });
      }
    }
    return Promise.all(jobs).then(function (hashes) {
      return merkleRoot(hashes).then(function (root) { return { root: root, problems: problems }; });
    });
  }

  /* decide(target, rec, getFolder) -> Promise of the same shape the MCP tool
     returns. rec is the parsed /api/verify/<id> answer (or {error, status});
     getFolder() resolves the parsed /api/verify_folder/<id> answer. */
  function decide(receiptId, target, rec, getFolder) {
    if (!rec || typeof rec !== "object") {
      return Promise.resolve({ ok: false, error: "the office answered with an unexpected shape" });
    }
    if (rec.error) {
      return Promise.resolve({ ok: false, error: String(rec.error), status: rec.status || null });
    }
    var anchored = typeof rec.hash_hex === "string" ? rec.hash_hex.toLowerCase() : "";
    var base = { ok: true, receipt_id: receiptId, output_sha256_hex: target,
                 anchored_hash_sha256: anchored || null,
                 anchored_at_utc: rec.created_at || null,
                 btc_pinned_at_utc: rec.btc_pinned_at || null };
    function out(extra) { var o = {}, k; for (k in base) o[k] = base[k]; for (k in extra) o[k] = extra[k]; return o; }
    if (!anchored) return Promise.resolve(out({ match: false, reason: "the receipt carries no anchored hash" }));
    if (anchored === target) return Promise.resolve(out({ match: true, kind: "single" }));
    return Promise.resolve(getFolder()).then(function (folder) {
      if (!folder || typeof folder !== "object") folder = { error: "bad_shape" };
      if (folder.error) {
        if (folder.status === 400) {
          return out({ match: false, kind: "single", reason: "the output's hash is not the anchored hash" });
        }
        return { ok: false, receipt_id: receiptId,
                 error: "could not fetch the run's manifest; nothing was decided" };
      }
      var served = folder.manifest;
      var leaves = served && typeof served === "object" && Array.isArray(served.leaves) ? served.leaves : [];
      return rebuildRoot(leaves).then(function (rb) {
        if (!rb.root || toHex(rb.root) !== anchored || rb.problems.length) {
          return out({ match: false, kind: "run",
                       reason: "the served manifest does not recompute to the anchored root; do not trust it",
                       problems: rb.problems.length ? rb.problems : ["recomputed root differs from the anchored hash"] });
        }
        var hits = leaves.filter(function (l) {
          return l && typeof l.file_sha256_hex === "string" && l.file_sha256_hex.toLowerCase() === target;
        });
        var bound = hits.filter(function (l) { return typeof l.path === "string"; })
                        .map(function (l) { return l.path; });
        if (bound.length) return out({ match: true, kind: "run", matched_paths: bound, leaf_count: leaves.length });
        if (hits.length) {
          return out({ match: false, kind: "run", digest_listed: true,
                       reason: "the digest is listed, but the owner kept paths private, so its leaf cannot be bound to it from here" });
        }
        return out({ match: false, kind: "run", leaf_count: leaves.length,
                     reason: "no step in this run has the output's hash" });
      });
    });
  }

  var api = { parseFragment: parseFragment, outputDigest: outputDigest, leafHash: leafHash,
              saltProblem: saltProblem, pasteVariants: pasteVariants,
              merkleRoot: merkleRoot, rebuildRoot: rebuildRoot, decide: decide, toHex: toHex,
              fromHex: fromHex, RID_RE: RID_RE };
  if (typeof module !== "undefined" && module.exports) { module.exports = api; return; }
  if (typeof document === "undefined") return;

  // ── page wiring ─────────────────────────────────────────────────
  function $(id) { return document.getElementById(id); }
  function apiBase() {
    return /^https?:$/.test(location.protocol) ? location.origin : "https://orphograph.com";
  }
  function getJson(path) {
    return fetch(apiBase() + path, { headers: { "Accept": "application/json" }, redirect: "error" })
      .then(function (r) {
        return r.json().catch(function () { return {}; }).then(function (body) {
          if (!r.ok) return { error: (body && body.error) || ("HTTP " + r.status), status: r.status };
          return body;
        });
      }, function () { return { error: "network", status: 0 }; });
  }
  function setText(el, s) { el.textContent = s; }

  var droppedFile = null;
  var frag = parseFragment(location.hash);
  if (frag.receipt_id) $("chk-rid").value = frag.receipt_id;
  if (frag.salt_hex) $("chk-salt").value = frag.salt_hex;

  $("chk-file").addEventListener("change", function (e) {
    droppedFile = e.target.files && e.target.files[0] ? e.target.files[0] : null;
    if (droppedFile) $("chk-text").value = "";
    setText($("chk-file-name"), droppedFile ? droppedFile.name + " (" + droppedFile.size + " bytes)" : "");
  });

  function render(res) {
    var box = $("chk-result");
    var good = res.ok && res.match;
    box.className = "v-result " + (good ? "valid" : "mismatch");
    var verdict = $("chk-verdict");
    verdict.className = "v-verdict " + (good ? "valid" : "mismatch");
    if (!res.ok) {
      setText(verdict, "Not decided: " + (res.error === "network" ? "the office could not be reached" : res.error));
    } else if (good) {
      setText(verdict, res.kind === "run"
        ? "Match. This exact output is " + res.matched_paths.join(", ") + " of a run of " + res.leaf_count + " anchored together."
        : "Match. This exact output is what the receipt anchored.");
    } else {
      setText(verdict, "No match. " + (res.reason || "The hashes differ."));
    }
    var rows = [];
    if (res.ok) {
      rows.push(["Anchored at", res.anchored_at_utc || "unknown"]);
      rows.push(["In a Bitcoin block", res.btc_pinned_at_utc ? "yes, by " + res.btc_pinned_at_utc : "not yet; Bitcoin confirmation has taken from about an hour to several days"]);
    }
    if (res.checked) rows.push(["Checked", res.checked]);
    if (res.output_sha256_hex) rows.push(["Your output's SHA-256", res.output_sha256_hex]);
    (res.problems || []).forEach(function (p) { rows.push(["Problem", p]); });
    var holder = $("chk-rows");
    while (holder.firstChild) holder.removeChild(holder.firstChild);
    rows.forEach(function (kv) {
      var row = document.createElement("p"), k = document.createElement("span"), v = document.createElement("span");
      row.className = "v-row"; k.className = "k"; v.className = "mono";
      setText(k, kv[0]); setText(v, kv[1]); row.appendChild(k); row.appendChild(v); holder.appendChild(row);
    });
    var rid = $("chk-rid").value.trim();
    var link = $("chk-receipt-link");
    if (RID_RE.test(rid)) { link.href = "/r/" + encodeURIComponent(rid); link.hidden = false; }
  }

  $("chk-text").addEventListener("input", function () {
    if (droppedFile && $("chk-text").value) {
      droppedFile = null; $("chk-file").value = ""; setText($("chk-file-name"), "");
    }
  });

  $("chk-form").addEventListener("submit", function (e) {
    e.preventDefault();
    var rid = $("chk-rid").value.trim();
    var saltHex = $("chk-salt").value.trim();
    if (!RID_RE.test(rid)) { render({ ok: false, error: "that is not a receipt id" }); return; }
    if (saltHex && saltProblem(saltHex)) { render({ ok: false, error: saltProblem(saltHex) }); return; }
    var file = droppedFile, pasted = $("chk-text").value;
    var btn = $("chk-go");
    btn.disabled = true;
    var recP = null, folderP = null;
    function rec() { return recP || (recP = getJson("/api/verify/" + encodeURIComponent(rid))); }
    function folder() { return folderP || (folderP = getJson("/api/verify_folder/" + encodeURIComponent(rid))); }
    function check(bytes) {
      return outputDigest(bytes, saltHex).then(function (target) {
        return rec().then(function (r) { return decide(rid, target, r, folder); });
      });
    }
    var work;
    if (file) {
      work = file.arrayBuffer().then(function (b) { return check(new Uint8Array(b)); })
        .then(function (res) { res.checked = "the file " + file.name + " (" + file.size + " bytes, exact)"; return res; });
    } else if (!pasted) {
      work = Promise.reject(new Error("paste the output or choose a file first"));
    } else {
      var variants = pasteVariants(pasted), first = null;
      work = (function next(k) {
        return check(utf8(variants[k].text)).then(function (res) {
          if (k === 0) first = res;
          if ((res.ok && res.match) || !res.ok || k + 1 >= variants.length) {
            var out = res.ok && res.match ? res : first;
            out.checked = "the pasted text" + (out === res && variants[k].label ? ", " + variants[k].label : "");
            if (!(out.ok && out.match) && out.ok) {
              out.reason = (out.reason || "the hashes differ") +
                ". Pasting can change line endings and the final newline; tried those too. If you have the original file, choose it instead";
            }
            return out;
          }
          return next(k + 1);
        });
      })(0);
    }
    work.then(render, function (err) {
      render({ ok: false, error: err && err.message ? err.message : "could not check" });
    }).then(function () { btn.disabled = false; });
  });
})();
