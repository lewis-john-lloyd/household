import { test } from "node:test";
import * as assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";

// shape.ts is an Apps Script file: no imports or exports, its functions are globals. Evaluate the
// compiled file the way Apps Script would and pick them out.
const compiled = ["shape.js", "budgetview.js"].map((f) => readFileSync(join(__dirname, "..", "src", f), "utf8")).join("\n");
const shape = new Function(
  `${compiled}\nreturn { parseRows, periods, toSankeyRows, nodeTotals, parseBudget, parseTransactions, transactionsFor, parseStatus, findLine, flowSummary, reviewLines, subscriptionsPage, budgetBook, budgetView, periodOf, periodDays, shiftPeriod, periodName, quickPicks, lensPeriods };`,
)() as {
  parseRows: (values: unknown[][]) => FlowRow[];
  periods: (rows: FlowRow[]) => string[];
  toSankeyRows: (rows: FlowRow[], period: string, level: string) => { rows: SankeyRow[]; dropped: SankeyRow[] };
  nodeTotals: (rows: SankeyRow[]) => NodeTotal[];
  parseBudget: (values: unknown[][]) => BudgetRow[];
  parseTransactions: (values: unknown[][]) => Txn[];
  transactionsFor: (txns: Txn[], period: string, categories: string[], through?: string) => Txn[];
  parseStatus: (runLog: unknown[][], notes: unknown[][], coverage?: unknown[][], review?: unknown[][]) => Status;
  findLine: (values: unknown[][], id: string) => Line | null;
  flowSummary: (totals: NodeTotal[]) => FlowSummary;
  reviewLines: (values: unknown[][], key: string, skip?: string[]) => ReviewLine[];
  subscriptionsPage: (values: unknown[][]) => SubscriptionsPage;
  budgetBook: (
    budget: unknown[][],
    daily: unknown[][],
    recurring: unknown[][],
    categories: unknown[][],
    lines?: unknown[][],
    flows?: unknown[][],
    giving?: unknown[][],
    funds?: unknown[][],
  ) => BudgetBook;
  budgetView: (book: BudgetBook, period: string, compare: string, sameWeeks: boolean, today: string) => BudgetView;
  periodOf: (date: string, lens: string) => string;
  periodDays: (period: string) => [string, string];
  shiftPeriod: (period: string, n: number) => string;
  periodName: (period: string) => string;
  quickPicks: (period: string) => string[];
  lensPeriods: (book: BudgetBook, lens: string, today: string) => string[];
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
  assert.deepEqual(rows[0], {
    period: "2001-03",
    level: "category",
    source: "gross pay (alex)",
    target: "net pay",
    amount: 2500,
  });
});

test("parseRows names missing columns", () => {
  assert.throws(() => shape.parseRows([["period", "source"]]), /level, target, amount/);
});

test("periods: newest year first, then its average, then its months newest first", () => {
  const rows = ["2001-03", "2001", "2002-01", "2001 monthly average", "2002", "2001-11", "2002 monthly average"].map(
    (period) => ({ period, level: "category", source: "a", target: "b", amount: 1 }),
  );
  assert.deepEqual(shape.periods(rows), [
    "2002",
    "2002 monthly average",
    "2002-01",
    "2001",
    "2001 monthly average",
    "2001-11",
    "2001-03",
  ]);
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
  assert.deepEqual(out.rows, [
    ["a", "b", 100],
    ["b", "c", 90],
  ]);
  assert.deepEqual(out.dropped, [["c", "a", 5]]);
});

test("nothing for a period that is not there", () => {
  assert.deepEqual(shape.toSankeyRows(shape.parseRows(VALUES), "1999", "group").rows, []);
});

