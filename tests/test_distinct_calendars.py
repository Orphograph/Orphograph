"""The durability threshold counts DISTINCT CALENDARS, not server acks.

`server/engine.py` submits every hash to five OpenTimestamps calendar
SERVERS. Two of them — `a.pool` and `b.pool` — are aggregators: a.pool
forwards to alice, b.pool forwards to bob. So five servers reach FOUR
distinct calendars across THREE operators.

Counting acknowledgements therefore overstates durability. `a.pool + alice +
b.pool` is three acknowledgements — past a `MIN_CALENDARS_OK = 3` floor —
while resting on TWO calendars, one of them counted twice, both inside one
operator's domain.

Evidence for the mapping:
  * a.pool -> alice (2026-09-18/19): a fresh a.pool proof names alice as its
    pending attestation, and on one receipt the a.pool and alice proofs were
    both still pending after 24h, both waiting on alice, while bob,
    catallaxy and finney had confirmed.
  * b.pool -> bob (2026-09-19): a proof obtained from b.pool, read with the
    OpenTimestamps library, carries the pending attestation URI
    https://bob.btc.calendar.opentimestamps.org, and after `ots upgrade` it
    confirmed in the same Bitcoin block as the bob proof path.

What this file pins:

  * the PROOF is the authority — each pending attestation names its own
    upstream calendar, and when the proof and the static table disagree the
    proof wins and the disagreement is logged;
  * the three hand-kept host lists (engine.CALENDARS/CALENDAR_UPSTREAM,
    engine.CALENDAR_HOST_UPSTREAM, upgrade_worker.ALLOWED_CALENDAR_HOSTS)
    do not drift apart;
  * the map is COMPLETE and its short tokens are unique (they are the `.ots`
    filenames, so a collision would also collide two proofs on disk);
  * the anchor path does NOT store a distinct count — it is derived on every
    read, so a correction to the table applies retroactively;
  * `calendars_ok` is not redefined and nothing new enters the renewal core,
    compared structurally against the offline verifier's own copy;
  * a receipt issued before any of this existed still verifies and reports
    the same numbers.
"""
from __future__ import annotations

import ast
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "server"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import engine  # noqa: E402
import ots_timestamp  # noqa: E402
import renewal  # noqa: E402
import upgrade_worker  # noqa: E402

A = "https://a.pool.opentimestamps.org"
B = "https://b.pool.opentimestamps.org"
ALICE = "https://alice.btc.calendar.opentimestamps.org"
FINNEY = "https://finney.calendar.eternitywall.com"
CATALLAXY = "https://btc.calendar.catallaxy.com"


def _varint(n: int) -> bytes:
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        out.append(b | (0x80 if n else 0))
        if not n:
            return bytes(out)


def pending_body(uri: str) -> bytes:
    """A calendar's POST /digest reply whose pending attestation names `uri`.

    Built here rather than copied from _ots_bodies because the point of these
    tests is WHICH calendar the proof names, and the shared fixture's URI is
    the placeholder "x".
    """
    raw = uri.encode("ascii")
    payload = _varint(len(raw)) + raw
    return (b"\xf0\x10" + b"\x01" * 16 + b"\x08"
            + b"\x00" + ots_timestamp.PENDING_ATTESTATION_TAG
            + _varint(len(payload)) + payload)


def pinned_body() -> bytes:
    """A reply carrying a Bitcoin attestation: confirmed, not merely stamped."""
    return b"\x08\x00" + ots_timestamp.BITCOIN_ATTESTATION_TAG + b"\x03\xa4\xf7\x39"


def proof(body: bytes, digest: bytes = b"\x11" * 32) -> bytes:
    return (ots_timestamp.OTS_HEADER_MAGIC + ots_timestamp.OTS_VERSION
            + ots_timestamp.OTS_TAG_SHA256 + digest + body)


