// Drives the REAL calendar-counting code out of web/receipt.js.
//
// receipt.js is a DOM script, not a module, so the block that owns the
// calendar tables and counters is sliced out of the file and evaluated here.
// The slice is the shipped source text — not a copy — so a change to the
// lookup rule shows up in this suite instead of quietly shipping.
//
// What it pins:
//   * prototype keys are not calendars. A proof file literally named
//     "constructor.ots" used to resolve to Object.prototype.constructor — a
//     truthy function — and was counted as a calendar, and in
//     calendarUrlFromFile reached `new URL(aFunction)`.
//   * the denominators are what we submit to (5 servers, 4 calendars), never
//     what one receipt happened to reach.
//   * confirmed counts come from the server's Bitcoin-attested numbers and
//     are null when it does not send them — the page never guesses a
//     confirmation from file validity.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const SRC = readFileSync(path.join(HERE, "..", "..", "web", "receipt.js"), "utf8");

const START = "const CALENDAR_HOSTS = {";
const END_MARKER = "\nfunction el(";
const begin = SRC.indexOf(START);
const end = SRC.indexOf(END_MARKER);
assert.ok(begin >= 0, "CALENDAR_HOSTS block not found in web/receipt.js");
assert.ok(end > begin, "end of the calendar block not found in web/receipt.js");

const slice = SRC.slice(begin, end);
for (const name of ["CALENDAR_UPSTREAM", "lookup", "calendarUrlFromFile",
                    "distinctFromFiles", "calendarCounts"]) {
  assert.ok(slice.includes(name), `slice is missing ${name} — extraction drifted`);
}

const { CALENDAR_UPSTREAM, calendarUrlFromFile, distinctFromFiles, calendarCounts } =
  new Function(`${slice}\nreturn { CALENDAR_UPSTREAM, calendarUrlFromFile, distinctFromFiles, calendarCounts };`)();

test("the slice really executes the shipped functions", () => {
  assert.equal(typeof calendarCounts, "function");
  assert.equal(distinctFromFiles(["a.ots", "alice.ots"]), 1);
  assert.equal(distinctFromFiles(["a.ots", "b.ots", "alice.ots",
                                  "finney.ots", "btc.ots"]), 4);
});

test("prototype keys are not calendars", () => {
  for (const evil of ["constructor.ots", "__proto__.ots", "toString.ots",
                      "hasOwnProperty.ots", "valueOf.ots"]) {
    assert.equal(distinctFromFiles([evil]), 0, `${evil} was counted`);
    assert.equal(calendarUrlFromFile(evil, "ab".repeat(32)), null,
                 `${evil} produced a calendar URL`);
  }
  // And they do not inflate a real count either.
  assert.equal(distinctFromFiles(["alice.ots", "constructor.ots"]), 1);
});

test("a real calendar still resolves", () => {
  assert.equal(CALENDAR_UPSTREAM["a"], "alice");
  assert.match(calendarUrlFromFile("alice.ots", "ab".repeat(32)),
               /^https:\/\/alice\.btc\.calendar\.opentimestamps\.org\/timestamp\//);
});

test("denominators are what we submit to, not what this receipt reached", () => {
  const degraded = {
    calendars_ok: 3,
    calendars_total: 3,
    calendars_submitted_total: 5,
    calendars_distinct_ok: 2,
    calendars_distinct_total: 4,
    checks: [{ file: "a.ots", ok: true }, { file: "b.ots", ok: true },
             { file: "alice.ots", ok: true }],
  };
  const c = calendarCounts(degraded);
  assert.equal(c.serversOk, 3);
  assert.equal(c.serversTotal, 5, "3 of 3 servers would read as a full score");
  assert.equal(c.distinctOk, 2);
  assert.equal(c.distinctTotal, 4, "2 of 2 calendars would read as a full score");
});

test("without the server's fields the page falls back, never to a full score", () => {
  const c = calendarCounts({
    checks: [{ file: "a.ots", ok: true }, { file: "b.ots", ok: true },
             { file: "alice.ots", ok: true }],
  });
  assert.equal(c.serversOk, 3);
  assert.equal(c.serversTotal, 5);
  assert.equal(c.distinctOk, 2);
  assert.equal(c.distinctTotal, 4);
});

test("confirmed counts are absent rather than guessed", () => {
  const c = calendarCounts({ calendars_ok: 5, checks: [] });
  assert.equal(c.serversPinned, null);
  assert.equal(c.distinctPinned, null);
  const withPins = calendarCounts({
    calendars_ok: 5, calendars_pinned_ok: 3, calendars_distinct_pinned: 3,
    checks: [],
  });
  assert.equal(withPins.serversPinned, 3);
  assert.equal(withPins.distinctPinned, 3);
});

test("a corrupt check never contributes", () => {
  assert.equal(distinctFromFiles([null, undefined, 17, ""]), 0);
  assert.equal(calendarCounts({ checks: "not an array" }).distinctOk, 0);
});
