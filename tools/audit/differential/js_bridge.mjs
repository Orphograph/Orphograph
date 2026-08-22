// js_bridge.mjs — exercises the JavaScript verifier implementations for the
// differential harness. Reads one JSON job per invocation on argv[2], writes a
// JSON result to stdout. No network, no installs.
//
// Implementations bridged:
//   verifier-js/orphograph_verify.js  -> verifyReceiptAgainstFile (binding check)
//   sdk-node/dist/merkle.js           -> MerkleTree.fromFolder / fromHex
//
// Every handler converts a throw into {status:"error"} rather than letting the
// process die, because "this implementation rejected the input" is a RESULT the
// harness needs to compare, not a harness failure.

import { readFileSync } from "node:fs";
import { pathToFileURL } from "node:url";

const job = JSON.parse(process.argv[2]);

function out(o) {
  process.stdout.write(JSON.stringify(o));
}

async function verifierJsBinding(job) {
  const mod = await import(pathToFileURL(job.verifier_js_path).href);
  const fn = mod.verifyReceiptAgainstFile || mod.default?.verifyReceiptAgainstFile;
  if (typeof fn !== "function") {
    return { status: "error", detail: "verifyReceiptAgainstFile not exported" };
  }
  const bytes = readFileSync(job.file_path);
  // The receipt is supplied verbatim from the fixture — including malformed
  // and missing hash_hex cases. That is the point of the test.
  const res = await fn(bytes, job.receipt);
  return {
    status: "ok",
    valid: res?.ok === true,
    raw: { ok: res?.ok ?? null, sha256_match: res?.sha256_match ?? null },
  };
}

async function sdkNodeFromHex(job) {
  const mod = await import(pathToFileURL(job.sdk_node_merkle_path).href);
  const MerkleTree = mod.MerkleTree || mod.default?.MerkleTree;
  const fromHex = MerkleTree?.fromHex || mod.fromHex;
  if (typeof fromHex !== "function") {
    return { status: "error", detail: "fromHex not reachable" };
  }
  try {
    const bytes = fromHex(job.hex);
    return { status: "ok", valid: true, raw: { len: bytes?.length ?? null } };
  } catch (e) {
    return { status: "error", detail: String(e.message || e) };
  }
}

const handlers = {
  "verifier_js.binding": verifierJsBinding,
  "sdk_node.from_hex": sdkNodeFromHex,
};

try {
  const h = handlers[job.op];
  if (!h) {
    out({ status: "error", detail: `unknown op ${job.op}` });
  } else {
    out(await h(job));
  }
} catch (e) {
  out({ status: "error", detail: String(e?.message || e) });
}
