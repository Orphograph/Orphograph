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

// Every CLI run here gets an egress guard: any request that is not plain HTTP
// to 127.0.0.1 throws. The defect these tests pin used to send requests to the
// live service, and a pre-fix run of a test for it must not be able to do that
// again (it did, once, in cycle 9: rate-limited, nothing anchored).
const GUARD = join(mkdtempSync(join(tmpdir(), "orpho-egress-guard-")), "guard.mjs");
writeFileSync(GUARD, `
import http from "node:http";
import https from "node:https";
import { syncBuiltinESMExports } from "node:module";
const realHttp = http.request;
https.request = () => { throw new Error("egress blocked by test guard: https"); };
https.get = https.request;
http.request = (opts, ...rest) => {
  const host = typeof opts === "string" || opts instanceof URL ? new URL(String(opts)).hostname : (opts.hostname || opts.host);
  if (host !== "127.0.0.1") throw new Error("egress blocked by test guard: http " + host);
  return realHttp(opts, ...rest);
};
syncBuiltinESMExports();
`);

async function anchorAgainst(calendarsOk: number, serverFlag = "--server"): Promise<{ code: number | null; stdout: string; stderr: string; requests: number }> {
  let requests = 0;
  const server = createServer((req, res) => {
    requests += 1;
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
      const child = spawn(process.execPath, ["--import", GUARD, CLI, "anchor", dir, serverFlag, `http://127.0.0.1:${port}`]);
      let stdout = "", stderr = "";
      child.stdout.on("data", (c) => (stdout += c));
      child.stderr.on("data", (c) => (stderr += c));
      child.on("close", (code) => resolveRun({ code, stdout, stderr, requests }));
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

// Cycle 9 incident: a reviewer ran `anchor <dir> --server-url <stub>`. The CLI
// knew only --server, dropped the unknown flag without a word, and anchored on
// the live service three times. Unknown options now stop the CLI before any
// network call, and --server-url (the Python CLI's spelling) is accepted.
for (const [flag, message] of [["--sever", /unknown option --sever/], ["-s", /unknown option -s/]]) {
  test(`an unknown option (${flag}) exits 2 before any request`, async () => {
    const run = await anchorAgainst(1, flag as string);
    assert.equal(run.code, 2, run.stderr);
    assert.match(run.stderr, message as RegExp);
    assert.equal(run.requests, 0);
    // Not even an attempt: the egress guard would turn one into an exit 2
    // too, so "exit 2" alone cannot tell "refused" from "tried and blocked".
    assert.doesNotMatch(run.stderr, /egress blocked/);
  });
}

test("a stray positional (a URL meant for --server) exits 2 before any request", async () => {
  const dir = mkdtempSync(join(tmpdir(), "orpho-stray-"));
  writeFileSync(join(dir, "a.txt"), "a");
  const run = await new Promise<{ code: number | null; stderr: string }>((r) => {
    const child = spawn(process.execPath, ["--import", GUARD, CLI, "anchor", dir, "http://127.0.0.1:9"]);
    let stderr = "";
    child.stderr.on("data", (c) => (stderr += c));
    child.on("close", (code) => r({ code, stderr }));
  });
  assert.equal(run.code, 2, run.stderr);
  assert.match(run.stderr, /unexpected argument/);
  assert.doesNotMatch(run.stderr, /egress blocked/);
});

// Round 2: a value flag with no value fell through to the live default too.
for (const argv of [["--server"], ["--server="], ["--server", ""], ["--server-url", "--label", "x"],
                    ["--server", "--server-url", "STUB"], ["--server=", "--server-url=STUB"],
                    ["--server", "STUB", "--server-url", "http://127.0.0.1:9"]]) {
  test(`a server flag with no usable value (${JSON.stringify(argv)}) exits 2 before any request`, async () => {
    let requests = 0;
    const server = createServer((req, res) => { requests += 1; res.writeHead(500); res.end(); });
    await new Promise<void>((r) => server.listen(0, "127.0.0.1", () => r()));
    const stub = `http://127.0.0.1:${(server.address() as AddressInfo).port}`;
    const dir = mkdtempSync(join(tmpdir(), "orpho-noval-"));
    writeFileSync(join(dir, "a.txt"), "a");
    try {
      const run = await new Promise<{ code: number | null; stderr: string }>((r) => {
        const child = spawn(process.execPath, ["--import", GUARD, CLI, "anchor", dir,
          ...argv.map((a) => a.replace("STUB", stub))]);
        let stderr = "";
        child.stderr.on("data", (c) => (stderr += c));
        child.on("close", (code) => r({ code, stderr }));
      });
      assert.equal(run.code, 2, run.stderr);
      assert.equal(requests, 0);
      assert.doesNotMatch(run.stderr, /egress blocked/);
    } finally {
      server.close();
    }
  });
}

test("--server-url is accepted as --server", async () => {
  const run = await anchorAgainst(1, "--server-url");
  assert.equal(run.code, 0, run.stderr);
  assert.equal(run.requests, 1);
});

test("the egress guard itself blocks an https request", async () => {
  // Negative control for the guard, aimed at a reserved name (.invalid never
  // resolves), not the live service: if the guard stopped working, this run
  // fails on DNS instead of anchoring anywhere real.
  const dir = mkdtempSync(join(tmpdir(), "orpho-guard-ctl-"));
  writeFileSync(join(dir, "a.txt"), "a");
  const run = await new Promise<{ code: number | null; stderr: string }>((r) => {
    const child = spawn(process.execPath, ["--import", GUARD, CLI, "anchor", dir, "--server", "https://guard-control.invalid"]);
    let stderr = "";
    child.stderr.on("data", (c) => (stderr += c));
    child.on("close", (code) => r({ code, stderr }));
  });
  assert.equal(run.code, 2, run.stderr);
  assert.match(run.stderr, /egress blocked by test guard/);
});

test("a receipt id that starts with '-' is still an argument, not an option", async () => {
  // token_urlsafe ids start with "-" about one time in 64; only one- or
  // two-letter dash forms are refused. Port 9 is closed, so this run ends on
  // the connection, never on argument parsing.
  const run = await new Promise<{ code: number | null; stderr: string }>((r) => {
    const child = spawn(process.execPath, ["--import", GUARD, CLI, "proof", "-abcDEFghiJKLmno", "a.txt",
      "--server", "http://127.0.0.1:9"]);
    let stderr = "";
    child.stderr.on("data", (c) => (stderr += c));
    child.on("close", (code) => r({ code, stderr }));
  });
  assert.doesNotMatch(run.stderr, /unknown option|unexpected argument/);
  assert.doesNotMatch(run.stderr, /egress blocked/);
});
