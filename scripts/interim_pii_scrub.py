#!/usr/bin/env python3
"""interim_pii_scrub.py — one-shot INTERIM PII scrub for the live Orphograph Fly volume.

WHY THIS EXISTS
---------------
Branch `harden/pii-log-scrub-2026-06-06` fixes PII leaking into logs/ledgers but is
frozen pending GitHub reinstatement. Production still runs the older code. This
script applies — at rest, on the volume — ONLY the subset of the branch's fixes
that the OLD code can tolerate, using the EXACT storage scheme the branch itself
uses (email_crypto `enc:v1:` authenticated encryption), so prod data converges
byte-for-byte in format with post-merge behavior and the deployed branch will
transparently decrypt these rows.

WHAT IT SCRUBS (allow-list — nothing else is ever written)
----------------------------------------------------------
  refund_requests.jsonl   field `email` -> email_crypto.encrypt(email)  [no AAD,
                          matching app.py on the branch]. Safe NOW because the
                          only reader in production code is a row COUNT
                          (founder snapshot, app.py ~2364) that never touches
                          the email field.

WHAT IT REFUSES TO TOUCH
------------------------
  * THE BOOKS (hard deny-list): ledger.jsonl, credit_ledger.jsonl,
    subscriptions.jsonl, stripe_customer_emails.jsonl, btc_orders.jsonl,
    btc_claims.jsonl, anchors.jsonl, stripe_processed_events.jsonl,
    nowpayments_processed_events.jsonl, manual_fulfillment_queue.jsonl,
    receipts/ — append-only money records. NEVER mutated, by anyone, ever.
  * DEFERRED-TO-DEPLOY ledgers: suppressions.jsonl, waitlist.jsonl,
    api_keys.jsonl, teams.jsonl, team_invites.jsonl, webhooks.jsonl,
    referrals.jsonl, onboarding_state.jsonl, gdpr_deletions.jsonl. The branch
    encrypts these too — but the OLD code still running on prod matches their
    `email` fields in CLEARTEXT (unsubscribe suppression, newsletter sends,
    API-key revocation, team membership, referral dedup, drip state, GDPR
    export/delete). Encrypting them before the branch deploys would silently
    break those functions (incl. CAN-SPAM suppression — a legal function).
    They become scrubbable the moment the branch is live; extend SCRUB_NOW then.
  * Anything it does not recognize: unknown files are skipped and reported.

HOW TO RUN (on the Fly box, from the main session — this script is stdlib-only)
-------------------------------------------------------------------------------
  # 0. PRE-REQ: the app secret ORPHO_EMAIL_ENC_KEY must exist (the branch's
  #    encryption key — inert for the old code, which never reads it):
  #      python3 -c "import secrets,base64; print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())"
  #      fly secrets set ORPHO_EMAIL_ENC_KEY=<value> -a <app>     # NOTE: restarts the machine
  # 1. Backup (REQUIRED — see runbook):
  #      fly ssh console -a <app> -C "tar -czf /app/data/pre_pii_scrub_$(date +%Y%m%d).tar.gz -C /app/data refund_requests.jsonl"
  # 2. Upload this file:
  #      fly ssh sftp shell -a <app>   ->   put .../interim_pii_scrub.py /tmp/interim_pii_scrub.py
  # 3. Dry run (no writes):
  #      fly ssh console -a <app> -C "python3 /tmp/interim_pii_scrub.py --data-dir /app/data"
  # 4. Execute:
  #      fly ssh console -a <app> -C "python3 /tmp/interim_pii_scrub.py --data-dir /app/data --yes"
  # 5. Idempotence check (must report 0 changes):
  #      fly ssh console -a <app> -C "python3 /tmp/interim_pii_scrub.py --data-dir /app/data --yes"

SAFETY PROPERTIES
-----------------
  * Refuses to mutate anything without --yes (dry-run report otherwise).
  * Refuses to run at all if ORPHO_EMAIL_ENC_KEY is missing/invalid (a silent
    no-op "scrub" must not be mistakable for a completed one).
  * In-process encrypt->decrypt self-test before any file is opened.
  * Stream-rewrites to <file>.tmp.<pid> in the same directory, fsync, then
    atomic os.replace(); ownership and permission bits are copied from the
    original. Unchanged lines are passed through byte-identical.
  * Idempotent: rows already carrying the `enc:v1:` prefix are never re-wrapped
    (same rule as the branch's email_crypto.encrypt).
  * Per-file counters: total / changed / already-encrypted / non-JSON / no-email.

The crypto below is a verbatim functional copy of the branch's
server/email_crypto.py (stdlib HMAC-SHA256 encrypt-then-MAC, PKCS7-padded CTR,
random 128-bit nonce, urlsafe-b64, prefix "enc:v1:"). It must NOT be "improved"
here: byte-format compatibility with the branch is the whole point.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import os
import secrets
import sys
from pathlib import Path

# --------------------------------------------------------------------------- #
# File classification (names only — matched against basenames in --data-dir)   #
# --------------------------------------------------------------------------- #
# file -> list of (field_name, aad_field_or_None). aad_field names a sibling
# key whose value is bound as AAD at encrypt time (branch behavior for
# credit_ledger uses claim_code; refund_requests uses no AAD).
SCRUB_NOW: dict[str, list[tuple[str, str | None]]] = {
    "refund_requests.jsonl": [("email", None)],
}

# Hard deny — THE BOOKS. Never touched, even if someone edits SCRUB_NOW badly.
BOOKS_DENY = frozenset({
    "ledger.jsonl",
    "credit_ledger.jsonl",
    "subscriptions.jsonl",
    "stripe_customer_emails.jsonl",
    "btc_orders.jsonl",
    "btc_claims.jsonl",
    "anchors.jsonl",
    "stripe_processed_events.jsonl",
    "nowpayments_processed_events.jsonl",
    "manual_fulfillment_queue.jsonl",
    "upgrade_log.jsonl",
    "expiry_log.jsonl",
    "affiliate_ledger.jsonl",
    "affiliate_codes.jsonl",
    "payout_pings.jsonl",
})

# Known but deferred until the branch deploys (old code matches these emails in
# cleartext). Reported, never written.
DEFERRED_TO_DEPLOY = frozenset({
    "suppressions.jsonl",
    "waitlist.jsonl",
    "api_keys.jsonl",
    "teams.jsonl",
    "team_invites.jsonl",
    "webhooks.jsonl",
    "referrals.jsonl",
    "onboarding_state.jsonl",
    "gdpr_deletions.jsonl",
    "auth_tokens.jsonl",
    "auth_sessions.jsonl",
    "stripe_cancellations.jsonl",
})

assert not (set(SCRUB_NOW) & BOOKS_DENY), "SCRUB_NOW overlaps the books deny-list"
assert not (set(SCRUB_NOW) & DEFERRED_TO_DEPLOY), "SCRUB_NOW overlaps deferred list"

# --------------------------------------------------------------------------- #
# email_crypto — functional copy of the branch's server/email_crypto.py        #
# --------------------------------------------------------------------------- #
_ENV_KEY = "ORPHO_EMAIL_ENC_KEY"
_PREFIX = "enc:v1:"
_DOMAIN = b"orpho-email-v1"
_NONCE_LEN = 16
_TAG_LEN = 32
_PAD_BLOCK = 16


def _decode_key(raw: str) -> bytes | None:
    raw = (raw or "").strip()
    if not raw:
        return None
    for dec in (base64.urlsafe_b64decode, base64.b64decode):
        try:
            k = dec(raw + "=" * (-len(raw) % 4))
        except Exception:
            continue
        if len(k) >= 32:
            return k[:32]
    return None


def _master() -> bytes | None:
    return _decode_key(os.environ.get(_ENV_KEY, ""))


def _subkey(master: bytes, label: bytes) -> bytes:
    return hmac.new(master, label, hashlib.sha256).digest()


def _keystream(enc_key: bytes, nonce: bytes, n: int) -> bytes:
    out = bytearray()
    ctr = 0
    while len(out) < n:
        out.extend(hmac.new(enc_key, nonce + ctr.to_bytes(4, "big"), hashlib.sha256).digest())
        ctr += 1
    return bytes(out[:n])


def _pad(b: bytes) -> bytes:
    n = _PAD_BLOCK - (len(b) % _PAD_BLOCK)
    return b + bytes([n]) * n


def _unpad(b: bytes) -> bytes:
    if not b:
        raise ValueError("empty plaintext")
    n = b[-1]
    if n < 1 or n > _PAD_BLOCK or n > len(b) or b[-n:] != bytes([n]) * n:
        raise ValueError("bad padding")
    return b[:-n]


def _seal(master: bytes, pt: bytes, aad: bytes = b"") -> bytes:
    enc_key = _subkey(master, b"orpho-email-enc-v1")
    mac_key = _subkey(master, b"orpho-email-mac-v1")
    pt = _pad(pt)
    nonce = secrets.token_bytes(_NONCE_LEN)
    ks = _keystream(enc_key, nonce, len(pt))
    ct = bytes(a ^ b for a, b in zip(pt, ks))
    tag = hmac.new(
        mac_key, _DOMAIN + len(aad).to_bytes(4, "big") + aad + nonce + ct, hashlib.sha256
    ).digest()
    return nonce + ct + tag


def _open(master: bytes, blob: bytes, aad: bytes = b"") -> bytes:
    if len(blob) < _NONCE_LEN + _TAG_LEN:
        raise ValueError("ciphertext too short")
    nonce = blob[:_NONCE_LEN]
    tag = blob[-_TAG_LEN:]
    ct = blob[_NONCE_LEN:-_TAG_LEN]
    mac_key = _subkey(master, b"orpho-email-mac-v1")
    expect = hmac.new(
        mac_key, _DOMAIN + len(aad).to_bytes(4, "big") + aad + nonce + ct, hashlib.sha256
    ).digest()
    if not hmac.compare_digest(tag, expect):
        raise ValueError("authentication failed")
    enc_key = _subkey(master, b"orpho-email-enc-v1")
    ks = _keystream(enc_key, nonce, len(ct))
    return _unpad(bytes(a ^ b for a, b in zip(ct, ks)))


def _b64e(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode("ascii").rstrip("=")


def _b64d(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def encrypt_field(master: bytes, value: str, aad: bytes = b"") -> str:
    """Branch-identical: pass-through on empty or already-encrypted values."""
    if not value or value.startswith(_PREFIX):
        return value
    return _PREFIX + _b64e(_seal(master, value.encode("utf-8"), aad))


def decrypt_field(master: bytes, value: str, aad: bytes = b"") -> str:
    if not value or not value.startswith(_PREFIX):
        return value
    return _open(master, _b64d(value[len(_PREFIX):]), aad).decode("utf-8")


def _self_test(master: bytes) -> None:
    """Round-trip + tamper test BEFORE any file is opened. Abort on failure."""
    for probe, aad in (("alex@example.com", b""), ("x@y.zz", b"pk_test")):
        sealed = encrypt_field(master, probe, aad)
        if not sealed.startswith(_PREFIX) or decrypt_field(master, sealed, aad) != probe:
            sys.stderr.write("FATAL: email_crypto self-test round-trip failed\n")
            raise SystemExit(3)
        if encrypt_field(master, sealed, aad) != sealed:
            sys.stderr.write("FATAL: self-test: double-wrap not idempotent\n")
            raise SystemExit(3)
        try:
            decrypt_field(master, sealed, aad + b"X")
        except ValueError:
            pass
        else:
            sys.stderr.write("FATAL: self-test: AAD tamper not detected\n")
            raise SystemExit(3)


# --------------------------------------------------------------------------- #
# Scrub engine                                                                  #
# --------------------------------------------------------------------------- #
def scrub_file(path: Path, fields: list[tuple[str, str | None]],
               master: bytes, write: bool) -> dict:
    stats = {"file": path.name, "lines": 0, "changed": 0,
             "already_encrypted": 0, "non_json": 0, "no_target_field": 0}
    tmp = path.with_name(path.name + f".tmp.{os.getpid()}")
    st = path.stat()
    out = tmp.open("w", encoding="utf-8") if write else None
    try:
        with path.open("r", encoding="utf-8", errors="surrogateescape") as f:
            for raw in f:
                stats["lines"] += 1
                line = raw.rstrip("\n")
                new_line = line
                stripped = line.strip()
                if not stripped:
                    pass  # blank line: pass through verbatim
                else:
                    try:
                        row = json.loads(stripped)
                    except json.JSONDecodeError:
                        row = None
                    if not isinstance(row, dict):
                        stats["non_json"] += 1
                    else:
                        touched = False
                        hit_field = False
                        for field, aad_field in fields:
                            v = row.get(field)
                            if not isinstance(v, str) or not v:
                                continue
                            hit_field = True
                            if v.startswith(_PREFIX):
                                stats["already_encrypted"] += 1
                                continue
                            aad = b""
                            if aad_field:
                                aad = str(row.get(aad_field) or "").encode("utf-8")
                            row[field] = encrypt_field(master, v, aad)
                            touched = True
                        if not hit_field:
                            stats["no_target_field"] += 1
                        if touched:
                            stats["changed"] += 1
                            new_line = json.dumps(row, separators=(",", ":"))
                if out is not None:
                    out.write(new_line + "\n")
        if out is not None:
            out.flush()
            os.fsync(out.fileno())
            out.close()
            out = None
            # Preserve mode + ownership, then atomically swap in.
            os.chmod(tmp, st.st_mode & 0o7777)
            try:
                os.chown(tmp, st.st_uid, st.st_gid)
            except (PermissionError, OSError):
                pass  # non-root local dry-runs; on the Fly box we are root
            os.replace(tmp, path)
            # fsync the directory so the rename survives a crash.
            dfd = os.open(str(path.parent), os.O_RDONLY)
            try:
                os.fsync(dfd)
            finally:
                os.close(dfd)
    finally:
        if out is not None:
            out.close()
        if tmp.exists():
            tmp.unlink()
    return stats


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--data-dir", default=os.environ.get("ORPHO_DATA_DIR", "/app/data"),
                    help="data directory on the volume (default /app/data)")
    ap.add_argument("--yes", action="store_true",
                    help="actually rewrite files; without it, dry-run report only")
    args = ap.parse_args(argv)

    data_dir = Path(args.data_dir).resolve()
    if not data_dir.is_dir():
        sys.stderr.write(f"FATAL: data dir not found: {data_dir}\n")
        return 2

    master = _master()
    if master is None:
        sys.stderr.write(
            f"FATAL: {_ENV_KEY} is not set (or does not decode to >=32 bytes).\n"
            f"Refusing to run: a key-less scrub would be a silent no-op.\n"
            f"Set the Fly secret first (see header), then re-run.\n"
        )
        return 2
    _self_test(master)

    mode = "EXECUTE" if args.yes else "DRY-RUN (no writes; pass --yes to execute)"
    print(f"interim_pii_scrub: mode={mode} data_dir={data_dir}")

    total_changed = 0
    for entry in sorted(data_dir.iterdir()):
        name = entry.name
        if entry.is_dir():
            if name == "receipts":
                print(f"  SKIP (books/dir)        {name}/")
            continue
        if name in BOOKS_DENY:
            print(f"  SKIP (books — never)    {name}")
            continue
        if name in DEFERRED_TO_DEPLOY:
            print(f"  SKIP (deferred: old code reads cleartext) {name}")
            continue
        if name in SCRUB_NOW:
            stats = scrub_file(entry, SCRUB_NOW[name], master, write=args.yes)
            total_changed += stats["changed"]
            print(f"  SCRUB {name}: lines={stats['lines']} changed={stats['changed']} "
                  f"already_encrypted={stats['already_encrypted']} "
                  f"non_json={stats['non_json']} no_target_field={stats['no_target_field']}")
            continue
        print(f"  SKIP (unrecognized)     {name}")

    print(f"interim_pii_scrub: done. lines_changed={total_changed} mode={mode}")
    if not args.yes:
        print("interim_pii_scrub: NOTHING was modified (dry run).")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
