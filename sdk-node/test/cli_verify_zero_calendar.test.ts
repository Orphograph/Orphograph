// `orphograph verify` refuses a receipt no calendar accepted (@agent-sdk-verify-zero-cal).
//
// The bytes may match the manifest root, but a receipt with calendars_ok 0
// has no Bitcoin commitment and never gets one, so nothing bounds the bytes
// in time. verify printed {"ok":true} and exited 0. The dataset CLI already
// fails it; this CLI now does too. The service is a stub on 127.0.0.1.
import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { spawn } from "node:child_process";
import { createServer } from "node:http";
import type { AddressInfo } from "node:net";
import { MerkleTree } from "../dist/merkle.js";

const CLI = resolve(import.meta.dirname, "..", "dist", "cli.js");

async function verifyAgainst(calendarsOk: number | undefined, tamper = false) {
  const dir = mkdtempSync(join(tmpdir(), "orpho-verify-zero-cal-"));
  writeFileSync(join(dir, "a.txt"), "a");
  const root = (await MerkleTree.fromFolder(dir)).rootHex();
  const server = createServer((req, res) => {
    res.writeHead(200, { "Content-Type": "application/json" });
    const receipt: Record<string, unknown> = { receipt_id: "RZEROCAL05", kind: "folder", hash_hex: root };
    if (calendarsOk !== undefined) receipt.calendars_ok = calendarsOk;
    res.end(JSON.stringify({ receipt, manifest: { root_hex: tamper ? "0".repeat(64) : root } }));
  });
  await new Promise<void>((r) => server.listen(0, "127.0.0.1", () => r()));
  const port = (server.address() as AddressInfo).port;
  try {
    return await new Promise<{ code: number | null; stdout: string; stderr: string }>((r) => {
      const child = spawn(process.execPath, [CLI, "verify", dir, "RZEROCAL05", "--server", `http://127.0.0.1:${port}`]);
      let stdout = "", stderr = "";
      child.stdout.on("data", (c) => (stdout += c));
      child.stderr.on("data", (c) => (stderr += c));
      child.on("close", (code) => r({ code, stdout, stderr }));
    });
  } finally {
    server.close();
  }
}

test("a matching receipt no calendar accepted does not verify", async () => {
  const run = await verifyAgainst(0);
  assert.equal(run.code, 1, run.stderr);
  assert.deepEqual(JSON.parse(run.stdout), { ok: false, match: true, calendars_ok: 0 });
  assert.match(run.stderr, /no calendar accepted/);
});

test("a matching receipt with a calendar, or an answer without the field, verifies", async () => {
  for (const cal of [1, undefined]) {
    const run = await verifyAgainst(cal);
    assert.equal(run.code, 0, run.stderr);
    assert.deepEqual(JSON.parse(run.stdout), { ok: true });
  }
});

test("a mismatch stays the plain mismatch verdict", async () => {
  const run = await verifyAgainst(0, true);
  assert.equal(run.code, 1, run.stderr);
  assert.deepEqual(JSON.parse(run.stdout), { ok: false });
});
