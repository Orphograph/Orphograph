#!/usr/bin/env python3
"""lineage_chain_e2e.py — drive the STANDALONE lineage verifier over a REAL
two-link chain, end to end, through the real HTTP entry point.

Why this exists
---------------
`tests/test_edit_lineage.py` proves the engine. `tests/test_lineage_endpoint.py`
proves the wire. Neither one ever runs `dist/orphograph-verify/verify_lineage.py`
-- the file a third party downloads and runs when they want to check a chain
without trusting us. That verifier had never been driven over a real bundle;
the 2026-08-19 cycle said so plainly rather than implying otherwise. This
closes it.

What it does, in order
----------------------
1. Starts a local server on a throwaway data dir with LIVE OTS submission
   (`engine._submit` is NOT stubbed), so every link gets real `.ots` bytes
   from the real calendars. Stubbed submission would leave a chain with no
   attestation, and `verify_lineage.py` correctly fails such a link -- so a
   stubbed run could never exercise the passing branch honestly.
2. Anchors draft-1, then draft-2 carrying the reserved `.orphograph/parent`
   leaf committing to draft-1's root, both through POST /api/anchor_folder.
3. Assembles CHAIN_DIR/<rid>/{receipt.json,manifest.json,*.ots} the way an
   export bundle lays it out.
4. Runs the REJECTION branches FIRST, then the acceptance branch. Every
   verdict is read from the EXIT CODE, never from the output text -- this
   project has been burned twice by predicates of the form "does the output
   contain X" over a tool that also sets an exit status.

Branches asserted (verify_lineage.py's documented codes):
    broken chain   parent removed from CHAIN_DIR ......... exit 5
    tampered leaf  one file digest edited in the child ... exit 3
    no attestation child's .ots files removed ............ nonzero
    intact chain   nothing touched ....................... exit 0

Honesty contract
----------------
If the calendars cannot be reached, or a link comes back with zero `.ots`
files, this reports UNAVAILABLE and exits 7. It does NOT report PASS. A check
that could not reach its dependency has not passed; it has not run.

Exit codes
    0  every branch behaved as documented
    1  a branch misbehaved -- a real finding
    7  UNAVAILABLE: could not build a real chain (network/calendars)
    2  bad usage / internal error
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VERIFIER = ROOT / "dist" / "orphograph-verify" / "verify_lineage.py"

EXIT_OK = 0
EXIT_FINDING = 1
EXIT_USAGE = 2
EXIT_UNAVAILABLE = 7

# verify_lineage.py's own documented codes, mirrored here so a drift between
# the two files shows up as a failing branch rather than as silence.
V_OK, V_USAGE, V_LINK, V_OTS, V_CHAIN = 0, 2, 3, 4, 5

_POLLUTED = (
    "app", "engine", "auth", "rate_limit", "credits", "stats",
    "health", "subscriptions", "teams", "stripe_webhook",
    "mailer", "api_keys", "affiliate", "newsletter", "waitlist",
    "blog", "unsubscribe", "gdpr", "public_config",
    "receipt_export", "btc_price", "btc_payments", "stripe_api",
    "og_svg", "qrcode_svg", "badge_svg", "analytics",
    "support_tools", "onboarding", "referrals", "file_lock",
    "merkle",
)


def _ots_hash_offset() -> int:
    """Byte offset of the committed digest inside an .ots file, taken FROM
    otscheck rather than copied. A hardcoded 32 here would silently stop
    corrupting the digest the day the header changes, and the corruption
    branch would start passing for the wrong reason."""
    sys.path.insert(0, str(VERIFIER.parent))
    import otscheck
    return otscheck._EMBEDDED_HASH_OFFSET


def _classify_attestations(chain_dir: Path, rids: list[str]) -> dict[str, int]:
    """Ask otscheck for each .ots file's verdict and tally the classes.

    This is how the harness tells "PENDING, which is the honest state of a
    three-second-old anchor" apart from "FAILED" and from "the ots client
    could not be reached". Without it, all three collapse into exit 4 and the
    run reports the wrong reason -- which is exactly what the first draft of
    this file did.
    """
    sys.path.insert(0, str(VERIFIER.parent))
    import otscheck
    tally: dict[str, int] = {}
    for rid in rids:
        receipt = json.loads((chain_dir / rid / "receipt.json").read_text())
        for ots in sorted((chain_dir / rid).glob("*.ots")):
            verdict, _height, _msg = otscheck.chain_verdict(
                ots, receipt.get("hash_hex", ""))
            tally[verdict] = tally.get(verdict, 0) + 1
    return tally


def _start_server(data_dir: Path):
    os.environ["ORPHO_DATA_DIR"] = str(data_dir)
    os.environ["HOST"] = "127.0.0.1"
    os.environ["PORT"] = "0"
    os.environ["ORPHO_COOKIE_SECURE"] = "0"
    os.environ["RATE_LIMIT_PER_DAY"] = "100000"
    os.environ.pop("STRIPE_WEBHOOK_SECRET", None)
    sys.path.insert(0, str(ROOT / "server"))
    for m in _POLLUTED:
        sys.modules.pop(m, None)
    import app
    from http.server import ThreadingHTTPServer
    server = ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}"


def _manifest_for(merkle, engine, files: dict, parent_root: str | None = None,
                  parent_rid: str | None = None) -> dict:
    """Build exactly what a lineage-aware client sends -- same construction as
    tests/test_lineage_endpoint.py, so this harness cannot pass against a
    shape the real client never produces."""
    leaves = []
    for name, content in files.items():
        digest = hashlib.sha256(content).digest()
        leaves.append({
            "path": name,
            "file_sha256_hex": digest.hex(),
            "leaf_hex": merkle._leaf_hash(name, digest).hex(),
            "size_bytes": len(content),
        })
    if parent_root is not None:
        leaves.append({
            "path": engine.RESERVED_PARENT_PATH,
            "file_sha256_hex": parent_root,
            "leaf_hex": hashlib.sha256(
                b"\x00" + engine.RESERVED_PARENT_PATH.encode("utf-8")
                + b"\x00" + bytes.fromhex(parent_root)).hexdigest(),
            "size_bytes": 0,
        })
    leaves.sort(key=lambda leaf: leaf["path"].encode("utf-8"))
    levels = merkle._build_levels([bytes.fromhex(x["leaf_hex"]) for x in leaves])
    manifest = {
        "algorithm": merkle.ALGORITHM,
        "version": merkle.VERSION,
        "root_hex": levels[-1][0].hex(),
        "leaves": leaves,
    }
    if parent_root is not None:
        manifest["parent"] = {"receipt_id": parent_rid, "root_hex": parent_root}
    return manifest


def _post_folder(base: str, manifest: dict, timeout: int):
    req = urllib.request.Request(
        f"{base}/api/anchor_folder",
        data=json.dumps(manifest).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    try:
        resp = urllib.request.urlopen(req, timeout=timeout)
        return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def _run_verifier(chain_dir: Path, ots_check: bool) -> tuple[int, str]:
    """Invoke the standalone verifier as a SUBPROCESS -- the way a third party
    runs it -- and return (exit_code, combined_output). The exit code is the
    verdict; the text is for the human reading the log."""
    argv = [sys.executable, str(VERIFIER), "--chain", str(chain_dir)]
    if ots_check:
        argv.append("--ots-check")
    p = subprocess.run(argv, capture_output=True, text=True, timeout=300)
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def _assemble(receipts_dir: Path, rids: list[str], dest: Path) -> tuple[bool, str]:
    dest.mkdir(parents=True, exist_ok=True)
    for rid in rids:
        src = receipts_dir / rid
        if not src.is_dir():
            return False, f"receipt dir missing for {rid}"
        shutil.copytree(src, dest / rid, dirs_exist_ok=True)
        n_ots = len(list((dest / rid).glob("*.ots")))
        if n_ots == 0:
            return False, f"{rid}: zero .ots files -- calendars unreachable"
        if not (dest / rid / "manifest.json").exists():
            return False, f"{rid}: manifest.json missing"
    return True, ""


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--keep", metavar="DIR",
                    help="keep the assembled chain here instead of a temp dir")
    ap.add_argument("--timeout", type=int, default=90,
                    help="per-anchor HTTP timeout in seconds (default 90)")
    ap.add_argument("--no-ots-binary", action="store_true",
                    help="skip --ots-check (static .ots binding is still checked)")
    args = ap.parse_args(argv)

    if not VERIFIER.exists():
        print(f"USAGE: verifier not found at {VERIFIER}", file=sys.stderr)
        return EXIT_USAGE

    tmp = tempfile.TemporaryDirectory()
    data_dir = Path(tmp.name) / "data"
    data_dir.mkdir(parents=True)
    server, base = _start_server(data_dir)
    try:
        import engine as engine_mod
        import merkle as merkle_mod

        tag = os.urandom(8).hex().encode()
        m1 = _manifest_for(merkle_mod, engine_mod, {"draft.md": b"v1 " + tag})
        s1, b1 = _post_folder(base, m1, args.timeout)
        if s1 != 200:
            print(f"UNAVAILABLE: draft-1 anchor returned {s1}: {b1}")
            return EXIT_UNAVAILABLE
        m2 = _manifest_for(merkle_mod, engine_mod, {"draft.md": b"v2 " + tag},
                           parent_root=b1["root_hex"], parent_rid=b1["receipt_id"])
        s2, b2 = _post_folder(base, m2, args.timeout)
        if s2 != 200:
            print(f"UNAVAILABLE: draft-2 anchor returned {s2}: {b2}")
            return EXIT_UNAVAILABLE

        parent_rid, child_rid = b1["receipt_id"], b2["receipt_id"]
        lineage = b2.get("lineage") or {}
        wire_ok = (lineage.get("parent_receipt_id") == parent_rid
                   and lineage.get("parent_root") == b1["root_hex"]
                   and lineage.get("committed") is True)
        print(f"anchored parent={parent_rid} child={child_rid} "
              f"wire_lineage={'OK' if wire_ok else 'MISSING'}")
        if not wire_ok:
            print(f"FINDING: /api/anchor_folder did not return a committed "
                  f"lineage block: {lineage}")
            return EXIT_FINDING

        chain_root = Path(args.keep) if args.keep else Path(tmp.name) / "chains"
        chain_root.mkdir(parents=True, exist_ok=True)
        good = chain_root / "intact"
        ok, why = _assemble(engine_mod.RECEIPTS_DIR, [parent_rid, child_rid], good)
        if not ok:
            print(f"UNAVAILABLE: could not assemble a real chain -- {why}")
            return EXIT_UNAVAILABLE

        n_par = len(list((good / parent_rid).glob("*.ots")))
        n_chi = len(list((good / child_rid).glob("*.ots")))
        print(f"chain assembled at {good} (.ots: parent={n_par} child={n_chi})")

        # --- REJECTION BRANCHES FIRST -----------------------------------
        # Order matters. If the acceptance branch runs first and passes, a
        # verifier that passes everything looks identical to a correct one.
        results: list[tuple[str, int, int, bool]] = []

        broken = chain_root / "broken_missing_parent"
        shutil.copytree(good, broken)
        shutil.rmtree(broken / parent_rid)
        rc, _ = _run_verifier(broken, ots_check=False)
        results.append(("missing parent", V_CHAIN, rc, rc == V_CHAIN))

        tampered = chain_root / "tampered_leaf"
        shutil.copytree(good, tampered)
        mpath = tampered / child_rid / "manifest.json"
        man = json.loads(mpath.read_text())
        for leaf in man["leaves"]:
            if leaf["path"] != engine_mod.RESERVED_PARENT_PATH:
                leaf["file_sha256_hex"] = "0" * 64
                break
        mpath.write_text(json.dumps(man, indent=2))
        rc, _ = _run_verifier(tampered, ots_check=False)
        results.append(("tampered leaf", V_LINK, rc, rc == V_LINK))

        stripped = chain_root / "no_attestation"
        shutil.copytree(good, stripped)
        for f in (stripped / child_rid).glob("*.ots"):
            f.unlink()
        rc, _ = _run_verifier(stripped, ots_check=False)
        # A link with no timestamp evidence must not pass. The exact code is
        # the verifier's business; that it is NONZERO is the contract.
        results.append(("no attestation", -1, rc, rc != V_OK))

        corrupt = chain_root / "corrupt_ots"
        shutil.copytree(good, corrupt)
        target = sorted((corrupt / child_rid).glob("*.ots"))[0]
        raw = bytearray(target.read_bytes())
        # Flip one byte inside the embedded digest, leaving the magic intact,
        # so the failure can only come from the hash-binding check and not
        # from a header sniff.
        raw[_ots_hash_offset()] ^= 0xFF
        target.write_bytes(bytes(raw))
        rc, _ = _run_verifier(corrupt, ots_check=False)
        results.append(("corrupt .ots binding", V_LINK, rc, rc == V_LINK))

        # --- ACCEPTANCE BRANCH ------------------------------------------
        # Two verdicts, because the verifier documents two contracts and they
        # legitimately differ on a chain this young.
        #
        #   static-only   the .ots files exist, carry the OTS magic, and embed
        #                 this receipt's hash -> exit 0.
        #   --ots-check   the `ots` client is additionally asked whether each
        #                 attestation is CONFIRMED ON BITCOIN. A chain anchored
        #                 seconds ago is PENDING, and otscheck.py states that
        #                 "PENDING is reported distinctly and is NOT a pass".
        #                 So exit 4 here is the DESIGNED answer, not a failure,
        #                 and a harness that called it one would be teaching
        #                 the project to paper over the honest reading.
        rc_static, out_good = _run_verifier(good, ots_check=False)
        results.append(("intact chain, static .ots", V_OK, rc_static,
                        rc_static == V_OK))

        if not args.no_ots_binary:
            classes = _classify_attestations(good, [parent_rid, child_rid])
            print(f"attestation classes across the chain: "
                  f"{', '.join(f'{k}={v}' for k, v in sorted(classes.items()))}")
            rc_ots, out_ots = _run_verifier(good, ots_check=True)
            fresh = set(classes) <= {"PENDING"}
            if fresh:
                # Freshly anchored: PENDING everywhere is expected and the
                # documented exit is 4.
                results.append(("intact chain, --ots-check (all PENDING)",
                                V_OTS, rc_ots, rc_ots == V_OTS))
            elif set(classes) <= {"VERIFIED"}:
                results.append(("intact chain, --ots-check (all VERIFIED)",
                                V_OK, rc_ots, rc_ots == V_OK))
            else:
                # UNAVAILABLE / FAILED / UNBOUND in the mix -- the sub-check
                # could not reach its dependency or genuinely rejected. Neither
                # is a pass and neither is this harness's finding to claim.
                print(f"UNAVAILABLE: attestation sub-check inconclusive "
                      f"({classes}); the chain itself verified statically.")
                print(out_ots[-1500:])
                return EXIT_UNAVAILABLE

        print()
        print(f"{'branch':<34} {'want':>6} {'got':>5}  verdict")
        for name, want, got, ok_ in results:
            w = "nonzero" if want == -1 else str(want)
            print(f"{name:<34} {w:>6} {got:>5}  {'OK' if ok_ else 'MISBEHAVED'}")

        if all(r[3] for r in results):
            print("\nPASS: verify_lineage.py accepts an intact real chain and "
                  "rejects every degraded one.")
            return EXIT_OK
        print("\nFINDING: at least one branch did not behave as documented.")
        print(out_good[-2000:])
        return EXIT_FINDING
    finally:
        server.shutdown()
        server.server_close()
        if not args.keep:
            tmp.cleanup()
        else:
            print(f"(kept chain under {args.keep}; temp data dir {tmp.name} left in place)")


if __name__ == "__main__":
    sys.exit(main())
