import { test } from "node:test";
import * as assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";

// shape.ts is an Apps Script file: no imports or exports, its functions are globals. Evaluate the
// compiled file the way Apps Script would and pick them out.
const compiled = readFileSync(join(__dirname, "..", "src", "shape.js"), "utf8");
const shape = new Function(`${compiled}\nreturn { parseRows, periods, toSankeyRows, nodeTotals, parseBudget, budgetPeriods, budgetLines, parseTransactions, transactionsFor, parseStatus };`)() as {
  parseRows: (values: unknown[][]) => FlowRow[];
  periods: (rows: FlowRow[]) => string[];
  toSankeyRows: (rows: FlowRow[], period: string, level: string) => { rows: SankeyRow[]; dropped: SankeyRow[] };
  nodeTotals: (rows: SankeyRow[]) => NodeTotal[];
  parseBudget: (values: unknown[][]) => BudgetRow[];
  budgetPeriods: (rows: BudgetRow[]) => string[];
  budgetLines: (rows: BudgetRow[], period: string, level: string) => BudgetLine[];
  parseTransactions: (values: unknown[][]) => Txn[];
  transactionsFor: (txns: Txn[], period: string, categories: string[]) => Txn[];
  parseStatus: (runLog: unknown[][], notes: unknown[][], coverage?: unknown[][], review?: unknown[][]) => Status;
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
  assert.deepEqual(txns[0].items, []);  // a report without an items column
  assert.deepEqual([txns[0].id, txns[0].split, txns[2].split], ["t1", "household -30.00; food -20.00", ""]);
});

test("transactions: a charge an order history explains carries its items, each part its own", () => {
  const items = JSON.stringify([["household", "Spatula", 30], ["food", "Coffee", 20]]);
  const txns = shape.parseTransactions([
    ["txn_id", "date", "account_id", "amount", "payee_raw", "payee_text", "key", "category", "splits", "items"],
    ["t1", "2001-02-03", "card", -50, "SHOP*1", "", "SHOP", "household", "household -30.00; food -20.00", items],
    ["t2", "2001-02-04", "card", -9, "SHOP*2", "", "SHOP", "household", "", "not json"],
    ["t3", "2001-02-05", "card", -30, "SHOP*3", "", "SHOP", "household", "", JSON.stringify([["household", "Spatula", 30]])],
  ]);
  assert.deepEqual(txns.map((t) => [t.category, t.items.map((i) => i.name)]), [
    ["household", ["Spatula"]], ["food", ["Coffee"]], ["household", []], ["household", ["Spatula"]],
  ]);
});

const RUN_LOG: unknown[][] = [
  ["run_id", "computed_at", "stage", "detail"],
  ["r2", "2001-03-02T07:00:00+00:00", "compute",
   JSON.stringify({ review_open_lines: 4, downloads_due: 1, balances_off: 0, transfers_unmatched: 2,
                    projection: { "as is": -1500, "no extra withholding": 300 }, projection_year: 2001,
                    categories: { since: "2001-01-01", decided: { "your decision": 30, you: 5, rule: 20, amazon: 8, co_op: 4 },
                                  waiting: { "waiting for amazon": 3 }, open_lines: 4 } })],
  ["r1", "2001-03-02T06:00:00+00:00", "ingest",
   JSON.stringify({ counts: { ingested: 1, unrecognized: 1 },
                    files: [{ file: "a.csv", status: "ingested" }, { file: "notes.txt", status: "unrecognized" }] })],
  ["r0", "2001-03-01T06:00:00+00:00", "versions", "pandas 2"],
];
const RUN_NOTES: unknown[][] = [
  ["run_id", "stage", "at", "n", "message"],
  ["r2", "compute", "2001-03-02T07:00:00+00:00", 1, "transfers: 9 pairs between your accounts, 2 transfer lines with no partner"],
  ["r2", "compute", "2001-03-02T07:00:00+00:00", 0, "run r2: reading the ledger"],
  ["r2", "compute", "2001-03-02T07:00:00+00:00", 2, "categories since 2001-01-01: decided {}; 4 lines open in review"],
  ["r2", "compute", "2001-03-02T07:00:00+00:00", 3, "coverage: 1 downloads due: amazon"],
  ["r1", "ingest", "2001-03-02T06:00:00+00:00", 0, "run r1: opening the ledger"],
  ["old", "ingest", "2001-02-01T06:00:00+00:00", 0, "an earlier run, not shown"],
];

