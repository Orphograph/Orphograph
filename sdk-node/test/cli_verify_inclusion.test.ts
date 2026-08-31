/**
 * cli_verify_inclusion.test.ts — the verify-inclusion CONTRACT through the
 * real CLI entry point (2026-08-31).
 *
 * Pinned against the Python CLI's shape (sdk-python/orphograph/_cli.py):
 * exit 0 = match, 1 = mismatch, 2 = error (a crash must NEVER wear the
 * mismatch code); root_hex optional with root_source echoed
 * ("argument" | "proof_json"); one stderr warning line when rel_path
 * disagrees with the path recorded in proof.json. Pre-fix, the Node CLI
 * required root_hex, echoed only {ok}, warned never, and returned 1 for
 * any exception — a missing file read as "not included".
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, writeFileSync, mkdirSync, readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { spawnSync } from "node:child_process";

import { MerkleTree } from "../src/merkle.ts";

// The BUILT entry point — what `npx orphograph` actually runs. The test
// script builds before testing, and CI runs plain `npm test`.
const CLI = resolve(import.meta.dirname, "..", "dist", "cli.js");

function runCli(args: string[]) {
  const r = spawnSync(
    process.execPath,
    [CLI, ...args],
    { encoding: "utf-8" },
  );
  return { code: r.status, out: r.stdout, err: r.stderr };
}

async function fixture() {
  const dir = mkdtempSync(join(tmpdir(), "orpho-cli-vi-"));
  mkdirSync(join(dir, "sub"));
  writeFileSync(join(dir, "a.txt"), "alpha");
  writeFileSync(join(dir, "sub", "c.txt"), "gamma");
  const tree = await MerkleTree.fromFolder(dir);
  const manifest = tree.manifest();
  const proofPath = join(dir, "proof.json");
  writeFileSync(
    proofPath,
    JSON.stringify({
      receipt_id: "rid-test",
      root_hex: manifest.root_hex,
      path: "sub/c.txt",
      proof: tree.inclusionProof("sub/c.txt"),
    }),
  );
  return { dir, proofPath, root: manifest.root_hex };
}

test("match with omitted root uses proof.json and says so", async () => {
  const { dir, proofPath } = await fixture();
  const r = runCli(["verify-inclusion", join(dir, "sub", "c.txt"), "sub/c.txt", proofPath]);
  assert.equal(r.code, 0, r.err);
  const verdict = JSON.parse(r.out);
  assert.equal(verdict.ok, true);
  assert.equal(verdict.root_source, "proof_json");
  assert.match(verdict.root_hex, /^[0-9a-f]{64}$/);
  assert.equal(r.err, "");
});

test("explicit root overrides and is echoed as argument", async () => {
  const { dir, proofPath, root } = await fixture();
  const r = runCli(["verify-inclusion", join(dir, "sub", "c.txt"), "sub/c.txt", proofPath, root]);
  assert.equal(r.code, 0, r.err);
  const verdict = JSON.parse(r.out);
  assert.equal(verdict.ok, true);
  assert.equal(verdict.root_source, "argument");
  assert.equal(verdict.root_hex, root);
});

test("tampered file is a mismatch verdict, exit 1", async () => {
  const { dir, proofPath } = await fixture();
  writeFileSync(join(dir, "sub", "c.txt"), "gamm2");
  const r = runCli(["verify-inclusion", join(dir, "sub", "c.txt"), "sub/c.txt", proofPath]);
  assert.equal(r.code, 1);
  assert.equal(JSON.parse(r.out).ok, false);
});

test("rel_path disagreeing with proof.json warns on stderr, verdict unchanged", async () => {
  const { dir, proofPath } = await fixture();
  const r = runCli(["verify-inclusion", join(dir, "sub", "c.txt"), "other/c.txt", proofPath]);
  assert.equal(r.code, 1);
  assert.equal(JSON.parse(r.out).ok, false);
  const warning = JSON.parse(r.err);
  assert.match(warning.warning, /rel_path/);
  assert.equal(warning.proof_json_path, "sub/c.txt");
  assert.equal(warning.rel_path, "other/c.txt");
});

test("missing local file is an ERROR (2), never the mismatch code", async () => {
  const { dir, proofPath } = await fixture();
  const r = runCli(["verify-inclusion", join(dir, "nope.txt"), "nope.txt", proofPath]);
  assert.equal(r.code, 2, `stdout=${r.out} stderr=${r.err}`);
  assert.equal(r.out, "");
  assert.match(JSON.parse(r.err).error, /./);
});

test("unparseable proof.json is an ERROR (2)", async () => {
  const { dir } = await fixture();
  const bad = join(dir, "bad.json");
  writeFileSync(bad, "{not json");
  const r = runCli(["verify-inclusion", join(dir, "a.txt"), "a.txt", bad]);
  assert.equal(r.code, 2, `stdout=${r.out} stderr=${r.err}`);
});

test("bare-array proof with no root argument is an ERROR (2) naming the gap", async () => {
  const { dir, proofPath } = await fixture();
  const bare = join(dir, "bare.json");
  writeFileSync(bare, JSON.stringify(JSON.parse(readFileSync(proofPath, "utf-8")).proof));
  const r = runCli(["verify-inclusion", join(dir, "sub", "c.txt"), "sub/c.txt", bare]);
  assert.equal(r.code, 2);
  assert.match(JSON.parse(r.err).error, /root_hex/);
});
