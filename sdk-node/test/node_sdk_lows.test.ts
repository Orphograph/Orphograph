// @node-sdk-lows: five gaps between the Node SDK and the Python SDK.
//
//   1. verify read a broken server answer (no manifest root) as a mismatch
//      and exited 1, the "these bytes were changed" verdict; it is an error.
//   2. verify-inclusion took a wrong file (the receipt, a bare number, a
//      proof that is not a list) as an empty proof and printed a mismatch.
//   3. verify ignored the manifest's scope.exclude, so a folder anchored with
//      custom excludes did not verify unless the caller repeated them.
//   4. a server URL with a path (https://host/orpho) lost the path.
//   5. the User-Agent's version is the package's version.
//
// The service is a stub on 127.0.0.1.
import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { spawn } from "node:child_process";
import { createServer, type IncomingMessage } from "node:http";
import type { AddressInfo } from "node:net";
import { MerkleTree } from "../dist/merkle.js";
import { verifyFolder, verifyFolderDetail } from "../dist/index.js";
import { USER_AGENT } from "../dist/client.js";

const CLI = resolve(import.meta.dirname, "..", "dist", "cli.js");
const PKG = JSON.parse(readFileSync(resolve(import.meta.dirname, "..", "package.json"), "utf-8"));

async function withStub(
  answer: (req: IncomingMessage) => unknown,
  fn: (base: string, seen: IncomingMessage[]) => Promise<void>,
) {
  const seen: IncomingMessage[] = [];
  const server = createServer((req, res) => {
    seen.push(req);
    res.writeHead(200, { "Content-Type": "application/json" });
    res.end(JSON.stringify(answer(req)));
  });
  await new Promise<void>((r) => server.listen(0, "127.0.0.1", () => r()));
  const port = (server.address() as AddressInfo).port;
  try {
    await fn(`http://127.0.0.1:${port}`, seen);
  } finally {
    server.close();
  }
}

function run(args: string[]) {
  return new Promise<{ code: number | null; stdout: string; stderr: string }>((r) => {
    const child = spawn(process.execPath, [CLI, ...args], {
      env: { ...process.env, ORPHO_SERVER_URL: "", ORPHO_API_KEY: "" },
    });
    let stdout = "", stderr = "";
    child.stdout.on("data", (c) => (stdout += c));
    child.stderr.on("data", (c) => (stderr += c));
    child.on("close", (code) => r({ code, stdout, stderr }));
  });
}

function folder(): string {
  const dir = mkdtempSync(join(tmpdir(), "orpho-node-lows-"));
  writeFileSync(join(dir, "a.txt"), "a");
  return dir;
}

test("verify: an answer with no manifest root is an error (exit 2), not a mismatch", async () => {
  const dir = folder();
  for (const answer of [
    { receipt: { receipt_id: "RNOROOT001", calendars_ok: 3 } },
    { receipt: { receipt_id: "RNOROOT001" }, manifest: {} },
    { receipt: { receipt_id: "RNOROOT001" }, manifest: { root_hex: 7 } },
    { receipt: { receipt_id: "RNOROOT001" }, manifest: "x" },
  ]) {
    await withStub(() => answer, async (base) => {
      const r = await run(["verify", dir, "RNOROOT001", "--server", base]);
      assert.equal(r.code, 2, JSON.stringify(answer) + r.stderr);
      assert.equal(r.stdout, "");
      assert.match(r.stderr, /no manifest root_hex/);
    });
  }
});

test("verify: the library keeps answering false for that answer (VERIFIER_SPEC 4.2)", async () => {
  const dir = folder();
  await withStub(() => ({ receipt: { receipt_id: "RNOROOT001" } }), async (base) => {
    assert.equal(await verifyFolder(dir, "RNOROOT001", { serverUrl: base }), false);
    const d = await verifyFolderDetail(dir, "RNOROOT001", { serverUrl: base });
    assert.deepEqual(d, { match: false, calendarsOk: null, rootServed: false });
  });
});

test("verify: a real mismatch stays exit 1", async () => {
  const dir = folder();
  await withStub(
    () => ({ receipt: { receipt_id: "RMISMATCH1", calendars_ok: 3 }, manifest: { root_hex: "0".repeat(64) } }),
    async (base) => {
      const r = await run(["verify", dir, "RMISMATCH1", "--server", base]);
      assert.equal(r.code, 1, r.stderr);
      assert.deepEqual(JSON.parse(r.stdout), { ok: false });
    },
  );
});

test("verify-inclusion: a wrong proof file is a usage error (exit 2)", async () => {
  const dir = folder();
  const file = join(dir, "a.txt");
  const root = "0".repeat(64);
  const cases: Array<[unknown, RegExp]> = [
    [{ receipt_id: "R1", root_hex: root }, /no 'proof' key/],
    [{ proof: "nope", root_hex: root }, /proof must be a list/],
    [{ proof: { a: 1 }, root_hex: root }, /proof must be a list/],
    [42, /object or an array/],
    [null, /object or an array/],
    ["text", /object or an array/],
  ];
  for (const [content, msg] of cases) {
    const pf = join(dir, "proof.json");
    writeFileSync(pf, JSON.stringify(content));
    const r = await run(["verify-inclusion", file, "a.txt", pf, root]);
    assert.equal(r.code, 2, JSON.stringify(content) + r.stdout);
    assert.equal(r.stdout, "");
    assert.match(r.stderr, msg);
  }
});