const COVERAGE: unknown[][] = [
  ["source", "kind", "newest", "age_days", "due_after_days", "waiting_lines", "status", "run_id"],
  ["checking", "account", "2001-02-28", 2, 45, "", "ok", "r2"],
  ["amazon", "explanation", "2001-01-31", 30, "", 3, "due", "r2"],
  ["paypal", "explanation", "", "", "", 0, "not used", "r2"],
];
const REVIEW: unknown[][] = [
  ["key", "guess", "lines", "kind", "run_id"],
  ["ACME GROCER", "food", 3, "payee", "r2"],
  ["t9", "hobby", 1, "line", "r2"],
];

test("the status page shows each stage's latest run, its lines in order, and what needs the owner", () => {
  const status = shape.parseStatus(RUN_LOG, RUN_NOTES, COVERAGE, REVIEW);
  assert.deepEqual(status.stages.map((s) => [s.stage, s.runId]), [["ingest", "r1"], ["compute", "r2"]]);
  assert.equal(status.stages[1].lines.length, 4);
  assert.equal(status.stages[1].lines[1], "transfers: 9 pairs between your accounts, 2 transfer lines with no partner");
  assert.deepEqual(status.attention.map((a) => [a.label, a.count, a.action]), [
    ["inbox files were not read", 1, ""], ["lines need a category", 4, "review"], ["downloads due", 1, ""],
    ["transfers have no partner", 2, "review"]]);
  const [unread, review, downloads, transfers] = status.attention;
  assert.deepEqual(unread.items, [{ title: "notes.txt", detail: "unrecognized" }]);
  assert.equal(review.note, "2 questions: 1 merchant answered once for all their lines, 1 single line. 3 more lines wait for a download.");
  assert.deepEqual(review.detail, []);  // said in words, not as the log line
  assert.deepEqual(downloads.items, [{ title: "Amazon", detail: "Has everything through 2001-01-31, 30 days ago; 3 lines wait for it" }]);
  assert.deepEqual(transfers.detail, ["transfers: 9 pairs between your accounts, 2 transfer lines with no partner"]);
  assert.equal(status.stale, false);
  assert.deepEqual(status.projection, [["as is", -1500], ["no extra withholding", 300]]);
  assert.equal(status.projectionYear, "2001");
});

test("the categorization breakdown groups who decided, then what waits", () => {
  const { categories } = shape.parseStatus(RUN_LOG, RUN_NOTES);
  assert.ok(categories);
  assert.equal(categories.since, "2001-01-01");
  assert.equal(categories.total, 74);
  assert.deepEqual(categories.segments.map((s) => [s.label, s.count, s.kind]), [
    ["Your answers", 35, "decided"], ["Payee rules", 20, "decided"], ["Receipts and order histories", 12, "decided"],
    ["Waiting for a download", 3, "waiting"], ["Waiting for you", 4, "open"]]);
});

test("the status page flags a compute older than the last ingest, and copes with no runs yet", () => {
  const later = RUN_LOG.map((r, n) => (n === 2 ? ["r3", "2001-03-02T08:00:00+00:00", "ingest", "{}"] : r));
  assert.equal(shape.parseStatus(later, []).stale, true);
  const empty = shape.parseStatus([], []);
  assert.deepEqual([empty.stages, empty.attention, empty.projection, empty.stale, empty.categories], [[], [], [], false, null]);
});
