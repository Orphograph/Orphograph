/*!
 * orphograph_signature.js — independent JavaScript verifier for the OPTIONAL
 * Ed25519 author signature on an Orphograph folder manifest.
 *
 * Scope:
 *   This module verifies the AUTHORSHIP claim a folder anchor MAY carry. The
 *   signature is an Ed25519 (EdDSA, curve Ed25519) signature over the
 *   canonical-JSON serialisation of the manifest with the `signature` field
 *   removed. The signer's public key is recovered from a did:key identifier
 *   (W3C did-method-key, base58btc multibase, multicodec 0xed01 prefix).
 *
 *   Bitcoin proves the manifest's root existed by time T. The signature
 *   proves a specific key claimed authorship of that manifest. The two
 *   checks together are stronger than either alone.
 *
 * Algorithm:
 *   - canonical_bytes := JSON.stringify(manifest minus signature/receipt_id/kind)
 *     with deterministic key order, no whitespace, ASCII-only.
 *   - public_key := base58btc-decode( kid after "did:key:z" ), strip 0xed01
 *     multicodec prefix; the remaining 32 bytes are the raw Ed25519 key.
 *   - WebCrypto verifies via `crypto.subtle.verify({name:"Ed25519"}, ...)`.
 *
 * Dependencies:
 *   None. Uses only WebCrypto. Ed25519 is now W3C-standardized in WebCrypto
 *   (Secure Curves spec) and is shipping in modern Chrome/Edge/Safari and in
 *   Node 20+ . If the host lacks Ed25519 support, this module fails with a
 *   clear error rather than silently returning false.
 *
 * Module format:
 *   ES module, zero dependencies. Sibling to orphograph_verify.js — neither
 *   module is modified by the presence of the other.
 *
 * License: MIT.
 */

"use strict";

function getSubtle() {
  if (typeof globalThis !== "undefined" && globalThis.crypto && globalThis.crypto.subtle) {
    return globalThis.crypto.subtle;
  }
  throw new Error("WebCrypto SubtleCrypto is not available in this environment");
}

// --------------------------------------------------------------------- base58

const B58_ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz";

function base58Decode(str) {
  if (typeof str !== "string") {
    throw new Error("base58 input must be a string");
  }
  // Count leading '1's; each represents a leading 0x00 byte.
  let leadingZeros = 0;
  while (leadingZeros < str.length && str[leadingZeros] === "1") {
    leadingZeros++;
  }
  // Convert from base 58 into a base-256 buffer using long arithmetic on bytes.
  const bytes = [];
  for (let i = 0; i < str.length; i++) {
    const ch = str[i];
    const value = B58_ALPHABET.indexOf(ch);
    if (value < 0) {
      throw new Error("invalid base58 character: " + ch);
    }
    let carry = value;
    for (let j = 0; j < bytes.length; j++) {
      carry += bytes[j] * 58;
      bytes[j] = carry & 0xff;
      carry >>= 8;
    }
    while (carry > 0) {
      bytes.push(carry & 0xff);
      carry >>= 8;
    }
  }
  bytes.reverse();
  const out = new Uint8Array(leadingZeros + bytes.length);
  for (let i = 0; i < bytes.length; i++) {
    out[leadingZeros + i] = bytes[i];
  }
  return out;
}

// ------------------------------------------------------------------ url-b64

function base64UrlDecode(s) {
  if (typeof s !== "string") {
    throw new Error("base64url input must be a string");
  }
  const padded = s.replace(/-/g, "+").replace(/_/g, "/")
    + "===".slice((s.length + 3) % 4);
  if (typeof atob === "function") {
    const bin = atob(padded);
    const out = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
    return out;
  }
  // Node fallback.
  // eslint-disable-next-line no-undef
  return Uint8Array.from(Buffer.from(padded, "base64"));
}

// --------------------------------------------------------------- did:key

const DIDKEY_PREFIX = "did:key:z";
const ED25519_MULTICODEC = [0xed, 0x01];

export function publicKeyFromDidKey(kid) {
  if (typeof kid !== "string" || !kid.startsWith(DIDKEY_PREFIX)) {
    throw new Error("kid must be a did:key:z... identifier");
  }
  const body = base58Decode(kid.slice(DIDKEY_PREFIX.length));
  if (body.length < 3
    || body[0] !== ED25519_MULTICODEC[0]
    || body[1] !== ED25519_MULTICODEC[1]) {
    throw new Error("did:key is not an Ed25519 key (wrong multicodec prefix)");
  }
  const pub = body.slice(2);
  if (pub.length !== 32) {
    throw new Error("decoded Ed25519 key is not 32 bytes");
  }
  return pub;
}

// ------------------------------------------------------------- canonical JSON

const POST_ANCHOR_FIELDS = new Set(["signature", "receipt_id", "kind"]);