class TestMappingCompleteness(unittest.TestCase):
    def test_every_submitted_server_has_an_upstream(self):
        """THE GATE. Add a server to CALENDARS and you must say which calendar
        it reaches — otherwise the distinct count silently under-reports and
        the threshold stops meaning what it says."""
        missing = [c for c in engine.CALENDARS
                   if c not in engine.CALENDAR_UPSTREAM]
        self.assertEqual(
            missing, [],
            "server(s) submitted to with no entry in engine.CALENDAR_UPSTREAM: "
            f"{missing} — add the upstream calendar each one reaches")

    def test_the_map_has_no_entries_for_servers_we_do_not_submit_to(self):
        extra = sorted(set(engine.CALENDAR_UPSTREAM) - set(engine.CALENDARS))
        self.assertEqual(extra, [],
                         f"CALENDAR_UPSTREAM maps servers not in CALENDARS: {extra}")

    def test_short_tokens_are_unique(self):
        """`_calendar_short` names the .ots file. Two servers sharing a token
        would overwrite each other's proof on disk AND make the filename
        ambiguous for the verify-side distinct count."""
        tokens = [engine._calendar_short(c) for c in engine.CALENDARS]
        self.assertEqual(len(set(tokens)), len(tokens),
                         f"duplicate .ots filename tokens: {tokens}")

    def test_the_counts_are_five_servers_four_calendars_three_operators(self):
        self.assertEqual(len(engine.CALENDARS), 5)
        self.assertEqual(engine.CALENDARS_DISTINCT_TOTAL, 4)
        operators = {".".join(c.split("//", 1)[1].split("/", 1)[0].split(".")[-2:])
                     for c in engine.CALENDARS}
        self.assertEqual(len(operators), 3, f"operators: {sorted(operators)}")

    def test_the_aggregators_are_mapped_to_their_upstreams(self):
        self.assertEqual(engine.CALENDAR_UPSTREAM[A],
                         engine.CALENDAR_UPSTREAM[ALICE])
        self.assertEqual(engine.CALENDAR_UPSTREAM[B], "bob")


class TestThreeHostListsAgree(unittest.TestCase):
    """The repo hand-keeps three lists of the same hosts. They have drifted
    before; this is the gate that says so out loud."""

    @staticmethod
    def _host(url: str) -> str:
        return url.split("//", 1)[1].split("/", 1)[0].lower()

    def test_every_submitted_server_host_resolves_in_the_host_table(self):
        for url in engine.CALENDARS:
            host = self._host(url)
            self.assertIn(host, engine.CALENDAR_HOST_UPSTREAM,
                          f"{host} is submitted to but absent from "
                          "engine.CALENDAR_HOST_UPSTREAM")
            self.assertEqual(
                engine.CALENDAR_HOST_UPSTREAM[host],
                engine.CALENDAR_UPSTREAM[url],
                f"the two tables disagree about {host}")

    def test_every_worker_allowed_host_resolves_in_the_host_table(self):
        """upgrade_worker will FETCH from any host in its allow-list; every
        one of them must be a calendar we can name. bob is here and not in
        CALENDARS — we reach it through b.pool, never directly — so this is
        asserted directionally, never as set equality."""
        missing = sorted(h for h in upgrade_worker.ALLOWED_CALENDAR_HOSTS
                         if h not in engine.CALENDAR_HOST_UPSTREAM)
        self.assertEqual(missing, [],
                         "upgrade_worker fetches from hosts engine cannot map "
                         f"to a calendar: {missing}")

    def test_every_host_table_entry_is_one_the_worker_would_fetch(self):
        missing = sorted(h for h in engine.CALENDAR_HOST_UPSTREAM
                         if h not in upgrade_worker.ALLOWED_CALENDAR_HOSTS)
        self.assertEqual(missing, [],
                         "engine maps hosts the upgrade worker refuses: "
                         f"{missing}")

    def test_negative_control_the_lists_are_not_empty(self):
        self.assertGreaterEqual(len(upgrade_worker.ALLOWED_CALENDAR_HOSTS), 5)
        self.assertGreaterEqual(len(engine.CALENDAR_HOST_UPSTREAM), 5)


