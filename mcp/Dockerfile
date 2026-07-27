# Orphograph MCP server — minimal container for directory listings (e.g. Glama).
#
# The server is a single stdlib-only Python file that speaks MCP (JSON-RPC 2.0)
# over stdio. No third-party dependencies, so the image is tiny and the build
# is deterministic. Directory crawlers start the container and send an
# `initialize` request over stdin; the server replies on stdout with its
# capabilities (verified locally: protocolVersion 2024-11-05, tools capability).
FROM python:3.12-slim

WORKDIR /app
COPY orphograph_mcp.py /app/orphograph_mcp.py

# stdio transport: the MCP host drives the server over stdin/stdout. No port is
# opened and nothing is exposed to the network — the container only relays the
# file's SHA-256 fingerprint to https://orphograph.com, never the file itself.
ENTRYPOINT ["python", "/app/orphograph_mcp.py"]
