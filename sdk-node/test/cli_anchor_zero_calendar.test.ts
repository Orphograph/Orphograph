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
import { MerkleTree } from "../dist/merkle.js";
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

// A stub on 127.0.0.1 that records every request line and answers a 1-calendar
// receipt (POST) or an inclusion proof (GET), and one CLI run against it.
async function runWithStub(argvFor: (stub: string) => string[], envFor: (stub: string) => Record<string, string> = () => ({})):
    Promise<{ code: number | null; stdout: string; stderr: string; urls: string[]; keyed: boolean[] }> {
  const urls: string[] = [];
  const keyed: boolean[] = [];
  const server = createServer((req, res) => {
    urls.push(req.url ?? "");
    keyed.push("x-orpho-api-key" in req.headers);
    let body = "";
    req.on("data", (c) => (body += c));
    req.on("end", () => {
      res.writeHead(200, { "Content-Type": "application/json" });
      if (req.method === "POST") {
        const manifest = JSON.parse(body).manifest;
        res.end(JSON.stringify({ receipt_id: "RSTUB0001", root_hex: manifest.root_hex,
          leaf_count: manifest.leaves.length, calendars_ok: 1, calendars_total: 5 }));
      } else {
        res.end(JSON.stringify({ proof: [], root_hex: "00".repeat(32), path: "x" }));
      }
    });
  });
  await new Promise<void>((r) => server.listen(0, "127.0.0.1", () => r()));
  const stub = `http://127.0.0.1:${(server.address() as AddressInfo).port}`;
  const env = envFor(stub);
  const childEnv: Record<string, string | undefined> = { ...process.env };
  delete childEnv.ORPHO_API_KEY;
  Object.assign(childEnv, env);
  if (!("ORPHO_SERVER_URL" in env)) delete childEnv.ORPHO_SERVER_URL;
  try {
    return await new Promise((r) => {
      const child = spawn(process.execPath, ["--import", GUARD, CLI, ...argvFor(stub)], { env: childEnv });
      let stdout = "", stderr = "";
      child.stdout.on("data", (c) => (stdout += c));
      child.stderr.on("data", (c) => (stderr += c));
      child.on("close", (code) => r({ code, stdout, stderr, urls, keyed }));
    });
  } finally {
    server.close();
  }
}

function folderWithOneFile(): string {
  const dir = mkdtempSync(join(tmpdir(), "orpho-cli-"));
  writeFileSync(join(dir, "a.txt"), "a");
  return dir;
}

// Round 3: this test used to aim at a closed port and only check that stderr
// had no refusal message, so a parser that dropped the id passed it.
test("a receipt id that starts with '-' reaches the named server as the id", async () => {
  const run = await runWithStub((s) => ["proof", "-abcDEFghiJKLmno", "a.txt", "--server", s]);
  assert.equal(run.code, 0, run.stderr);
  assert.equal(run.urls.length, 1);
  assert.match(run.urls[0], /receipt_id=-abcDEFghiJKLmno/);
  assert.match(run.urls[0], /path=a\.txt/);
});

test("after a bare --, a receipt id starting with -- and a rel_path -x are arguments", async () => {
  const a = await runWithStub((s) => ["proof", "--server", s, "--", "--abcDEFghiJKLmn", "a.txt"]);
  assert.equal(a.code, 0, a.stderr);
  assert.match(a.urls[0] ?? "", /receipt_id=--abcDEFghiJKLmn/);
  const b = await runWithStub((s) => ["proof", "--server", s, "AbcDEFghi_JK-mno", "--", "-x"]);
  assert.equal(b.code, 0, b.stderr);
  assert.match(b.urls[0] ?? "", /path=-x/);
  // Without the --, -x is still refused before any request.
  const c = await runWithStub((s) => ["proof", "--server", s, "AbcDEFghi_JK-mno", "-x"]);
  assert.equal(c.code, 2, c.stderr);
  assert.equal(c.urls.length, 0);
});

