#!/usr/bin/env python3
# AUTO-COPIED from server/merkle.py — keep in sync.
# Source SHA-256 (server/merkle.py at copy time):
#   564dd480a4e793867c20c6fe22d265a3382674250023e8095b48b951db2d352d
# If the upstream file changes, recompute with:
#   shasum -a 256 server/merkle.py
# and update both this banner and this file. The SDK refuses to drift
# silently from the server's reference implementation.
"""merkle.py — RFC 6962-compliant Merkle tree for folder anchoring.

The office uses this module to commit a whole folder of evidence to a single
32-byte root, which is then submitted to OpenTimestamps the same way a single
file hash is. Every file's path is bound into its leaf so that renaming a file
changes the root — paths are evidence, not incidental metadata.

Design notes (intentional, formal):

  * Leaf:     SHA-256(0x00 || rel_path_utf8 || 0x00 || file_sha256)
  * Internal: SHA-256(0x01 || left || right)
  * Odd-level handling: the lone last node is PROMOTED to the next level
    (RFC 6962). The tree never duplicates a node — duplication produces the
    CVE-2012-2459 second-preimage ambiguity, which the office rejects.
  * Algorithm tag: "orphograph-merkle-v1-rfc6962" — embedded in every manifest
    so a future v2 can be distinguished without ambiguity.
  * Streaming: files are hashed in 1 MiB chunks; no file is ever fully buffered.
  * Empty folders are rejected. A single-file folder yields root == leaf hash.
  * Symlinks are skipped (not followed). Hidden dotfiles are included by
    default — evidentiary cases often need them.
  * Paths are normalised to POSIX form (forward slashes) before sorting.
    Unicode normalisation is NOT performed; the receipt is committed as-is
    in NFC by convention. This is a documented v1 limitation.

This module is MIT licensed and uses only the Python standard library.
"""
from __future__ import annotations

import fnmatch
import hashlib
import json
import os
from pathlib import Path
from typing import Iterable

ALGORITHM = "orphograph-merkle-v1-rfc6962"
VERSION = 1
CHUNK_SIZE = 1024 * 1024  # 1 MiB

LEAF_PREFIX = b"\x00"
INTERNAL_PREFIX = b"\x01"

# Default deny-list — files the office considers incidental to the evidence
# itself (OS detritus, editor backups, build caches). The caller may supply a
# different list; supplying [] disables exclusion entirely.
DEFAULT_EXCLUDE = (
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
)


def _matches_any(rel_path: str, patterns: Iterable[str]) -> bool:
    """Return True if rel_path is excluded by any glob pattern.

    A pattern with no slash matches against the basename only (so ``.DS_Store``
    catches the file at any depth). A pattern with a slash matches against the
    full POSIX relative path (so ``.git/*`` catches everything inside .git).
    """
    name = rel_path.rsplit("/", 1)[-1]
    for pat in patterns:
        if "/" in pat:
            if fnmatch.fnmatch(rel_path, pat):
                return True
            # Also match if any ancestor segment is the prefix dir.
            # e.g. ``.git/*`` should also catch ``.git/sub/file``.
            prefix = pat.rstrip("*").rstrip("/")
            if prefix and (rel_path == prefix or rel_path.startswith(prefix + "/")):
                return True
        else:
            if fnmatch.fnmatch(name, pat):
                return True
    return False


def _hash_file(path: Path) -> tuple[bytes, int]:
    """Stream a file through SHA-256 in 1 MiB chunks. Returns (digest, size)."""
    h = hashlib.sha256()
    size = 0
    with path.open("rb") as f:
        while True:
            chunk = f.read(CHUNK_SIZE)
            if not chunk:
                break
            h.update(chunk)
            size += len(chunk)
    return h.digest(), size


def _leaf_hash(rel_path: str, file_digest: bytes) -> bytes:
    """Compute the RFC 6962-style leaf hash with the relative path bound in."""
    if len(file_digest) != 32:
        raise ValueError("file_digest must be exactly 32 bytes")
    return hashlib.sha256(
        LEAF_PREFIX + rel_path.encode("utf-8") + b"\x00" + file_digest
    ).digest()


def _internal_hash(left: bytes, right: bytes) -> bytes:
    """Compute the RFC 6962-style internal node hash."""
    if len(left) != 32 or len(right) != 32:
        raise ValueError("internal hash inputs must be 32 bytes")
    return hashlib.sha256(INTERNAL_PREFIX + left + right).digest()


