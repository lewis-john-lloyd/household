import { test } from "node:test";
import * as assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";

// shape.ts is an Apps Script file: no imports or exports, its functions are globals. Evaluate the
// compiled file the way Apps Script would and pick them out.
const compiled = readFileSync(join(__dirname, "..", "src", "shape.js"), "utf8");
const shape = new Function(`${compiled}\nreturn { parseRows, periods, toSankeyRows };`)() as {
  parseRows: (values: unknown[][]) => FlowRow[];
  periods: (rows: FlowRow[]) => string[];
  toSankeyRows: (rows: FlowRow[], period: string, level: string) => { rows: SankeyRow[]; dropped: SankeyRow[] };
};

const VALUES: unknown[][] = [
  ["period", "level", "source", "target", "amount", "run_id"],
  ["2001-03", "category", "gross pay (alex)", "net pay", 2500, "r1"],
  ["2001-03", "category", "net pay", "accounts", 2500, "r1"],
  ["2001-03", "category", "accounts", "food", 400.005, "r1"],
  ["2001-03", "category", "accounts", "food", 99.995, "r1"],
  ["2001-03", "category", "accounts", "pets", 0, "r1"],
  ["2001-03", "category", "accounts", "refunds", -5, "r1"],
  ["2001-03", "category", "accounts", "accounts", 10, "r1"],
  ["2001-03", "group", "accounts", "food", 500, "r1"],
  ["2001", "category", "accounts", "food", 6000, "r1"],
  ["", "category", "accounts", "food", 1, "r1"],
];

test("parseRows reads the tab by header and skips incomplete rows", () => {
  const rows = shape.parseRows(VALUES);
  assert.equal(rows.length, 9);
  assert.deepEqual(rows[0], { period: "2001-03", level: "category", source: "gross pay (alex)", target: "net pay",
    amount: 2500 });
});

test("parseRows names missing columns", () => {
  assert.throws(() => shape.parseRows([["period", "source"]]), /level, target, amount/);
});

test("periods: newest year first, then its average, then its months newest first", () => {
  const rows = ["2001-03", "2001", "2002-01", "2001 monthly average", "2002", "2001-11", "2002 monthly average"]
    .map((period) => ({ period, level: "category", source: "a", target: "b", amount: 1 }));
  assert.deepEqual(shape.periods(rows), ["2002", "2002 monthly average", "2002-01", "2001",
    "2001 monthly average", "2001-11", "2001-03"]);
});

test("one period and level: merged, rounded, positive, no self loops, biggest first", () => {
  const { rows, dropped } = shape.toSankeyRows(shape.parseRows(VALUES), "2001-03", "category");
  assert.deepEqual(rows, [
    ["gross pay (alex)", "net pay", 2500],
    ["net pay", "accounts", 2500],
    ["accounts", "food", 500],
  ]);
  assert.deepEqual(dropped, []);
});

test("an edge that would close a cycle is dropped, the smaller one", () => {
  const rows = shape.parseRows([
    ["period", "level", "source", "target", "amount"],
    ["p", "group", "a", "b", 100],
    ["p", "group", "b", "c", 90],
    ["p", "group", "c", "a", 5],
  ]);
  const out = shape.toSankeyRows(rows, "p", "group");
  assert.deepEqual(out.rows, [["a", "b", 100], ["b", "c", 90]]);
  assert.deepEqual(out.dropped, [["c", "a", 5]]);
});

test("nothing for a period that is not there", () => {
  assert.deepEqual(shape.toSankeyRows(shape.parseRows(VALUES), "1999", "group").rows, []);
});
