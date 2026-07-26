// parity_bridge.mjs — computes Merkle roots with the two JavaScript
// implementations for the three-way parity harness.
//
//   sdk-node/dist/merkle.js  — exports leafHash / internalHash
//   web/folder.js            — browser impl; _leafFor/_buildTree are
//                              module-private, so the harness writes a temp
//                              copy with ONE appended line:
//                                  export { _leafFor, _buildTree };
//                              Nothing else is altered. The algorithm under
//                              test is theirs, byte for byte.
//
// Input: argv[2] = path to a JSON job file
//   { impl: "sdk_node"|"web_folder", module: "<abs path>",
//     cases: [ { name, leaves: [ [relPath, fileSha256Hex], ... ] } ] }
// Output: JSON to stdout { results: { caseName: {root|error} } }

import { pathToFileURL } from "node:url";
import { readFileSync } from "node:fs";

const job = JSON.parse(readFileSync(process.argv[2], "utf8"));

function hexToBytes(hex) {
  const out = new Uint8Array(hex.length / 2);
  for (let i = 0; i < out.length; i++) out[i] = parseInt(hex.slice(i * 2, i * 2 + 2), 16);
  return out;
}
function bytesToHex(b) {
  return Array.from(b).map((x) => x.toString(16).padStart(2, "0")).join("");
}
function byteCompare(a, b) {
  const len = Math.min(a.length, b.length);
  for (let i = 0; i < len; i++) if (a[i] !== b[i]) return a[i] - b[i];
  return a.length - b.length;
}

const enc = new TextEncoder();

async function rootWebFolder(mod, leaves) {
  // Sort by UTF-8 byte order of path, exactly as anchorFolder does (folder.js:410).
  const rows = leaves.map(([p, h]) => ({ p, bytes: enc.encode(p), digest: hexToBytes(h) }));
  rows.sort((a, b) => byteCompare(a.bytes, b.bytes));
  const leafHashes = [];
  for (const r of rows) leafHashes.push(await mod._leafFor(r.p, r.digest));
  const root = await mod._buildTree(leafHashes);
  return bytesToHex(root);
}

async function rootSdkNode(mod, leaves) {
  const rows = leaves.map(([p, h]) => ({ p, bytes: enc.encode(p), digest: hexToBytes(h) }));
  rows.sort((a, b) => byteCompare(a.bytes, b.bytes));
  let level = rows.map((r) => mod.leafHash(r.p, r.digest));
  if (level.length === 0) {
    const empty = await crypto.subtle.digest("SHA-256", new Uint8Array(0));
    return bytesToHex(new Uint8Array(empty));
  }
  while (level.length > 1) {
    const next = [];
    for (let i = 0; i < level.length; i += 2) {
      if (i + 1 >= level.length) { next.push(level[i]); continue; }  // promote
      next.push(mod.internalHash(level[i], level[i + 1]));
    }
    level = next;
  }
  return bytesToHex(level[0]);
}

// web/folder.js ends with a DOM bootstrap at module scope (folder.js:540,
// `if (document.readyState === "loading")`), so importing it under node throws
// ReferenceError before any export is reachable. Stub the minimum surface it
// touches — addEventListener / body / createElement / querySelector /
// readyState, plus window.showDirectoryPicker. This lets the module load; it
// does NOT touch the hashing or tree code, which is what is under test.
if (job.impl === "web_folder" && typeof globalThis.document === "undefined") {
  const el = () => ({
    addEventListener() {}, appendChild() {}, setAttribute() {},
    classList: { add() {}, remove() {} }, style: {}, dataset: {},
    get textContent() { return ""; }, set textContent(_) {},
    get innerHTML() { return ""; }, set innerHTML(_) {},
  });
  globalThis.document = {
    readyState: "complete",
    addEventListener() {}, querySelector() { return null; },
    querySelectorAll() { return []; }, createElement: el, body: el(),
  };
  globalThis.window = globalThis;
}

const mod = await import(pathToFileURL(job.module).href);
const results = {};
for (const c of job.cases) {
  try {
    results[c.name] = {
      root: job.impl === "web_folder"
        ? await rootWebFolder(mod, c.leaves)
        : await rootSdkNode(mod, c.leaves),
    };
  } catch (e) {
    results[c.name] = { error: String(e?.message || e) };
  }
}
process.stdout.write(JSON.stringify({ results }));