test("node totals: what each node took in and sent out, biggest first", () => {
  const totals = shape.nodeTotals([
    ["net pay", "accounts", 2500],
    ["other income", "accounts", 100.005],
    ["accounts", "food", 400],
    ["accounts", "pets", 99.99],
    ["accounts", "to cash balances", 2100.02],
  ]);
  assert.deepEqual(
    totals.map((t) => [t.name, t.into, t.out, t.total]),
    [
      ["accounts", 2600.01, 2600.01, 2600.01],
      ["net pay", 0, 2500, 2500],
      ["to cash balances", 2100.02, 0, 2100.02],
      ["food", 400, 0, 400],
      ["other income", 0, 100.01, 100.01],
      ["pets", 99.99, 0, 99.99],
    ],
  );
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

test("transactions: split charges become one row per part, filtered to the period and categories", () => {
  const txns = shape.parseTransactions([
    ["txn_id", "date", "account_id", "amount", "payee_raw", "payee_text", "key", "category", "splits"],
    ["t1", "2001-02-03", "card", -50, "SHOP*1", "Shop", "SHOP", "household", "household -30.00; food -20.00"],
    ["t2", "2001-02-10", "card", -12.5, "GROCER", "", "GROCER", "food", ""],
    ["t3", "2001-03-01", "card", -99, "GROCER", "", "GROCER", "food", ""],
  ]);
  assert.equal(txns.length, 4);
  assert.deepEqual(
    shape.transactionsFor(txns, "2001-02", ["food"]).map((t) => [t.date, t.payee, t.amount]),
    [
      ["2001-02-03", "Shop", -20],
      ["2001-02-10", "GROCER", -12.5],
    ],
  );
  assert.equal(shape.transactionsFor(txns, "2001", ["food"]).length, 3);
  assert.equal(shape.transactionsFor(txns, "2001-Q1", ["food"]).length, 3);
  assert.deepEqual(
    shape.transactionsFor(txns, "2001-Q1", ["food"], "2001-02-05").map((t) => t.id),
    ["t1"],
  ); // the same weeks
  assert.deepEqual(txns[0].items, []); // a report without an items column
  assert.deepEqual([txns[0].id, txns[0].split, txns[2].split], ["t1", "household -30.00; food -20.00", ""]);
});

test("transactions: a charge an order history explains carries its items, each part its own", () => {
  const items = JSON.stringify([
    ["household", "Spatula", 30],
    ["food", "Coffee", 20],
  ]);
  const txns = shape.parseTransactions([
    ["txn_id", "date", "account_id", "amount", "payee_raw", "payee_text", "key", "category", "splits", "items"],
    ["t1", "2001-02-03", "card", -50, "SHOP*1", "", "SHOP", "household", "household -30.00; food -20.00", items],
    ["t2", "2001-02-04", "card", -9, "SHOP*2", "", "SHOP", "household", "", "not json"],
    ["t3", "2001-02-05", "card", -30, "SHOP*3", "", "SHOP", "household", "", JSON.stringify([["household", "Spatula", 30]])],
  ]);
  assert.deepEqual(
    txns.map((t) => [t.category, t.items.map((i) => i.name)]),
    [
      ["household", ["Spatula"]],
      ["food", ["Coffee"]],
      ["household", []],
      ["household", ["Spatula"]],
    ],
  );
});

const RUN_LOG: unknown[][] = [
  ["run_id", "computed_at", "stage", "detail"],
  [
    "r2",
    "2001-03-02T07:00:00+00:00",
    "compute",
    JSON.stringify({
      review_open_lines: 4,
      downloads_due: 1,
      balances_off: 0,
      transfers_unmatched: 2,
      projection: { "as is": -1500, "no extra withholding": 300 },
      projection_year: 2001,
      categories: {
        since: "2001-01-01",
        decided: { "your decision": 30, you: 5, rule: 20, amazon: 8, co_op: 4 },
        waiting: { "waiting for amazon": 3 },
        open_lines: 4,
      },
    }),
  ],
  [
    "r1",
    "2001-03-02T06:00:00+00:00",
    "ingest",
    JSON.stringify({
      counts: { ingested: 1, unrecognized: 1 },
      files: [
        { file: "a.csv", status: "ingested" },
        { file: "notes.txt", status: "unrecognized" },
      ],
    }),
  ],
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
  assert.deepEqual(
    status.stages.map((s) => [s.stage, s.runId]),
    [
      ["ingest", "r1"],
      ["compute", "r2"],
    ],
  );
  assert.equal(status.stages[1].lines.length, 4);
  assert.equal(status.stages[1].lines[1], "transfers: 9 pairs between your accounts, 2 transfer lines with no partner");
  assert.deepEqual(
    status.attention.map((a) => [a.label, a.count, a.action]),
    [
      ["inbox file was not read", 1, ""],
      ["lines need a category", 4, "review"],
      ["download due", 1, ""],
      ["transfers have no partner", 2, "review"],
    ],
  );
  const [unread, review, downloads, transfers] = status.attention;
  assert.deepEqual(unread.items, [{ title: "notes.txt", detail: "unrecognized" }]);
  assert.equal(
    review.note,
    "2 questions: 1 merchant answered once for all their lines, 1 single line. 3 more lines wait for a download.",
  );
  assert.deepEqual(review.detail, []); // said in words, not as the log line
  assert.deepEqual(downloads.items, [
    { title: "Amazon", detail: "Has everything through 2001-01-31, 30 days ago; 3 lines wait for it" },
  ]);
  assert.deepEqual(transfers.detail, ["transfers: 9 pairs between your accounts, 2 transfer lines with no partner"]);
  assert.equal(status.stale, false);
  assert.deepEqual(status.projection, [
    ["as is", -1500],
    ["no extra withholding", 300],
  ]);
  assert.equal(status.projectionYear, "2001");
});

test("the categorization breakdown groups who decided, then what waits", () => {
  const { categories } = shape.parseStatus(RUN_LOG, RUN_NOTES);
  assert.ok(categories);
  assert.equal(categories.since, "2001-01-01");
  assert.equal(categories.total, 74);
  assert.deepEqual(
    categories.segments.map((s) => [s.label, s.count, s.kind]),
    [
      ["Your answers", 35, "decided"],
      ["Payee rules", 20, "decided"],
      ["Receipts and order histories", 12, "decided"],
      ["Waiting for a download", 3, "waiting"],
      ["Waiting for you", 4, "open"],
    ],
  );
});

test("the status page flags a compute older than the last ingest, and copes with no runs yet", () => {
  const later = RUN_LOG.map((r, n) => (n === 2 ? ["r3", "2001-03-02T08:00:00+00:00", "ingest", "{}"] : r));
  assert.equal(shape.parseStatus(later, []).stale, true);
  const empty = shape.parseStatus([], []);
  assert.deepEqual([empty.stages, empty.attention, empty.projection, empty.stale, empty.categories], [[], [], [], false, null]);
});

test("findLine gives one whole line, a split with all its parts and the items it bought", () => {
  const values: unknown[][] = [
    [
      "txn_id",
      "date",
      "account_id",
      "amount",
      "payee_raw",
      "payee_text",
      "key",
      "category",
      "splits",
      "items",
      "decided_by",
      "status",
      "note",
      "one_off",
      "run_id",
    ],
    [
      "t1",
      "2001-03-04",
      "card",
      -50,
      "ACME GROCER #2",
      "",
      "ACME GROCER",
      "food",
      "food -30.00; household -20.00",
      JSON.stringify([
        ["food", "Oats", 30, "i1"],
        ["household", "Candle", 20],
      ]),
      "you, split",
      "decided",
      "for the party",
      "TRUE",
      "r1",
    ],
    ["t2", "2001-03-05", "card", -12, "QUILL & INK", "", "QUILL & INK", "hobby", "", "", "rule", "decided", "", "", "r1"],
  ];
  const line = shape.findLine(values, "t1");
  assert.ok(line);
  assert.deepEqual(
    [line.amount, line.payee, line.parts, line.note, line.oneOff, line.key],
    [
      -50,
      "ACME GROCER #2",
      [
        ["food", -30],
        ["household", -20],
      ],
      "for the party",
      true,
      "ACME GROCER",
    ],
  );
  assert.deepEqual(line.items[0], { category: "food", name: "Oats", amount: 30, id: "i1" });
  assert.deepEqual(line.items[1], { category: "household", name: "Candle", amount: 20, id: "" }); // an older report
  assert.deepEqual(shape.findLine(values, "t2")?.parts, []);
  assert.equal(shape.findLine(values, "t9"), null);
});

test("the flow summary sorts nodes into where money came from, taxes, where it went, and stops on the way", () => {
  const node = (name: string, into: number, out: number): NodeTotal => ({ name, into, out, total: Math.max(into, out) });
  const s = shape.flowSummary([
    node("gross pay (alex)", 0, 5000),
    node("from savings: brokerage", 0, 900),
    node("net pay", 3500, 3500),
    node("accounts", 4400, 4400),
    node("federal tax", 800, 0),
    node("pre-tax: 401k", 700, 0),
    node("food", 600, 0),
    node("to cash balances", 1200, 0),
    node("kids", 2600, 0),
  ]);
  assert.deepEqual(
    s.income.map((i) => i.name),
    ["gross pay (alex)"],
  );
  assert.deepEqual(
    s.other.map((i) => i.name),
    ["from savings: brokerage"],
  );
  assert.deepEqual(
    s.taxes.map((i) => [i.name, i.amount]),
    [
      ["federal tax", 800],
      ["pre-tax: 401k", 700],
    ],
  );
  assert.deepEqual(
    s.went.map((i) => i.name),
    ["kids", "to cash balances", "food"],
  );
  assert.deepEqual(
    s.through.map((i) => [i.name, i.amount]),
    [
      ["accounts", 4400],
      ["net pay", 3500],
    ],
  );
});

test("shares sold count only their gain or loss as income; the proceeds and money back are other money in", () => {
  const node = (name: string, into: number, out: number): NodeTotal => ({ name, into, out, total: Math.max(into, out) });
  const s = shape.flowSummary([
    node("gross pay (alex)", 0, 5000),
    node("from savings: stock_sales", 0, 900),
    node("capital gains", 0, 25),
    node("capital gains (to savings)", 25, 0),
    node("food (money back)", 0, 30),
    node("from cash balances", 0, 70),
    node("accounts", 6000, 6000),
    node("food", 6000, 0),
  ]);
  assert.deepEqual(
    s.income.map((i) => [i.name, i.amount]),
    [
      ["gross pay (alex)", 5000],
      ["capital gains", 25],
    ],
  );
  assert.deepEqual(
    s.other.map((i) => i.name),
    ["from savings: stock_sales", "from cash balances", "food (money back)"],
  );
  assert.ok(s.went.some((i) => i.name === "capital gains (to savings)"));
  const lost = shape.flowSummary([
    node("gross pay (alex)", 0, 5000),
    node("from savings: capital losses", 0, 15),
    node("capital losses", 15, 0),
  ]);
  assert.deepEqual(
    lost.income.map((i) => [i.name, i.amount]),
    [
      ["gross pay (alex)", 5000],
      ["capital losses", -15],
    ],
  );
  assert.deepEqual(lost.other, []);
  assert.deepEqual(lost.went, []);
});

test("a merchant question's open lines, newest first, for answering one by one", () => {
  const values = [
    ["txn_id", "date", "account_id", "amount", "payee_raw", "payee_text", "key", "status"],
    ["t1", "2026-09-01", "checking", "-20", "WILLOW ST 1", "WILLOW ST 1", "WILLOW ST", "open"],
    ["t2", "2026-10-01", "card", "-35", "WILLOW ST CAFE", "WILLOW ST CAFE", "WILLOW ST CAFE", "open"],
    ["t3", "2026-10-02", "card", "-10", "WILLOW ST", "WILLOW ST", "WILLOW ST", "decided"],
    ["t4", "2026-10-03", "card", "-12", "ACME GROCER", "ACME GROCER", "ACME GROCER", "open"],
    ["t5", "2026-10-04", "card", "-15", "WILLOW ST", "WILLOW ST", "WILLOW ST", "open"],
  ];
  assert.deepEqual(
    shape.reviewLines(values, "WILLOW ST", ["t5"]).map((l) => [l.id, l.amount]),
    [
      ["t2", -35],
      ["t1", -20],
    ],
  );
});

test("the subscriptions page totals what is still charged", () => {
  const page = shape.subscriptionsPage([
    ["key", "status", "yearly", "run_id"],
    ["PAGE TURNER BOOKS", "price changed", "155.88", "r1"],
    ["QUILL & INK", "active", "60", "r1"],
    ["WILLOW ST", "stopped", "240", "r1"],
  ]);
  assert.equal(page.total, 215.88);
  assert.deepEqual(
    page.subscriptions.map((r) => r.key),
    ["PAGE TURNER BOOKS", "QUILL & INK", "WILLOW ST"],
  );
  assert.equal("run_id" in page.subscriptions[0], false);
});

test("periods: a month, a quarter or a calendar year, shifted and named", () => {
  assert.deepEqual(
    ["month", "quarter", "year"].map((l) => shape.periodOf("2026-11-07", l)),
    ["2026-11", "2026-Q4", "2026"],
  );
  assert.deepEqual(shape.periodDays("2024-02"), ["2024-02-01", "2024-02-29"]);
  assert.deepEqual(shape.periodDays("2026-Q4"), ["2026-10-01", "2026-12-31"]);
  assert.deepEqual(shape.periodDays("2025"), ["2025-01-01", "2025-12-31"]);
  assert.deepEqual(
    [
      shape.shiftPeriod("2026-01", -1),
      shape.shiftPeriod("2026-Q1", -1),
      shape.shiftPeriod("2026-11", -12),
      shape.shiftPeriod("2026", -3),
    ],
    ["2025-12", "2025-Q4", "2025-11", "2023"],
  );
  assert.deepEqual(
    [shape.periodName("2026-10"), shape.periodName("2026-Q4"), shape.periodName("2026")],
    ["October 2026", "Q4 2026", "2026"],
  );
  assert.deepEqual(shape.quickPicks("2026-Q4"), ["2026-Q3", "2025-Q4"]);
  assert.deepEqual(shape.quickPicks("2026"), ["2025"]);
});

// food: 60 a month budgeted in October and November 2026; spending in November 2026 so far, and November 2025
const BOOK_VALUES = {
  budget: [
    ["month", "category", "group", "priority", "target", "actual"],
    ["2026-10", "food", "living", "high", 60, 55],
    ["2026-11", "food", "living", "high", 60, 12],
    ["2026-11", "media", "fun", "", 20, 13],
  ],
  daily: [
    ["date", "category", "spend", "run_id"],
    ["2026-10-12", "food", 55, "r1"],
    ["2026-11-03", "food", 10, "r1"],
    ["2026-11-06", "food", 2, "r1"],
    ["2026-11-05", "media", 13, "r1"],
    ["2025-11-02", "food", 8, "r1"],
    ["2025-11-15", "food", 40, "r1"],
    ["2024-10-20", "food", 30, "r1"],
    ["2024-11-01", "gifts", 25, "r1"],
  ],
  recurring: [
    ["month", "key", "category", "group", "expected", "actual", "run_id"],
    ["2026-11", "PAGE TURNER BOOKS", "media", "fun", "10", "13", "r1"],
    ["2025-11", "PAGE TURNER BOOKS", "media", "fun", "10", "10", "r1"],
  ],
  categories: [
    ["category", "group", "priority", "run_id"],
    ["food", "living", "high", "r1"],
    ["media", "fun", "", "r1"],
  ],
};
const book = (): BudgetBook =>
  shape.budgetBook(BOOK_VALUES.budget, BOOK_VALUES.daily, BOOK_VALUES.recurring, BOOK_VALUES.categories);

test("a month not over, beside the same days of another: the whole month's target, the other cut to as many days", () => {
  const v = shape.budgetView(book(), "2026-11", "2025-11", true, "2026-11-07");
  assert.deepEqual(
    [v.partial, v.sameWeeks, v.from, v.through, v.compareFrom, v.compareThrough],
    [true, true, "2026-11-01", "2026-11-07", "2025-11-01", "2025-11-07"],
  );
  assert.deepEqual(
    v.groups.map((g) => [g.name, g.target, g.actual, g.compared]),
    [
      ["living", 60, 12, 8],
      ["fun", 20, 13, 0],
    ],
  );
  const media = v.groups[1].lines[0];
  assert.deepEqual(media.recurring, [{ key: "PAGE TURNER BOOKS", expected: 10, actual: 13, compared: 10 }]);
  assert.deepEqual([v.target, v.actual, v.compared, v.recurring], [80, 25, 8, 13]);
  const whole = shape.budgetView(book(), "2026-11", "2025-11", false, "2026-11-07");
  assert.deepEqual(
    [whole.sameWeeks, whole.through, whole.compareThrough, whole.target, whole.compared],
    [false, "2026-11-30", "2025-11-30", 80, 48],
  );
});

test("a quarter beside any other: every month's target, and a category only spent in the other still shows", () => {
  const v = shape.budgetView(book(), "2026-Q4", "2024-Q4", true, "2026-11-07");
  assert.deepEqual([v.through, v.compareThrough], ["2026-11-07", "2024-11-07"]);
  assert.deepEqual(
    v.groups.map((g) => [g.name, g.target, g.actual, g.compared, g.lines.map((l) => l.category)]),
    [
      ["living", 120, 67, 30, ["food"]],
      ["fun", 20, 13, 0, ["media"]],
      ["Other", 0, 0, 25, ["gifts"]],
    ],
  );
  const past = shape.budgetView(book(), "2025", "2024", true, "2026-11-07"); // a year that is over is whole
  assert.deepEqual(
    [past.partial, past.sameWeeks, past.through, past.actual, past.compared],
    [false, false, "2025-12-31", 48, 55],
  );
});

test("a lens's periods run from the first spending to today's, newest first", () => {
  assert.deepEqual(shape.lensPeriods(book(), "quarter", "2026-11-07").slice(0, 3), ["2026-Q4", "2026-Q3", "2026-Q2"]);
  assert.deepEqual(shape.lensPeriods(book(), "year", "2026-11-07"), ["2026", "2025", "2024"]);
  assert.equal(shape.lensPeriods(book(), "month", "2026-11-07").slice(-1)[0], "2024-10");
});

test("the run as a whole comes first with each line's level, and a failed stage needs you", () => {
  const log: unknown[][] = [
    ["run_id", "computed_at", "stage", "detail"],
    [
      "r9",
      "2001-03-02T07:10:00Z",
      "run",
      JSON.stringify({
        failed: ["legacy equity"],
        stages: [
          { stage: "legacy equity", status: "failed", seconds: 1, result: "ValueError: no grants tab" },
          { stage: "compute", status: "done", seconds: 9, result: "0 lines open in review" },
        ],
      }),
    ],
    ["r2", "2001-03-02T07:05:00Z", "compute", "{}"],
  ];
  const notes: unknown[][] = [
    ["run_id", "stage", "at", "n", "message", "level"],
    ["r9", "run", "", 0, "legacy equity: failed after 0:01: ValueError: no grants tab", "ERROR"],
    ["r9", "run", "", 1, "summary: 1 of 2 stages done", "ERROR"],
    ["r2", "compute", "", 0, "lots: a vest is missing", "WARN"],
  ];
  const status = shape.parseStatus(log, notes);
  assert.deepEqual(
    status.stages.map((s) => s.stage),
    ["run", "compute"],
  );
  assert.deepEqual(status.stages[1].levels, ["WARN"]);
  const failed = status.attention.find((a) => a.key === "stages_failed");
  assert.deepEqual(failed?.items, [{ title: "legacy equity", detail: "ValueError: no grants tab" }]);
  assert.equal(failed?.flags.length, 2);
  // Reports from before levels: every line INFO
  const old = shape.parseStatus(log.slice(0, 1).concat([log[2]]), [notes[0].slice(0, 5), notes[3].slice(0, 5)]);
  assert.deepEqual(old.stages[0].levels, ["INFO"]);
});

test("budget view: a category's budget lines each keep their part of its target, when it has two or more", () => {
  const lineTargets = [
    ["month", "line", "category", "target"],
    ["2026-10", "camp alex", "kids", 30],
    ["2026-11", "camp alex", "kids", 30],
    ["2026-11", "camp sam", "kids", 20],
    ["2026-11", "food", "food", 60],
  ];
  const budget = BOOK_VALUES.budget.concat([
    ["2026-10", "kids", "family", "high", 30, 0],
    ["2026-11", "kids", "family", "high", 50, 0],
  ]);
  const b = shape.budgetBook(budget, BOOK_VALUES.daily, BOOK_VALUES.recurring, BOOK_VALUES.categories, lineTargets);
  const lines = (period: string) => {
    const out: Record<string, { line: string; target: number }[]> = {};
    for (const g of shape.budgetView(b, period, "2025-11", false, "2026-11-07").groups)
      for (const l of g.lines) out[l.category] = l.byLine;
    return out;
  };
  const month = lines("2026-11");
  assert.deepEqual(month.kids, [
    { line: "camp alex", target: 30 },
    { line: "camp sam", target: 20 },
  ]);
  assert.deepEqual(month.food, []); // one line: the category's row is the line
  assert.deepEqual(lines("2026-Q4").kids[0], { line: "camp alex", target: 60 }); // summed over the period
});

test("budget view: what came in, from the flows report's months, summed over the period", () => {
  const flows = [
    ["period", "level", "source", "target", "amount", "run_id"],
    ["2026-10", "group", "net pay", "accounts", 50, "r1"],
    ["2026-11", "group", "net pay", "accounts", 60, "r1"],
    ["2026-11", "group", "other income", "accounts", 5, "r1"],
    ["2026-11", "category", "net pay", "accounts", 60, "r1"], // the category level repeats it: left out
    ["2026", "group", "net pay", "accounts", 110, "r1"], // a year: left out, the months add up
  ];
  const b = shape.budgetBook(BOOK_VALUES.budget, BOOK_VALUES.daily, BOOK_VALUES.recurring, BOOK_VALUES.categories, [], flows);
  const month = shape.budgetView(b, "2026-11", "2025-11", false, "2026-11-07");
  assert.deepEqual([month.pay, month.otherIncome], [60, 5]);
  const quarter = shape.budgetView(b, "2026-Q4", "2025-Q4", false, "2026-11-07");
  assert.deepEqual([quarter.pay, quarter.otherIncome], [110, 5]);
});

test("budget view: a giving line item's gifts by recipient, and the year's gifts to deductible recipients", () => {
  const giving = [
    ["month", "key", "name", "category", "given", "gifts", "deductible", "run_id"],
    ["2026-03", "QUILL & INK", "Quill & Ink", "gifts", 50, 2, "yes", "r1"],
    ["2026-11", "QUILL & INK", "Quill & Ink", "gifts", 20, 1, "yes", "r1"],
    ["2026-11", "WILLOW ST", "", "gifts", 40, 1, "", "r1"],
    ["2026-12", "WILLOW ST", "", "gifts", 10, 1, "", "r1"], // after today: not in the month so far
    ["2025-11", "WILLOW ST", "", "gifts", 30, 1, "no", "r1"],
  ];
  const b = shape.budgetBook(
    BOOK_VALUES.budget,
    BOOK_VALUES.daily,
    BOOK_VALUES.recurring,
    BOOK_VALUES.categories,
    [],
    [],
    giving,
  );
  const month = shape.budgetView(b, "2026-11", "2025-11", true, "2026-11-07");
  const gifts = month.groups.flatMap((g) => g.lines).find((l) => l.category === "gifts");
  assert.deepEqual(gifts?.recipients, [
    { key: "WILLOW ST", name: "WILLOW ST", given: 40, gifts: 1, deductible: "" },
    { key: "QUILL & INK", name: "Quill & Ink", given: 20, gifts: 1, deductible: "yes" },
  ]);
  assert.deepEqual(month.deductible, { year: "2026", given: 70, unsaid: 1 }); // March and November so far
  const last = shape.budgetView(b, "2025", "2024", false, "2026-11-07");
  assert.deepEqual(last.deductible, { year: "2025", given: 0, unsaid: 0 });
  const none = shape.budgetView(book(), "2026-11", "2025-11", true, "2026-11-07");
  assert.equal(none.deductible, null);
});

test("budget view: an annual line item's fund balance at the period's last month so far", () => {
  const funds = [
    ["month", "line", "category", "set_aside", "spent", "balance", "run_id"],
    ["2026-09", "upkeep", "food", 50, 0, 50, "r1"],
    ["2026-10", "upkeep", "food", 50, 20, 80, "r1"],
    ["2026-11", "upkeep", "food", 50, 90, 40, "r1"],
    ["2026-12", "upkeep", "food", 50, 0, 90, "r1"], // after today: not counted in November so far
  ];
  const b = shape.budgetBook(
    BOOK_VALUES.budget,
    BOOK_VALUES.daily,
    BOOK_VALUES.recurring,
    BOOK_VALUES.categories,
    [],
    [],
    [],
    funds,
  );
  const food = (period: string) =>
    shape
      .budgetView(b, period, "2025-11", true, "2026-11-07")
      .groups.flatMap((g) => g.lines)
      .find((l) => l.category === "food");
  assert.equal(food("2026-11")?.balance, 40);
  assert.equal(food("2026-10")?.balance, 80); // a month that is over: its own end
  assert.equal(food("2026-Q4")?.balance, 40); // a quarter: its last month so far
  const none = shape.budgetView(book(), "2026-11", "2025-11", true, "2026-11-07").groups[0].lines[0];
  assert.equal(none.balance, null);
});

test("budget book: each category's loan comes from editor_categories, and an older tab without the column has none", () => {
  const cats = [
    ["category", "group", "priority", "loan", "run_id"],
    ["food", "living", "high", "", "r1"],
    ["interest", "home", "high", "acme_loan", "r1"],
  ];
  const b = shape.budgetBook(BOOK_VALUES.budget, BOOK_VALUES.daily, BOOK_VALUES.recurring, cats);
  assert.deepEqual(b.loans, { interest: "acme_loan" });
  assert.equal(book().loans, undefined);
});
