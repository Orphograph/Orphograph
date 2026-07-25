/*!
 * verify_js_page.test.mjs — conformance tests for the /verify-js page's
 * verifier script (web/verify-js.js), executed exactly as shipped (via the
 * DOM-stub harness).
 *
 * Canon (server/engine.py verify_hash_against_receipt, VERIFIER_SPEC §3.2):
 * the stored receipt hash is compared VERBATIM; only the locally computed
 * side is lowercase. A receipt whose stored hash was tampered to uppercase
 * or mixed case is a byte-for-byte-different receipt and must NOT verify.
 *
 * Run: node --test tests/js/verify_js_page.test.mjs
 */
import test from "node:test";
import assert from "node:assert/strict";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { runPageVerify, sha256Hex, sha512Hex } from "./verify_js_page_harness.mjs";

const PAGE = join(dirname(fileURLToPath(import.meta.url)), "..", "..", "web", "verify-js.js");

const fileBytes = new TextEncoder().encode("orphograph evidence file v1\n");
const otherBytes = new TextEncoder().encode("a different byte sequence\n");
const lower256 = await sha256Hex(fileBytes);
const lower512 = await sha512Hex(fileBytes);

function receipt(fields) {
  return JSON.stringify(Object.assign({ receipt_id: "XwTULwlh76PcCst9" }, fields));
}

test("canonical lowercase receipt verifies", async () => {
  const out = await runPageVerify(PAGE, fileBytes, receipt({ hash_hex: lower256 }));
  assert.equal(out.verdictClass, "v-result valid");
});

test("uppercase-tampered hash_hex must NOT verify (AUDIT D1 regression)", async () => {
  const out = await runPageVerify(
    PAGE, fileBytes, receipt({ hash_hex: lower256.toUpperCase() })
  );
  assert.equal(out.verdictClass, "v-result mismatch");
  assert.match(out.verdict, /Mismatch/);
});

test("mixed-case-tampered hash_hex must NOT verify", async () => {
  const mixed = lower256.slice(0, 32).toUpperCase() + lower256.slice(32);
  const out = await runPageVerify(PAGE, fileBytes, receipt({ hash_hex: mixed }));
  assert.equal(out.verdictClass, "v-result mismatch");
});

test("uppercase-tampered sha512_hex must NOT verify", async () => {
  const out = await runPageVerify(
    PAGE, fileBytes,
    receipt({ hash_hex: lower256, sha512_hex: lower512.toUpperCase() })
  );
  assert.equal(out.verdictClass, "v-result mismatch");
});

test("canonical receipt with sha512 sibling verifies", async () => {
  const out = await runPageVerify(
    PAGE, fileBytes, receipt({ hash_hex: lower256, sha512_hex: lower512 })
  );
  assert.equal(out.verdictClass, "v-result valid");
});

test("alias-only receipt (sha256, no hash_hex) must NOT verify (AUDIT D5)", async () => {
  const out = await runPageVerify(PAGE, fileBytes, receipt({ sha256: lower256 }));
  assert.notEqual(out.verdictClass, "v-result valid");
  assert.match(out.verdict, /Receipt incomplete/);
});

test("wrong file is a mismatch", async () => {
  const out = await runPageVerify(PAGE, otherBytes, receipt({ hash_hex: lower256 }));
  assert.equal(out.verdictClass, "v-result mismatch");
  assert.match(out.verdict, /does not correspond/);
});