// JSON.stringify with sorted keys, compact separators, ASCII-safe. Matches
// Python's json.dumps(..., sort_keys=True, separators=(",",":"), ensure_ascii=True).
function canonicalStringify(value) {
  if (value === null) return "null";
  if (typeof value === "boolean") return value ? "true" : "false";
  if (typeof value === "number") {
    if (!Number.isFinite(value)) {
      throw new Error("non-finite number cannot be canonicalised");
    }
    return JSON.stringify(value);
  }
  if (typeof value === "string") {
    return jsonEscapeAscii(value);
  }
  if (Array.isArray(value)) {
    return "[" + value.map(canonicalStringify).join(",") + "]";
  }
  if (typeof value === "object") {
    const keys = Object.keys(value).sort();
    const parts = [];
    for (const k of keys) {
      parts.push(jsonEscapeAscii(k) + ":" + canonicalStringify(value[k]));
    }
    return "{" + parts.join(",") + "}";
  }
  throw new Error("unsupported JSON value: " + typeof value);
}

function jsonEscapeAscii(s) {
  // Use JSON.stringify for correct escaping of control chars and quotes,
  // then escape any remaining non-ASCII codepoints as \uXXXX to match
  // ensure_ascii=True semantics.
  const escaped = JSON.stringify(s);
  let out = "";
  for (let i = 0; i < escaped.length; i++) {
    const code = escaped.charCodeAt(i);
    if (code > 0x7e || code < 0x20) {
      // The only chars in this range left in JSON.stringify output should be
      // non-ASCII; escape them. Control chars are already \uXXXX-escaped.
      if (code <= 0xffff) {
        out += "\\u" + code.toString(16).padStart(4, "0");
      } else {
        out += escaped[i];
      }
    } else {
      out += escaped[i];
    }
  }
  return out;
}

export function canonicalManifestBytes(manifest) {
  if (manifest === null || typeof manifest !== "object" || Array.isArray(manifest)) {
    throw new Error("manifest must be an object");
  }
  const cleaned = {};
  for (const k of Object.keys(manifest)) {
    if (!POST_ANCHOR_FIELDS.has(k)) cleaned[k] = manifest[k];
  }
  const str = canonicalStringify(cleaned);
  const enc = new TextEncoder();
  return enc.encode(str);
}

// --------------------------------------------------------------- verify

/**
 * Verify the optional Ed25519 signature block on a folder manifest.
 *
 * @param {object} manifest — the folder manifest as parsed JSON.
 * @returns {Promise<{ok: boolean, reason: string, kid: string|null}>}
 *
 * Returns ok=false with reason="no signature present" if the manifest carries
 * no signature block — callers should branch on field presence first.
 *
 * Fails with a clear error message rather than silently returning false if
 * the host's WebCrypto lacks Ed25519 support (older browsers / older Node).
 */
export async function verifyManifestSignature(manifest) {
  if (manifest === null || typeof manifest !== "object" || Array.isArray(manifest)) {
    return { ok: false, reason: "manifest must be an object", kid: null };
  }
  const block = manifest.signature;
  if (block === undefined || block === null) {
    return { ok: false, reason: "no signature present", kid: null };
  }
  if (typeof block !== "object") {
    return { ok: false, reason: "signature must be an object", kid: null };
  }
  if (block.alg !== "EdDSA" || block.curve !== "Ed25519") {
    return {
      ok: false,
      reason: "unsupported algorithm: alg=" + block.alg + " curve=" + block.curve,
      kid: null,
    };
  }
  if (typeof block.kid !== "string" || typeof block.signature_b64 !== "string") {
    return { ok: false, reason: "kid/signature_b64 must be strings", kid: null };
  }

  let pubKeyBytes;
  try {
    pubKeyBytes = publicKeyFromDidKey(block.kid);
  } catch (e) {
    return { ok: false, reason: "invalid kid: " + e.message, kid: block.kid };
  }

  let sigBytes;
  try {
    sigBytes = base64UrlDecode(block.signature_b64);
  } catch (e) {
    return { ok: false, reason: "signature_b64 not valid base64url", kid: block.kid };
  }
  if (sigBytes.length !== 64) {
    return { ok: false, reason: "signature must decode to 64 bytes", kid: block.kid };
  }

  const subtle = getSubtle();
  let key;
  try {
    key = await subtle.importKey(
      "raw",
      pubKeyBytes,
      { name: "Ed25519" },
      false,
      ["verify"],
    );
  } catch (e) {
    // Older browsers / older Node lacking Ed25519 in WebCrypto fail here.
    return {
      ok: false,
      reason: "browser missing Ed25519 support in WebCrypto: " + (e && e.message ? e.message : e),
      kid: block.kid,
    };
  }

  const message = canonicalManifestBytes(manifest);
  let ok = false;
  try {
    ok = await subtle.verify({ name: "Ed25519" }, key, sigBytes, message);
  } catch (e) {
    return {
      ok: false,
      reason: "WebCrypto verify threw: " + (e && e.message ? e.message : e),
      kid: block.kid,
    };
  }
  if (!ok) {
    return {
      ok: false,
      reason: "signature does not verify against canonical manifest bytes",
      kid: block.kid,
    };
  }
  return {
    ok: true,
    reason: "EdDSA/Ed25519 signature verified for " + block.kid,
    kid: block.kid,
  };
}

export default { verifyManifestSignature, canonicalManifestBytes, publicKeyFromDidKey };
