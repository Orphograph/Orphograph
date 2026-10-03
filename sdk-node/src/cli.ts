#!/usr/bin/env node
// cli.ts — command-line entry for the Orphograph Node SDK.
//
// Usage:
//   orphograph anchor <folder> [--server URL] [--api-key KEY] [--label TEXT]
//   orphograph verify <folder> <receipt_id> [--server URL]
//   orphograph proof  <receipt_id> <rel_path> [--server URL]
//   (a bare -- ends the options; ORPHO_SERVER_URL is used when no --server is given)
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
  // How many leading positionals came before a bare "--". Everything after
  // "--" is an argument even if it starts with "-" (a rel_path "-x", a
  // receipt id "--a…").
  beforeDashDash: number;
  flags: Record<string, string | boolean>;
}

function parseArgs(argv: string[]): ParsedArgs {
  const positional: string[] = [];
  // No prototype: "--__proto__ URL" must land as an own (unknown) key, not
  // vanish into Object.prototype and leave the default server in place.
  const flags: Record<string, string | boolean> = Object.create(null);
  let beforeDashDash = -1;
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (beforeDashDash !== -1) {
      positional.push(a);
    } else if (a === "--") {
      beforeDashDash = positional.length;
    } else if (a.startsWith("--")) {
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
  return { positional, beforeDashDash: beforeDashDash === -1 ? positional.length : beforeDashDash, flags };
}

// Every option a subcommand reads. An unknown one used to be dropped without a
// word, so a typo or the Python CLI's --server-url sent the request to the
// live service instead of the server the caller named (cycle 9: three real
// anchors that way). Unknown options now stop the CLI before any request.
const KNOWN_FLAGS = new Set(["server", "server-url", "api-key", "label"]);
// Positionals each subcommand reads. An extra one (a stray URL meant for
// --server) is an error too, for the same reason. Short options (-s) are not
// options here at all; a 16-character receipt id may start with "-", so only
// one- or two-letter forms are refused as unknown options, and only before a
// bare "--" and only on subcommands that make a request (verify-inclusion is
// offline, so a real rel_path "-x" is just a path there).
const MAX_POSITIONAL: Record<string, number> = { anchor: 1, verify: 2, proof: 2, "verify-inclusion": 4 };
const SHORT_OPTION = /^-[A-Za-z]{1,2}$/;
// The server's receipt id format. A URL in the receipt id slot (`verify <dir>
// <URL>`, with the server forgotten) is refused here instead of being sent as
// an id to the default server.
const RECEIPT_ID = /^[A-Za-z0-9_-]{1,64}$/;
// A manifest rel_path never holds "://"; one that does is a server URL in the
// wrong slot (`proof <id> <URL>`).
const LOOKS_LIKE_URL = /^[A-Za-z][A-Za-z0-9+.-]*:\/\//;

// A server flag given with no value (`--server` at the end, `--server=`, or an
// unset variable in `--server "$URL"`) is refused too: an empty server fell
// through to the live default exactly like an unknown flag did. --api-key and
// --label only need a value after them; an explicit empty --api-key means no
// key (ORPHO_API_KEY is not read then), as in the Python CLI.
function badFlagValue(flags: Record<string, string | boolean>): string | undefined {
  for (const k of ["server", "server-url"]) {
    if (k in flags && (typeof flags[k] !== "string" || flags[k] === "")) return k;
  }
  for (const k of ["api-key", "label"]) {
    if (k in flags && typeof flags[k] !== "string") return k;
  }
  return undefined;
}

function sameServer(a: string, b: string): boolean {
  return a.replace(/\/+$/, "") === b.replace(/\/+$/, "");
}

// ORPHO_SERVER_URL is the Python CLI's environment form, and both packages
// install a command named `orphograph`; ignoring it sent a shell set up for
// one CLI to the live default with the other. Empty means unset.
function getServer(flags: Record<string, string | boolean>): string {
  const v = flags["server"] ?? flags["server-url"];
  if (typeof v === "string") return v;
  const env = process.env.ORPHO_SERVER_URL;
  return env && env.trim().length > 0 ? env.trim() : DEFAULT_SERVER_URL;
}

function getApiKey(flags: Record<string, string | boolean>): string | undefined {
  const v = flags["api-key"];
  if (typeof v === "string") return v.length > 0 ? v : undefined;
  const env = process.env.ORPHO_API_KEY;
  return env && env.length > 0 ? env : undefined;
}

function printUsage(): void {
  const usage = [
    "Usage:",
    "  orphograph anchor <folder> [--server URL] [--api-key KEY] [--label TEXT]",
    "  (--server-url is accepted for --server; any other option, or an extra argument, is an error)",
    "  orphograph verify <folder> <receipt_id> [--server URL]",
    "  orphograph proof  <receipt_id> <rel_path> [--server URL]",
    "  orphograph verify-inclusion <local_file> <rel_path> <proof.json> [root_hex]",
    "",
    "  A bare -- ends the options: orphograph proof <receipt_id> -- -x",
    "",
    "Environment:",
    "  ORPHO_SERVER_URL  server to use when no --server is given.",
    "  ORPHO_API_KEY     optional API key sent as X-Orpho-Api-Key.",
    "",
    "Privacy: file contents are read locally and never transmitted.",
    "Only the manifest (paths, sizes and SHA-256 digests) and any label cross the network.",
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
  // calendars_ok 0: no calendar accepted the root, so this receipt has no
  // Bitcoin commitment and never gets one. Exiting 0 let a CI gate pass it.
  // 2, not 1: exit 1 is the verify MISMATCH verdict (same rule as the Python CLI).
  if (!result.receipt_id) {
    process.stderr.write("orphograph: the service answered without a receipt.\n");
    return 2;
  }
  if (result.calendars_ok === 0) {
    process.stderr.write(
      "orphograph: no calendar accepted the root; this receipt has no Bitcoin commitment. Try again later.\n",
    );
    return 2;
  }
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
  // Contract shared with the Python CLI (sdk-python/orphograph/_cli.py):
  // exit 0 = match, 1 = mismatch, 2 = error — a crash must never wear the
  // mismatch code. root_hex is optional: omitted, the root inside proof.json
  // is used and the verdict says root_source "proof_json" (self-attested —
  // match it against the receipt before treating ok as meaningful); an
  // explicit argument always wins and echoes "argument".
  const localFile = args.positional[0];
  const relPath = args.positional[1];
  const proofFile = args.positional[2];
  const rootOverride = args.positional[3];
  if (!localFile || !relPath || !proofFile) {
    printUsage();
    return 2;
  }
  try {
    const raw = await readFile(proofFile, "utf-8");
    const parsed = JSON.parse(raw) as
      | { proof?: ProofStep[]; root_hex?: string; path?: string }
      | ProofStep[];
    const isBare = Array.isArray(parsed);
    const proof: ProofStep[] = isBare
      ? (parsed as ProofStep[])
      : ((parsed as { proof?: ProofStep[] }).proof ?? []);
    const embeddedRoot = isBare ? undefined : (parsed as { root_hex?: string }).root_hex;
    const embeddedPath = isBare ? undefined : (parsed as { path?: string }).path;
    const root = rootOverride ?? embeddedRoot;
    const source = rootOverride ? "argument" : "proof_json";
    if (!root) {
      process.stderr.write(
        JSON.stringify({
          error:
            "no root_hex: pass it as the 4th argument or use the JSON written by the proof subcommand",
        }) + "\n",
      );
      return 2;
    }
    const ok = await verifyInclusion(localFile, relPath, proof, root);
    process.stdout.write(
      JSON.stringify({ ok, root_hex: root, root_source: source }) + "\n",
    );
    // After the verdict, so an I/O or parse failure keeps stderr to its
    // single error line. Advisory only: the leaf hash binds relPath.
    if (typeof embeddedPath === "string" && embeddedPath !== relPath) {
      process.stderr.write(
        JSON.stringify({
          warning:
            "rel_path differs from the path recorded in proof_json; the leaf hash binds rel_path, so a mismatch verdict is expected",
          rel_path: relPath,
          proof_json_path: embeddedPath,
        }) + "\n",
      );
    }
    return ok ? 0 : 1;
  } catch (e) {
    const msg = e instanceof Error ? e.message : String(e);
    process.stderr.write(JSON.stringify({ error: msg }) + "\n");
    return 2;
  }
}

async function main(): Promise<number> {
  const argv = process.argv.slice(2);
  const subcommand = argv[0];
  const args = parseArgs(argv.slice(1));
  const unknown = Object.keys(args.flags).filter((k) => !KNOWN_FLAGS.has(k));
  const short = subcommand === "verify-inclusion"
    ? undefined
    : args.positional.slice(0, args.beforeDashDash).find((p) => SHORT_OPTION.test(p));
  if (unknown.length > 0 || short) {
    process.stderr.write(`unknown option ${unknown.length > 0 ? "--" + unknown[0] : short}\n`);
    printUsage();
    return 2;
  }
  const missing = badFlagValue(args.flags);
  if (missing) {
    process.stderr.write(`option --${missing} needs a value\n`);
    printUsage();
    return 2;
  }
  if (typeof args.flags["server"] === "string" && typeof args.flags["server-url"] === "string"
      && !sameServer(args.flags["server"], args.flags["server-url"])) {
    process.stderr.write("--server and --server-url name different servers; give one\n");
    printUsage();
    return 2;
  }
  const max = MAX_POSITIONAL[subcommand];
  if (max !== undefined && args.positional.length > max) {
    process.stderr.write(`unexpected argument: ${args.positional[max]}\n`);
    printUsage();
    return 2;
  }
  const idSlot = subcommand === "verify" ? 1 : subcommand === "proof" ? 0 : -1;
  const id = idSlot >= 0 ? args.positional[idSlot] : undefined;
  if (id !== undefined && !RECEIPT_ID.test(id)) {
    process.stderr.write(`not a receipt id: ${id}\n`);
    printUsage();
    return 2;
  }
  if (subcommand === "proof" && args.positional[1] !== undefined && LOOKS_LIKE_URL.test(args.positional[1])) {
    process.stderr.write(`rel_path looks like a server URL: ${args.positional[1]} (use --server)\n`);
    printUsage();
    return 2;
  }
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
    // 2, not 1: exit 1 is the verify/verify-inclusion MISMATCH verdict, and
    // a network failure or unreadable input must never impersonate it
    // (same rule as the Python CLI).
    process.stderr.write(`error: ${msg}\n`);
    return 2;
  }
}

main().then((code) => process.exit(code));
