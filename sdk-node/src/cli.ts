#!/usr/bin/env node
// cli.ts — command-line entry for the Orphograph Node SDK.
//
// Usage:
//   orphograph anchor <folder> [--server URL] [--api-key KEY] [--label TEXT]
//   orphograph verify <folder> <receipt_id> [--server URL]
//   orphograph proof  <receipt_id> <rel_path> [--server URL]
//   orphograph verify-inclusion <local_file> <rel_path> <proof.json> <root_hex>
//
// `anchor` and `verify` connect to the hosted service. `verify-inclusion`
// is a purely local check that needs no network access.
//
// MIT — see LICENSE.

import { readFile } from "node:fs/promises";

import {
  anchorFolder,
  verifyFolder,
  inclusionProof,
  verifyInclusion,
  DEFAULT_SERVER_URL,
} from "./index.js";
import type { ProofStep } from "./merkle.js";

interface ParsedArgs {
  positional: string[];
  flags: Record<string, string | boolean>;
}

function parseArgs(argv: string[]): ParsedArgs {
  const positional: string[] = [];
  const flags: Record<string, string | boolean> = {};
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a.startsWith("--")) {
      const eq = a.indexOf("=");
      if (eq !== -1) {
        flags[a.slice(2, eq)] = a.slice(eq + 1);
      } else {
        const key = a.slice(2);
        const next = argv[i + 1];
        if (next !== undefined && !next.startsWith("--")) {
          flags[key] = next;
          i++;
        } else {
          flags[key] = true;
        }
      }
    } else {
      positional.push(a);
    }
  }
  return { positional, flags };
}

function getServer(flags: Record<string, string | boolean>): string {
  const v = flags["server"];
  return typeof v === "string" ? v : DEFAULT_SERVER_URL;
}

function getApiKey(flags: Record<string, string | boolean>): string | undefined {
  const v = flags["api-key"];
  if (typeof v === "string") return v;
  const env = process.env.ORPHO_API_KEY;
  return env && env.length > 0 ? env : undefined;
}

function printUsage(): void {
  const usage = [
    "Usage:",
    "  orphograph anchor <folder> [--server URL] [--api-key KEY] [--label TEXT]",
    "  orphograph verify <folder> <receipt_id> [--server URL]",
    "  orphograph proof  <receipt_id> <rel_path> [--server URL]",
    "  orphograph verify-inclusion <local_file> <rel_path> <proof.json> <root_hex>",
    "",
    "Environment:",
    "  ORPHO_API_KEY  optional API key sent as X-Orpho-Api-Key.",
    "",
    "Privacy: file contents are read locally and never transmitted.",
    "Only the manifest (paths plus SHA-256 digests) crosses the network.",
  ].join("\n");
  process.stderr.write(usage + "\n");
}

async function cmdAnchor(args: ParsedArgs): Promise<number> {
  const folder = args.positional[0];
  if (!folder) {
    printUsage();
    return 2;
  }
  const result = await anchorFolder(folder, {
    serverUrl: getServer(args.flags),
    apiKey: getApiKey(args.flags),
    clientLabel:
      typeof args.flags["label"] === "string"
        ? (args.flags["label"] as string)
        : undefined,
  });
  process.stdout.write(JSON.stringify(result) + "\n");
  return 0;
}

async function cmdVerify(args: ParsedArgs): Promise<number> {
  const folder = args.positional[0];
  const receiptId = args.positional[1];
  if (!folder || !receiptId) {
    printUsage();
    return 2;
  }
  const ok = await verifyFolder(folder, receiptId, {
    serverUrl: getServer(args.flags),
    apiKey: getApiKey(args.flags),
  });
  process.stdout.write(JSON.stringify({ ok }) + "\n");
  return ok ? 0 : 1;
}

async function cmdProof(args: ParsedArgs): Promise<number> {
  const receiptId = args.positional[0];
  const relPath = args.positional[1];
  if (!receiptId || !relPath) {
    printUsage();
    return 2;
  }
  const proof = await inclusionProof(receiptId, relPath, {
    serverUrl: getServer(args.flags),
    apiKey: getApiKey(args.flags),
  });
  process.stdout.write(JSON.stringify(proof) + "\n");
  return 0;
}

async function cmdVerifyInclusion(args: ParsedArgs): Promise<number> {
  const localFile = args.positional[0];
  const relPath = args.positional[1];
  const proofFile = args.positional[2];
  const rootHex = args.positional[3];
  if (!localFile || !relPath || !proofFile || !rootHex) {
    printUsage();
    return 2;
  }
  const raw = await readFile(proofFile, "utf-8");
  const parsed = JSON.parse(raw) as { proof?: ProofStep[] } | ProofStep[];
  const proof: ProofStep[] = Array.isArray(parsed)
    ? (parsed as ProofStep[])
    : ((parsed as { proof?: ProofStep[] }).proof ?? []);
  const ok = await verifyInclusion(localFile, relPath, proof, rootHex);
  process.stdout.write(JSON.stringify({ ok }) + "\n");
  return ok ? 0 : 1;
}

async function main(): Promise<number> {
  const argv = process.argv.slice(2);
  const subcommand = argv[0];
  const args = parseArgs(argv.slice(1));
  try {
    switch (subcommand) {
      case "anchor":
        return await cmdAnchor(args);
      case "verify":
        return await cmdVerify(args);
      case "proof":
        return await cmdProof(args);
      case "verify-inclusion":
        return await cmdVerifyInclusion(args);
      case "--help":
      case "-h":
      case "help":
      case undefined:
        printUsage();
        return subcommand === undefined ? 2 : 0;
      default:
        process.stderr.write(`unknown subcommand: ${subcommand}\n`);
        printUsage();
        return 2;
    }
  } catch (e) {
    const msg = e instanceof Error ? e.message : String(e);
    process.stderr.write(`error: ${msg}\n`);
    return 1;
  }
}

main().then((code) => process.exit(code));
