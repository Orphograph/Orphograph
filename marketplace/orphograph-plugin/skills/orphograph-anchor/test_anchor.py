"""Tests for the plugin anchor.py — focuses on the auth headers actually sent.
Regression guard for the Pack-token gap (a bought Writer Pack must be spendable
from the plugin via X-Pack-Token). No network: urlopen is monkeypatched.

Run:  python3 test_anchor.py
"""
import importlib.util
import io
import json
import sys
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("anchor", HERE / "anchor.py")
anchor = importlib.util.module_from_spec(spec)
spec.loader.exec_module(anchor)


class _Resp(io.BytesIO):
    status = 200
    def __enter__(self): return self
    def __exit__(self, *a): return False


def _capture(monkeypatched_body=None):
    """Return (call, captured) where call(**kw) invokes post_anchor and
    captured['headers'] holds the lowercased request headers sent."""
    captured = {}

    def fake_urlopen(req, timeout=None):
        captured["headers"] = {k.lower(): v for k, v in req.header_items()}
        captured["url"] = req.full_url
        payload = monkeypatched_body or {"receipt_id": "r1", "calendars_ok": 5,
                                         "calendars_total": 5}
        return _Resp(json.dumps(payload).encode())

    orig = urllib.request.urlopen
    urllib.request.urlopen = fake_urlopen
    return captured, (lambda: setattr(urllib.request, "urlopen", orig))


def run():
    fails = []

    # 1. pack token → X-Pack-Token header present
    cap, restore = _capture()
    anchor.post_anchor("https://x.test", "a" * 64, None, "", "", pack_token="pk_123")
    restore()
    if cap["headers"].get("x-pack-token") != "pk_123":
        fails.append("pack token not sent as X-Pack-Token")
    if "x-orpho-api-key" in cap["headers"]:
        fails.append("api-key header leaked when only pack token given")

    # 2. api key only → X-Orpho-Api-Key present, no pack header
    cap, restore = _capture()
    anchor.post_anchor("https://x.test", "a" * 64, None, "", "sk_test", pack_token="")
    restore()
    if cap["headers"].get("x-orpho-api-key") != "sk_test":
        fails.append("api key not sent as X-Orpho-Api-Key")
    if "x-pack-token" in cap["headers"]:
        fails.append("pack header present when no pack token given")

    # 3. both supplied → both headers present (server consumes pack first)
    cap, restore = _capture()
    anchor.post_anchor("https://x.test", "a" * 64, None, "", "sk_test", pack_token="pk_9")
    restore()
    if cap["headers"].get("x-pack-token") != "pk_9" or \
       cap["headers"].get("x-orpho-api-key") != "sk_test":
        fails.append("both headers not sent when both supplied")

    # 4. neither → no auth headers (free tier)
    cap, restore = _capture()
    anchor.post_anchor("https://x.test", "a" * 64, None, "", "", pack_token="")
    restore()
    if "x-pack-token" in cap["headers"] or "x-orpho-api-key" in cap["headers"]:
        fails.append("auth header sent when none supplied")

    # 5. --pack-token env wiring reaches post_anchor — canonical ORPHO_PACK_TOKEN
    #    and back-compat ORPHOGRAPH_PACK_TOKEN both work.
    import os
    for env_name in ("ORPHO_PACK_TOKEN", "ORPHOGRAPH_PACK_TOKEN"):
        os.environ[env_name] = "pk_env"
        cap, restore = _capture()
        argv = sys.argv
        sys.argv = ["anchor.py", "b" * 64, "--json"]
        try:
            anchor.main()
        finally:
            sys.argv = argv
            restore()
            del os.environ[env_name]
        if cap["headers"].get("x-pack-token") != "pk_env":
            fails.append(f"${env_name} not wired through main()")

    if fails:
        print("FAIL:")
        for f in fails:
            print("  -", f)
        return 1
    print("ok — all 5 header/auth checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(run())
