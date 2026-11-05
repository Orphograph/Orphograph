# Orphograph MCP server

A single-file Model Context Protocol server that lets a Claude / Claude Code /
Cursor / any-MCP-host instance anchor files to the Bitcoin chain via
[Orphograph](https://orphograph.com) without the file ever leaving the user's
machine.

<!-- Ownership marker for the official MCP registry (registry.modelcontextprotocol.io).
     The registry fetches this README from PyPI and requires this exact line to
     confirm the package belongs to the io.github.Orphograph namespace. -->
`mcp-name: io.github.Orphograph/orphograph`

- **License:** MIT
- **Dependencies:** Python ≥ 3.9 standard library only. Also on PyPI as `orphograph-mcp` 0.1.1.
- **Lines of code:** ~310, single file.
- **Protocol:** MCP 2024-11-05 over stdio.

## Install

Save the script anywhere:

```
curl -sSL https://orphograph.com/mcp/orphograph_mcp.py -o ~/bin/orphograph_mcp.py
```

Then register it with your MCP host.

### Claude Desktop (`~/Library/Application Support/Claude/claude_desktop_config.json`)

```json
{
  "mcpServers": {
    "orphograph": {
      "command": "python3",
      "args": ["/Users/YOU/bin/orphograph_mcp.py"],
      "env": {
        "ORPHO_API_KEY": ""
      }
    }
  }
}
```

### Claude Code

```
claude mcp add orphograph python3 /Users/YOU/bin/orphograph_mcp.py
```

Or edit `~/.claude/settings.json` and add the same `mcpServers` block.

### Cursor (`~/.cursor/mcp.json`)

```json
{
  "mcpServers": {
    "orphograph": {
      "command": "python3",
      "args": ["/Users/YOU/bin/orphograph_mcp.py"],
      "env": {
        "ORPHO_API_KEY": ""
      }
    }
  }
}
```

## Tools exposed

| Tool | Purpose | Auth |
|---|---|---|
| `orphograph_anchor_file(path)` | Hash a local file and register the fingerprint on Bitcoin. | Optional API key |
| `orphograph_anchor_folder(path)` | Hash a whole folder into an RFC-6962 Merkle tree and anchor one root covering every file. | Optional API key |
| `orphograph_anchor_output(text, …)` | Hash generated output (an agent result, a transcript) in-process and anchor it. Accepts an optional `zk_proof`. The fingerprint is public on the receipt, so for short or guessable output pass `salt: true`: it anchors SHA-256(salt + text), returns `salt_hex` (never transmitted) and needs it to verify. Every anchor reply says `bitcoin_status`: `pending` until the calendars commit to a Bitcoin block (usually hours), then `confirmed`. | Optional API key |
| `orphograph_verify_receipt(receipt_id)` | Look up what the office recorded for a receipt. A lookup — it does NOT consult the chain; run `ots verify` for that. | None |
| `orphograph_verify_lineage(receipt_id)` | Walk an edit-lineage chain back through its committed parents. | None |
| `orphograph_list_vault(limit?)` | List the subscriber's receipts. | API key required |
| `orphograph_anchor_run(steps, label?)` | Anchor every step of an agent run under one receipt; each step provable alone. | None |
| `orphograph_verify_output(receipt_id, text \| sha256_hex, salt_hex?)` | Check whether a receipt covers an exact output, trusting only the anchored hash. Pass `salt_hex` for an output anchored with a salt. | None |

## Privacy contract

- The file body never leaves the device.
- The script computes SHA-256 + SHA-512 in-process and transmits only those
  fingerprints to `https://orphograph.com/api/anchor`.
- The script makes no network call until a tool is explicitly invoked by
  the host.
- File reads stream in 1 MB chunks; large files do not load into memory.

## Configuration

| Environment variable | Purpose |
|---|---|
| `ORPHO_API_KEY` | Optional. Issued from [orphograph.com/account.html](https://orphograph.com/account.html). Without it, the install is rate-limited to the free tier (3 anchors per 24 hours). |
| `ORPHO_BASE_URL` | Optional. Defaults to `https://orphograph.com`. Override for testing or self-hosted instances. |
| `ORPHO_X402_PAYER_KEY_FILE` | Optional (0.1.6). Path to a `chmod 600` file holding the hex private key of a dedicated Base wallet with USDC. With the daily cap below, anchors past the free tier are paid with x402. The key never leaves the machine. |
| `ORPHO_X402_DAILY_CAP_CENTS` | Optional (0.1.6). The most the server may pay in one UTC day, in cents. Paying stays off unless this and the key file are both set. |
| `ORPHO_X402_MAX_CENTS_PER_CALL` | Optional (0.1.6). The most one anchor may cost, in cents. Default 5. |

With paying on, the server pins the USDC contract, its signing domain and the chain (Base); a 402 asking for anything else, or for more than either cap, is declined and nothing is signed. Spending is recorded in `~/.orphograph/x402_spend.json`. A paid result carries `x402_payment` with the amount and the on-chain transaction.

## Tiers and limits

| Tier | Cap | Price |
|---|---|---|
| Free | 3 anchors / 24h | $0 |
| Writer Pack | 25 anchors, never expires | $5 one-time |
| Pack of Two Hundred | 200 anchors, never expires | $20 one-time |
| Standing Order | Unrestricted anchoring | $9 / month |
| Agents (x402) | Past the free tier, per anchor | 1 cent in USDC on Base |

## Verification

The MCP server only issues calls. Every receipt it produces can be checked
without this office, in two independent steps:

1. **Structure** — the open-source verifier at
   [github.com/Orphograph/Orphograph](https://github.com/Orphograph/Orphograph)
   re-derives the hashes and checks the receipt offline. It makes no network
   calls of its own.
2. **Chain** — the [OpenTimestamps client](https://github.com/opentimestamps/opentimestamps-client)
   (`ots verify`) confirms the commitment actually landed in a Bitcoin block.
   The bundled verifier will invoke it for you when passed `--ots`.

Only step 2 consults Bitcoin.

## Reporting issues

Open an issue at the public repo, or write to `hello@orphograph.com`.
