// `orphograph anchor` exits 2 for a root no calendar accepted.
//
// A 200 with calendars_ok 0 is a receipt with no Bitcoin commitment, and it
// never gets one. The CLI printed it and exited 0, so a CI step using it as a
// gate passed. 2, not 1: exit 1 is the verify MISMATCH verdict. The service is
// a stub on 127.0.0.1; nothing leaves the machine.
import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { spawn } from "node:child_process";
import { createServer } from "node:http";
import type { AddressInfo } from "node:net";

const CLI = resolve(import.meta.dirname, "..", "dist", "cli.js");

async function anchorAgainst(calendarsOk: number): Promise<{ code: number | null; stdout: string; stderr: string }> {
  const server = createServer((req, res) => {
    let body = "";
    req.on("data", (c) => (body += c));
    req.on("end", () => {
      const manifest = JSON.parse(body).manifest;
      res.writeHead(200, { "Content-Type": "application/json" });
      res.end(JSON.stringify({
        receipt_id: "RZEROCAL04", root_hex: manifest.root_hex,
        leaf_count: manifest.leaves.length, calendars_ok: calendarsOk, calendars_total: 5,
      }));
    });
  });
  await new Promise<void>((r) => server.listen(0, "127.0.0.1", () => r()));
  const port = (server.address() as AddressInfo).port;
  const dir = mkdtempSync(join(tmpdir(), "orpho-zero-cal-"));
  writeFileSync(join(dir, "a.txt"), "a");
  try {
    return await new Promise((resolveRun) => {
      const child = spawn(process.execPath, [CLI, "anchor", dir, "--server", `http://127.0.0.1:${port}`]);
      let stdout = "", stderr = "";
      child.stdout.on("data", (c) => (stdout += c));
      child.stderr.on("data", (c) => (stderr += c));
      child.on("close", (code) => resolveRun({ code, stdout, stderr }));
    });
  } finally {
    server.close();
  }
}

test("a root no calendar accepted exits 2 and says why", async () => {
  const run = await anchorAgainst(0);
  assert.equal(run.code, 2, run.stderr);
  assert.equal(JSON.parse(run.stdout).receipt_id, "RZEROCAL04");
  assert.match(run.stderr, /no calendar accepted/);
});

test("a root one calendar accepted still exits 0", async () => {
  const run = await anchorAgainst(1);
  assert.equal(run.code, 0, run.stderr);
});
