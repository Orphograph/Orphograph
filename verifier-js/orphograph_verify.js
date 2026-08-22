/*!
 * orphograph_verify.js — independent JavaScript verifier for Orphograph receipts.
 *
 * Scope of attestation:
 *   This module verifies the FILE-TO-RECEIPT BINDING only. Given a file and a
 *   receipt JSON, it recomputes the file's SHA-256 (and SHA-512 when the
 *   receipt carries one) and compares the result against the fingerprints
 *   recorded in the receipt. A successful match establishes that the receipt
 *   was issued for that exact byte sequence.
 *
 *   This module DOES NOT verify the Bitcoin-chain attestation. The chain
 *   verification is borne by the receipt's `.ots` proof files and requires a
 *   Bitcoin node or an OpenTimestamps client. For full chain-level
 *   verification, the Python verifier published at
 *   https://github.com/Orphograph/Orphograph or any OpenTimestamps client
 *   may be used. The two checks together constitute complete independent
 *   verification of an Orphograph receipt.
 *
 * Dependencies:
 *   None. The module uses only the WebCrypto API (`crypto.subtle.digest`),
 *   which is native in Node 18+ and in every modern browser. No npm install
 *   is required; the file may be copied directly into any project.
 *
 * Module format:
 *   This file is an ES module. It loads natively in modern browsers via
 *   `<script type="module">` and in Node 18+ via `import` (use `.mjs`, or
 *   set `"type": "module"` in package.json, or run with
 *   `node --input-type=module`). CommonJS consumers may load it through
 *   dynamic `import()`:
 *
 *     const { verifyReceiptAgainstFile } = await import("./orphograph_verify.js");
 *
 * License: MIT. See accompanying LICENSE file.
 */

"use strict";

// Resolve the WebCrypto SubtleCrypto provider in either Node or browser.
function getSubtle() {
  if (typeof globalThis !== "undefined" && globalThis.crypto && globalThis.crypto.subtle) {
    return globalThis.crypto.subtle;
  }
  if (typeof self !== "undefined" && self.crypto && self.crypto.subtle) {
    return self.crypto.subtle;
  }
  throw new Error("WebCrypto SubtleCrypto is not available in this environment. Node 18+ or a modern browser is required.");
}

function bufToHex(buf) {
  const bytes = new Uint8Array(buf);
  let hex = "";
  for (let i = 0; i < bytes.length; i++) {
    const h = bytes[i].toString(16);
    hex += h.length === 1 ? "0" + h : h;
  }
  return hex;
}

function isBlobLike(x) {
  return typeof Blob !== "undefined" && x instanceof Blob;
}

// Async generator that yields ArrayBuffer chunks from a Blob/File without
// loading the entire object into memory at once. Used for the browser path
// where File may be very large.
async function* blobChunks(blob, chunkSize) {
  let off = 0;
  while (off < blob.size) {
    const slice = blob.slice(off, Math.min(off + chunkSize, blob.size));
    yield await slice.arrayBuffer();
    off += chunkSize;
  }
}

// Collect any supported input shape into a single Uint8Array. For Blob/File
// inputs the body is streamed in chunks; for in-memory inputs (Uint8Array,
// Buffer, ArrayBuffer) the bytes are forwarded directly without copy where
// possible. WebCrypto's digest() does not accept a stream, so the final
// digest call requires a contiguous buffer regardless.
async function materialise(fileBytes, chunkSize) {
  if (isBlobLike(fileBytes)) {
    const size = fileBytes.size;
    const out = new Uint8Array(size);
    let off = 0;
    for await (const c of blobChunks(fileBytes, chunkSize)) {
      const u = new Uint8Array(c);
      out.set(u, off);
      off += u.byteLength;
    }
    return out;
  }
  if (fileBytes instanceof ArrayBuffer) {
    return new Uint8Array(fileBytes);
  }
  if (fileBytes && typeof fileBytes.byteLength === "number" && typeof fileBytes.buffer !== "undefined") {
    // Uint8Array, Node Buffer, or other typed-array view.
    return new Uint8Array(fileBytes.buffer, fileBytes.byteOffset, fileBytes.byteLength);
  }
  if (fileBytes && typeof fileBytes.byteLength === "number") {
    return new Uint8Array(fileBytes);
  }
  throw new TypeError("hashFile expected a Uint8Array, ArrayBuffer, Buffer, Blob, or File.");
}

/**
 * Compute the SHA-256 (and optional SHA-512) of a file.
 *
 * @param {Uint8Array | ArrayBuffer | Buffer | Blob | File} fileBytes
 * @param {{ sha512?: boolean, chunkSize?: number }} [opts]
 * @returns {Promise<{ sha256_hex: string, sha512_hex: string | null, size: number }>}
 */
