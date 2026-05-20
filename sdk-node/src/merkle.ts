// merkle.ts — RFC 6962-compliant Merkle tree for folder anchoring.
//
// This module is the byte-for-byte counterpart to server/merkle.py and
// web/folder.js. The three implementations must produce the same root hex
// for the same input. The reference source is server/merkle.py at
// SHA-256 564dd480a4e793867c20c6fe22d265a3382674250023e8095b48b951db2d352d.
//
// Design (kept identical to the reference):
//
//   * Leaf:     SHA-256(0x00 || rel_path_utf8 || 0x00 || file_sha256)
//   * Internal: SHA-256(0x01 || left || right)
//   * Odd-level handling: the lone last node is PROMOTED to the next level
//     (RFC 6962). The tree never duplicates a node.
//   * Algorithm tag: "orphograph-merkle-v1-rfc6962".
//   * Streaming: files are hashed in 1 MiB chunks; no file is fully buffered.
//   * Empty folders are rejected.
//   * Symlinks are skipped (not followed). Hidden dotfiles are included.
//   * Paths are normalised to POSIX form before sorting.
//   * Sort key: UTF-8 byte order of the POSIX relative path.
//
// MIT — see LICENSE.

import { createHash } from "node:crypto";
import { createReadStream } from "node:fs";
import { lstat, readdir, stat } from "node:fs/promises";
import { posix as pathPosix, join, relative, sep } from "node:path";

export const ALGORITHM = "orphograph-merkle-v1-rfc6962";
export const VERSION = 1;
export const CHUNK_SIZE = 1024 * 1024; // 1 MiB

const LEAF_PREFIX = Uint8Array.from([0x00]);
const INTERNAL_PREFIX = Uint8Array.from([0x01]);
const PATH_SEPARATOR = Uint8Array.from([0x00]);

/**
 * Default deny-list — files the office considers incidental to the evidence
 * itself (OS detritus, editor backups, build caches). Pass `[]` to disable.
 */
export const DEFAULT_EXCLUDE: readonly string[] = [
  ".DS_Store",
  "Thumbs.db",
  "desktop.ini",
  ".git/*",
  "node_modules/*",
  "__pycache__/*",
  "*.tmp",
  "*.swp",
  "*.swo",
  "~$*",
];

export interface LeafMeta {
  path: string;
  file_sha256_hex: string;
  leaf_hex: string;
  size_bytes: number;
}

export interface Manifest {
  algorithm: string;
  version: number;
  root_hex: string;
  leaves: LeafMeta[];
}

export type ProofStep = ["L" | "R", string];

// ─── helpers ────────────────────────────────────────────────────────────

function toHex(bytes: Uint8Array): string {
  let out = "";
  for (let i = 0; i < bytes.length; i++) {
    out += bytes[i].toString(16).padStart(2, "0");
  }
  return out;
}

function fromHex(hex: string): Uint8Array {
  if (typeof hex !== "string" || hex.length % 2 !== 0) {
    throw new Error("invalid hex string");
  }
  const out = new Uint8Array(hex.length / 2);
  for (let i = 0; i < out.length; i++) {
    const byte = parseInt(hex.substr(i * 2, 2), 16);
    if (Number.isNaN(byte)) throw new Error("invalid hex string");
    out[i] = byte;
  }
  return out;
}

function concatBytes(...parts: Uint8Array[]): Uint8Array {
  let total = 0;
  for (const p of parts) total += p.length;
  const out = new Uint8Array(total);
  let off = 0;
  for (const p of parts) {
    out.set(p, off);
    off += p.length;
  }
  return out;
}

function sha256(bytes: Uint8Array): Uint8Array {
  const h = createHash("sha256");
  h.update(bytes);
  return new Uint8Array(h.digest());
}

/**
 * fnmatch-equivalent: translates a glob pattern to a RegExp.
 * Supports `*`, `?`, and character classes — matches Python fnmatch.
 */
