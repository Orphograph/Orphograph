/**
 * published_vectors.test.ts
 *
 * Holds the TypeScript implementation to the SAME published conformance
 * vectors the Python side is held to (2026-08-25).
 *
 * docs/test-vectors/ is published as the conformance contract, and
 * orphograph.com/docs/sdk tells developers the implementations are
 * "bit-for-bit compatible with each other and with the browser". Until now
 * only tests/test_published_vectors.py consumed those vectors — nothing in
 * sdk-node read them. A shared vector suite that only one implementation is
 * tested against does not prove conformance; it proves the Python engine
 * agrees with itself.
 *
 * The roots do currently agree (verified 2026-08-25: the same three-file
 * folder yields ff928cc4…35dc from both implementations). This test is what
 * makes a FUTURE divergence fail the build instead of quietly falsifying a
 * published claim.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

import { MerkleTree, type ProofStep } from "../src/merkle.ts";

const HERE = dirname(fileURLToPath(import.meta.url));
const VECTORS = join(HERE, "..", "..", "docs", "test-vectors", "folder.json");

// The suite splits inclusion vectors across two kinds — positives under
// "merkle_inclusion", the tampered-content and renamed-path negatives under
// "merkle_inclusion_negative". Filtering on the first alone silently drops
// every negative, which is how a verifier that always returns true would
// sail through this file.
const INCLUSION_KINDS = new Set(["merkle_inclusion", "merkle_inclusion_negative"]);

type Vector = {
  id: string;
  kind: string;
  rel_path: string;
  file_sha256_hex: string;
  proof: ProofStep[];
  root_hex: string;
  expect: { included: boolean };
};

function hexToBytes(hex: string): Uint8Array {
  const out = new Uint8Array(hex.length / 2);
  for (let i = 0; i < out.length; i++) out[i] = parseInt(hex.slice(i * 2, i * 2 + 2), 16);
  return out;
}

function loadSuite(): { format: string; vectors: Vector[] } {
  return JSON.parse(readFileSync(VECTORS, "utf8"));
}

test("published folder vectors load and are the expected suite", () => {
  const suite = loadSuite();
  assert.equal(suite.format, "orphograph-published-vectors-v1");
  // NEGATIVE CONTROL. If the path resolved to something empty, every
  // assertion below would vacuously pass and this file would report
  // conformance for a suite it never read.
  assert.ok(suite.vectors.length > 0, "no vectors loaded — path or suite is wrong");
  const inclusion = suite.vectors.filter((v) => INCLUSION_KINDS.has(v.kind));
  assert.ok(inclusion.length >= 4, `expected >=4 inclusion vectors, got ${inclusion.length}`);
  // Both polarities must be PRESENT in the suite, or the loop below cannot
  // exercise them however carefully it is written.
  assert.ok(inclusion.some((v) => v.expect.included), "suite has no positive vector");
  assert.ok(inclusion.some((v) => !v.expect.included), "suite has no negative vector");
});

test("the TS implementation reproduces every published inclusion verdict", () => {
  const suite = loadSuite();
  const seen: string[] = [];
  for (const v of suite.vectors) {
    if (!INCLUSION_KINDS.has(v.kind)) continue;
    seen.push(v.id);
    const got = MerkleTree.verifyInclusion(
      hexToBytes(v.file_sha256_hex),
      v.rel_path,
      v.proof,
      v.root_hex,
    );
    assert.equal(
      got,
      v.expect.included,
      `vector ${v.id}: TS returned ${got}, published contract says ${v.expect.included}`,
    );
  }
  // Both polarities must be exercised — a verifier that always returns true
  // would pass a suite of only-positive vectors.
  const suiteById = new Map(suite.vectors.map((v) => [v.id, v]));
  const positives = seen.filter((id) => suiteById.get(id)!.expect.included);
  const negatives = seen.filter((id) => !suiteById.get(id)!.expect.included);
  assert.ok(positives.length > 0, "no positive vectors exercised");
  assert.ok(negatives.length > 0, "no negative vectors exercised — a bare `return true` would pass");
});
