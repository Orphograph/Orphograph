/*!
 * verifier_module.test.mjs — conformance tests for the standalone
 * verifier-js/orphograph_verify.js module (the copy-paste independent
 * verifier). Pins the AUDIT D1/D5 fixes: stored hash compared verbatim,
 * canonical fields only.
 *
 * Run: node --test tests/js/verifier_module.test.mjs
 */
import test from "node:test";
import assert from "node:assert/strict";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const MODULE = join(
  dirname(fileURLToPath(import.meta.url)), "..", "..", "verifier-js", "orphograph_verify.js"
);
const { verifyReceiptAgainstFile, hashFile } = await import(MODULE);

const fileBytes = new TextEncoder().encode("orphograph evidence file v1\n");
const { sha256_hex: lower256, sha512_hex: lower512 } = await hashFile(fileBytes, { sha512: true });

test("canonical lowercase receipt verifies", async () => {
  const out = await verifyReceiptAgainstFile(fileBytes, { receipt_id: "r", hash_hex: lower256 });
  assert.equal(out.ok, true);
  assert.equal(out.sha256_match, true);
});

test("uppercase-tampered stored hash must NOT verify (AUDIT D1 regression)", async () => {
  const out = await verifyReceiptAgainstFile(fileBytes, {
    receipt_id: "r", hash_hex: lower256.toUpperCase(),
  });
  assert.equal(out.ok, false);
  assert.equal(out.sha256_match, false);
});

test("mixed-case-tampered stored hash must NOT verify", async () => {
  const mixed = lower256.slice(0, 32).toUpperCase() + lower256.slice(32);
  const out = await verifyReceiptAgainstFile(fileBytes, { receipt_id: "r", hash_hex: mixed });
  assert.equal(out.ok, false);
});

test("uppercase-tampered sha512 sibling must NOT verify", async () => {
  const out = await verifyReceiptAgainstFile(fileBytes, {
    receipt_id: "r", hash_hex: lower256, sha512_hex: lower512.toUpperCase(),
  });
  assert.equal(out.ok, false);
  assert.equal(out.sha512_match, false);
});

test("alias-only receipt (sha256, no hash_hex) must NOT verify (AUDIT D5)", async () => {
  const out = await verifyReceiptAgainstFile(fileBytes, { receipt_id: "r", sha256: lower256 });
  assert.equal(out.ok, false);
});