function fnmatchToRegex(pat: string): RegExp {
  let re = "";
  let i = 0;
  while (i < pat.length) {
    const c = pat[i];
    if (c === "*") {
      re += ".*";
    } else if (c === "?") {
      re += ".";
    } else if (c === "[") {
      let j = i + 1;
      if (j < pat.length && pat[j] === "!") j++;
      if (j < pat.length && pat[j] === "]") j++;
      while (j < pat.length && pat[j] !== "]") j++;
      if (j >= pat.length) {
        re += "\\[";
      } else {
        let cls = pat.slice(i + 1, j);
        if (cls.startsWith("!")) cls = "^" + cls.slice(1);
        re += `[${cls}]`;
        i = j;
      }
    } else if (/[.\\+(){}^$|]/.test(c)) {
      re += "\\" + c;
    } else {
      re += c;
    }
    i++;
  }
  return new RegExp(`^${re}$`);
}

const _fnmatchCache = new Map<string, RegExp>();
function fnmatch(name: string, pat: string): boolean {
  let re = _fnmatchCache.get(pat);
  if (!re) {
    re = fnmatchToRegex(pat);
    _fnmatchCache.set(pat, re);
  }
  return re.test(name);
}

function matchesAny(relPath: string, patterns: readonly string[]): boolean {
  const lastSlash = relPath.lastIndexOf("/");
  const name = lastSlash === -1 ? relPath : relPath.slice(lastSlash + 1);
  for (const pat of patterns) {
    if (pat.includes("/")) {
      if (fnmatch(relPath, pat)) return true;
      // Also match if any ancestor segment is the prefix dir.
      // e.g. ``.git/*`` should also catch ``.git/sub/file``.
      let prefix = pat;
      while (prefix.endsWith("*")) prefix = prefix.slice(0, -1);
      while (prefix.endsWith("/")) prefix = prefix.slice(0, -1);
      if (prefix && (relPath === prefix || relPath.startsWith(prefix + "/"))) {
        return true;
      }
    } else {
      if (fnmatch(name, pat)) return true;
    }
  }
  return false;
}

function utf8(s: string): Uint8Array {
  return new TextEncoder().encode(s);
}

function byteCompare(a: Uint8Array, b: Uint8Array): number {
  const len = Math.min(a.length, b.length);
  for (let i = 0; i < len; i++) {
    if (a[i] !== b[i]) return a[i] - b[i];
  }
  return a.length - b.length;
}

// ─── hashing ────────────────────────────────────────────────────────────

/**
 * Stream a file through SHA-256 in 1 MiB chunks.
 * Returns [digest, size] — digest is exactly 32 bytes.
 */
export async function hashFileStream(
  absolutePath: string,
): Promise<{ digest: Uint8Array; size: number }> {
  return new Promise((resolve, reject) => {
    const h = createHash("sha256");
    let size = 0;
    const stream = createReadStream(absolutePath, { highWaterMark: CHUNK_SIZE });
    stream.on("data", (chunk: Buffer | string) => {
      const buf =
        typeof chunk === "string"
          ? Buffer.from(chunk)
          : (chunk as Buffer);
      size += buf.length;
      h.update(buf);
    });
    stream.on("end", () => {
      resolve({ digest: new Uint8Array(h.digest()), size });
    });
    stream.on("error", reject);
  });
}

export function leafHash(relPath: string, fileDigest: Uint8Array): Uint8Array {
  if (fileDigest.length !== 32) {
    throw new Error("file_digest must be exactly 32 bytes");
  }
  return sha256(concatBytes(LEAF_PREFIX, utf8(relPath), PATH_SEPARATOR, fileDigest));
}

export function internalHash(left: Uint8Array, right: Uint8Array): Uint8Array {
  if (left.length !== 32 || right.length !== 32) {
    throw new Error("internal hash inputs must be 32 bytes");
  }
  return sha256(concatBytes(INTERNAL_PREFIX, left, right));
}

// ─── folder walk ────────────────────────────────────────────────────────

interface Entry {
  relPosix: string;
  abs: string;
}

