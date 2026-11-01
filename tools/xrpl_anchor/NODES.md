# XRPL testnet nodes — who runs them, and our rules for using them

This tool is an internal experiment on the XRPL **testnet** only (founder ruling
2026-10-31). The nodes below belong to other people. We use them lightly, under one
honest identity, and we stop the moment an operator says no.

## Default nodes

| Node | Operator | Published as public | Terms status |
|---|---|---|---|
| `https://s.altnet.rippletest.net:51234/` | Ripple | Listed as a Testnet public server on https://xrpl.org/docs/tutorials/public-servers (checked 2026-10-30, re-checked 2026-10-31) | **Ripple: not for sustained or business use** ("Ripple's public servers are not for sustained or business use, and they may become unavailable at any time.", same page) |
| `https://testnet.xrpl-labs.com/` | XRPL Labs | Listed as a Testnet public server on the same page (checked 2026-10-30, re-checked 2026-10-31) | **XRPL Labs: no node policy published; usage question staged 2026-10-30, unanswered.** Their General Terms and Conditions (B2C) v1.3, April 2025, cover the Xaman app and services offered through it, not direct RPC use of their public nodes. |

## Documented alternative (not a default)

| Node | Operator | Published as public | Terms status |
|---|---|---|---|
| `https://testnet.honeycluster.io/` | Honeycluster | Listed as a Testnet public server on the same page (checked 2026-10-30, re-checked 2026-10-31) | **Honeycluster: check before adding.** Use it with `--node` only after its usage terms are read. It is the replacement if XRPL Labs declines or never answers. |

## How the code keeps to this

- **One honest identity.** Every request carries `orphograph-xrpl-verify/0 (+https://github.com/Orphograph/Orphograph)`. It is a module constant with no flag or environment variable, and it contains no browser token.
- **Blocked means stop.** An HTTP 401, 403 or 429, or a reply that names a rate limit (`slowDown`, `tooBusy`, or the words "rate limit"), marks that node blocked for the rest of the run. The result is UNAVAILABLE, "operator refused", never FAIL, and the node is not asked again. There is no proxy, IP rotation or UA rotation option.
- **Light traffic.** At most one `tx` request per node per verify call, no retry loop, and a 10-second timeout. The anchor tool submits through the one node it is given and never fans out.

`tests/test_xrpl_verify.py` (class `NodeUsePolicy`) fails if any of these changes.

## Rule

**If any operator asks us to stop, take their node off the defaults in the next commit,** and record the date and the request here. Adding a node to the defaults requires its terms to be read and recorded in this table first.
