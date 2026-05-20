// client.test.ts — verify the HTTP client by spinning up a local mock
// server with node:http. The mock records every request body so we can
// assert that file contents NEVER appear in the wire payload.

import { test } from "node:test";
import assert from "node:assert/strict";
import { createServer, type IncomingMessage, type ServerResponse, type Server } from "node:http";
import {
  mkdtempSync,
  mkdirSync,
  writeFileSync,
  rmSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

// The tests import from the compiled dist/. Run `npm run build` (or `tsc`)
// before `npm test`. This keeps the imports identical to what consumers
// of the published package will write — no source-only path tricks.
import { anchorFolder, verifyFolder, inclusionProof } from "../dist/index.js";
import { submitManifest } from "../dist/client.js";
import { MerkleTree } from "../dist/merkle.js";

interface MockRequest {
  method: string;
  url: string;
  body: string;
  headers: Record<string, string | string[] | undefined>;
}

interface Mock {
  url: string;
  requests: MockRequest[];
  close: () => Promise<void>;
}

async function startMock(
  handler: (req: MockRequest, res: ServerResponse) => void,
): Promise<Mock> {
  const requests: MockRequest[] = [];
  const server: Server = createServer(
    (req: IncomingMessage, res: ServerResponse) => {
      const chunks: Buffer[] = [];
      req.on("data", (c: Buffer) => chunks.push(c));
      req.on("end", () => {
        const record: MockRequest = {
          method: req.method || "",
          url: req.url || "",
          body: Buffer.concat(chunks).toString("utf-8"),
          headers: req.headers,
        };
        requests.push(record);
        handler(record, res);
      });
    },
  );
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
  const addr = server.address();
  if (!addr || typeof addr === "string") throw new Error("no addr");
  const url = `http://127.0.0.1:${addr.port}`;
  return {
    url,
    requests,
    close: () =>
      new Promise<void>((resolve, reject) =>
        server.close((err?: Error) => (err ? reject(err) : resolve())),
      ),
  };
}

function makeFixture(): { dir: string; cleanup: () => void } {
  const dir = mkdtempSync(join(tmpdir(), "orpho-sdk-node-client-"));
  mkdirSync(join(dir, "sub"));
  writeFileSync(join(dir, "a.txt"), "hello\n");
  writeFileSync(join(dir, "sub", "b.txt"), "world\n");
  return { dir, cleanup: () => rmSync(dir, { recursive: true, force: true }) };
}

const SECRET_BYTES = "SUPER_SECRET_PAYLOAD_DO_NOT_LEAK";

function makeSecretFixture(): { dir: string; cleanup: () => void } {
  const dir = mkdtempSync(join(tmpdir(), "orpho-sdk-node-secret-"));
  writeFileSync(join(dir, "secret.txt"), SECRET_BYTES);
  return { dir, cleanup: () => rmSync(dir, { recursive: true, force: true }) };
}

// ─── anchorFolder: privacy + happy path ─────────────────────────────────

test("anchorFolder transmits manifest only — file content does NOT appear in the request body", async () => {
  const fix = makeSecretFixture();
  const mock = await startMock((req, res) => {
    res.writeHead(200, { "Content-Type": "application/json" });
    res.end(
      JSON.stringify({
        receipt_id: "test_receipt_123",
        root_hex: "0".repeat(64),
        leaf_count: 1,
        kind: "folder",
        merkle_algorithm: "orphograph-merkle-v1-rfc6962",
        calendars_ok: 5,
        calendars_total: 5,
        created_at: "2026-05-20T00:00:00Z",
      }),
    );
  });
  try {
    const result = await anchorFolder(fix.dir, { serverUrl: mock.url });
    assert.equal(result.receipt_id, "test_receipt_123");
    assert.equal(result.calendars_ok, 5);
    assert.equal(mock.requests.length, 1);
    const req = mock.requests[0];
    assert.equal(req.method, "POST");
    assert.equal(req.url, "/api/anchor_folder");
    // The secret payload bytes must NOT appear anywhere in the body.
    assert.ok(
      !req.body.includes(SECRET_BYTES),
      "anchor body must not contain raw file contents",
    );
    // The manifest must be present and well-formed.
    const parsed = JSON.parse(req.body) as { manifest: { algorithm: string; root_hex: string } };
    assert.equal(parsed.manifest.algorithm, "orphograph-merkle-v1-rfc6962");
    assert.equal(parsed.manifest.root_hex.length, 64);
  } finally {
    await mock.close();
    fix.cleanup();
  }
});

test("anchorFolder sends X-Orpho-Api-Key when apiKey is provided", async () => {
  const fix = makeFixture();
  const mock = await startMock((req, res) => {
    res.writeHead(200, { "Content-Type": "application/json" });
    res.end(
      JSON.stringify({
        receipt_id: "rid_xyz",
        root_hex: "1".repeat(64),
        leaf_count: 2,
        kind: "folder",
        merkle_algorithm: "orphograph-merkle-v1-rfc6962",
        calendars_ok: 4,
        calendars_total: 5,
        created_at: "2026-05-20T00:00:00Z",
      }),
    );
  });
  try {
    await anchorFolder(fix.dir, { serverUrl: mock.url, apiKey: "sk_test_abc" });
    assert.equal(mock.requests[0].headers["x-orpho-api-key"], "sk_test_abc");
  } finally {
    await mock.close();
    fix.cleanup();
  }
});

test("anchorFolder surfaces server errors", async () => {
  const fix = makeFixture();
  const mock = await startMock((req, res) => {
    res.writeHead(429, { "Content-Type": "application/json" });
    res.end(JSON.stringify({ error: "rate limit exceeded" }));
  });
  try {
    await assert.rejects(
      () => anchorFolder(fix.dir, { serverUrl: mock.url }),
      /HTTP 429.*rate limit exceeded/,
    );
  } finally {
    await mock.close();
    fix.cleanup();
  }
});

// ─── verifyFolder: round-trip with the locally-computed root ─────────────

test("verifyFolder returns true when local root matches server root", async () => {
  const fix = makeFixture();
  const localTree = await MerkleTree.fromFolder(fix.dir);
  const expectedRoot = localTree.rootHex();
  const mock = await startMock((req, res) => {
    res.writeHead(200, { "Content-Type": "application/json" });
    res.end(
      JSON.stringify({
        receipt: {
          receipt_id: "rid_match",
          hash_hex: expectedRoot,
          kind: "folder",
          found: true,
        },
        manifest: localTree.manifest(),
      }),
    );
  });
  try {
    const ok = await verifyFolder(fix.dir, "rid_match", { serverUrl: mock.url });
    assert.equal(ok, true);
    assert.equal(mock.requests[0].url, "/api/verify_folder/rid_match");
  } finally {
    await mock.close();
    fix.cleanup();
  }
});

test("verifyFolder returns false when local root differs from server root", async () => {
  const fix = makeFixture();
  const localTree = await MerkleTree.fromFolder(fix.dir);
  // Forge a manifest with a different root by mutating the path of one leaf.
  const tamperedManifest = localTree.manifest();
  tamperedManifest.root_hex = "f".repeat(64);
  const mock = await startMock((req, res) => {
    res.writeHead(200, { "Content-Type": "application/json" });
    res.end(
      JSON.stringify({
        receipt: {
          receipt_id: "rid_mismatch",
          hash_hex: "f".repeat(64),
          kind: "folder",
          found: true,
        },
        manifest: tamperedManifest,
      }),
    );
  });
  try {
    const ok = await verifyFolder(fix.dir, "rid_mismatch", { serverUrl: mock.url });
    assert.equal(ok, false);
  } finally {
    await mock.close();
    fix.cleanup();
  }
});

// ─── inclusionProof: query shape and response parsing ────────────────────

test("inclusionProof builds query string with receipt_id and path", async () => {
  const mock = await startMock((req, res) => {
    res.writeHead(200, { "Content-Type": "application/json" });
    res.end(
      JSON.stringify({
        receipt_id: "rid_qry",
        root_hex: "a".repeat(64),
        path: "sub/b.txt",
        file_sha256_hex: "b".repeat(64),
        merkle_algorithm: "orphograph-merkle-v1-rfc6962",
        proof: [["R", "c".repeat(64)]],
      }),
    );
  });
  try {
    const proof = await inclusionProof("rid_qry", "sub/b.txt", {
      serverUrl: mock.url,
    });
    assert.equal(proof.receipt_id, "rid_qry");
    assert.equal(proof.proof.length, 1);
    assert.equal(proof.proof[0][0], "R");
    // The server received the path as a properly-escaped query parameter.
    const url = mock.requests[0].url;
    assert.ok(url.startsWith("/api/inclusion_proof?"));
    assert.ok(url.includes("receipt_id=rid_qry"));
    assert.ok(url.includes("path=sub%2Fb.txt"));
  } finally {
    await mock.close();
  }
});

// ─── submitManifest unit: stable header set ──────────────────────────────

test("submitManifest sets a stable Content-Type and User-Agent", async () => {
  const mock = await startMock((req, res) => {
    res.writeHead(200, { "Content-Type": "application/json" });
    res.end(
      JSON.stringify({
        receipt_id: "rid_ua",
        root_hex: "d".repeat(64),
        leaf_count: 1,
        kind: "folder",
        merkle_algorithm: "orphograph-merkle-v1-rfc6962",
        calendars_ok: 5,
        calendars_total: 5,
        created_at: "2026-05-20T00:00:00Z",
      }),
    );
  });
  try {
    const fakeManifest = {
      algorithm: "orphograph-merkle-v1-rfc6962",
      version: 1,
      root_hex: "0".repeat(64),
      leaves: [
        {
          path: "a.txt",
          file_sha256_hex: "0".repeat(64),
          leaf_hex: "0".repeat(64),
          size_bytes: 0,
        },
      ],
    };
    await submitManifest(fakeManifest, { serverUrl: mock.url });
    const h = mock.requests[0].headers;
    assert.equal(h["content-type"], "application/json");
    const ua = h["user-agent"];
    assert.ok(
      typeof ua === "string" && ua.startsWith("orphograph-node/"),
      `expected User-Agent to start with orphograph-node/, got ${String(ua)}`,
    );
  } finally {
    await mock.close();
  }
});
