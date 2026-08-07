#!/usr/bin/env python3
"""otscheck.py — the ONE place this bundle decides whether the OpenTimestamps
client actually confirmed a Bitcoin attestation.

WHY THIS FILE EXISTS
--------------------
2026-08-06 defect hunt. Two scripts in this bundle ran `ots verify` and then
decided the result by asking "does the expected hash appear anywhere in the
output?" — while printing, and completely ignoring, the client's own exit
code. Reproduced with a stand-in client that printed

    Failed! Attestation for aabb… could not be verified

and exited 1. Both call sites reported OK.

Two things were wrong at once:

  * A FAILED verification passed, because the failure message names the hash
    it failed on, which satisfied the substring test.
  * A GENUINE success would have FAILED, because the real client's success
    line ("Success! Bitcoin block 700000 attests existence as of …") does not
    echo the hash at all.

The verdict was, near enough, inverted. This was the only path in the whole
published toolchain that consults the chain, so it is the one check a reader
leans on hardest.

THE RULE HERE
-------------
Binding and verdict are separated, and both must hold:

  1. BINDING is established LOCALLY, before the client runs — the .ots file's
     header magic and embedded 32-byte digest must equal the hash we claim to
     be checking. This is what stops a client's output about some *other*
     timestamp from being read as evidence about ours. Scraping stdout for a
     hex string never established this.
  2. The VERDICT is the client's, not ours. Exit code 0 AND an affirmative
     attestation marker. Anything unrecognised fails closed.

`PENDING` is reported distinctly and is NOT a pass: an attestation waiting on
confirmation is exactly the state a reader must not mistake for "on Bitcoin".
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

OTS_HEADER_MAGIC = b"\x00OpenTimestamps\x00\x00Proof\x00\xbf\x89\xe2\xe8\x84\xe8\x92\x94"
_EMBEDDED_HASH_OFFSET = len(OTS_HEADER_MAGIC) + 2

# Only this one counts as "the chain confirmed it".
VERIFIED = "VERIFIED"
# Everything below is a non-pass, kept distinct so callers can explain WHY.
PENDING = "PENDING"            # attestation exists, not yet confirmed
FAILED = "FAILED"              # the client rejected it
UNAVAILABLE = "UNAVAILABLE"    # the check could not RUN (no client, no node)
UNBOUND = "UNBOUND"            # .ots does not commit to the hash we asked about
INDETERMINATE = "INDETERMINATE"  # exit 0 but no verdict we recognise

PASSING = (VERIFIED,)

# The real client says "Success! Bitcoin block N attests existence as of …".
_SUCCESS = re.compile(r"success!|attests existence", re.I)
_PENDING = re.compile(r"pending confirmation|pending attestation", re.I)
# Deliberately NOT a bare \bfailed\b: "failed to connect" is infrastructure,
# not a verdict on the proof, and matching it here would flip every
# unreachable-node run back into a false "your receipt is bad". These are the
# client's actual rejection wordings.
_FAILED = re.compile(
    r"failed!|could not be verified|invalid timestamp|invalid attestation|"
    r"bad attestation", re.I)

# "The check could not run" must NEVER be reported as "your proof is bad".
# Verified against opentimestamps-client v0.7.2, which needs a local Bitcoin
# node and does NOT fall back to a block explorer: with no node it exits 1
# with "Could not connect to Bitcoin node: Cookie file unusable …". Folding
# that into FAILED would tell a customer holding a perfectly good receipt
# that it failed verification — a false alarm on a trust product is its own
# kind of harm.
_INFRA = re.compile(
    r"could not connect to bitcoin node|cookie file unusable|"
    r"rpcpassword not specified|connection refused|failed to connect|"
    r"connection reset|temporary failure in name resolution|timed out",
    re.I)


def local_binding(ots_path: Path, expected_hash_hex: str) -> tuple[bool, str]:
    """Does this .ots file actually commit to expected_hash_hex?

    Structural, offline, and cheap. It proves the file is an OpenTimestamps
    proof ABOUT OUR HASH; it proves nothing about Bitcoin.
    """
    try:
        data = ots_path.read_bytes()
    except OSError as e:
        return False, f"unreadable: {e}"
    if not data.startswith(OTS_HEADER_MAGIC):
        return False, "not an OpenTimestamps proof (bad header magic)"
    try:
        expected = bytes.fromhex(expected_hash_hex)
    except ValueError:
        return False, f"expected hash is not hex: {expected_hash_hex!r}"
    if len(expected) != 32:
        return False, "expected hash is not 32 bytes"
    embedded = data[_EMBEDDED_HASH_OFFSET:_EMBEDDED_HASH_OFFSET + 32]
    if embedded != expected:
        return False, (f"proof commits to {embedded.hex()[:16]}… but we are "
                       f"checking {expected_hash_hex[:16]}… — this proof is "
                       f"about a different file")
    return True, "commits to the expected hash"


def chain_verdict(ots_path: Path, expected_hash_hex: str,
                  timeout: int = 120) -> tuple[str, int | None, str]:
    """Return (status, bitcoin_block_height_or_None, human_message).

    Only a status of VERIFIED means the OpenTimestamps client confirmed a
    Bitcoin attestation for this exact hash. Callers MUST NOT treat any other
    status as success — that is the bug this module was written to end.
    """
    bound, why = local_binding(ots_path, expected_hash_hex)
    if not bound:
        return UNBOUND, None, f"{ots_path.name}: {why}"

    try:
        # `-d` makes the CLIENT bind to our digest. Without it the client
        # infers a target filename by stripping ".ots" and fails outright
        # when the original file is not sitting next to the proof — which is
        # the normal case for someone checking a receipt they were handed.
        proc = subprocess.run(
            ["ots", "verify", "-d", expected_hash_hex, str(ots_path)],
            check=False, capture_output=True, text=True, timeout=timeout,
        )
    except FileNotFoundError:
        return (UNAVAILABLE, None,
                "ots binary not found on PATH — the chain step did NOT run. "
                "install: pip install opentimestamps-client")
    except subprocess.TimeoutExpired:
        return UNAVAILABLE, None, f"ots verify timed out after {timeout}s"

    combined = (proc.stdout or "") + (proc.stderr or "")
    height = None
    for m in re.finditer(r"block\s+(\d+)", combined, re.I):
        h = int(m.group(1))
        height = h if height is None else max(height, h)

    # An EXPLICIT rejection outranks any infrastructure noise. The client can
    # reject a proof and, in the same run, mention a calendar it could not
    # reach; classifying that as "the check did not run" would be this
    # module's own bug in reverse — a real failure reported as a non-answer.
    # Only when the client rendered no verdict at all do we call it
    # UNAVAILABLE.
    explicit_rejection = _FAILED.search(combined)

    # A client that could not reach a Bitcoin node has not judged this
    # attestation, and saying FAILED would be a lie about the customer's
    # evidence.
    if _INFRA.search(combined) and not explicit_rejection:
        return (UNAVAILABLE, height,
                f"{ots_path.name}: the OpenTimestamps client could not reach a "
                f"Bitcoin node, so the chain step did NOT run. This says "
                f"NOTHING about whether the timestamp is good. Point `ots` at "
                f"a node (it needs one — it does not use a block explorer), "
                f"or run `ots info` to read the attestation offline.")

    # Order matters: an explicit failure or pending marker outranks a stray
    # success-looking word elsewhere in the output.
    if _PENDING.search(combined):
        return (PENDING, height,
                f"{ots_path.name}: attestation is PENDING confirmation — not "
                f"yet on Bitcoin")
    if proc.returncode != 0:
        return (FAILED, height,
                f"{ots_path.name}: ots verify exited {proc.returncode} — the "
                f"client did NOT confirm this attestation")
    if explicit_rejection:
        return (FAILED, height,
                f"{ots_path.name}: ots verify reported a failure despite exit "
                f"0 — treating as NOT confirmed")
    if _SUCCESS.search(combined):
        at = f" (Bitcoin block {height})" if height is not None else ""
        return VERIFIED, height, f"{ots_path.name}: confirmed on Bitcoin{at}"
    return (INDETERMINATE, height,
            f"{ots_path.name}: ots verify exited 0 but printed no attestation "
            f"we recognise — failing closed rather than assuming success")


def check_dir(ots_dir: Path, expected_hash_hex: str
              ) -> tuple[bool, int | None, list[str]]:
    """Verdict over every *.ots in a directory.

    Returns (all_verified, best_block_height, messages). ZERO .ots files is a
    FAILURE, not a vacuous pass: with no proof file the loop below never runs,
    and a bundle carrying no timestamp evidence would otherwise be reported as
    confirmed.
    """
    msgs: list[str] = []
    files = sorted(ots_dir.glob("*.ots"))
    if not files:
        return False, None, [
            f"NO .ots FILES in {ots_dir} — there is no timestamp evidence "
            f"here; nothing establishes when this existed"]
    ok = True
    height: int | None = None
    for p in files:
        status, h, msg = chain_verdict(p, expected_hash_hex)
        if h is not None:
            height = h if height is None else max(height, h)
        msgs.append(f"[{status}] {msg}")
        if status not in PASSING:
            ok = False
    return ok, height, msgs
