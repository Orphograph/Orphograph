"""Guards for scripts/all_endpoints_probe.py — the smoke probe must be able to run.

Two failures this locks down, both measured against production on 2026-09-12:
  1. Every request went out with urllib's default `Python-urllib/3.x` User-Agent,
     which Cloudflare's bot rules answer with 403 "error code: 1010". The probe
     could not reach a single endpoint, so it reported nothing useful and nobody
     noticed the next defect.
  2. Two static probes pointed at pages that no longer exist: /compare.html
     (removed in the 2026-05-18 launch sprint) and /favicon.svg (removed in #86).
     Both 404 in production, so the probe would fail on them forever.
"""
import importlib.util
import contextlib
import io
import json
import pathlib
import sys
import unittest
from unittest import mock

REPO = pathlib.Path(__file__).resolve().parent.parent
PROBE_SCRIPT = REPO / "scripts" / "all_endpoints_probe.py"
WEB = REPO / "web"
# Served by explicit routes in server/app.py, not by files under web/.
ROUTED_FILES = {"/sitemap.xml", "/robots.txt", "/LICENSE", "/llms.txt", "/security.txt"}


class TestWriteOptIn(unittest.TestCase):
    def run_cli(self, *args):
        mod = _load()
        sent = []
        def hit(server, probe):
            sent.append(probe)
            return mod.Result(probe.name, True, 200, "fixture", 0)
        out = io.StringIO()
        with mock.patch.object(sys, "argv", ["probe", "--json", *args]), \
             mock.patch.object(mod, "hit", hit), contextlib.redirect_stdout(out):
            self.assertEqual(mod.main(), 0)
        return sent, json.loads(out.getvalue())

    def test_default_cli_never_sends_write_requests(self):
        sent, report = self.run_cli()
        self.assertGreater(len(sent), 10)
        self.assertTrue(all(p.method in {"GET", "HEAD"} for p in sent))
        self.assertIn("Waitlist signup", report["skipped_write_probes"])
        self.assertEqual(report["total"], len(sent))

    def test_explicit_opt_in_includes_waitlist(self):
        sent, report = self.run_cli("--allow-writes")
        self.assertTrue(any(p.path == "/api/waitlist" and p.method == "POST" for p in sent))
        self.assertEqual(report["skipped_write_probes"], [])


