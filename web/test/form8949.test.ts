import { test } from "node:test";
import * as assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";

const compiled = readFileSync(join(__dirname, "..", "src", "form8949.js"), "utf8");
const page = new Function(`${compiled}\nreturn form8949Page;`)() as (tabs: Record<string, unknown[][]>) => F8949Page;

const FORM: unknown[][] = [
  [
    "year",
    "sale_date",
    "account_id",
    "symbol",
    "shares",
    "acquired",
    "holding_from",
    "term",
    "proceeds",
    "basis",
    "wash_carried",
    "code",
    "adjustment",
    "gain",
    "lot_id",
    "piece",
    "replaced_by",
    "reported_basis",
    "note",
    "run_id",
  ],
  [
    2001,
    "2001-06-01",
    "alex_brokerage",
    "ACMEX",
    2,
    "2001-02-01",
    "2001-02-01",
    "short_term",
    40,
    50,
    0,
    "W",
    10,
    0,
    "l1",
    0,
    "l2",
    "",
    "",
    "r",
  ],
  [
    2001,
    "2001-03-01",
    "alex_brokerage",
    "ACMEX",
    1,
    "1999-01-04",
    "1999-01-04",
    "long_term",
    30,
    10,
    0,
    "",
    0,
    20,
    "l0",
    0,
    "",
    10,
    "",
    "r",
  ],
  [
    2001,
    "2001-01-15",
    "alex_brokerage",
    "WILLX",
    1,
    "2000-12-01",
    "2000-12-01",
    "short_term",
    20,
    15,
    0,
    "",
    0,
    5,
    "l3",
    0,
    "",
    "",
    "",
    "r",
  ],
  [
    2002,
    "2002-05-01",
    "alex_brokerage",
    "ACMEX",
    1,
    "2001-07-01",
    "2001-05-01",
    "long_term",
    50,
    60,
    10,
    "",
    0,
    -10,
    "l2",
    0,
    "",
    "",
    "",
    "r",
  ],
];
const FILED: unknown[][] = [
  ["year", "form", "order", "line", "key", "label", "value", "run_id"],
  [2001, "Form 1040", 7, "Sch D 7", "capital_gain_short", "Short-term", 5, "r"],
  [2001, "Form 1040", 8, "Sch D 15", "capital_gain_long", "Long-term", 25, "r"],
  [2001, "Form 1040", 1, "1z", "1z", "Wages", 90, "r"],
];

test("each year's sales split into Part I and Part II, oldest sale first, with totals beside the filed lines", () => {
  const p = page({ form_8949: FORM, filed_lines: FILED });
  assert.deepEqual(
    p.years.map((y) => y.year),
    ["2002", "2001"],
  );
  const y = p.years[1];
  assert.deepEqual(
    y.parts.map((x) => x.term),
    ["short_term", "long_term"],
  );
  const short = y.parts[0];
  assert.deepEqual(
    short.rows.map((r) => r.symbol),
    ["WILLX", "ACMEX"],
  );
  assert.deepEqual([short.proceeds, short.basis, short.adjustment, short.gain, short.filed], [60, 65, 10, 5, 5]);
  assert.equal(y.parts[1].filed, 25);
  assert.equal(y.parts[1].rows[0].reportedBasis, 10);
  assert.equal(short.rows[1].reportedBasis, null);
  assert.equal(y.disallowed, 10);
  assert.ok(y.filedOnFile && !p.years[0].filedOnFile && p.years[0].parts[0].filed === null);
});