def _walk_folder(
    root: Path, exclude: Iterable[str]
) -> list[tuple[str, Path]]:
    """Walk root and return [(rel_posix_path, absolute_path), ...] sorted.

    Symlinks are skipped (not followed). The sort is by the UTF-8 byte order
    of the POSIX relative path, which is the canonical order the office uses
    when building the tree.
    """
    entries: list[tuple[str, Path]] = []
    root = root.resolve()
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        # Skip symlinked directories — os.walk would otherwise descend into
        # them with followlinks=True. With followlinks=False they appear in
        # dirnames but are not recursed into. We additionally filter them
        # out so they aren't reported as files either.
        dirnames[:] = [d for d in dirnames if not (Path(dirpath) / d).is_symlink()]
        for fname in filenames:
            abs_path = Path(dirpath) / fname
            if abs_path.is_symlink():
                continue
            if not abs_path.is_file():
                continue
            rel = abs_path.relative_to(root)
            # Normalise to POSIX (forward slashes) regardless of host OS.
            rel_posix = rel.as_posix().replace("\\", "/")
            if _matches_any(rel_posix, exclude):
                continue
            entries.append((rel_posix, abs_path))
    # UTF-8 byte order on the POSIX path string.
    entries.sort(key=lambda e: e[0].encode("utf-8"))
    return entries


def _build_levels(leaves: list[bytes]) -> list[list[bytes]]:
    """Build the full set of tree levels, bottom-up, RFC 6962 promotion.

    Returns a list of levels where level[0] is the leaves and level[-1] is
    the single-node root. For a single leaf, returns ``[[leaf]]``.
    """
    if not leaves:
        raise ValueError("cannot build a tree with no leaves")
    levels: list[list[bytes]] = [list(leaves)]
    while len(levels[-1]) > 1:
        cur = levels[-1]
        nxt: list[bytes] = []
        i = 0
        while i + 1 < len(cur):
            nxt.append(_internal_hash(cur[i], cur[i + 1]))
            i += 2
        if i < len(cur):
            # Odd remainder: PROMOTE the lone node unchanged.
            nxt.append(cur[i])
        levels.append(nxt)
    return levels


