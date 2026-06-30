#!/usr/bin/env python3
"""provenance.py — dataset-provenance receipts built on Orphograph.

A pipeline-friendly CLI that takes a *bundle* — your dataset plus the
license/consent documents and the acquisition log that say where it came
from — and binds all of it into a single Bitcoin-anchored Merkle receipt
and a one-page provenance certificate.

What it proves
--------------
That this exact dataset, together with these exact license documents and
this exact acquisition log, all existed in this form by a specific date,
and that no file has changed since. Each file is independently verifiable
(Merkle inclusion proof) without re-disclosing the whole set.

What it does NOT prove
----------------------
That you had the legal right to the data, that the acquisition log is
truthful, or who authored anything. It proves *integrity and time*, not
*lawful sourcing*. The certificate says so in plain language — provenance
tooling that overclaims is worse than none.

Privacy
-------
The dataset never leaves your environment. Files are hashed locally; only
the manifest (relative paths + SHA-256 digests + the Merkle root) is sent
to Orphograph to be anchored. With --offline, nothing leaves at all and
you anchor the root yourself later.

Reuses Orphograph's canonical Merkle implementation (server/merkle.py,
algorithm "orphograph-merkle-v1-rfc6962") so every receipt is verifiable
with the public MIT verifier at orphograph.com/verify — no lock-in.

Usage
-----
  provenance.py anchor --bundle <dir> --name "<dataset name>" [--out <dir>]
                       [--offline] [--api https://orphograph.com] [--label <text>]
  provenance.py verify --cert <certificate.json> --bundle <dir> [--file <relpath>]

Bundle convention (all parts optional except data/)
  <bundle>/
    data/                  the dataset itself (any tree of files)
    licenses/              license / consent / terms documents (PDF, txt, …)
    acquisition_log.*      where each source came from, when, under what terms
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

# Reuse the canonical Orphograph Merkle implementation so the manifest is
# byte-identical to what /api/anchor_folder expects and what the public
# verifier checks. The tool lives in the repo; server/ is one level up.
_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "server"))
import merkle  # noqa: E402  (path set above)

DEFAULT_API = "https://orphograph.com"
# Cloudflare (Error 1010) blocks the bare Python-urllib User-Agent; present the
# same client signature the browser uses.
_BROWSER_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
               "AppleWebKit/537.36 (KHTML, like Gecko) "
               "Chrome/124.0.0.0 Safari/537.36")


# --------------------------------------------------------------------------- categorise

def _categorise(leaves: list[dict]) -> dict[str, list[dict]]:
    """Sort manifest leaves into data / licenses / acquisition-log / other.

    Categorisation is by the leaf's top-level path segment so the certificate
    can summarise each part of the bundle separately. It is purely descriptive;
    every leaf is still bound into the one Merkle root regardless of bucket.
    """
    buckets: dict[str, list[dict]] = {"data": [], "licenses": [], "log": [], "other": []}
    for leaf in leaves:
        path = leaf["path"]
        head = path.split("/", 1)[0].lower()
        name = path.rsplit("/", 1)[-1].lower()
        if head == "data":
            buckets["data"].append(leaf)
        elif head in ("licenses", "license", "consent"):
            buckets["licenses"].append(leaf)
        elif head in ("provenance",) or name.startswith("acquisition_log") or name in (
            "acquisition_log.json", "acquisition.log", "provenance.json",
        ):
            buckets["log"].append(leaf)
        else:
            buckets["other"].append(leaf)
    return buckets


def _total_bytes(leaves: list[dict]) -> int:
    return sum(int(l["size_bytes"]) for l in leaves)


# --------------------------------------------------------------------------- anchor

def _post_manifest(api: str, manifest: dict, label: str | None,
                   public_paths: bool = False) -> dict:
    """POST the manifest (NOT the data) to /api/anchor_folder; return the receipt.

    Raises on any network/HTTP error so the caller can fall back to offline.
    """
    body = {"manifest": manifest}
    if label:
        body["client_label"] = label
    if public_paths:
        body["paths_public"] = True
    data = json.dumps(body).encode("utf-8")
    # Cloudflare (Error 1010) blocks the default Python-urllib User-Agent. Present
    # the same client signature the browser frontend uses to reach this endpoint.
    req = urllib.request.Request(
        f"{api.rstrip('/')}/api/anchor_folder",
        data=data,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                           "AppleWebKit/537.36 (KHTML, like Gecko) "
                           "Chrome/124.0.0.0 Safari/537.36"),
            "Origin": api.rstrip("/"),
            "Referer": f"{api.rstrip('/')}/",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def cmd_anchor(args: argparse.Namespace) -> int:
    bundle = Path(args.bundle).resolve()
    if not bundle.is_dir():
        print(f"error: --bundle is not a directory: {bundle}", file=sys.stderr)
        return 2

    try:
        tree = merkle.MerkleTree.from_folder(bundle)
    except ValueError as e:
        print(f"error: could not build Merkle tree: {e}", file=sys.stderr)
        return 2

    manifest = tree.manifest()
    root_hex = tree.root_hex()
    leaves = manifest["leaves"]
    buckets = _categorise(leaves)
    created_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    # Anchor the root (manifest only — data stays local) unless offline.
    receipt: dict | None = None
    anchor_error: str | None = None
    if args.offline:
        anchor_error = "skipped (--offline); root not yet anchored"
    else:
        try:
            receipt = _post_manifest(args.api, manifest, args.label or args.name,
                                     public_paths=getattr(args, "public_paths", False))
        except urllib.error.HTTPError as e:
            anchor_error = f"HTTP {e.code}: {e.read().decode('utf-8', 'replace')[:200]}"
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            anchor_error = f"network error: {e}"

    # Sanity: the server must agree on the root it anchored.
    if receipt and receipt.get("root_hex") and receipt["root_hex"] != root_hex:
        print("error: server returned a different root than we computed — aborting",
              file=sys.stderr)
        return 3

    cert = _build_certificate(
        name=args.name, created_at=created_at, root_hex=root_hex,
        manifest=manifest, buckets=buckets, receipt=receipt,
        anchor_error=anchor_error, api=args.api,
    )

    out_dir = Path(args.out).resolve() if args.out else bundle.parent / f"{bundle.name}-provenance"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "certificate.json").write_text(json.dumps(cert, indent=2) + "\n")
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    cert_text = _render_certificate_text(cert)
    (out_dir / "certificate.txt").write_text(cert_text)
    pdf_written = False
    if getattr(args, "pdf", False):
        (out_dir / "certificate.pdf").write_bytes(_text_to_pdf(cert_text))
        pdf_written = True

    print(f"Dataset:    {args.name}")
    print(f"Files:      {len(leaves)} ({_total_bytes(leaves):,} bytes) — "
          f"{len(buckets['data'])} data, {len(buckets['licenses'])} license(s), "
          f"{len(buckets['log'])} log, {len(buckets['other'])} other")
    print(f"Merkle root: {root_hex}")
    if receipt:
        rid = receipt.get('receipt_id')
        print(f"Receipt:    {rid}  (anchored {receipt.get('created_at')})")
        print(f"Hosted:     {args.api.rstrip('/')}/certificate/{rid}")
    else:
        print(f"Anchor:     NOT anchored — {anchor_error}")
    written = "certificate.txt  certificate.json  manifest.json"
    if pdf_written:
        written += "  certificate.pdf"
    print(f"Written:    {out_dir}/  ({written})")
    return 0


# --------------------------------------------------------------------------- certificate

def _build_certificate(*, name, created_at, root_hex, manifest, buckets, receipt,
                       anchor_error, api) -> dict:
    def _digests(bucket):
        return [{"path": l["path"], "sha256": l["file_sha256_hex"],
                 "size_bytes": int(l["size_bytes"])} for l in bucket]

    anchor: dict = {"status": "anchored" if receipt else "unanchored"}
    if receipt:
        rid = receipt.get("receipt_id")
        anchor.update({
            "service": "Orphograph",
            "receipt_id": rid,
            "anchored_at": receipt.get("created_at"),
            "receipt_url": f"{api.rstrip('/')}/api/receipt/{rid}",
            "certificate_url": f"{api.rstrip('/')}/certificate/{rid}",
            "chain": "Bitcoin (via OpenTimestamps)",
        })
    else:
        anchor["note"] = anchor_error

    return {
        "document": "Dataset Provenance Certificate",
        "schema": "orphograph-dataset-provenance/v1",
        "dataset_name": name,
        "generated_at": created_at,
        "algorithm": manifest["algorithm"],
        "merkle_root": root_hex,
        "summary": {
            "total_files": len(manifest["leaves"]),
            "total_bytes": _total_bytes(manifest["leaves"]),
            "data_files": len(buckets["data"]),
            "license_documents": len(buckets["licenses"]),
            "acquisition_log_files": len(buckets["log"]),
            "other_files": len(buckets["other"]),
        },
        "anchor": anchor,
        "license_documents": _digests(buckets["licenses"]),
        "acquisition_log": _digests(buckets["log"]),
        "other_files": _digests(buckets["other"]),
        "scope": {
            "proves": [
                "This dataset, these license documents, and this acquisition log "
                "all existed in this exact form by the anchored date.",
                "No file has been added, removed, or modified since (any change "
                "alters the Merkle root).",
                "Each file is independently verifiable via a Merkle inclusion "
                "proof without re-disclosing the rest of the set.",
            ],
            "does_not_prove": [
                "That the data was lawfully sourced, licensed, or owned.",
                "That the acquisition log is truthful or complete.",
                "Authorship of any file.",
            ],
        },
        "verification": {
            "integrity": "Re-run: provenance.py verify --cert certificate.json "
                         "--bundle <dir>. It rebuilds the Merkle root from your "
                         "files and confirms it matches this certificate.",
            "single_file": "provenance.py verify --cert certificate.json "
                           "--bundle <dir> --file data/<relpath> proves one file "
                           "belongs to the certified set.",
            "bitcoin_anchor": "The Merkle root is the receipt's hash. Verify the "
                              "OpenTimestamps proof with the public MIT verifier "
                              "(orphograph.com/verify) — no account, no network "
                              "dependency on Orphograph.",
            "manifest": "manifest.json (next to this certificate) holds every "
                        "leaf; from it any inclusion proof can be recomputed "
                        "offline.",
        },
    }


def _text_to_pdf(text: str) -> bytes:
    """Render monospaced text into a minimal multi-page PDF (stdlib only).

    Uses the standard PDF Courier font (base-14, no embedding) so the output
    is portable and dependency-free — no cupsfilter / wkhtmltopdf needed.
    US Letter, 0.75" margins, 9pt text on 11pt leading; long lines hard-wrap.
    """
    page_w, page_h, margin = 612, 792, 54
    font_size, leading = 9, 11
    char_w = font_size * 0.6                      # Courier is 600/1000 em wide
    max_chars = max(1, int((page_w - 2 * margin) / char_w))
    lines_per_page = max(1, int((page_h - 2 * margin) / leading))

    wrapped: list[str] = []
    for ln in text.replace("\t", "    ").split("\n"):
        if len(ln) <= max_chars:
            wrapped.append(ln)
        else:
            while ln:
                wrapped.append(ln[:max_chars]); ln = ln[max_chars:]
    pages = [wrapped[i:i + lines_per_page]
             for i in range(0, len(wrapped), lines_per_page)] or [[""]]

    def esc(s: str) -> str:
        return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")

    streams: list[bytes] = []
    for page in pages:
        top = page_h - margin - font_size
        body = [f"BT /F1 {font_size} Tf {leading} TL {margin} {top:.0f} Td"]
        for i, ln in enumerate(page):
            body.append(f"({esc(ln)}) Tj" if i == 0 else f"T* ({esc(ln)}) Tj")
        body.append("ET")
        streams.append("\n".join(body).encode("latin-1", "replace"))

    # Object layout: 1 Catalog, 2 Pages, 3 Font, then per page Page+Contents.
    n_pages = len(pages)
    page_ids = [4 + 2 * i for i in range(n_pages)]
    content_ids = [5 + 2 * i for i in range(n_pages)]
    objs: list[bytes] = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        f"<< /Type /Pages /Kids [{' '.join(f'{p} 0 R' for p in page_ids)}] "
        f"/Count {n_pages} >>".encode(),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Courier >>",
    ]
    for i in range(n_pages):
        objs.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {page_w} {page_h}] "
            f"/Resources << /Font << /F1 3 0 R >> >> "
            f"/Contents {content_ids[i]} 0 R >>".encode())
        objs.append(b"<< /Length %d >>\nstream\n%s\nendstream" % (len(streams[i]), streams[i]))

    out = bytearray(b"%PDF-1.4\n")
    offsets: list[int] = []
    for idx, obj in enumerate(objs, start=1):
        offsets.append(len(out))
        out += f"{idx} 0 obj\n".encode() + obj + b"\nendobj\n"
    xref_pos = len(out)
    n_obj = len(objs) + 1
    out += f"xref\n0 {n_obj}\n".encode() + b"0000000000 65535 f \n"
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {n_obj} /Root 1 0 R >>\nstartxref\n{xref_pos}\n%%EOF\n".encode()
    return bytes(out)


def _render_certificate_text(cert: dict) -> str:
    L: list[str] = []
    bar = "=" * 70
    L.append(bar)
    L.append("DATASET PROVENANCE CERTIFICATE")
    L.append(bar)
    L.append("")
    L.append(f"Dataset:        {cert['dataset_name']}")
    L.append(f"Generated:      {cert['generated_at']}")
    s = cert["summary"]
    L.append(f"Contents:       {s['total_files']} files, {s['total_bytes']:,} bytes")
    L.append(f"                  {s['data_files']} dataset file(s)")
    L.append(f"                  {s['license_documents']} license/consent document(s)")
    L.append(f"                  {s['acquisition_log_files']} acquisition-log file(s)")
    if s["other_files"]:
        L.append(f"                  {s['other_files']} other file(s)")
    L.append("")
    L.append(f"Merkle root:    {cert['merkle_root']}")
    L.append(f"Algorithm:      {cert['algorithm']}")
    a = cert["anchor"]
    if a["status"] == "anchored":
        L.append(f"Anchored:       {a['anchored_at']}  on {a['chain']}")
        L.append(f"Receipt:        {a['receipt_id']}")
        L.append(f"Hosted view:    {a['certificate_url']}")
        L.append(f"Receipt JSON:   {a['receipt_url']}")
    else:
        L.append(f"Anchored:       NOT YET — {a.get('note', 'unanchored')}")
    L.append("")
    if cert["license_documents"]:
        L.append("LICENSE / CONSENT DOCUMENTS (sha256)")
        for d in cert["license_documents"]:
            L.append(f"  {d['sha256']}  {d['path']}")
        L.append("")
    if cert["acquisition_log"]:
        L.append("ACQUISITION LOG (sha256)")
        for d in cert["acquisition_log"]:
            L.append(f"  {d['sha256']}  {d['path']}")
        L.append("")
    L.append("WHAT THIS PROVES")
    for line in cert["scope"]["proves"]:
        L.append(f"  + {line}")
    L.append("")
    L.append("WHAT THIS DOES NOT PROVE")
    for line in cert["scope"]["does_not_prove"]:
        L.append(f"  - {line}")
    L.append("")
    L.append("HOW TO VERIFY")
    L.append(f"  Integrity:    {cert['verification']['integrity']}")
    L.append(f"  One file:     {cert['verification']['single_file']}")
    L.append(f"  Bitcoin:      {cert['verification']['bitcoin_anchor']}")
    L.append("")
    L.append(bar)
    return "\n".join(L) + "\n"


# --------------------------------------------------------------------------- verify

def _fetch_receipt_root(api: str, rid: str) -> "tuple[str | None, str | None]":
    """Fetch a live receipt and return (root_hex, error).

    Only the receipt id crosses the network — never the bundle. Confirms the
    receipt exists and is a folder (dataset) anchor, and returns its Merkle
    root (the receipt's hash_hex).
    """
    url = f"{api.rstrip('/')}/api/receipt/{rid}"
    req = urllib.request.Request(
        url, headers={"User-Agent": _BROWSER_UA, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            rec = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None, f"receipt not found: {rid}"
        return None, f"HTTP {e.code} fetching receipt {rid}"
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as e:
        return None, f"could not fetch receipt {rid}: {e}"
    if rec.get("found") is False:
        return None, f"receipt not found: {rid}"
    kind = rec.get("kind")
    if kind and kind != "folder":
        return None, f"receipt {rid} is a {kind} receipt, not a dataset (folder) anchor"
    root = rec.get("hash_hex")
    if not root:
        return None, f"receipt {rid} has no root hash"
    return root, None


def cmd_verify(args: argparse.Namespace) -> int:
    bundle = Path(args.bundle).resolve()
    if not bundle.is_dir():
        print(f"error: --bundle is not a directory: {bundle}", file=sys.stderr)
        return 2

    # The expected root comes from a local certificate OR a live anchored
    # receipt (only the receipt id crosses the network; the bundle never does).
    cert_path = None
    if args.receipt:
        expected_root, err = _fetch_receipt_root(args.api, args.receipt)
        if err:
            print(f"error: {err}", file=sys.stderr)
            return 2
        source = f"live receipt {args.receipt}"
    else:
        cert_path = Path(args.cert).resolve()
        try:
            cert = json.loads(cert_path.read_text())
        except (OSError, json.JSONDecodeError) as e:
            print(f"error: could not read certificate: {e}", file=sys.stderr)
            return 2
        expected_root = cert.get("merkle_root")
        source = "certificate"

    ok = True

    # 1. Rebuild the Merkle root from the bundle on disk.
    try:
        tree = merkle.MerkleTree.from_folder(bundle)
    except ValueError as e:
        print(f"FAIL  could not rebuild tree from bundle: {e}")
        return 1
    recomputed = tree.root_hex()
    if recomputed == expected_root:
        print(f"PASS  bundle integrity — Merkle root matches {source}")
        print(f"      {recomputed}")
    else:
        ok = False
        print(f"FAIL  bundle integrity — root MISMATCH (a file changed/added/removed)")
        print(f"      {source}: {expected_root}")
        print(f"      recomputed:  {recomputed}")

    # 2. Confirm the sibling manifest is internally consistent (cert mode only).
    mpath = cert_path.parent / "manifest.json" if cert_path is not None else None
    if mpath is not None and mpath.exists():
        try:
            mtree = merkle.MerkleTree.from_manifest(json.loads(mpath.read_text()))
            tag = "matches" if mtree.root_hex() == expected_root else "DIFFERS from cert"
            print(f"PASS  manifest.json self-consistent — root {tag}")
            if mtree.root_hex() != expected_root:
                ok = False
        except (ValueError, OSError, json.JSONDecodeError) as e:
            ok = False
            print(f"FAIL  manifest.json invalid: {e}")

    # 3. Optional single-file inclusion proof. Re-hash the file independently
    #    and verify its leaf sits under the *certificate's* root (not just the
    #    freshly-rebuilt one), so the check is meaningful even if step 1 failed.
    if args.file:
        target = bundle / args.file
        try:
            if not target.is_file():
                raise FileNotFoundError(target)
            file_hash = _sha256(target)
            proof = tree.inclusion_proof(args.file)
            verified = merkle.MerkleTree.verify_inclusion(
                file_hash, args.file, proof, bytes.fromhex(expected_root)
            )
            if verified:
                print(f"PASS  inclusion — '{args.file}' is part of the certified set")
            else:
                ok = False
                print(f"FAIL  inclusion — '{args.file}' did NOT verify against the certified root")
        except (ValueError, KeyError, FileNotFoundError) as e:
            ok = False
            print(f"FAIL  inclusion — {args.file}: {e}")

    print("")
    print("RESULT: VERIFIED" if ok else "RESULT: FAILED")
    return 0 if ok else 1


def _sha256(path: Path) -> bytes:
    import hashlib
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.digest()


# --------------------------------------------------------------------------- main

def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="provenance.py",
        description="Dataset-provenance receipts + certificates, anchored via Orphograph.",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("anchor", help="build a provenance receipt + certificate for a bundle")
    a.add_argument("--bundle", required=True, help="bundle dir (data/, licenses/, acquisition_log.*)")
    a.add_argument("--name", required=True, help="human-readable dataset name")
    a.add_argument("--out", help="output dir (default: <bundle>-provenance next to the bundle)")
    a.add_argument("--api", default=DEFAULT_API, help=f"Orphograph base URL (default {DEFAULT_API})")
    a.add_argument("--label", help="optional client_label stored on the receipt")
    a.add_argument("--offline", action="store_true", help="do not anchor; nothing leaves the machine")
    a.add_argument("--pdf", action="store_true", help="also write certificate.pdf (portable, stdlib-only)")
    a.add_argument("--public-paths", dest="public_paths", action="store_true",
                   help="publish file paths so a shared certificate shows them (default: owner-only)")
    a.set_defaults(func=cmd_anchor)

    v = sub.add_parser("verify", help="re-verify a bundle against its certificate or a live receipt")
    src = v.add_mutually_exclusive_group(required=True)
    src.add_argument("--cert", help="path to a local certificate.json")
    src.add_argument("--receipt", help="verify against a live anchored receipt id (no local cert needed)")
    v.add_argument("--bundle", required=True, help="bundle dir to re-hash")
    v.add_argument("--file", help="optional: prove one file (relpath) belongs to the set")
    v.add_argument("--api", default=DEFAULT_API, help=f"Orphograph base URL for --receipt (default {DEFAULT_API})")
    v.set_defaults(func=cmd_verify)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