def _load():
    spec = importlib.util.spec_from_file_location("all_endpoints_probe", PROBE_SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    # @dataclass resolves annotations through sys.modules[cls.__module__]; an
    # unregistered module makes the import itself fail and every test "fail" for
    # a reason unrelated to the probe.
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


class TestStaticProbesPointAtServedFiles(unittest.TestCase):
    def test_every_static_get_probe_resolves_to_a_file_under_web(self):
        mod = _load()
        checked = 0
        for probe in mod.PROBES:
            path = probe.path.split("?", 1)[0]
            if probe.method != "GET" or path.startswith("/api/") or path in ROUTED_FILES:
                continue
            last = path.rstrip("/").rsplit("/", 1)[-1]
            if path.endswith("/"):
                target = WEB / path.strip("/") / "index.html"
            elif "." in last:
                target = WEB / path.lstrip("/")
            else:
                continue
            checked += 1
            self.assertTrue(target.is_file(),
                            f"probe {probe.name!r} targets {probe.path}, but {target.relative_to(REPO)} "
                            f"does not exist — the probe can only ever fail")
        # A filter that skipped everything would pass vacuously.
        self.assertGreater(checked, 5)


class TestProbeIdentifiesItself(unittest.TestCase):
    def _sent_user_agent(self, mod, probe):
        seen = {}

        class _Resp:
            status = 200
            headers = {}

            def read(self):
                return b""

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        def fake_urlopen(req, timeout=None):
            seen["ua"] = req.get_header("User-agent")
            return _Resp()

        with mock.patch.object(mod.urllib.request, "urlopen", fake_urlopen):
            mod.hit("https://example.invalid", probe)
        return seen["ua"]

    def test_hit_sends_an_honest_user_agent(self):
        mod = _load()
        ua = self._sent_user_agent(mod, mod.Probe("health", "GET", "/api/health"))
        self.assertIsNotNone(ua, "no User-Agent: urllib's default is blocked by Cloudflare (1010)")
        self.assertFalse(ua.startswith("Python-urllib"), ua)
        self.assertNotIn("Mozilla", ua, "never a browser-spoofing User-Agent")
        self.assertIn("orphograph.com", ua)

    def test_probe_level_user_agent_still_wins(self):
        mod = _load()
        probe = mod.Probe("custom", "GET", "/", headers={"User-Agent": "custom-check/1.0"})
        self.assertEqual(self._sent_user_agent(mod, probe), "custom-check/1.0")


class TestOneBadProbeCannotAbortTheRun(unittest.TestCase):
    """3. Measured 2026-09-12: the "Receipt (invalid id)" probe path contains raw
    spaces; http.client raises InvalidURL (a ValueError), hit() did not catch it,
    and the whole run died with a traceback before printing a single result."""

    def test_path_with_spaces_is_sent_percent_encoded(self):
        mod = _load()
        sent = {}

        class _Resp:
            status = 404
            headers = {}

            def read(self):
                return b""

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        def fake_urlopen(req, timeout=None):
            sent["url"] = req.full_url
            return _Resp()

        with mock.patch.object(mod.urllib.request, "urlopen", fake_urlopen):
            mod.hit("https://example.invalid", mod.Probe("r", "GET", "/api/receipt/not a valid id?x=1&y=%2F"))
        self.assertEqual(sent["url"], "https://example.invalid/api/receipt/not%20a%20valid%20id?x=1&y=%2F")

    def test_header_checks_are_case_insensitive_like_http(self):
        """4. Measured 2026-09-12: behind Cloudflare the landing page's security
        headers arrive with lowercase names. hit() flattened them into a plain dict,
        so check_security_headers ("X-Frame-Options" in headers) reported all four
        missing while curl showed all four present — a probe that can never pass."""
        import email.parser
        import http.client

        mod = _load()
        lowercase = email.parser.Parser(_class=http.client.HTTPMessage).parsestr(
            "content-security-policy: default-src 'self'\n"
            "x-content-type-options: nosniff\n"
            "x-frame-options: DENY\n"
            "strict-transport-security: max-age=31536000\n\n")

        class _Resp:
            status = 200
            headers = lowercase

            def read(self):
                return b"<html></html>"

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        with mock.patch.object(mod.urllib.request, "urlopen", lambda req, timeout=None: _Resp()):
            result = mod.hit("https://example.invalid",
                             mod.Probe("headers", "GET", "/", check=mod.check_security_headers))
        self.assertTrue(result.ok, result.detail)

    def test_request_that_cannot_be_built_is_a_failed_result_not_a_crash(self):
        mod = _load()

        def refuse(req, timeout=None):
            raise ValueError("URL can't contain control characters")

        with mock.patch.object(mod.urllib.request, "urlopen", refuse):
            result = mod.hit("https://example.invalid", mod.Probe("bad", "GET", "/x"))
        self.assertFalse(result.ok)
        self.assertEqual(result.status, 0)
        self.assertIn("ValueError", result.detail)


if __name__ == "__main__":
    unittest.main()


class TestCanonicalReceipt(unittest.TestCase):
    def test_real_receipt_and_in_domain_mutations(self):
        mod = _load()
        payload = dict(mod.SAMPLE, found=True)
        self.assertTrue(mod.check_sample_receipt(200, {}, json.dumps(payload).encode())[0])
        for key, value in [("receipt_id", "wrong"), ("hash_hex", "0" * 64), ("found", False)]:
            with self.subTest(key=key):
                mutated = dict(payload, **{key: value})
                self.assertFalse(mod.check_sample_receipt(200, {}, json.dumps(mutated).encode())[0])
        for body in (b"[]", b"not-json"):
            self.assertFalse(mod.check_sample_receipt(200, {}, body)[0])
