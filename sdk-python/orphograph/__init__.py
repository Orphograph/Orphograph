"""orphograph — Python SDK for anchoring folders to Bitcoin.

The SDK constructs an RFC 6962-style Merkle tree from a local folder, then
submits only the manifest (paths, per-file SHA-256 digests, leaf hashes,
and a 32-byte root) to the Orphograph hosted service. File bodies do not
cross the network at any point in this module.

Public API:

    anchor_folder(folder_path, server_url=..., api_key=..., client_label=..., exclude=...)
    verify_folder(folder_path, receipt_id, server_url=...)
    inclusion_proof(receipt_id, path, server_url=...)
    verify_inclusion(file_path, rel_path, proof, root_hex)

Algorithm tag: ``orphograph-merkle-v1-rfc6962``.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Optional, Sequence

from . import _client
from ._client import OrphographError
from ._merkle import ALGORITHM, MerkleTree

__all__ = [
    "ALGORITHM",
    "OrphographError",
    "anchor_folder",
    "verify_folder",
    "inclusion_proof",
    "verify_inclusion",
]

__version__ = "0.1.0"


def anchor_folder(
    folder_path: str,
    *,
    server_url: str = _client.DEFAULT_SERVER_URL,
    api_key: Optional[str] = None,
    client_label: Optional[str] = None,
    exclude: Optional[Sequence[str]] = None,
) -> dict:
    """Anchor a folder to Bitcoin via the Orphograph hosted service.

    The Merkle tree is constructed locally; only the resulting manifest
    (paths + SHA-256 digests + leaf hashes + root) is transmitted to the
    server. File contents stay on disk.

    Returns a dict with keys: ``receipt_id``, ``root_hex``, ``leaf_count``,
    ``calendars_ok``, ``calendars_total``.
    """
    root = Path(folder_path)
    if not root.is_dir():
        raise ValueError(f"not a directory: {folder_path}")
    excl_list = list(exclude) if exclude is not None else None
    tree = MerkleTree.from_folder(root, exclude=excl_list)
    manifest = tree.manifest()
    response = _client.post_anchor_folder(
        manifest,
        server_url=server_url,
        api_key=api_key,
        client_label=client_label,
    )
    return {
        "receipt_id": response.get("receipt_id"),
        "root_hex": response.get("root_hex", tree.root_hex()),
        "leaf_count": response.get("leaf_count", len(manifest["leaves"])),
        "calendars_ok": response.get("calendars_ok"),
        "calendars_total": response.get("calendars_total"),
    }


def verify_folder(
    folder_path: str,
    receipt_id: str,
    *,
    server_url: str = _client.DEFAULT_SERVER_URL,
    api_key: Optional[str] = None,
) -> bool:
    """Verify a local folder against a previously anchored receipt.

    Rebuilds the Merkle root locally from the folder on disk and compares
    it byte-for-byte to the root recorded in the receipt's manifest. The
    server is consulted only to fetch the manifest; file contents are not
    transmitted.
    """
    root = Path(folder_path)
    if not root.is_dir():
        raise ValueError(f"not a directory: {folder_path}")
    response = _client.get_verify_folder(receipt_id, server_url=server_url, api_key=api_key)
    manifest = response.get("manifest") or {}
    server_root = manifest.get("root_hex")
    if not server_root:
        return False
    # Use the same exclude list the server's manifest implies (the default
    # deny list is the office convention).
    tree = MerkleTree.from_folder(root, exclude=None)
    return tree.root_hex() == server_root


def inclusion_proof(
    receipt_id: str,
    path: str,
    *,
    server_url: str = _client.DEFAULT_SERVER_URL,
    api_key: Optional[str] = None,
) -> dict:
    """Fetch an inclusion proof for one POSIX-relative path in a folder receipt.

    Returns the server's JSON payload: ``receipt_id``, ``root_hex``,
    ``path``, ``file_sha256_hex``, ``proof`` (list of ``[direction, hex]``).
    """
    return _client.get_inclusion_proof(
        receipt_id, path, server_url=server_url, api_key=api_key
    )


def verify_inclusion(
    file_path: str,
    rel_path: str,
    proof: Sequence,
    root_hex: str,
) -> bool:
    """Verify locally that a file was included in an anchored folder.

    Reads the file from disk, computes its SHA-256, and walks the proof
    upward against the supplied root. No network call is made.
    """
    p = Path(file_path)
    if not p.is_file():
        return False
    h = hashlib.sha256()
    with p.open("rb") as f:
        while True:
            chunk = f.read(1024 * 1024)
            if not chunk:
                break
            h.update(chunk)
    file_hash = h.digest()
    try:
        root = bytes.fromhex(root_hex)
    except (ValueError, TypeError):
        return False
    # Coerce proof entries (which may arrive as lists from JSON) to tuples.
    normalised: list = []
    for step in proof:
        if not isinstance(step, (list, tuple)) or len(step) != 2:
            return False
        normalised.append((step[0], step[1]))
    return MerkleTree.verify_inclusion(file_hash, rel_path, normalised, root)