class MerkleTree:
    """An immutable Merkle tree over a sorted list of (path, file_hash) leaves.

    Instances are constructed from a folder on disk via :meth:`from_folder` or
    from a previously emitted manifest via :meth:`from_manifest`. The tree
    object holds the leaves, the per-leaf metadata (path, size, file hash),
    and every internal level so inclusion proofs can be served without
    rebuilding.
    """

    __slots__ = ("_leaves_meta", "_levels")

    def __init__(self, leaves_meta: list[dict], levels: list[list[bytes]]):
        # Internal constructor — callers use the classmethods.
        self._leaves_meta = leaves_meta
        self._levels = levels

    # ------------------------------------------------------------------ build

    @classmethod
    def from_folder(
        cls, root: Path, exclude: list[str] | None = None
    ) -> "MerkleTree":
        """Build a tree by walking ``root`` and streaming each file.

        ``exclude`` defaults to the office's standard deny-list. Passing
        ``[]`` disables exclusion entirely; passing a custom list replaces
        the defaults (it does not extend them).
        """
        root = Path(root)
        if not root.is_dir():
            raise ValueError(f"not a directory: {root}")
        patterns = DEFAULT_EXCLUDE if exclude is None else tuple(exclude)
        entries = _walk_folder(root, patterns)
        if not entries:
            raise ValueError("Empty folders are not supported in v1.")

        leaves_meta: list[dict] = []
        leaf_hashes: list[bytes] = []
        for rel_path, abs_path in entries:
            file_digest, size = _hash_file(abs_path)
            leaf = _leaf_hash(rel_path, file_digest)
            leaves_meta.append({
                "path": rel_path,
                "file_sha256_hex": file_digest.hex(),
                "leaf_hex": leaf.hex(),
                "size_bytes": size,
            })
            leaf_hashes.append(leaf)

        levels = _build_levels(leaf_hashes)
        return cls(leaves_meta, levels)

    @classmethod
    def from_manifest(cls, manifest: dict) -> "MerkleTree":
        """Reconstruct a tree from a manifest produced by :meth:`manifest`.

        The reconstruction recomputes every internal node from the leaf
        hashes in the manifest, then verifies that the recomputed root
        matches the manifest's ``root_hex``. A mismatch raises ValueError —
        the office will not certify a manifest whose root does not derive
        from its own leaves.
        """
        if not isinstance(manifest, dict):
            raise ValueError("manifest must be a dict")
        if manifest.get("algorithm") != ALGORITHM:
            raise ValueError(f"unsupported algorithm: {manifest.get('algorithm')!r}")
        if manifest.get("version") != VERSION:
            raise ValueError(f"unsupported version: {manifest.get('version')!r}")
        leaves = manifest.get("leaves")
        if not isinstance(leaves, list) or not leaves:
            raise ValueError("manifest leaves must be a non-empty list")

        leaves_meta: list[dict] = []
        leaf_hashes: list[bytes] = []
        for entry in leaves:
            path = entry["path"]
            file_hex = entry["file_sha256_hex"]
            leaf_hex = entry["leaf_hex"]
            size = int(entry["size_bytes"])
            file_digest = bytes.fromhex(file_hex)
            recomputed = _leaf_hash(path, file_digest)
            if recomputed.hex() != leaf_hex:
                raise ValueError(
                    f"manifest leaf hash mismatch for {path!r}: "
                    "the stored leaf does not derive from the stored file hash"
                )
            leaves_meta.append({
                "path": path,
                "file_sha256_hex": file_hex,
                "leaf_hex": leaf_hex,
                "size_bytes": size,
            })
            leaf_hashes.append(recomputed)

        levels = _build_levels(leaf_hashes)
        root_hex_expected = manifest.get("root_hex")
        if levels[-1][0].hex() != root_hex_expected:
            raise ValueError("manifest root_hex does not match recomputed root")
        return cls(leaves_meta, levels)

    # --------------------------------------------------------------- accessors

    def root(self) -> bytes:
        """Return the 32-byte root of the tree."""
        return self._levels[-1][0]

    def root_hex(self) -> str:
        """Return the root as 64 lowercase hex characters."""
        return self.root().hex()

    def manifest(self) -> dict:
        """Return a JSON-serialisable manifest describing the tree."""
        return {
            "algorithm": ALGORITHM,
            "version": VERSION,
            "root_hex": self.root_hex(),
            "leaves": [dict(m) for m in self._leaves_meta],
        }

    # --------------------------------------------------------------- proofs

    def inclusion_proof(self, file_path: str) -> list[tuple[str, str]]:
        """Return the inclusion proof for ``file_path`` (POSIX relative).

        The proof is a list of (direction, sibling_hex) tuples ordered from
        the leaf upward. ``direction == "L"`` means the sibling sits on the
        LEFT of the running hash at that level; ``"R"`` means it sits on the
        right. A promoted (lone-last) node contributes no proof step at that
        level — there is no sibling to record.
        """
        idx = None
        for i, m in enumerate(self._leaves_meta):
            if m["path"] == file_path:
                idx = i
                break
        if idx is None:
            raise ValueError(f"path not in tree: {file_path!r}")

        proof: list[tuple[str, str]] = []
        for level in self._levels[:-1]:
            # Was this node the lone-last (promoted) node at this level?
            if idx == len(level) - 1 and len(level) % 2 == 1:
                # No sibling — promote to next level with the same index/2.
                idx = idx // 2
                continue
            if idx % 2 == 0:
                # Current is left, sibling is on the right.
                sibling = level[idx + 1]
                proof.append(("R", sibling.hex()))
            else:
                # Current is right, sibling is on the left.
                sibling = level[idx - 1]
                proof.append(("L", sibling.hex()))
            idx = idx // 2
        return proof

    @staticmethod
    def verify_inclusion(
        file_hash: bytes,
        rel_path: str,
        proof: list[tuple[str, str]],
        root: bytes,
    ) -> bool:
        """Verify a file's inclusion against a known root.

        ``file_hash`` is the raw SHA-256 of the file content (not the leaf).
        ``rel_path`` is the POSIX path under which the file was committed.
        ``proof`` is the list of (direction, sibling_hex) tuples returned by
        :meth:`inclusion_proof`. ``root`` is the 32-byte tree root.
        """
        try:
            current = _leaf_hash(rel_path, file_hash)
        except ValueError:
            return False
        if not isinstance(root, (bytes, bytearray)) or len(root) != 32:
            return False
        for step in proof:
            if (
                not isinstance(step, tuple)
                or len(step) != 2
                or step[0] not in ("L", "R")
            ):
                return False
            direction, sibling_hex = step
            try:
                sibling = bytes.fromhex(sibling_hex)
            except ValueError:
                return False
            if len(sibling) != 32:
                return False
            if direction == "L":
                current = _internal_hash(sibling, current)
            else:
                current = _internal_hash(current, sibling)
        return current == bytes(root)