test("verify-inclusion: a good proof still verifies and a bare array still works", async () => {
  const dir = folder();
  writeFileSync(join(dir, "b.txt"), "b");
  const tree = await MerkleTree.fromFolder(dir);
  const root = tree.rootHex();
  const proof = tree.inclusionProof("a.txt");
  for (const content of [{ proof, root_hex: root }, proof]) {
    const pf = join(dir, "..", `p-${Math.random()}.json`);
    writeFileSync(pf, JSON.stringify(content));
    const r = await run(["verify-inclusion", join(dir, "a.txt"), "a.txt", pf, root]);
    assert.equal(r.code, 0, r.stderr);
    assert.equal(JSON.parse(r.stdout).ok, true);
  }
});

test("verify: the manifest's scope.exclude is used when the caller passes none", async () => {
  const dir = folder();
  writeFileSync(join(dir, "skip.log"), "noise");
  const anchored = (await MerkleTree.fromFolder(dir, { exclude: ["*.log"] })).rootHex();
  const plain = (await MerkleTree.fromFolder(dir)).rootHex();
  assert.notEqual(anchored, plain, "the fixture must make the exclude matter");
  const answer = {
    receipt: { receipt_id: "RSCOPE0001", calendars_ok: 3 },
    manifest: { root_hex: anchored, scope: { exclude: ["*.log"], exclude_source: "custom" } },
  };
  await withStub(() => answer, async (base) => {
    assert.equal(await verifyFolder(dir, "RSCOPE0001", { serverUrl: base }), true);
    // The manifest wins over a caller's different list, as in Python.
    assert.equal(await verifyFolder(dir, "RSCOPE0001", { serverUrl: base, exclude: [] }), true);
    const r = await run(["verify", dir, "RSCOPE0001", "--server", base]);
    assert.equal(r.code, 0, r.stderr);
  });
  // No scope: the caller's list is the fallback, unchanged.
  await withStub(
    () => ({ receipt: answer.receipt, manifest: { root_hex: anchored } }),
    async (base) => {
      assert.equal(await verifyFolder(dir, "RSCOPE0001", { serverUrl: base }), false);
      assert.equal(await verifyFolder(dir, "RSCOPE0001", { serverUrl: base, exclude: ["*.log"] }), true);
    },
  );
});

test("a server URL's path is kept", async () => {
  const dir = folder();
  const root = (await MerkleTree.fromFolder(dir)).rootHex();
  await withStub(
    () => ({ receipt: { receipt_id: "RPATH00001", calendars_ok: 3 }, manifest: { root_hex: root } }),
    async (base, seen) => {
      for (const suffix of ["/orpho", "/orpho/"]) {
        seen.length = 0;
        assert.equal(await verifyFolder(dir, "RPATH00001", { serverUrl: base + suffix }), true);
        assert.equal(seen[0].url, "/orpho/api/verify_folder/RPATH00001");
      }
      seen.length = 0;
      assert.equal(await verifyFolder(dir, "RPATH00001", { serverUrl: base }), true);
      assert.equal(seen[0].url, "/api/verify_folder/RPATH00001");
    },
  );
});

test("the User-Agent names the package's version", async () => {
  assert.equal(USER_AGENT.split(" ")[0], `orphograph-node/${PKG.version}`);
  const dir = folder();
  const root = (await MerkleTree.fromFolder(dir)).rootHex();
  await withStub(
    () => ({ receipt: { receipt_id: "RUAVER0001" }, manifest: { root_hex: root } }),
    async (base, seen) => {
      await verifyFolder(dir, "RUAVER0001", { serverUrl: base });
      assert.equal(seen[0].headers["user-agent"], USER_AGENT);
    },
  );
});

test("verify: a hostile scope.exclude from the server neither hangs nor throws", async () => {
  // The patterns now come from the server's answer. As RegExps,
  // `*a*a*a*a*a*a*a*a*b` backtracked for seconds per long name and `[z-a]x`
  // threw "Range out of order"; the glob walk is linear and total.
  const dir = folder();
  const long = "a".repeat(200);
  writeFileSync(join(dir, long), "x");
  const root = (await MerkleTree.fromFolder(dir, { exclude: ["*a*a*a*a*a*a*a*a*b", "[z-a]x"] })).rootHex();
  const answer = {
    receipt: { receipt_id: "RHOSTILE01", calendars_ok: 3 },
    manifest: { root_hex: root, scope: { exclude: ["*a*a*a*a*a*a*a*a*b", "[z-a]x"] } },
  };
  await withStub(() => answer, async (base) => {
    const t0 = Date.now();
    assert.equal(await verifyFolder(dir, "RHOSTILE01", { serverUrl: base }), true);
    assert.ok(Date.now() - t0 < 2000, `took ${Date.now() - t0}ms`);
  });
});
