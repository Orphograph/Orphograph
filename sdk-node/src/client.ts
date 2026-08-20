// client.ts — minimal HTTP transport for the Orphograph hosted service.
//
// PRIVACY CONTRACT: this module NEVER sends file contents over the network.
// The body of POST /api/anchor_folder is the manifest only — paths plus
// SHA-256 digests plus the Merkle root. The raw bytes of the files being
// anchored are read locally by src/merkle.ts and never leave this process.
//
// Transport: node:http and node:https from the standard library. There is
// no dependency on third-party HTTP clients (no fetch polyfill, no axios).
// This keeps the install footprint to one package and makes auditing the
// data path trivial.
//
// MIT — see LICENSE.

import { request as httpRequest } from "node:http";
import { request as httpsRequest } from "node:https";
import type { IncomingMessage } from "node:http";
import { URL } from "node:url";

import type { Manifest, ProofStep } from "./merkle.js";

export interface ClientOptions {
  serverUrl?: string;
  apiKey?: string;
  clientLabel?: string;
  timeoutMs?: number;
}

export interface AnchorResponse {
  receipt_id: string;
  root_hex: string;
  leaf_count: number;
  kind: string;
  merkle_algorithm: string;
  calendars_ok: number;
  calendars_total: number;
  created_at: string;
}

export interface VerifyFolderResponse {
  receipt: {
    receipt_id: string;
    hash_hex: string;
    kind: string;
    leaf_count?: number;
    found?: boolean;
    [key: string]: unknown;
  };
  manifest: Manifest;
}

export interface InclusionProofResponse {
  receipt_id: string;
  root_hex: string;
  path: string;
  file_sha256_hex: string;
  merkle_algorithm: string;
  proof: ProofStep[];
}

export const DEFAULT_SERVER_URL = "https://orphograph.com";
const DEFAULT_TIMEOUT_MS = 60_000;
// Honest, self-identifying User-Agent. NEVER a browser-spoofing string.
//
// The comment that used to sit here claimed the service "sits behind a CDN
// whose default-deny posture blocks scripted clients identifying themselves
// as such" and that "only the leading Mozilla/5.0 appeases the gateway".
// Measured 2026-08-20 against https://orphograph.com/api/health:
//
//   Python-urllib/3.11 ........................ 403
//   no User-Agent header at all ............... 200
//   curl/8.7.1 ................................ 200
//   a named agent like this one ............... 200
//
// The premise was right and the conclusion was wrong: the gateway blocks one
// literal token, not scripted clients as a class. test/client.test.ts has
// asserted `ua.startsWith("orphograph-node/")` this whole time and has been
// FAILING, unnoticed, because no workflow ran this suite.
const USER_AGENT = "orphograph-node/0.1.0 (+https://orphograph.com)";

interface HttpResult {
  status: number;
  body: string;
}

function postJson(
  url: URL,
  body: string,
  headers: Record<string, string>,
  timeoutMs: number,
): Promise<HttpResult> {
  return new Promise((resolve, reject) => {
    const isHttps = url.protocol === "https:";
    const req = (isHttps ? httpsRequest : httpRequest)(
      {
        method: "POST",
        hostname: url.hostname,
        port: url.port || (isHttps ? 443 : 80),
        path: url.pathname + url.search,
        headers: {
          "Content-Type": "application/json",
          "Content-Length": Buffer.byteLength(body).toString(),
          "User-Agent": USER_AGENT,
          ...headers,
        },
      },
      (res: IncomingMessage) => collectResponse(res, resolve, reject),
    );
    req.on("error", reject);
    req.setTimeout(timeoutMs, () => {
      req.destroy(new Error(`request timed out after ${timeoutMs}ms`));
    });
    req.write(body);
    req.end();
  });
}

function getJson(
  url: URL,
  headers: Record<string, string>,
  timeoutMs: number,
): Promise<HttpResult> {
  return new Promise((resolve, reject) => {
    const isHttps = url.protocol === "https:";
    const req = (isHttps ? httpsRequest : httpRequest)(
      {
        method: "GET",
        hostname: url.hostname,
        port: url.port || (isHttps ? 443 : 80),
        path: url.pathname + url.search,
        headers: {
          "User-Agent": USER_AGENT,
          ...headers,
        },
      },
      (res: IncomingMessage) => collectResponse(res, resolve, reject),
    );
    req.on("error", reject);
    req.setTimeout(timeoutMs, () => {
      req.destroy(new Error(`request timed out after ${timeoutMs}ms`));
    });
    req.end();
  });
}

