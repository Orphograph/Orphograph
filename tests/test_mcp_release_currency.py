"""The packaged MCP release must never drift from the source it claims to ship.

Regression for the 0.1.0 incident (found 2026-08-12): commit ba1713d fixed
orphograph_mcp.py on 2026-08-06, but the released orphograph-mcp-0.1.0.mcpb kept
the pre-fix bytes — every registry installer received the zk_proof bug while the
curl-install path served the fixed file. Two channels, silently diverged.

The invariant enforced here: any change to mcp/orphograph_mcp.py must roll the
release metadata in mcp/server.json in the same commit (new sourceFileSha256 pin,
which in practice means a new version + new .mcpb + new fileSha256). A stale pin
fails CI instead of shipping stale artifacts.
"""
import hashlib
import json
import pathlib

MCP = pathlib.Path(__file__).resolve().parent.parent / "mcp"


def _server_json():
    return json.loads((MCP / "server.json").read_text())


def test_server_json_pin_matches_source_file():
    pinned = _server_json()["_meta"]["sourceFileSha256"]
    actual = hashlib.sha256((MCP / "orphograph_mcp.py").read_bytes()).hexdigest()
    assert pinned == actual, (
        "mcp/orphograph_mcp.py changed but mcp/server.json _meta.sourceFileSha256 "
        "was not rolled — cut a new release (version bump, new .mcpb, new "
        "fileSha256) or registry installers ship stale bytes again."
    )


def test_all_four_version_holders_agree():
    s = _server_json()
    versions = {
        "server.json": s["version"],
        "server.json packages[0]": s["packages"][0]["version"],
        "manifest.json": json.loads((MCP / "manifest.json").read_text())["version"],
        "pyproject.toml": next(
            line.split('"')[1]
            for line in (MCP / "pyproject.toml").read_text().splitlines()
            if line.startswith("version = ")
        ),
        "orphograph_mcp.py SERVER_VERSION": next(
            line.split('"')[1]
            for line in (MCP / "orphograph_mcp.py").read_text().splitlines()
            if line.startswith("SERVER_VERSION = ")
        ),
    }
    assert len(set(versions.values())) == 1, f"version drift: {versions}"


def test_mcpb_identifier_and_version_are_consistent():
    p = _server_json()["packages"][0]
    assert f"mcp-v{p['version']}/orphograph-mcp-{p['version']}.mcpb" in p["identifier"], (
        "packages[0].identifier release tag/filename does not match its version"
    )