class TestProofIsTheAuthority(unittest.TestCase):
    """Each pending proof names the calendar that will carry its commitment.
    The static table is the fallback, not the source of truth."""

    def test_a_pool_proof_names_alice(self):
        blob = proof(pending_body("https://alice.btc.calendar.opentimestamps.org"))
        self.assertEqual(engine.upstream_from_proof(blob), "alice")

    def test_b_pool_proof_names_bob(self):
        blob = proof(pending_body("https://bob.btc.calendar.opentimestamps.org"))
        self.assertEqual(engine.upstream_from_proof(blob), "bob")

    def test_an_upgraded_proof_names_nothing_and_falls_back_to_the_table(self):
        blob = proof(pinned_body())
        self.assertIsNone(engine.upstream_from_proof(blob))
        self.assertEqual(engine._resolve_upstream(A, blob), "alice")

    def test_a_malformed_blob_falls_back_instead_of_raising(self):
        for bad in (b"", b"not an ots file", None, "a.ots"):
            self.assertIsNone(engine.upstream_from_proof(bad))
        self.assertEqual(engine._resolve_upstream(B, b"garbage"), "bob")

    def test_an_unknown_host_in_a_proof_is_not_a_calendar(self):
        blob = proof(pending_body("https://evil.example.com"))
        self.assertIsNone(engine.upstream_from_proof(blob))

    def test_when_they_disagree_the_proof_wins_and_it_is_logged(self):
        """THE PRECEDENCE TEST. A proof filed as `a.ots` — which the table
        says is alice — that actually names finney must count as finney, and
        must not do so quietly."""
        blob = proof(pending_body("https://finney.calendar.eternitywall.com"))
        import io
        import contextlib
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            got = engine._resolve_upstream("a.ots", blob)
        self.assertEqual(got, "finney", "the table overrode the artifact")
        msg = err.getvalue()
        self.assertIn("disagreement", msg, f"nothing logged: {msg!r}")
        self.assertIn("alice", msg)
        self.assertIn("finney", msg)

    def test_agreement_is_not_logged(self):
        """NEGATIVE CONTROL: if every resolution logged, the test above would
        pass without the precedence rule existing."""
        import io
        import contextlib
        blob = proof(pending_body("https://alice.btc.calendar.opentimestamps.org"))
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            engine._resolve_upstream("a.ots", blob)
        self.assertEqual(err.getvalue(), "")

    def test_distinct_calendars_of_uses_the_proofs(self):
        """a.ots and b.ots alone: the table says alice+bob = 2. Proofs that
        both name alice say 1, and the proofs win."""
        both_alice = pending_body("https://alice.btc.calendar.opentimestamps.org")
        import io
        import contextlib
        with contextlib.redirect_stderr(io.StringIO()):
            got = engine.distinct_calendars_of(
                [("a.ots", proof(both_alice)), ("b.ots", proof(both_alice))])
        self.assertEqual(got, 1)
        self.assertEqual(engine.distinct_calendars(["a.ots", "b.ots"]), 2)


class TestDistinctCount(unittest.TestCase):
    def test_a_pool_plus_alice_is_one_calendar(self):
        self.assertEqual(engine.distinct_calendars([A, ALICE]), 1)

    def test_a_pool_plus_alice_plus_b_pool_is_two_calendars(self):
        """Three acknowledgements. Two calendars. One operator. This is the
        case the old count read as a full-redundancy receipt."""
        self.assertEqual(engine.distinct_calendars([A, ALICE, B]), 2)

    def test_all_five_servers_are_four_calendars(self):
        self.assertEqual(engine.distinct_calendars(engine.CALENDARS), 4)

    def test_three_genuinely_distinct_servers_are_three_calendars(self):
        self.assertEqual(engine.distinct_calendars([A, FINNEY, CATALLAXY]), 3)

    def test_it_reads_the_success_record_shape(self):
        successes = [{"calendar": A, "ots_path": "receipts/r/a.ots"},
                     {"calendar": ALICE, "ots_path": "receipts/r/alice.ots"}]
        self.assertEqual(engine.distinct_calendars(successes), 1)

    def test_it_reads_ots_filenames(self):
        self.assertEqual(engine.distinct_calendars(["a.ots", "alice.ots"]), 1)
        self.assertEqual(
            engine.distinct_calendars(
                ["a.ots", "b.ots", "alice.ots", "finney.ots", "btc.ots"]), 4)

    def test_empty_and_malformed_input_count_zero(self):
        for bad in ([], None, 17, "alice.ots", [None], [{}], [{"calendar": 5}]):
            self.assertEqual(engine.distinct_calendars(bad), 0, repr(bad))

    def test_an_unknown_server_is_not_counted_as_independent(self):
        """UNKNOWN is never promoted to a calendar. An unmapped server
        under-counts (loudly, via the completeness test above) rather than
        overstating durability quietly — which is the bug being fixed."""
        self.assertEqual(
            engine.distinct_calendars(["https://unknown.example.com"]), 0)
        self.assertEqual(
            engine.distinct_calendars([FINNEY, "https://unknown.example.com"]), 1)

    def test_negative_control_the_function_can_return_more_than_one(self):
        self.assertGreater(engine.distinct_calendars(engine.CALENDARS), 1)


