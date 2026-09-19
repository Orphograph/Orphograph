"""The durability threshold counts DISTINCT CALENDARS, not server acks.

`server/engine.py` submits every hash to five OpenTimestamps calendar
SERVERS. Two of them — `a.pool` and `b.pool` — are aggregators: a.pool
forwards to alice, b.pool forwards to bob. So five servers reach FOUR
distinct calendars across THREE operators.

Counting acknowledgements therefore overstates durability. `a.pool + alice +
b.pool` is three acknowledgements — past a `MIN_CALENDARS_OK = 3` floor —
while resting on TWO calendars, one of them counted twice, both inside one
operator's domain. Evidence, 2026-09-18/19: a fresh a.pool proof names alice
as its pending attestation, and on one receipt the a.pool and alice proofs
were both still pending after 24h, both waiting on alice, while bob,
catallaxy and finney had confirmed.

What this file pins:

  * the map is COMPLETE — a server added to CALENDARS without a mapping
    entry fails here rather than silently counting as a new independent
    calendar;
  * the short tokens are unique — they are the `.ots` filenames, so a
    collision would also collide two proofs into one file on disk;
  * the counting itself, on the three shapes a receipt carries a calendar in;
  * `calendars_ok` is NOT redefined, and the new field is NOT in
    CORE_ALWAYS — either would void the renewal commitment of every
    already-issued receipt;
  * a receipt issued BEFORE the field existed still verifies, and reports
    the same distinct count, derived from the proofs on disk.
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "server"))

import engine  # noqa: E402
import renewal  # noqa: E402


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
        """A stale entry is a claim about a server that is not in the product."""
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
        """The public sentence, pinned to the code that produces it."""
        self.assertEqual(len(engine.CALENDARS), 5)
        self.assertEqual(engine.CALENDARS_DISTINCT_TOTAL, 4)
        operators = {".".join(c.split("//", 1)[1].split("/", 1)[0].split(".")[-2:])
                     for c in engine.CALENDARS}
        self.assertEqual(len(operators), 3, f"operators: {sorted(operators)}")

    def test_any_three_distinct_calendars_span_at_least_two_operators(self):
        """Why the threshold stays at 3 under the new counting. alice and bob
        are the only two calendars sharing an operator, so three distinct
        calendars cannot all sit behind one domain — which three server
        acknowledgements could."""
        import itertools
        by_operator: dict = {}
        for url, upstream in engine.CALENDAR_UPSTREAM.items():
            host = url.split("//", 1)[1].split("/", 1)[0]
            by_operator[upstream] = ".".join(host.split(".")[-2:])
        for trio in itertools.combinations(sorted(set(engine.CALENDAR_UPSTREAM.values())), 3):
            self.assertGreaterEqual(
                len({by_operator[u] for u in trio}), 2,
                f"three distinct calendars {trio} sit under one operator")

    def test_the_aggregators_are_mapped_to_their_upstreams(self):
        """The specific fact this whole change rests on."""
        self.assertEqual(
            engine.CALENDAR_UPSTREAM["https://a.pool.opentimestamps.org"],
            engine.CALENDAR_UPSTREAM["https://alice.btc.calendar.opentimestamps.org"])
        self.assertEqual(
            engine.CALENDAR_UPSTREAM["https://b.pool.opentimestamps.org"], "bob")


class TestDistinctCount(unittest.TestCase):
    A = "https://a.pool.opentimestamps.org"
    B = "https://b.pool.opentimestamps.org"
    ALICE = "https://alice.btc.calendar.opentimestamps.org"
    FINNEY = "https://finney.calendar.eternitywall.com"
    CATALLAXY = "https://btc.calendar.catallaxy.com"

    def test_a_pool_plus_alice_is_one_calendar(self):
        self.assertEqual(engine.distinct_calendars([self.A, self.ALICE]), 1)

    def test_a_pool_plus_alice_plus_b_pool_is_two_calendars(self):
        """Three acknowledgements. Two calendars. One operator. This is the
        case the old count read as a full-redundancy receipt."""
        self.assertEqual(
            engine.distinct_calendars([self.A, self.ALICE, self.B]), 2)

    def test_all_five_servers_are_four_calendars(self):
        self.assertEqual(engine.distinct_calendars(engine.CALENDARS), 4)

    def test_three_genuinely_distinct_servers_are_three_calendars(self):
        self.assertEqual(
            engine.distinct_calendars([self.A, self.FINNEY, self.CATALLAXY]), 3)

    def test_it_reads_the_success_record_shape(self):
        """`successes` is a list of dicts, which is what every caller holds."""
        successes = [{"calendar": self.A, "ots_path": "receipts/r/a.ots"},
                     {"calendar": self.ALICE, "ots_path": "receipts/r/alice.ots"}]
        self.assertEqual(engine.distinct_calendars(successes), 1)

    def test_it_reads_ots_filenames(self):
        """The verify side has only the files on disk."""
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
            engine.distinct_calendars([self.FINNEY, "https://unknown.example.com"]), 1)

    def test_negative_control_the_function_can_return_more_than_one(self):
        """If distinct_calendars always returned 0 or 1, most assertions above
        would still pass. This is the control that it actually counts."""
        self.assertGreater(engine.distinct_calendars(engine.CALENDARS), 1)


class TestCalendarsOkIsNotRedefined(unittest.TestCase):
    """The hard constraint. `calendars_ok`, `calendars_total`, `successes` and
    `failures` are committed by renewal records for issued receipts. Their
    meaning may not change, and the new field may not join them."""

    def test_calendars_ok_still_counts_server_acknowledgements(self):
        successes = [{"calendar": c} for c in engine.CALENDARS[:3]]
        record = {"calendars_ok": len(successes), "successes": successes}
        self.assertEqual(record["calendars_ok"], 3)
        self.assertEqual(engine.distinct_calendars(record["successes"]), 2)

    def test_the_new_field_is_not_in_core_always(self):
        self.assertNotIn("calendars_distinct_ok", renewal.CORE_ALWAYS)
        self.assertNotIn("calendars_distinct_ok", renewal.CORE_IF_PRESENT)

    def test_the_offline_verifier_core_list_matches(self):
        """dist/orphograph-verify/verify_renewal.py carries its own copy of
        CORE_ALWAYS; the two must not drift."""
        src = (ROOT / "dist" / "orphograph-verify" / "verify_renewal.py")
        text = src.read_text(encoding="utf-8")
        self.assertNotIn("calendars_distinct_ok", text)

    def test_core_always_still_holds_the_four_committed_calendar_fields(self):
        for key in ("calendars_ok", "calendars_total", "successes", "failures"):
            self.assertIn(key, renewal.CORE_ALWAYS)


class TestOldReceiptWithoutTheField(unittest.TestCase):
    """A receipt issued before `calendars_distinct_ok` existed must verify,
    renew, and report the same distinct count as a fresh one."""

    def _old_receipt(self, tmp: Path, rid: str, servers) -> dict:
        from conftest import make_pending_ots  # the suite's own OTS builder
        rd = tmp / rid
        rd.mkdir(parents=True)
        hash_hex = "ab" * 32
        digest = bytes.fromhex(hash_hex)
        successes = []
        for cal in servers:
            short = engine._calendar_short(cal)
            (rd / f"{short}.ots").write_bytes(make_pending_ots(digest))
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

    def test_it_has_no_new_field_control(self):
        rec = self._old_receipt(Path(self._tmp.name), "old1", engine.CALENDARS)
        self.assertNotIn("calendars_distinct_ok", rec)

    def test_verify_still_finds_it_and_reports_the_distinct_count(self):
        self._old_receipt(Path(self._tmp.name), "old2", engine.CALENDARS)
        out = engine.verify_receipt("old2")
        self.assertTrue(out["found"], out)
        self.assertEqual(out["calendars_ok"], 5)
        self.assertEqual(out["calendars_distinct_ok"], 4)
        self.assertEqual(out["calendars_distinct_total"], 4)

    def test_verify_derives_two_for_the_aggregator_trio(self):
        """The old receipt whose three acknowledgements are two calendars."""
        trio = ["https://a.pool.opentimestamps.org",
                "https://alice.btc.calendar.opentimestamps.org",
                "https://b.pool.opentimestamps.org"]
        self._old_receipt(Path(self._tmp.name), "old3", trio)
        out = engine.verify_receipt("old3")
        self.assertEqual(out["calendars_ok"], 3)
        self.assertEqual(out["calendars_distinct_ok"], 2)

    def test_a_corrupt_proof_does_not_add_a_calendar(self):
        """calendars_distinct_ok counts PASSING checks, like calendars_ok."""
        rd = Path(self._tmp.name) / "old4"
        self._old_receipt(Path(self._tmp.name), "old4",
                          ["https://a.pool.opentimestamps.org",
                           "https://finney.calendar.eternitywall.com"])
        (rd / "finney.ots").write_bytes(b"not an ots file at all")
        out = engine.verify_receipt("old4")
        self.assertEqual(out["calendars_ok"], 1)
        self.assertEqual(out["calendars_distinct_ok"], 1)
        self.assertEqual(out["calendars_distinct_total"], 2)

    def test_renewal_core_accepts_the_old_receipt_unchanged(self):
        rec = self._old_receipt(Path(self._tmp.name), "old5", engine.CALENDARS)
        core = renewal.receipt_core(rec)
        self.assertNotIn("calendars_distinct_ok", core)
        self.assertEqual(core["calendars_ok"], 5)

    def test_renewal_core_ignores_the_new_field_on_a_fresh_receipt(self):
        """A receipt WITH the field must produce the same core as one without,
        or the two corpora would hash differently for no evidentiary reason."""
        rec = self._old_receipt(Path(self._tmp.name), "old6", engine.CALENDARS)
        fresh = dict(rec, calendars_distinct_ok=4)
        self.assertEqual(renewal.receipt_core(rec), renewal.receipt_core(fresh))


if __name__ == "__main__":
    unittest.main()
