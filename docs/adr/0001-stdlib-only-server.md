# ADR 0001 — Server is Python standard library only

**Status:** Accepted
**Date:** 2026-05-12
**Reaffirmed:** 2026-05-18

## Context

The Orphograph server can be implemented with any number of HTTP
frameworks (Flask, FastAPI, Starlette, …) and any number of utility
dependencies. The trade-off is one of trust surface against developer
ergonomics.

For an empirical notary whose central trust claim is "structural
privacy — the architecture cannot disclose what it never received,"
every third-party package introduced into the server is a potential
disclosure surface, a potential supply-chain compromise vector, and a
piece of code that the founder did not write and cannot fully audit.

## Decision

The server is implemented using Python's standard library only. No
`pip install` is required to run the server. The HTTP transport is
`http.server`. The TLS termination is handled upstream at Fly.io's
proxy. Templating is plain string substitution. Storage is JSONL on
the volume.

Frontend follows the same principle: vanilla HTML, vanilla CSS, vanilla
ES modules. No React, no build step.

The MCP server (`mcp/orphograph_mcp.py`, 2026-05-18) is implemented
under the same constraint — single file, standard library only,
hand-rolled JSON-RPC 2.0 over stdio.

## Consequences

**Positive.**
- Auditable in an afternoon: the entire server is a few thousand lines.
- No supply-chain attack surface beyond CPython itself.
- Zero install friction for self-hosters and for the MCP server users.
- The "verifies without us" claim is harder to dispute when the
  service that issued the receipt has no dependencies that could
  silently compromise it.

**Negative.**
- More hand-rolled code (HTTP parsing, JSON-RPC framing, cookie
  handling).
- Slower feature velocity in some areas.
- New contributors must write to a constraint they may not be used to.

**Allowed exceptions.**
- The standalone JS verifier may use a build step ONLY within its
  isolated subdirectory.
- The browser extension (planned Q3 2026) may use Manifest V3's
  required tooling within its subdirectory.
- A future PDF receipt may use a single MIT-licensed pure-Python PDF
  library if and only if the stdlib hand-rolled path proves
  infeasible. The current implementation (`server/receipt_pdf.py`,
  2026-05-18) hand-rolled the format and required no exception.

## Reaffirmation

Reaffirmed during the 2026-05-18 launch sprint. Every artifact added
that day held to the constraint, including the MCP server and the
PDF generator. No exception was needed.
