#!/usr/bin/env python3
"""standing_record.py — which receipts the public Standing Record may list.

The Standing Record (/standing-record, /api/standing-record) shows the
anchors the office makes of its own work every week (scripts/weekly_anchor.py).
It used to list ANY public receipt whose client_label started with "weekly-",
so anyone could anchor with such a label and appear there as if the office
had published it. Founder decision 2026-09-28 (option A): a row is listed only
when

  (a) it carries the office's Ed25519 signature over its own label and hash,
      checked against a pinned office public key every time the list is built
      (a stored "verified" flag is never believed), or
  (b) its receipt id is in HISTORICAL_RECEIPT_IDS, the closed list of rows the
      job made before it signed anything.

The weekly job imports this module to build the very bytes the server checks,
so it must stay standard library only and run on Python 3.9 (the macOS system
Python launchd uses).
"""
from __future__ import annotations

import os
import sys

import manifest_signature

# The signed statement. The domain label keeps an office signature made for
# this purpose from ever being read as a signature over anything else.
DOMAIN = b"orphograph-standing-record-v1"
LABEL_PREFIX = "weekly-"
# The request field, and the receipt field it is stored under: 128 lowercase
# hex characters (a 64-byte signature).
FIELD = "office_signature"

# Office public keys, hex. A row signed by any of them is listed. To rotate,
# ADD the new key and keep the old one, so what the old key signed stays
# listed; removing a key takes everything it signed off the page.
PINNED_OFFICE_KEYS = (
    # 2026-09-30, scripts/office_key.py generate, ~/.orphograph/office_signing_key
    # on the founder's Mac (the machine the weekly job runs on).
    "92597195903e762c61854705eba3c26339f4816dc86973d09c1e26d9a56615d9",
)

# Receipt ids the weekly job recorded in its own log
# (outbox/weekly_anchor_log.jsonl on the founder's Mac, read 2026-09-30) for
# every run from 2026-05-24 to 2026-09-27, before it signed anything: 16 from
# the old one-receipt-per-file runs and 11 from the folder runs. 28 runs are
# logged; 14 of them made no receipt. This list is CLOSED. No id is ever
# added to it; a row made from now on is listed only if it is signed.
HISTORICAL_RECEIPT_IDS = frozenset({
    # per-file runs, 2026-07-04 and 2026-07-27
    "vOKfLv_xbYIBOCE-", "00vXka2H3Inj07DI", "Rk-yp-IAXds7YN9w", "Jv6GfXmodsN17N5X",
    "dt6YYKVfDQ4IOJDw", "fOhU99ujOchpn5As", "ILW0K1iSh2NWVjHJ", "9R7vMbR8D62iMUNH",
    "Te7CljnhZ9mI4KgV", "KzAy1k4af1G7Mu3m", "t6pEsXORtgBpf_bH", "Mni6abG5Ie1aYHw2",
    "ABsX1FpzdFjaaiSs", "0piaMM_z79uSFil5", "ScIiLCo5SL2saHJH", "fT_CzN5glbBWykPb",
    # folder runs, 2026-07-27 to 2026-09-27
    "7NM30DhgcAkmiXyr", "AVfwIT1EWlG2FOvD", "2CJLTNmNrQwN7cOC", "yjnk7_pAruwzvv2T",
    "VMJb4eVJ-7KDGGAY", "5CKMcXII4yCnjaiw", "3zDvbIE-aCABFDit", "uUDuAJfo23DkYwE8",
    "kaMnglXCi9fTZPSB", "oRfnPlcYH3pI95UG", "xgHsOc463fnB74Yu",
})

# Ed25519 checks one cold listing may spend, at most. Every stored signature
# was already checked when it was anchored, so in normal running a listing
# spends one check per listed signed row (16 at most, times the number of
# pinned keys). The cap is for the abnormal case, such as a removed key
# leaving many rows that no longer verify. The stdlib code production runs
# takes about 7 ms a check, so this is well under a second every 300 s.
MAX_SIGNATURE_CHECKS = 48

_HEX = frozenset("0123456789abcdef")


def _is_hex(value: object, length: int) -> bool:
    return isinstance(value, str) and len(value) == length and set(value) <= _HEX


def _keys_from_env() -> tuple:
    """Extra trusted keys from ORPHO_OFFICE_PUBLIC_KEYS (comma-separated hex),
    read once when the server starts. Tests give the server a throwaway key
    this way; production trusts PINNED_OFFICE_KEYS."""
    out = []
    for part in os.environ.get("ORPHO_OFFICE_PUBLIC_KEYS", "").split(","):
        part = part.strip().lower()
        if not part:
            continue
        if _is_hex(part, 64):
            out.append(bytes.fromhex(part))
        else:
            sys.stderr.write("[standing_record] ignoring a malformed key in "
                             "ORPHO_OFFICE_PUBLIC_KEYS\n")
    return tuple(out)


