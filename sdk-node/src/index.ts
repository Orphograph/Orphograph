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
import { stat } from "node:fs/promises";
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
  return (await verifyFolderDetail(folderPath, receiptId, options)).match;
}

export interface VerifyFolderDetail {
  /** The locally recomputed root equals the receipt's manifest root. */
  match: boolean;
  /**
   * How many calendars hold a proof for the receipt, as the service reports
   * it; null when the answer does not say. 0 means the receipt has no
   * Bitcoin commitment and never gets one: matching bytes, no time bound.
   */
  calendarsOk: number | null;
  /**
   * The service's answer carried a manifest root_hex. false means the answer
   * was broken or partial: `match` is then false (VERIFIER_SPEC §4.2, no
   * fallback), but nothing was compared, so a caller must not report it as
   * a mismatch. The CLI's verify exits 2 on it.
   */
  rootServed: boolean;
}

/**
 * verifyFolder plus the receipt's calendars_ok, so a caller can refuse a
 * receipt no calendar accepted (the CLI's verify does).
 */
export async function verifyFolderDetail(
  folderPath: string,
  receiptId: string,
  options: VerifyFolderOptions = {},
): Promise<VerifyFolderDetail> {
  // The folder is checked before the request (the walk now follows it, to
  // read the manifest's scope): a typo in the path fails without touching
  // the network, as it did when the walk came first.
  if (!(await stat(folderPath)).isDirectory()) {
    throw new Error(`not a directory: ${folderPath}`);
  }
  const remote = await fetchVerifyFolder(receiptId, {
    serverUrl: options.serverUrl,
    apiKey: options.apiKey,
    timeoutMs: options.timeoutMs,
  });
  const receipt = remote && typeof remote === "object" ? remote.receipt : undefined;
  const cal = receipt && typeof receipt === "object" ? receipt.calendars_ok : undefined;
  const calendarsOk = typeof cal === "number" ? cal : null;
  // The manifest's root_hex is the ONLY acceptable remote side. A degraded
  // or partial response (missing manifest / missing root_hex) MUST NOT
  // verify — no fallback to receipt.hash_hex (VERIFIER_SPEC §4.2, audit D3).
  const manifest: unknown = remote && typeof remote === "object" ? remote.manifest : undefined;
  const m = manifest && typeof manifest === "object" ? (manifest as Record<string, unknown>) : {};
  const remoteRoot = m.root_hex;
  if (typeof remoteRoot !== "string" || !remoteRoot) {
    return { match: false, calendarsOk, rootServed: false };
  }
  // The manifest is authoritative for its own scope, as in the Python SDK:
  // a folder anchored with custom excludes re-derives its root only with the
  // same patterns, which the manifest records under scope.exclude. The
  // caller's `exclude` is the fallback for manifests issued before scope.
  let exclude = options.exclude;
  const scope = m.scope;
  if (scope && typeof scope === "object" && !Array.isArray(scope)) {
    const recorded = (scope as Record<string, unknown>).exclude;
    if (Array.isArray(recorded) && recorded.every((p) => typeof p === "string")) {
      exclude = recorded as string[];
    }
  }
  const tree = await MerkleTree.fromFolder(folderPath, { exclude });
  return { match: tree.rootHex() === remoteRoot, calendarsOk, rootServed: true };
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
