// merkle.test.ts — cross-check the TypeScript Merkle implementation against
// the reference server/merkle.py. The same fixture folder is built twice:
// once for Python (via a one-shot `python3 -c` invocation) and once for
// the TypeScript module. Both must produce the same root_hex.
//
// The test also includes hard-coded vectors for the unit-level helpers
// (leaf hash, internal hash) so the suite remains useful even if python3
// is not present on the host. When python3 is unavailable, the cross-check
// subtests are skipped with a clear t.skip() reason rather than failing.

import { test } from "node:test";
import assert from "node:assert/strict";
import {
  mkdtempSync,
  mkdirSync,
  writeFileSync,
  rmSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { spawnSync } from "node:child_process";
import { createHash } from "node:crypto";

import {
  MerkleTree,
  ALGORITHM,
  VERSION,
  leafHash,
  internalHash,
  bytesToHex,
  hexToBytes,
} from "../src/merkle.ts";

// ─── unit-level hash vectors ────────────────────────────────────────────

test("leaf hash matches the documented construction", () => {
  // leaf = SHA-256(0x00 || "a.txt" || 0x00 || SHA-256("hello\n"))
  const fileDigest = new Uint8Array(
    createHash("sha256").update("hello\n").digest(),
  );
  const got = leafHash("a.txt", fileDigest);
  // Independently constructed reference using node:crypto:
  const h = createHash("sha256");
  h.update(Buffer.from([0x00]));
  h.update(Buffer.from("a.txt", "utf-8"));
  h.update(Buffer.from([0x00]));
  h.update(Buffer.from(fileDigest));
  const expected = new Uint8Array(h.digest());
  assert.equal(bytesToHex(got), bytesToHex(expected));
});

test("internal hash matches the documented construction", () => {
  const left = new Uint8Array(32);
  const right = new Uint8Array(32);
  for (let i = 0; i < 32; i++) {
    left[i] = i;
    right[i] = 31 - i;
  }
  const got = internalHash(left, right);
  const h = createHash("sha256");
  h.update(Buffer.from([0x01]));
  h.update(Buffer.from(left));
  h.update(Buffer.from(right));
  assert.equal(bytesToHex(got), bytesToHex(new Uint8Array(h.digest())));
});

test("hex helpers round-trip", () => {
  const bytes = new Uint8Array([0xde, 0xad, 0xbe, 0xef, 0x00, 0xff]);
  assert.equal(bytesToHex(bytes), "deadbeef00ff");
  assert.deepEqual(hexToBytes("deadbeef00ff"), bytes);
});

// ─── tree-shape vectors (single file, three files, odd count) ────────────

interface Fixture {
  dir: string;
  cleanup: () => void;
}

function makeFixture(files: { path: string; content: Buffer | string }[]): Fixture {
  const dir = mkdtempSync(join(tmpdir(), "orpho-sdk-node-test-"));
  for (const { path, content } of files) {
    const abs = join(dir, path);
    const lastSlash = abs.lastIndexOf("/");
    if (lastSlash !== -1) {
      mkdirSync(abs.slice(0, lastSlash), { recursive: true });
    }
    writeFileSync(
      abs,
      typeof content === "string" ? Buffer.from(content) : content,
    );
  }
  return { dir, cleanup: () => rmSync(dir, { recursive: true, force: true }) };
}

test("single-file folder root equals its leaf hash", async () => {
  const fix = makeFixture([{ path: "only.txt", content: "single\n" }]);
  try {
    const tree = await MerkleTree.fromFolder(fix.dir);
    const manifest = tree.manifest();
    assert.equal(manifest.algorithm, ALGORITHM);
    assert.equal(manifest.version, VERSION);
    assert.equal(manifest.leaves.length, 1);
    assert.equal(manifest.root_hex, manifest.leaves[0].leaf_hex);
    assert.equal(manifest.leaves[0].path, "only.txt");
    assert.equal(manifest.leaves[0].size_bytes, "single\n".length);
  } finally {
    fix.cleanup();
  }
});

test("manifest round-trips through MerkleTree.fromManifest", async () => {
  const fix = makeFixture([
    { path: "a.txt", content: "hello\n" },
    { path: "sub/b.txt", content: "world\n" },
    { path: "sub/c.bin", content: Buffer.from(Array.from({ length: 256 }, (_, i) => i)) },
  ]);
  try {
    const tree = await MerkleTree.fromFolder(fix.dir);
    const manifest = tree.manifest();
    const rebuilt = MerkleTree.fromManifest(manifest);
    assert.equal(rebuilt.rootHex(), tree.rootHex());
    assert.equal(rebuilt.leafCount(), 3);
  } finally {
    fix.cleanup();
  }
});

test("inclusion proof verifies, tampered proof fails", async () => {
  const fix = makeFixture([
    { path: "a.txt", content: "hello\n" },
    { path: "sub/b.txt", content: "world\n" },
    { path: "sub/c.bin", content: Buffer.from(Array.from({ length: 256 }, (_, i) => i)) },
  ]);
  try {
    const tree = await MerkleTree.fromFolder(fix.dir);
    const proof = tree.inclusionProof("sub/b.txt");
    const fileHash = new Uint8Array(
      createHash("sha256").update("world\n").digest(),
    );
    const ok = MerkleTree.verifyInclusion(
      fileHash,
      "sub/b.txt",
      proof,
      tree.rootHex(),
    );
    assert.equal(ok, true);

    // Tampered file hash → verification must fail.
    const badHash = new Uint8Array(32);
    const okBad = MerkleTree.verifyInclusion(
      badHash,
      "sub/b.txt",
      proof,
      tree.rootHex(),
    );
    assert.equal(okBad, false);

    // Wrong rel_path → must fail.
    const okWrongPath = MerkleTree.verifyInclusion(
      fileHash,
      "a.txt",
      proof,
      tree.rootHex(),
    );
    assert.equal(okWrongPath, false);
  } finally {
    fix.cleanup();
  }
});

test("odd-count tree (5 files) promotes lone node — last-file proof has fewer steps", async () => {
  const files = [];
  for (let i = 0; i < 5; i++) {
    files.push({ path: `f${i}.txt`, content: `file-${i}\n` });
  }
  const fix = makeFixture(files);
  try {
    const tree = await MerkleTree.fromFolder(fix.dir);
    assert.equal(tree.leafCount(), 5);
    // For RFC 6962 promotion on 5 leaves, the last leaf (index 4)
    // is promoted at the first level — its proof should have fewer
    // sibling steps than a fully-paired leaf.
    const proofLast = tree.inclusionProof("f4.txt");
    const proofFirst = tree.inclusionProof("f0.txt");
    assert.ok(
      proofLast.length < proofFirst.length,
      `expected promoted-leaf proof shorter than fully-paired-leaf proof, ` +
        `got ${proofLast.length} vs ${proofFirst.length}`,
    );
    // Both must still verify.
    const f4Hash = new Uint8Array(createHash("sha256").update("file-4\n").digest());
    const f0Hash = new Uint8Array(createHash("sha256").update("file-0\n").digest());
    assert.equal(
      MerkleTree.verifyInclusion(f4Hash, "f4.txt", proofLast, tree.rootHex()),
      true,
    );
    assert.equal(
      MerkleTree.verifyInclusion(f0Hash, "f0.txt", proofFirst, tree.rootHex()),
      true,
    );
  } finally {
    fix.cleanup();
  }
});

test("default exclude list filters .DS_Store and node_modules", async () => {
  const fix = makeFixture([
    { path: "kept.txt", content: "kept\n" },
    { path: ".DS_Store", content: "junk\n" },
    { path: "node_modules/x.js", content: "junk\n" },
  ]);
  try {
    const tree = await MerkleTree.fromFolder(fix.dir);
    assert.equal(tree.leafCount(), 1);
    assert.equal(tree.manifest().leaves[0].path, "kept.txt");
  } finally {
    fix.cleanup();
  }
});

test("explicit empty exclude list keeps every file", async () => {
  const fix = makeFixture([
    { path: "kept.txt", content: "kept\n" },
    { path: ".DS_Store", content: "junk\n" },
  ]);
  try {
    const tree = await MerkleTree.fromFolder(fix.dir, { exclude: [] });
    assert.equal(tree.leafCount(), 2);
  } finally {
    fix.cleanup();
  }
});

test("empty folder is rejected", async () => {
  const fix = makeFixture([]);
  try {
    await assert.rejects(() => MerkleTree.fromFolder(fix.dir), /Empty folders/);
  } finally {
    fix.cleanup();
  }
});

// ─── cross-check against the Python reference implementation ─────────────
//
// If python3 is not on PATH, this test self-skips rather than failing —
// the suite is still useful in CI environments without Python.

function pythonAvailable(): boolean {
  const r = spawnSync("python3", ["--version"], { encoding: "utf-8" });
  return r.status === 0;
}

const PY_SCRIPT = `
import sys, json
sys.path.insert(0, sys.argv[1])
import merkle
tree = merkle.MerkleTree.from_folder(sys.argv[2])
print(json.dumps({
    "root_hex": tree.root_hex(),
    "leaves": [m["leaf_hex"] for m in tree.manifest()["leaves"]],
    "proof_sub_b": tree.inclusion_proof("sub/b.txt") if any(
        l["path"] == "sub/b.txt" for l in tree.manifest()["leaves"]
    ) else None,
}))
`;

test("TypeScript root matches Python reference (3-file fixture)", async (t) => {
  if (!pythonAvailable()) {
    t.skip("python3 not available");
    return;
  }
  const serverDir = "/Users/founder/orphograph/server";
  const fix = makeFixture([
    { path: "a.txt", content: "hello\n" },
    { path: "sub/b.txt", content: "world\n" },
    { path: "sub/c.bin", content: Buffer.from(Array.from({ length: 256 }, (_, i) => i)) },
  ]);
  try {
    const tsTree = await MerkleTree.fromFolder(fix.dir);
    const py = spawnSync(
      "python3",
      ["-c", PY_SCRIPT, serverDir, fix.dir],
      { encoding: "utf-8" },
    );
    if (py.status !== 0) {
      t.skip(`python3 reference invocation failed: ${py.stderr}`);
      return;
    }
    const pyOut = JSON.parse(py.stdout) as {
      root_hex: string;
      leaves: string[];
      proof_sub_b: ["L" | "R", string][] | null;
    };
    assert.equal(tsTree.rootHex(), pyOut.root_hex);
    const tsLeaves = tsTree.manifest().leaves.map((l) => l.leaf_hex);
    assert.deepEqual(tsLeaves, pyOut.leaves);
    assert.ok(pyOut.proof_sub_b, "python proof must be present");
    const tsProof = tsTree.inclusionProof("sub/b.txt");
    assert.deepEqual(tsProof, pyOut.proof_sub_b);
  } finally {
    fix.cleanup();
  }
});

test("TypeScript root matches Python reference (5-file odd fixture)", async (t) => {
  if (!pythonAvailable()) {
    t.skip("python3 not available");
    return;
  }
  const serverDir = "/Users/founder/orphograph/server";
  const files = [];
  for (let i = 0; i < 5; i++) {
    files.push({ path: `f${i}.txt`, content: `file-${i}\n` });
  }
  const fix = makeFixture(files);
  try {
    const tsTree = await MerkleTree.fromFolder(fix.dir);
    const py = spawnSync(
      "python3",
      ["-c", PY_SCRIPT, serverDir, fix.dir],
      { encoding: "utf-8" },
    );
    if (py.status !== 0) {
      t.skip(`python3 reference invocation failed: ${py.stderr}`);
      return;
    }
    const pyOut = JSON.parse(py.stdout) as { root_hex: string };
    assert.equal(tsTree.rootHex(), pyOut.root_hex);
  } finally {
    fix.cleanup();
  }
});