def _pinned() -> tuple:
    # A malformed pin is skipped rather than crashing the server at start;
    # a test holds every pin to 64 lowercase hex characters.
    return tuple(bytes.fromhex(k) for k in PINNED_OFFICE_KEYS if _is_hex(k, 64))


TRUSTED_KEYS = _pinned() + _keys_from_env()


def backend_available() -> bool:
    return manifest_signature.signature_backend_available()


def statement(client_label: str, hash_hex: str) -> bytes:
    """The bytes the office signs for one row. The hash is fixed-width hex at
    the end, so a label containing a newline still cannot make two different
    (label, hash) pairs give the same bytes."""
    if not isinstance(client_label, str):
        raise ValueError("client_label must be a string")
    if not _is_hex(hash_hex, 64):
        raise ValueError("hash_hex must be 64 lowercase hex characters")
    return DOMAIN + b"\n" + client_label.encode("utf-8") + b"\n" + hash_hex.encode("ascii")


def parse_signature(value: object):
    """The 64 signature bytes, or None when the value is not 128 lowercase hex."""
    return bytes.fromhex(value) if _is_hex(value, 128) else None


def public_key(seed: bytes) -> bytes:
    # Signing anything also yields the public key; manifest_signature has no
    # separate call for it and this keeps one backend switch in one place.
    _sig, pk = manifest_signature._sign_raw(DOMAIN, seed)
    return pk


def sign(client_label: str, hash_hex: str, seed: bytes) -> str:
    """The office signature (hex) for one row. Used by the weekly job."""
    sig, _pk = manifest_signature._sign_raw(statement(client_label, hash_hex), seed)
    return sig.hex()


def verifies(client_label: str, hash_hex: str, signature: bytes, keys=None) -> bool:
    keys = TRUSTED_KEYS if keys is None else keys
    try:
        msg = statement(client_label, hash_hex)
    except ValueError:
        return False
    return any(manifest_signature._verify_raw(msg, signature, k) for k in keys)


def _order(rec: dict) -> tuple:
    return (str(rec.get("created_at") or ""), str(rec.get("receipt_id") or ""))


def listed(records, limit: int = 16, keys=None) -> list:
    """The receipts the Standing Record shows, newest first.

    `records` are receipt.json dicts. A row must be public and labelled
    "weekly-", and then either be in HISTORICAL_RECEIPT_IDS or carry an
    office signature that verifies now. Signed rows are grouped by the
    statement they sign and each statement is shown once, at its earliest
    row: a signature is public once stored, so a stranger can re-anchor the
    same label and hash with a copy of it, and the copy must add nothing.
    """
    keys = TRUSTED_KEYS if keys is None else tuple(keys)
    shown = []
    by_statement: dict = {}
    for rec in records:
        if not isinstance(rec, dict) or rec.get("private"):
            continue
        label = rec.get("client_label")
        if not isinstance(label, str) or not label.startswith(LABEL_PREFIX):
            continue
        rid = rec.get("receipt_id")
        if isinstance(rid, str) and rid in HISTORICAL_RECEIPT_IDS:
            shown.append(rec)
            continue
        sig = parse_signature(rec.get(FIELD))
        if sig is None or not _is_hex(rec.get("hash_hex"), 64):
            continue
        msg = statement(label, rec["hash_hex"])
        by_statement.setdefault(msg, []).append((rec, sig))

    groups = sorted(by_statement.items(), key=lambda g: min(_order(r) for r, _ in g[1]),
                    reverse=True)
    checks = 0
    found = 0
    for msg, rows in groups:
        if found >= limit:
            # Older than `limit` statements already found: cannot be shown.
            break
        rows.sort(key=lambda pair: _order(pair[0]))
        tried = set()
        for rec, sig in rows:
            if sig in tried:
                continue          # a copy of a signature already refused
            tried.add(sig)
            ok = False
            for key in keys:
                if checks >= MAX_SIGNATURE_CHECKS:
                    break
                checks += 1
                if manifest_signature._verify_raw(msg, sig, key):
                    ok = True
                    break
            if ok:
                shown.append(rec)
                found += 1
                break
            if checks >= MAX_SIGNATURE_CHECKS:
                break
        if checks >= MAX_SIGNATURE_CHECKS:
            break
    shown.sort(key=_order, reverse=True)
    return shown[:limit]


__all__ = [
    "DOMAIN", "FIELD", "PINNED_OFFICE_KEYS", "HISTORICAL_RECEIPT_IDS",
    "TRUSTED_KEYS", "MAX_SIGNATURE_CHECKS", "statement", "parse_signature",
    "public_key", "sign", "verifies", "listed", "backend_available",
]