test("verify-inclusion (offline) takes a rel_path -x as a path", async () => {
  const dir = mkdtempSync(join(tmpdir(), "orpho-dash-"));
  writeFileSync(join(dir, "-x"), "dash file");
  writeFileSync(join(dir, "b.txt"), "b");
  const tree = await MerkleTree.fromFolder(dir);
  const proofFile = join(mkdtempSync(join(tmpdir(), "orpho-proof-")), "p.json");
  writeFileSync(proofFile, JSON.stringify({ proof: tree.inclusionProof("-x"), root_hex: tree.rootHex() }));
  const run = await runWithStub(() => ["verify-inclusion", join(dir, "-x"), "-x", proofFile]);
  assert.equal(run.code, 0, run.stderr);
  assert.equal(JSON.parse(run.stdout).ok, true);
  assert.equal(run.urls.length, 0);
});

for (const [name, argvFor] of [
  ["a URL in the receipt id slot of verify", (s: string) => ["verify", folderWithOneFile(), s]],
  ["a URL in the rel_path slot of proof", (s: string) => ["proof", "AbcDEFghi_JK-mno", s]],
  // Round 4: a URL stuck to a short option, or after a bare "--".
  ["proof <id> -sURL", (s: string) => ["proof", "AbcDEFghi_JK-mno", "-s" + s]],
  ["proof <id> -s=URL", (s: string) => ["proof", "AbcDEFghi_JK-mno", "-s=" + s]],
  ["proof <id> -- --server=URL", (s: string) => ["proof", "AbcDEFghi_JK-mno", "--", "--server=" + s]],
  ["proof -- <id> --server-url=URL", (s: string) => ["proof", "--", "AbcDEFghi_JK-mno", "--server-url=" + s]],
  ["anchor -- --server=URL", (s: string) => ["anchor", "--", "--server=" + s]],
  ["a receipt id outside the server's format", (s: string) => ["verify", folderWithOneFile(), "not an id", "--server", s]],
  ["--__proto__ URL", (s: string) => ["anchor", folderWithOneFile(), "--__proto__", s]],
  ["a valueless --api-key", (s: string) => ["anchor", folderWithOneFile(), "--server", s, "--api-key"]],
  ["a valueless --label", (s: string) => ["anchor", folderWithOneFile(), "--server", s, "--label"]],
] as const) {
  test(`${name} exits 2 before any request`, async () => {
    const run = await runWithStub(argvFor);
    assert.equal(run.code, 2, run.stderr);
    assert.equal(run.urls.length, 0);
    assert.doesNotMatch(run.stderr, /egress blocked/);
  });
}

test("an explicit empty --api-key means no key, as in the Python CLI", async () => {
  // A dummy value, not a key: the stub records only whether a key header came.
  const env = () => ({ ORPHO_API_KEY: "dummy-not-a-key" });
  const run = await runWithStub((s) => ["anchor", folderWithOneFile(), "--server", s, "--api-key", ""], env);
  assert.equal(run.code, 0, run.stderr);
  assert.deepEqual(run.keyed, [false]);
  // Control: without the flag, the environment key is sent.
  const withEnv = await runWithStub((s) => ["anchor", folderWithOneFile(), "--server", s], env);
  assert.deepEqual(withEnv.keyed, [true]);
});

test("--server and --server-url naming one server (trailing slash aside) are accepted", async () => {
  const run = await runWithStub((s) => ["anchor", folderWithOneFile(), "--server", s, "--server-url", s + "/"]);
  assert.equal(run.code, 0, run.stderr);
  assert.equal(run.urls.length, 1);
});

test("ORPHO_SERVER_URL is used when no --server is given", async () => {
  // The Python CLI's environment form; both packages install `orphograph`.
  const run = await runWithStub(() => ["anchor", folderWithOneFile()], (s) => ({ ORPHO_SERVER_URL: s }));
  // The stub's port is only in the variable, so one request here proves it
  // was read. (Round 4: a "without it" control here headed for the live
  // default on every run, held back only by the guard; removed.)
  assert.equal(run.code, 0, run.stderr);
  assert.equal(run.urls.length, 1);
});