async function walkFolder(
  root: string,
  exclude: readonly string[],
): Promise<Entry[]> {
  const entries: Entry[] = [];

  async function walk(dir: string): Promise<void> {
    let dirents;
    try {
      dirents = await readdir(dir, { withFileTypes: true });
    } catch {
      return;
    }
    for (const dirent of dirents) {
      const abs = join(dir, dirent.name);
      // Skip symlinks entirely — neither follow nor record.
      let lst;
      try {
        lst = await lstat(abs);
      } catch {
        continue;
      }
      if (lst.isSymbolicLink()) continue;
      if (lst.isDirectory()) {
        await walk(abs);
      } else if (lst.isFile()) {
        const rel = relative(root, abs);
        // Normalise to POSIX (forward slashes) regardless of host OS.
        const relPosix = sep === "/" ? rel : rel.split(sep).join("/");
        if (matchesAny(relPosix, exclude)) continue;
        entries.push({ relPosix, abs });
      }
    }
  }

  const rs = await stat(root);
  if (!rs.isDirectory()) {
    throw new Error(`not a directory: ${root}`);
  }
  await walk(root);

  // UTF-8 byte order on the POSIX path string.
  entries.sort((a, b) => byteCompare(utf8(a.relPosix), utf8(b.relPosix)));
  return entries;
}

// ─── tree build ─────────────────────────────────────────────────────────

function buildLevels(leaves: Uint8Array[]): Uint8Array[][] {
  if (leaves.length === 0) {
    throw new Error("cannot build a tree with no leaves");
  }
  const levels: Uint8Array[][] = [leaves.slice()];
  while (levels[levels.length - 1].length > 1) {
    const cur = levels[levels.length - 1];
    const nxt: Uint8Array[] = [];
    let i = 0;
    while (i + 1 < cur.length) {
      nxt.push(internalHash(cur[i], cur[i + 1]));
      i += 2;
    }
    if (i < cur.length) {
      // Odd remainder: PROMOTE the lone node unchanged.
      nxt.push(cur[i]);
    }
    levels.push(nxt);
  }
  return levels;
}

// ─── MerkleTree class ───────────────────────────────────────────────────

export class MerkleTree {
  private readonly _leavesMeta: LeafMeta[];
  private readonly _levels: Uint8Array[][];

  private constructor(leavesMeta: LeafMeta[], levels: Uint8Array[][]) {
    this._leavesMeta = leavesMeta;
    this._levels = levels;
  }

  /**
   * Build a tree by walking `root` and streaming each file's bytes through
   * SHA-256. Pass `exclude: []` to disable the default deny-list; passing a
   * custom list replaces the defaults (it does not extend them).
   */
  static async fromFolder(
    root: string,
    options: { exclude?: readonly string[] } = {},
  ): Promise<MerkleTree> {
    const patterns =
      options.exclude === undefined ? DEFAULT_EXCLUDE : options.exclude;
    const entries = await walkFolder(root, patterns);
    if (entries.length === 0) {
      throw new Error("Empty folders are not supported in v1.");
    }
    const leavesMeta: LeafMeta[] = [];
    const leafHashes: Uint8Array[] = [];
    for (const { relPosix, abs } of entries) {
      const { digest, size } = await hashFileStream(abs);
      const leaf = leafHash(relPosix, digest);
      leavesMeta.push({
        path: relPosix,
        file_sha256_hex: toHex(digest),
        leaf_hex: toHex(leaf),
        size_bytes: size,
      });
      leafHashes.push(leaf);
    }
    const levels = buildLevels(leafHashes);
    return new MerkleTree(leavesMeta, levels);
  }

  /**
   * Reconstruct a tree from a manifest. Every leaf is recomputed from
   * (path, file_sha256_hex); the recomputed root MUST equal the manifest's
   * root_hex or this throws.
   */
  static fromManifest(manifest: Manifest): MerkleTree {
    if (!manifest || typeof manifest !== "object") {
      throw new Error("manifest must be an object");
    }
    if (manifest.algorithm !== ALGORITHM) {
      throw new Error(`unsupported algorithm: ${String(manifest.algorithm)}`);
    }
    if (manifest.version !== VERSION) {
      throw new Error(`unsupported version: ${String(manifest.version)}`);
    }
    const leaves = manifest.leaves;
    if (!Array.isArray(leaves) || leaves.length === 0) {
      throw new Error("manifest leaves must be a non-empty list");
    }
    const leavesMeta: LeafMeta[] = [];
    const leafHashes: Uint8Array[] = [];
    for (const entry of leaves) {
      const path = entry.path;
      const fileHex = entry.file_sha256_hex;
      const leafHex = entry.leaf_hex;
      const size = Number(entry.size_bytes);
      const fileDigest = fromHex(fileHex);
      const recomputed = leafHash(path, fileDigest);
      if (toHex(recomputed) !== leafHex) {
        throw new Error(
          `manifest leaf hash mismatch for ${JSON.stringify(path)}: ` +
            "the stored leaf does not derive from the stored file hash",
        );
      }
      leavesMeta.push({
        path,
        file_sha256_hex: fileHex,
        leaf_hex: leafHex,
        size_bytes: size,
      });
      leafHashes.push(recomputed);
    }
    const levels = buildLevels(leafHashes);
    if (toHex(levels[levels.length - 1][0]) !== manifest.root_hex) {
      throw new Error("manifest root_hex does not match recomputed root");
    }
    return new MerkleTree(leavesMeta, levels);
  }