class TestAnchorPathDoesNotStoreTheCount(unittest.TestCase):
    """F5. The count is derived on every read. Storing it would freeze
    today's table into a permanent record AND put a new field in front of
    every consumer that enumerates receipt keys."""

    def setUp(self):
        import tempfile
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self._saved = (engine.RECEIPTS_DIR, engine.LEDGER, engine._submit)
        engine.RECEIPTS_DIR = root / "receipts"
        engine.LEDGER = root / "ledger.jsonl"

    def tearDown(self):
        engine.RECEIPTS_DIR, engine.LEDGER, engine._submit = self._saved
        self._tmp.cleanup()

    def _anchor(self, accept, body_for=None):
        def submit(cal, hash_bytes):
            if cal not in accept:
                return False, "HTTP 503: stubbed outage"
            # Default: the server names ITSELF, which the host table resolves
            # the same way CALENDAR_UPSTREAM does — so no disagreement fires
            # unless a test asks for one through `body_for`.
            uri = (body_for or {}).get(cal) or cal
            return True, pending_body(uri)
        engine._submit = submit
        return engine.anchor_hash("ab" * 32, client_label="t")

    def test_the_real_anchor_path_writes_no_distinct_field(self):
        record = self._anchor(set(engine.CALENDARS))
        self.assertNotIn("calendars_distinct_ok", record)
        on_disk = json.loads(
            (engine.RECEIPTS_DIR / record["receipt_id"] / "receipt.json").read_text())
        self.assertNotIn("calendars_distinct_ok", on_disk)
        self.assertNotIn("calendars_distinct_total", on_disk)

    def test_the_ledger_line_carries_no_distinct_field(self):
        record = self._anchor(set(engine.CALENDARS))
        rows = [json.loads(ln) for ln in
                engine.LEDGER.read_text().splitlines() if ln.strip()]
        self.assertTrue(rows)
        self.assertNotIn("calendars_distinct_ok", rows[-1])
        self.assertEqual(rows[-1]["receipt_id"], record["receipt_id"])

    def test_calendars_ok_still_counts_server_acknowledgements(self):
        """Drives the REAL anchor path with three servers answering: three
        acknowledgements, two calendars. `calendars_ok` keeps its committed
        meaning and the distinct helper disagrees with it on purpose."""
        record = self._anchor({A, B, ALICE})
        self.assertEqual(record["calendars_ok"], 3)
        self.assertEqual(record["calendars_total"], 5)
        counts = engine.receipt_distinct_counts(record)
        self.assertEqual(counts["calendars_distinct_ok"], 2)
        self.assertEqual(counts["calendars_distinct_total"], 4)

    def test_the_helper_reads_the_proofs_that_were_just_written(self):
        """receipt_distinct_counts is proof-first. A a.pool proof naming
        finney makes the count 2, not the table's 1."""
        import io
        import contextlib
        record = self._anchor(
            {A, ALICE},
            body_for={A: "https://finney.calendar.eternitywall.com",
                      ALICE: "https://alice.btc.calendar.opentimestamps.org"})
        with contextlib.redirect_stderr(io.StringIO()):
            counts = engine.receipt_distinct_counts(record)
        self.assertEqual(counts["calendars_distinct_ok"], 2)

    def test_the_total_is_always_four_never_what_this_receipt_reached(self):
        record = self._anchor({A, B})
        counts = engine.receipt_distinct_counts(record)
        self.assertEqual(counts["calendars_distinct_ok"], 2)
        self.assertEqual(counts["calendars_distinct_total"], 4,
                         "a receipt on two calendars must read 2 of 4, never 2 of 2")