export async function hashFile(fileBytes, opts) {
  const options = opts || {};
  const wantSha512 = !!options.sha512;
  const chunkSize = options.chunkSize || (1024 * 1024);

  const bytes = await materialise(fileBytes, chunkSize);
  const subtle = getSubtle();

  // Pass an ArrayBuffer of exactly the right byte range; some hosts reject
  // views whose underlying buffer is larger than the view itself.
  const view = bytes.byteOffset === 0 && bytes.byteLength === bytes.buffer.byteLength
    ? bytes.buffer
    : bytes.slice().buffer;

  const sha256_hex = bufToHex(await subtle.digest("SHA-256", view));
  let sha512_hex = null;
  if (wantSha512) {
    sha512_hex = bufToHex(await subtle.digest("SHA-512", view));
  }
  return { sha256_hex, sha512_hex, size: bytes.byteLength };
}

/**
 * Verify a receipt against the file it claims to attest.
 *
 * @param {Uint8Array | ArrayBuffer | Buffer | Blob | File} fileBytes
 * @param {Object} receipt — receipt JSON object; must carry `hash_hex`,
 *                          optionally `sha512_hex`.
 * @returns {Promise<{
 *   ok: boolean,
 *   sha256_match: boolean,
 *   sha512_match: boolean | null,
 *   receipt_id: string,
 *   notes: string[]
 * }>}
 */
export async function verifyReceiptAgainstFile(fileBytes, receipt) {
  const notes = [];
  if (!receipt || typeof receipt !== "object") {
    return {
      ok: false, sha256_match: false, sha512_match: null,
      receipt_id: "", notes: ["The supplied receipt is not an object."]
    };
  }

  // Canonical fields only, stored value compared as-is: the engine
  // (server/engine.py verify_hash_against_receipt) lowercases the SUPPLIED
  // side and takes the stored hash verbatim, so a receipt whose stored hash
  // was tampered to uppercase must NOT verify here either (VERIFIER_SPEC.md).
  const receiptSha256 = String(receipt.hash_hex || "");
  const receiptSha512 = String(receipt.sha512_hex || "");
  const receiptId = String(receipt.receipt_id || receipt.id || receipt.receiptId || "");

  if (!receiptSha256) {
    return {
      ok: false, sha256_match: false, sha512_match: null,
      receipt_id: receiptId,
      notes: ["The receipt carries no hash_hex field; the file-to-receipt binding cannot be checked."]
    };
  }

  let hashes;
  try {
    hashes = await hashFile(fileBytes, { sha512: !!receiptSha512 });
  } catch (err) {
    return {
      ok: false, sha256_match: false, sha512_match: null,
      receipt_id: receiptId,
      notes: ["Hashing failed: " + (err && err.message ? err.message : String(err))]
    };
  }

  const sha256_match = hashes.sha256_hex.toLowerCase() === receiptSha256;
  let sha512_match = null;
  if (receiptSha512) {
    sha512_match = (hashes.sha512_hex || "").toLowerCase() === receiptSha512;
  }

  if (sha256_match) {
    notes.push("SHA-256 of the file matches the receipt.");
  } else if (!/^[0-9a-f]{64}$/.test(receiptSha256)) {
    // AUDIT D6. A receipt whose stored hash is not 64 lowercase hex characters
    // is MALFORMED — the engine calls that "corrupt receipt". Reporting it as
    // "the file is not the one attested" blames the file, which may be
    // perfectly intact, and sends the reader looking in the wrong place. The
    // verdict is unchanged (still not a match); only the diagnosis is honest.
    notes.push(
      "The receipt's hash_hex is not a valid SHA-256 digest (64 lowercase hex " +
      "characters), so the file cannot be checked against it. The receipt is " +
      "malformed; this says nothing about the file."
    );
  } else {
    notes.push("SHA-256 of the file does NOT match the receipt; the file is not the one attested.");
  }
  if (receiptSha512) {
    notes.push(sha512_match
      ? "SHA-512 sibling matches the receipt."
      : "SHA-512 sibling does NOT match the receipt.");
  } else {
    notes.push("The receipt does not carry a SHA-512 sibling; the single-hash check is sufficient for binding.");
  }
  notes.push("This verifier checks the file-to-receipt binding only. For Bitcoin-chain verification, run the receipt's .ots files through an OpenTimestamps client or the Python verifier at https://github.com/Orphograph/Orphograph.");

  const ok = sha256_match && (receiptSha512 ? !!sha512_match : true);

  return {
    ok,
    sha256_match,
    sha512_match,
    receipt_id: receiptId,
    notes
  };
}

export default { verifyReceiptAgainstFile, hashFile };