function collectResponse(
  res: IncomingMessage,
  resolve: (v: HttpResult) => void,
  reject: (e: Error) => void,
): void {
  const chunks: Buffer[] = [];
  res.on("data", (c: Buffer) => chunks.push(c));
  res.on("end", () => {
    const body = Buffer.concat(chunks).toString("utf-8");
    resolve({ status: res.statusCode || 0, body });
  });
  res.on("error", reject);
}

function parseJsonOrThrow(text: string, status: number): unknown {
  try {
    return JSON.parse(text);
  } catch {
    throw new Error(
      `server returned non-JSON response (status ${status}): ${text.slice(0, 200)}`,
    );
  }
}

function authHeaders(apiKey: string | undefined): Record<string, string> {
  return apiKey ? { "X-Orpho-Api-Key": apiKey } : {};
}

export interface Transport {
  postJson: typeof postJson;
  getJson: typeof getJson;
}

const defaultTransport: Transport = { postJson, getJson };

/**
 * POST a manifest to /api/anchor_folder. Returns the receipt object.
 * The file contents are NOT in the request body — only the manifest is.
 */
export async function submitManifest(
  manifest: Manifest,
  options: ClientOptions = {},
  transport: Transport = defaultTransport,
): Promise<AnchorResponse> {
  const base = options.serverUrl || DEFAULT_SERVER_URL;
  const url = new URL("/api/anchor_folder", base);
  const timeoutMs = options.timeoutMs ?? DEFAULT_TIMEOUT_MS;
  const payload: Record<string, unknown> = { manifest };
  if (options.clientLabel) {
    payload.client_label = options.clientLabel.slice(0, 200);
  }
  const { status, body } = await transport.postJson(
    url,
    JSON.stringify(payload),
    authHeaders(options.apiKey),
    timeoutMs,
  );
  if (status < 200 || status >= 300) {
    let detail = body;
    try {
      const parsed = JSON.parse(body) as { error?: string };
      if (parsed && typeof parsed.error === "string") detail = parsed.error;
    } catch {
      /* not JSON; keep raw body */
    }
    throw new Error(`anchor failed (HTTP ${status}): ${detail}`);
  }
  return parseJsonOrThrow(body, status) as AnchorResponse;
}

/**
 * GET /api/verify_folder/<receipt_id>. Returns the receipt and the manifest.
 */
export async function fetchVerifyFolder(
  receiptId: string,
  options: ClientOptions = {},
  transport: Transport = defaultTransport,
): Promise<VerifyFolderResponse> {
  const base = options.serverUrl || DEFAULT_SERVER_URL;
  const url = new URL(`/api/verify_folder/${encodeURIComponent(receiptId)}`, base);
  const timeoutMs = options.timeoutMs ?? DEFAULT_TIMEOUT_MS;
  const { status, body } = await transport.getJson(
    url,
    authHeaders(options.apiKey),
    timeoutMs,
  );
  if (status < 200 || status >= 300) {
    throw new Error(`verify failed (HTTP ${status}): ${body.slice(0, 200)}`);
  }
  return parseJsonOrThrow(body, status) as VerifyFolderResponse;
}

/**
 * GET /api/inclusion_proof?receipt_id=...&path=...
 */
export async function fetchInclusionProof(
  receiptId: string,
  relPath: string,
  options: ClientOptions = {},
  transport: Transport = defaultTransport,
): Promise<InclusionProofResponse> {
  const base = options.serverUrl || DEFAULT_SERVER_URL;
  const url = new URL("/api/inclusion_proof", base);
  url.searchParams.set("receipt_id", receiptId);
  url.searchParams.set("path", relPath);
  const timeoutMs = options.timeoutMs ?? DEFAULT_TIMEOUT_MS;
  const { status, body } = await transport.getJson(
    url,
    authHeaders(options.apiKey),
    timeoutMs,
  );
  if (status < 200 || status >= 300) {
    throw new Error(`inclusion proof failed (HTTP ${status}): ${body.slice(0, 200)}`);
  }
  return parseJsonOrThrow(body, status) as InclusionProofResponse;
}
