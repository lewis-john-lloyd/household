import { test } from "node:test";
import * as assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";

// shape.ts is an Apps Script file: no imports or exports, its functions are globals. Evaluate the
// compiled file the way Apps Script would and pick them out.
const compiled = readFileSync(join(__dirname, "..", "src", "shape.js"), "utf8");
const shape = new Function(`${compiled}\nreturn { parseRows, periods, toSankeyRows, nodeTotals, parseBudget, budgetPeriods, budgetLines, parseTransactions, transactionsFor };`)() as {
  parseRows: (values: unknown[][]) => FlowRow[];
  periods: (rows: FlowRow[]) => string[];
  toSankeyRows: (rows: FlowRow[], period: string, level: string) => { rows: SankeyRow[]; dropped: SankeyRow[] };
  nodeTotals: (rows: SankeyRow[]) => NodeTotal[];
  parseBudget: (values: unknown[][]) => BudgetRow[];
  budgetPeriods: (rows: BudgetRow[]) => string[];
  budgetLines: (rows: BudgetRow[], period: string, level: string) => BudgetLine[];
  parseTransactions: (values: unknown[][]) => Txn[];
  transactionsFor: (txns: Txn[], period: string, categories: string[]) => Txn[];
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

test("node totals: what each node took in and sent out, biggest first", () => {
  const totals = shape.nodeTotals([
    ["net pay", "accounts", 2500], ["other income", "accounts", 100.005],
    ["accounts", "food", 400], ["accounts", "pets", 99.99], ["accounts", "to cash balances", 2100.02],
  ]);
  assert.deepEqual(totals.map((t) => [t.name, t.into, t.out, t.total]), [
    ["accounts", 2600.01, 2600.01, 2600.01],
    ["net pay", 0, 2500, 2500],
    ["to cash balances", 2100.02, 0, 2100.02],
    ["food", 400, 0, 400],
    ["other income", 0, 100.01, 100.01],
    ["pets", 99.99, 0, 99.99],
  ]);
});

const BUDGET: unknown[][] = [
  ["month", "category", "group", "priority", "target", "actual", "remaining", "run_id"],
  ["2001-02", "food", "food", "high", 1000, 1100, -100, "r1"],
  ["2001-02", "restaurant", "food", "low", 300, 120.5, 179.5, "r1"],
  ["2001-02", "booze", "family_fun", "", 0, 40, -40, "r1"],
  ["2001-01", "food", "food", "high", 1000, 900, 100, "r1"],
  ["2001-01", "restaurant", "food", "low", 300, 310, -10, "r1"],
  ["2000-12", "food", "food", "medium", 900, 950, -50, "r1"],
];

test("budget periods: each year so far, then its months, newest first", () => {
  assert.deepEqual(shape.budgetPeriods(shape.parseBudget(BUDGET)), ["2001", "2001-02", "2001-01", "2000", "2000-12"]);
});

test("budget lines for a month by category: over budget first within each priority", () => {
  const lines = shape.budgetLines(shape.parseBudget(BUDGET), "2001-02", "category");
  assert.deepEqual(lines.map((l) => [l.name, l.priority, l.target, l.actual, l.remaining]), [
    ["food", "high", 1000, 1100, -100],
    ["restaurant", "low", 300, 120.5, 179.5],
    ["booze", "", 0, 40, -40],
  ]);
});

test("budget lines for a year by group add the months and keep the highest priority", () => {
  const lines = shape.budgetLines(shape.parseBudget(BUDGET), "2001", "group");
  assert.deepEqual(lines.map((l) => [l.name, l.priority, l.target, l.actual, l.remaining, l.categories]), [
    ["food", "high", 2600, 2430.5, 169.5, ["food", "restaurant"]],
    ["family_fun", "", 0, 40, -40, ["booze"]],
  ]);
});

test("transactions: split charges become one row per part, filtered to the period and categories", () => {
  const txns = shape.parseTransactions([
    ["txn_id", "date", "account_id", "amount", "payee_raw", "payee_text", "key", "category", "splits"],
    ["t1", "2001-02-03", "card", -50, "SHOP*1", "Shop", "SHOP", "household", "household -30.00; food -20.00"],
    ["t2", "2001-02-10", "card", -12.5, "GROCER", "", "GROCER", "food", ""],
    ["t3", "2001-03-01", "card", -99, "GROCER", "", "GROCER", "food", ""],
  ]);
  assert.equal(txns.length, 4);
  assert.deepEqual(shape.transactionsFor(txns, "2001-02", ["food"]).map((t) => [t.date, t.payee, t.amount]), [
    ["2001-02-03", "Shop", -20],
    ["2001-02-10", "GROCER", -12.5],
  ]);
  assert.equal(shape.transactionsFor(txns, "2001", ["food"]).length, 3);
});
