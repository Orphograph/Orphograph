#!/usr/bin/env python3
"""build_mcpb.py — build the MCP release package (.mcpb) reproducibly.

The .mcpb is a zip of mcp/manifest.json and mcp/orphograph_mcp.py. Until
2026-11-04 it was built by hand and the procedure was written down nowhere.
This script was checked by rebuilding the published 0.1.3 package from the
0.1.3 sources: the bytes matched the released file's sha256
(9b0ec403…6c20) exactly. The recipe is Python's zipfile with default deflate,
mode 0644, creating system 3 (Unix), both members dated midnight of the build
date, members in a fixed order.

From 0.1.4 the members are STORED, not deflated: deflate output depends on
the zlib build (this Mac has 1.2.12; a CI runner may not), and a pin that
only reproduces on one machine is a check that lies everywhere else. Stored
bytes are the same under any zlib. `--method deflate` keeps the 0.1.3 recipe.

The build date and method are part of the bytes, so they are recorded in
mcp/server.json as _meta.mcpbBuildDate and _meta.mcpbZipMethod, and
tests/test_mcp_release_currency.py rebuilds the
package from the committed sources and requires the result to equal the
published fileSha256. A stale pin, or a package nobody can rebuild, fails CI.

    python3 scripts/build_mcpb.py            # uses _meta.mcpbBuildDate
    python3 scripts/build_mcpb.py --date 2026-11-04 --out dist/

Prints the sha256 and writes <out>/orphograph-mcp-<version>.mcpb plus a
.sha256 file in the `sha256sum` format the release carries.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import io
import json
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MCP = ROOT / "mcp"
MEMBERS = ("manifest.json", "orphograph_mcp.py")


METHODS = {"stored": zipfile.ZIP_STORED, "deflate": zipfile.ZIP_DEFLATED}


def build_bytes(manifest: bytes, source: bytes, date: _dt.date,
                method: str = "stored") -> bytes:
    """The package bytes for these two members on this date."""
    kind = METHODS[method]
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", kind) as zf:
        for name, data in zip(MEMBERS, (manifest, source)):
            info = zipfile.ZipInfo(name, (date.year, date.month, date.day, 0, 0, 0))
            info.compress_type = kind
            info.external_attr = 0o644 << 16
            info.create_system = 3
            zf.writestr(info, data)
    return buf.getvalue()


def build_from_tree(date: _dt.date | None = None,
                    method: str | None = None) -> tuple[str, bytes, str]:
    """(version, package bytes, sha256 hex) from the committed mcp/ sources."""
    meta = json.loads((MCP / "server.json").read_text())["_meta"]
    if date is None:
        date = _dt.date.fromisoformat(meta["mcpbBuildDate"])
    if method is None:
        method = meta.get("mcpbZipMethod", "stored")
    manifest = (MCP / "manifest.json").read_bytes()
    version = json.loads(manifest)["version"]
    data = build_bytes(manifest, (MCP / "orphograph_mcp.py").read_bytes(), date,
                       method)
    return version, data, hashlib.sha256(data).hexdigest()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--date", help="build date YYYY-MM-DD (default: "
                    "_meta.mcpbBuildDate in mcp/server.json)")
    ap.add_argument("--method", choices=sorted(METHODS),
                    help="zip method (default: _meta.mcpbZipMethod, else stored)")
    ap.add_argument("--out", default=str(ROOT / "dist"),
                    help="output directory (default: dist/)")
    args = ap.parse_args(argv)
    date = _dt.date.fromisoformat(args.date) if args.date else None
    version, data, digest = build_from_tree(date, args.method)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    name = f"orphograph-mcp-{version}.mcpb"
    (out / name).write_bytes(data)
    (out / f"{name}.sha256").write_text(f"{digest}  dist/{name}\n")
    print(f"{digest}  {out / name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
