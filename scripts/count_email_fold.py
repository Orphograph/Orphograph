#!/usr/bin/env python3
"""count_email_fold.py - read-only census for the account-id case-fold decision.

auth.email_id keys accounts on HMAC(email.lower()), and EMAIL_RE accepts
non-ASCII local parts, so a spelling with U+212A KELVIN SIGN folds onto the
ASCII "k" address of someone else. This counts, on the live data, how many
stored spellings each fix option would strand, re-key or merge, and whether
any twin spelling is live today (sessions, link tokens, API keys, webhooks,
suppressions).

Run on the app machine:

    fly ssh console -a orphograph -C "python3 /app/scripts/count_email_fold.py"

Stdlib only. Opens every file read-only and never creates the HMAC secret
(mirrors auth.existing_hmac_secret). Prints fixed key names and integers
only, never a value read from disk.
"""
import glob, hashlib, hmac, json, os, string, sys, time, unicodedata as ud
from collections import defaultdict

DATA = os.environ.get("ORPHO_DATA_DIR", "/app/data")   # fly.toml:14,30
T = str.maketrans(string.ascii_uppercase, string.ascii_lowercase)
def fold(s): return s.strip().translate(T)                     # email_fold.py:26-31
def low(s): return s.lower()                                   # auth.py:121 (callers strip)
def nkcf(s): return ud.normalize("NFKC", ud.normalize("NFKC", s).casefold())
TWIN = {c for c in map(chr, range(0x80, 0x110000))
        if not 0xD800 <= ord(c) <= 0xDFFF and c.lower() != c and c.lower().upper() != c}
KEYS = ("email", "owner", "member", "invitee")

def secret():
    env = os.environ.get("ORPHO_HMAC_SECRET", "")
    if env:
        return env.encode("utf-8")
    p = os.environ.get("ORPHO_HMAC_SECRET_PATH", os.path.join(DATA, ".hmac_secret"))
    try:
        with open(p, "rb") as f:
            return f.read()
    except OSError:
        return None
SEC = secret()
def hid(s): return hmac.new(SEC, s.encode("utf-8"), hashlib.sha256).hexdigest()[:16]

def rows(path):
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if isinstance(r, dict):
                    yield r
    except OSError:
        return

def addrs(r):
    for k, v in r.items():
        if not any(t in k.lower() for t in KEYS):
            continue
        for x in (v if isinstance(v, list) else [v]):
            if isinstance(x, str) and (x.startswith("enc:") or "@" in x):
                yield x.strip()

out = defaultdict(int)
spell = defaultdict(set)          # spelling -> set of ledger basenames
enc = 0
for path in sorted(glob.glob(os.path.join(DATA, "*.jsonl"))):
    base = os.path.basename(path)
    for r in rows(path):
        for a in addrs(r):
            if a.startswith("enc:"):
                enc += 1
                continue
            spell[a].add(base)
receipts = []
for rp in glob.glob(os.path.join(DATA, "receipts", "*", "receipt.json")):
    try:
        with open(rp, "r", encoding="utf-8") as f:
            rec = json.load(f)
    except (OSError, ValueError):
        out["receipts_unreadable"] += 1
        continue
    if not isinstance(rec, dict):
        continue
    receipts.append(rec)
    ne = rec.get("notify_email")
    if isinstance(ne, str) and "@" in ne and not ne.startswith("enc:"):
        spell[ne.strip()].add("receipt.notify_email")

S = list(spell)
na = [s for s in S if not s.isascii()]
out["spellings_total"] = len(S)
out["spellings_encrypted_values_skipped"] = enc
out["spellings_any_nonascii"] = len(na)
out["spellings_fold_ne_lower"] = sum(fold(s) != low(s) for s in S)
out["spellings_nfkc_casefold_ne_lower"] = sum(nkcf(s) != low(s) for s in S)
out["spellings_with_twin_codepoint"] = sum(any(c in TWIN for c in s) for s in S)
out["spellings_with_U+212A"] = sum("\u212a" in s for s in S)
per = defaultdict(int)
for s in S:
    if fold(s) != low(s):
        for b in spell[s]:
            per[b] += 1
for b in sorted(per):
    out["fold_ne_lower_in:" + b] = per[b]

now = time.time()
def live(path, idkey, okevent):
    state = {}
    for r in rows(os.path.join(DATA, path)):
        h = r.get(idkey)
        if h:
            state[h] = r
    n = 0
    for r in state.values():
        e = r.get("email")
        if (r.get("event") == okevent and isinstance(e, str) and fold(e) != low(e.strip())
                and float(r.get("expires_unix", now + 1) or 0) > now):
            n += 1
    return n