class TestCalendarsOkIsNotRedefined(unittest.TestCase):
    """The hard constraint. `calendars_ok`, `calendars_total`, `successes` and
    `failures` are committed by renewal records for issued receipts."""

    @staticmethod
    def _core_always(path: Path) -> tuple:
        """The CORE_ALWAYS tuple as the module actually defines it, read
        structurally. A substring search would pass on a file that renamed
        the constant, reordered it, or defined it twice."""
        tree = ast.parse(path.read_text(encoding="utf-8"))
        found = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id == "CORE_ALWAYS":
                        found.append(ast.literal_eval(node.value))
        assert len(found) == 1, f"{path}: expected one CORE_ALWAYS, found {len(found)}"
        return found[0]

    def test_the_offline_verifier_core_list_matches_structurally(self):
        """dist/orphograph-verify/verify_renewal.py carries its own copy.
        Compared as tuples, so order and membership both count — a copy that
        merely happens not to contain a substring is not agreement."""
        server = self._core_always(ROOT / "server" / "renewal.py")
        offline = self._core_always(
            ROOT / "dist" / "orphograph-verify" / "verify_renewal.py")
        self.assertEqual(server, offline,
                         "the renewal core has drifted between the server and "
                         "the offline verifier — every renewal record ever "
                         "written is decided by these two agreeing")
        self.assertEqual(server, renewal.CORE_ALWAYS)

    def test_negative_control_the_reader_sees_real_content(self):
        core = self._core_always(ROOT / "server" / "renewal.py")
        self.assertIn("hash_hex", core)
        self.assertGreater(len(core), 10)

    def test_no_distinct_field_entered_the_core(self):
        for key in renewal.CORE_ALWAYS + renewal.CORE_IF_PRESENT:
            self.assertNotIn("distinct", key)

    def test_core_always_still_holds_the_four_committed_calendar_fields(self):
        for key in ("calendars_ok", "calendars_total", "successes", "failures"):
            self.assertIn(key, renewal.CORE_ALWAYS)


