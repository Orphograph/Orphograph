// index.ts — public API for the Orphograph Node SDK.
//
// The SDK exposes four operations:
//
//   anchorFolder      build a Merkle tree of a folder locally, submit only
//                     the manifest (paths + SHA-256 + root) to the hosted
//                     service, and receive a receipt identifier.
//
//   verifyFolder      re-walk the same folder, recompute the root, and
//                     compare against the receipt held by the service.
//
//   inclusionProof    fetch a third-party-verifiable proof that one file
//                     belonged to an anchored folder.
//
//   verifyInclusion   verify such a proof locally against a known root.
//
// File contents NEVER leave the local machine. Only the manifest crosses
// the network. See src/client.ts for the explicit privacy contract.
//
// MIT — see LICENSE.

import { createReadStream } from "node:fs";
import { createHash } from "node:crypto";

import {
  MerkleTree,
  type LeafMeta,
  type Manifest,
  type ProofStep,
  ALGORITHM,
  VERSION,
  DEFAULT_EXCLUDE,
  CHUNK_SIZE,
} from "./merkle.js";
import {
  submitManifest,
  fetchVerifyFolder,
  fetchInclusionProof,
  DEFAULT_SERVER_URL,
  type AnchorResponse,
  type InclusionProofResponse,
  type ClientOptions,
} from "./client.js";

export {
  MerkleTree,
  ALGORITHM,
  VERSION,
  DEFAULT_EXCLUDE,
  DEFAULT_SERVER_URL,
};
export type {
  LeafMeta,
  Manifest,
  ProofStep,
  AnchorResponse,
  InclusionProofResponse,
  ClientOptions,
};

export interface AnchorFolderResult {
  receipt_id: string;
  root_hex: string;
  leaf_count: number;
  calendars_ok: number;
  calendars_total: number;
}

export interface AnchorFolderOptions {
  serverUrl?: string;
  apiKey?: string;
  clientLabel?: string;
  exclude?: readonly string[];
  timeoutMs?: number;
}

/**
 * Build a Merkle tree of `folderPath`, submit only the manifest to the
 * hosted service, and return a small summary of the resulting receipt.
 *
 * The file contents are read locally and hashed in 1 MiB streamed chunks;
 * they never enter the request body.
 */
export async function anchorFolder(
  folderPath: string,
  options: AnchorFolderOptions = {},
): Promise<AnchorFolderResult> {
  const tree = await MerkleTree.fromFolder(folderPath, {
    exclude: options.exclude,
  });
  const manifest = tree.manifest();
  const response = await submitManifest(manifest, {
    serverUrl: options.serverUrl,
    apiKey: options.apiKey,
    clientLabel: options.clientLabel,
    timeoutMs: options.timeoutMs,
  });
  return {
    receipt_id: response.receipt_id,
    root_hex: response.root_hex,
    leaf_count: response.leaf_count,
    calendars_ok: response.calendars_ok,
    calendars_total: response.calendars_total,
  };
}

export interface VerifyFolderOptions {
  serverUrl?: string;
  apiKey?: string;
  exclude?: readonly string[];
  timeoutMs?: number;
}

/**
 * Re-walk `folderPath`, recompute the Merkle root locally, and compare it
 * to the root recorded by the hosted service under `receiptId`.
 *
 * Returns true when the locally-recomputed root matches the server's
 * stored root. Throws on transport failure.
 */
export async function verifyFolder(
  folderPath: string,
  receiptId: string,
  options: VerifyFolderOptions = {},
): Promise<boolean> {
  const tree = await MerkleTree.fromFolder(folderPath, {
    exclude: options.exclude,
  });
  const localRoot = tree.rootHex();
  const remote = await fetchVerifyFolder(receiptId, {
    serverUrl: options.serverUrl,
    apiKey: options.apiKey,
    timeoutMs: options.timeoutMs,
  });
  // The manifest's root_hex is the ONLY acceptable remote side. A degraded
  // or partial response (missing manifest / missing root_hex) MUST NOT
  // verify — no fallback to receipt.hash_hex (VERIFIER_SPEC §4.2, audit D3).
  const remoteRoot = remote.manifest && remote.manifest.root_hex;
  if (!remoteRoot) return false;
  return localRoot === remoteRoot;
}

/**
 * Fetch a third-party inclusion proof for one file inside an anchored folder.
 */
export async function inclusionProof(
  receiptId: string,
  relPath: string,
  options: ClientOptions = {},
): Promise<InclusionProofResponse> {
  return fetchInclusionProof(receiptId, relPath, options);
}

/**
 * Stream `localFilePath` through SHA-256, then verify the resulting digest
 * against the supplied inclusion proof and root. Both `rootHex` may be the
 * 64-char hex string returned by `inclusionProof`.
 *
 * The verifier needs NO network access — once the proof and the root are
 * in hand, the check is purely local. This is the intended path for a
 * third-party recipient who wants to confirm that a file they hold
 * belonged to a folder anchored by someone else.
 *
 * Rejects with the underlying filesystem error (e.g. ENOENT) when
 * `localFilePath` does not exist — a missing local file is an I/O
 * precondition failure, not a "not included" verdict (audit D7; the
 * Python SDK raises FileNotFoundError for the same case). A malformed
 * proof or root still resolves `false` per VERIFIER_SPEC §4.1.
 */
export async function verifyInclusion(
  localFilePath: string,
  relPath: string,
  proof: ProofStep[],
  rootHex: string,
): Promise<boolean> {
  const fileHash = await streamSha256(localFilePath);
  return MerkleTree.verifyInclusion(fileHash, relPath, proof, rootHex);
}

function streamSha256(absolutePath: string): Promise<Uint8Array> {
  return new Promise((resolve, reject) => {
    const h = createHash("sha256");
    const stream = createReadStream(absolutePath, { highWaterMark: CHUNK_SIZE });
    stream.on("data", (chunk: Buffer | string) => {
      h.update(typeof chunk === "string" ? Buffer.from(chunk) : (chunk as Buffer));
    });
    stream.on("end", () => resolve(new Uint8Array(h.digest())));
    stream.on("error", reject);
  });
}