def tw(e): return isinstance(e, str) and any(c in TWIN for c in e)
def fl(e): return isinstance(e, str) and fold(e) != low(e.strip())
def live2(path, idkey, okevent):
    state = {}
    for r in rows(os.path.join(DATA, path)):
        h = r.get(idkey)
        if h:
            state[h] = r
    return [r.get("email") for r in state.values() if r.get("event") == okevent
            and float(r.get("expires_unix", now + 1) or 0) > now]
for name, path, idk, ev in (("live_sessions", "auth_sessions.jsonl", "session_hash", "created"),
                            ("live_link_tokens", "auth_tokens.jsonl", "token_hash", "issued")):
    es = live2(path, idk, ev)
    out[name + "_fold_ne_lower"] = sum(map(fl, es))
    out[name + "_twin_codepoint"] = sum(map(tw, es))
kr = list(rows(os.path.join(DATA, "api_keys.jsonl")))
revoked = {r.get("key_hash") for r in kr if r.get("event") == "revoked"}
ke = [r.get("email") for r in kr if r.get("event") == "issued" and r.get("key_hash") not in revoked]
out["live_api_keys_fold_ne_lower"] = sum(map(fl, ke))
out["live_api_keys_twin_codepoint"] = sum(map(tw, ke))
wst = {}
for r in rows(os.path.join(DATA, "webhooks.jsonl")):          # keyed as webhooks.py:109-113
    e, u = r.get("email"), r.get("url")
    if isinstance(e, str) and e and isinstance(u, str) and u:
        wst[(e.lower(), u)] = r
we = [r.get("email") for r in wst.values() if r.get("event") != "deleted"]
out["active_webhooks_fold_ne_lower"] = sum(map(fl, we))
out["active_webhooks_twin_codepoint"] = sum(map(tw, we))

if SEC is None:
    out["hmac_secret_missing_id_counts_skipped"] = 1
else:
    by_old = defaultdict(set)                 # old id -> spellings
    for s in S:
        by_old[hid(low(s))].add(s)
    rekeyed = {x for x, ss in by_old.items() if any(fold(s) != low(s) for s in ss)}
    no_lower_preimage = {x for x in rekeyed if not any(fold(s) == low(s) for s in by_old[x])}
    ambiguous = {x for x, ss in by_old.items() if len({fold(s) for s in ss}) > 1}
    nonascii_ids = {x for x, ss in by_old.items() if any(not s.isascii() for s in ss)}
    out["old_ids_with_nonascii_spelling"] = len(nonascii_ids)
    amb_twin = {x for x in ambiguous if any(any(c in TWIN for c in s) for s in by_old[x])}
    nk = defaultdict(set)
    for x, ss in by_old.items():
        for s in ss:
            nk[nkcf(s)].add(x)
    out["old_ids_total"] = len(by_old)
    out["old_ids_rekeyed_under_ascii_fold"] = len(rekeyed)
    out["old_ids_rekeyed_with_no_lowercase_spelling_on_file"] = len(no_lower_preimage)
    out["old_ids_with_more_than_one_fold_class"] = len(ambiguous)
    out["old_ids_ambiguous_involving_twin_codepoint"] = len(amb_twin)
    out["nfkc_casefold_classes_merging_2plus_existing_ids"] = sum(len(v) > 1 for v in nk.values())
    known = set(by_old)
    for rec in receipts:
        ids = set()
        for k in ("account_id", "owner_id"):
            if isinstance(rec.get(k), str) and rec[k]:
                ids.add(rec[k])
        src = rec.get("source")
        if isinstance(src, str) and src.startswith("sub:"):
            ids.add(src[4:])
        if not ids:
            continue
        out["receipts_with_account_identity"] += 1
        if ids & rekeyed:
            out["receipts_on_rekeyed_ids"] += 1
            if rec.get("private"):
                out["private_receipts_on_rekeyed_ids"] += 1
        if ids & no_lower_preimage:
            out["receipts_on_ids_with_no_lowercase_spelling"] += 1
        if ids & nonascii_ids:
            out["receipts_on_ids_with_nonascii_spelling"] += 1
        if ids & ambiguous:
            out["receipts_on_ambiguous_ids"] += 1
        if not ids & known:
            out["receipts_with_no_plaintext_preimage_on_file"] += 1
    for r in rows(os.path.join(DATA, "x402_ledger.jsonl")):
        if r.get("owner_id") in rekeyed:
            out["x402_rows_owner_on_rekeyed_ids"] += 1
    sup = {(r.get("email") or "").strip() for f in ("resend_suppressed_emails.jsonl", "suppressions.jsonl")
           for r in rows(os.path.join(DATA, f))}
    out["suppressed_addrs_equal_to_lower_of_a_twin_spelling"] = sum(
        1 for s in S if any(c in TWIN for c in s) and low(s) in sup)

for k in sorted(out):
    print(f"{k}={int(out[k])}")
