#!/usr/bin/env python3
"""email_fold.py — the one form two spellings of a mailbox compare in.

Sign-in keeps the case the person typed and the Stripe webhook keeps the case
Stripe sends, so "Alice@Example.com" and "alice@example.com" arrive as two
strings for one person. The team ledger, the API-key ledger and the key
issuance limiter all ask "is this the same account?", and each of them
compares fold_email() on both sides.

Only ASCII A-Z is folded. str.lower() also maps some characters outside ASCII
onto ASCII letters: U+212A KELVIN SIGN lowers to "k". EMAIL_RE admits that
character, so an address typed with a Kelvin sign in place of "K" counted as
the owner of the plain-"k" address's team, and the two accounts shared one
key-issuance budget (2026-09-26). Folding only A-Z keeps every non-ASCII
character exact. That can keep two spellings of one mailbox apart, but it can
never merge two different mailboxes, and merging is the failure that hands
one person another person's team.
"""
from __future__ import annotations

import string

_ASCII_UPPER_TO_LOWER = str.maketrans(string.ascii_uppercase, string.ascii_lowercase)


def fold_email(email) -> str:
    """`email` stripped, with ASCII A-Z lowercased and nothing else changed.
    Anything that is not a string folds to "", which matches no account."""
    if not isinstance(email, str):
        return ""
    return email.strip().translate(_ASCII_UPPER_TO_LOWER)