class TestOldReceiptWithoutTheField(unittest.TestCase):
    """A receipt issued before any of this existed must verify and report the
    same numbers as a fresh one."""

    def _old_receipt(self, tmp: Path, rid: str, servers, body=None) -> dict:
        rd = tmp / rid
        rd.mkdir(parents=True)
        hash_hex = "ab" * 32
        digest = bytes.fromhex(hash_hex)
        successes = []
        for cal in servers:
            short = engine._calendar_short(cal)
            blob = body(cal) if body else pending_body("https://" + cal.split("//", 1)[1])
            (rd / f"{short}.ots").write_bytes(proof(blob, digest))
            successes.append({"calendar": cal,
                              "ots_path": f"receipts/{rid}/{short}.ots"})
        record = {
            "receipt_id": rid,
            "created_at": "2026-01-02T03:04:05+00:00",
            "hash_hex": hash_hex,
            "sha512_hex": None,
            "client_label": None,
            "source": "free",
            "private": False,
            "owner_id": None,
            "attestation": None,
            "c2pa_manifest_hash": None,
            "metadata": None,
            "calendars_ok": len(successes),
            "calendars_total": len(engine.CALENDARS),
            "successes": successes,
            "failures": [],
        }
        (rd / "receipt.json").write_text(json.dumps(record, indent=2))
        return record

    def setUp(self):
        import tempfile
        self._tmp = tempfile.TemporaryDirectory()
        self._prev = engine.RECEIPTS_DIR
        engine.RECEIPTS_DIR = Path(self._tmp.name)

    def tearDown(self):
        engine.RECEIPTS_DIR = self._prev
        self._tmp.cleanup()

    def test_verify_still_finds_it_and_reports_the_distinct_count(self):
        self._old_receipt(Path(self._tmp.name), "old2", engine.CALENDARS)
        out = engine.verify_receipt("old2")
        self.assertTrue(out["found"], out)
        self.assertEqual(out["calendars_ok"], 5)
        self.assertEqual(out["calendars_distinct_ok"], 4)
        self.assertEqual(out["calendars_distinct_total"], 4)
        self.assertEqual(out["calendars_submitted_total"], 5)

    def test_verify_derives_two_for_the_aggregator_trio(self):
        self._old_receipt(Path(self._tmp.name), "old3", [A, ALICE, B])
        out = engine.verify_receipt("old3")
        self.assertEqual(out["calendars_ok"], 3)
        self.assertEqual(out["calendars_distinct_ok"], 2)

    def test_a_degraded_receipt_never_reads_as_a_full_score(self):
        """F1. The denominators are what we AIM at, not what this receipt
        reached: 3 of 5 servers and 2 of 4 calendars — never 3 of 3, 2 of 2."""
        self._old_receipt(Path(self._tmp.name), "old3b", [A, ALICE, B])
        out = engine.verify_receipt("old3b")
        self.assertEqual(out["calendars_submitted_total"], 5)
        self.assertEqual(out["calendars_distinct_total"], 4)
        self.assertNotEqual(out["calendars_ok"], out["calendars_submitted_total"])
        self.assertNotEqual(out["calendars_distinct_ok"],
                            out["calendars_distinct_total"])

    def test_a_corrupt_proof_does_not_add_a_calendar(self):
        rd = Path(self._tmp.name) / "old4"
        self._old_receipt(Path(self._tmp.name), "old4", [A, FINNEY])
        (rd / "finney.ots").write_bytes(b"not an ots file at all")
        out = engine.verify_receipt("old4")
        self.assertEqual(out["calendars_ok"], 1)
        self.assertEqual(out["calendars_distinct_ok"], 1)
        self.assertEqual(out["calendars_distinct_total"], 4)
        self.assertEqual(out["calendars_total"], 2, "files on disk, unchanged")

    def test_confirmed_counts_are_bitcoin_facts_not_file_facts(self):
        """F4. Today's real case: a.pool and alice still pending past 24h
        while bob, finney and catallaxy were in a block. Proof validity must
        not be reported as confirmation."""
        def body(cal):
            if cal in (A, ALICE):
                return pending_body("https://alice.btc.calendar.opentimestamps.org")
            return pinned_body()
        self._old_receipt(Path(self._tmp.name), "old7", engine.CALENDARS, body=body)
        out = engine.verify_receipt("old7")
        self.assertEqual(out["calendars_ok"], 5, "all five proofs parse and match")
        self.assertEqual(out["calendars_distinct_ok"], 4)
        self.assertEqual(out["calendars_pinned_ok"], 3, "only three are in a block")
        self.assertEqual(out["calendars_distinct_pinned"], 3)

    def test_nothing_is_pinned_when_every_proof_is_pending(self):
        """NEGATIVE CONTROL for the pair above."""
        self._old_receipt(Path(self._tmp.name), "old8", engine.CALENDARS)
        out = engine.verify_receipt("old8")
        self.assertEqual(out["calendars_ok"], 5)
        self.assertEqual(out["calendars_pinned_ok"], 0)
        self.assertEqual(out["calendars_distinct_pinned"], 0)

    def test_renewal_core_accepts_the_old_receipt_unchanged(self):
        rec = self._old_receipt(Path(self._tmp.name), "old5", engine.CALENDARS)
        core = renewal.receipt_core(rec)
        self.assertNotIn("calendars_distinct_ok", core)
        self.assertEqual(core["calendars_ok"], 5)


if __name__ == "__main__":
    unittest.main()