  root(): Uint8Array {
    return this._levels[this._levels.length - 1][0];
  }

  rootHex(): string {
    return toHex(this.root());
  }

  leafCount(): number {
    return this._leavesMeta.length;
  }

  manifest(): Manifest {
    return {
      algorithm: ALGORITHM,
      version: VERSION,
      root_hex: this.rootHex(),
      leaves: this._leavesMeta.map((m) => ({ ...m })),
    };
  }

  /**
   * Inclusion proof for one POSIX-relative path. Returns an ordered list of
   * `[direction, sibling_hex]` tuples from leaf upward. `"L"` means the
   * sibling sits on the LEFT of the running hash; `"R"` means on the right.
   * Promoted (lone-last) nodes contribute no proof step at that level.
   */
  inclusionProof(filePath: string): ProofStep[] {
    let idx = -1;
    for (let i = 0; i < this._leavesMeta.length; i++) {
      if (this._leavesMeta[i].path === filePath) {
        idx = i;
        break;
      }
    }
    if (idx === -1) {
      throw new Error(`path not in tree: ${JSON.stringify(filePath)}`);
    }
    const proof: ProofStep[] = [];
    for (let l = 0; l < this._levels.length - 1; l++) {
      const level = this._levels[l];
      // Was this node the lone-last (promoted) node at this level?
      if (idx === level.length - 1 && level.length % 2 === 1) {
        idx = Math.floor(idx / 2);
        continue;
      }
      if (idx % 2 === 0) {
        // Current is left, sibling is on the right.
        proof.push(["R", toHex(level[idx + 1])]);
      } else {
        // Current is right, sibling is on the left.
        proof.push(["L", toHex(level[idx - 1])]);
      }
      idx = Math.floor(idx / 2);
    }
    return proof;
  }

  /**
   * Verify a file's inclusion against a known root.
   *
   * @param fileHash raw SHA-256 of the file content (not the leaf).
   * @param relPath POSIX path under which the file was committed.
   * @param proof list of [direction, sibling_hex] tuples.
   * @param root 32-byte tree root (Uint8Array) or 64-char hex string.
   */
  static verifyInclusion(
    fileHash: Uint8Array,
    relPath: string,
    proof: ProofStep[],
    root: Uint8Array | string,
  ): boolean {
    let rootBytes: Uint8Array;
    if (typeof root === "string") {
      try {
        rootBytes = fromHex(root);
      } catch {
        return false;
      }
    } else {
      rootBytes = root;
    }
    if (rootBytes.length !== 32) return false;
    if (!(fileHash instanceof Uint8Array) || fileHash.length !== 32) return false;
    let current: Uint8Array;
    try {
      current = leafHash(relPath, fileHash);
    } catch {
      return false;
    }
    if (!Array.isArray(proof)) return false;
    for (const step of proof) {
      if (!Array.isArray(step) || step.length !== 2) return false;
      const [direction, siblingHex] = step;
      if (direction !== "L" && direction !== "R") return false;
      let sibling: Uint8Array;
      try {
        sibling = fromHex(siblingHex);
      } catch {
        return false;
      }
      if (sibling.length !== 32) return false;
      current =
        direction === "L"
          ? internalHash(sibling, current)
          : internalHash(current, sibling);
    }
    if (current.length !== rootBytes.length) return false;
    for (let i = 0; i < current.length; i++) {
      if (current[i] !== rootBytes[i]) return false;
    }
    return true;
  }
}

// Re-exports for direct utility use.
export { toHex as bytesToHex, fromHex as hexToBytes };

// Path-normalisation helper for CLI callers and tests.
export function toPosixPath(p: string): string {
  // pathPosix.normalize plus a backslash→slash conversion handles Windows.
  return pathPosix.normalize(p.split(sep).join("/"));
}
