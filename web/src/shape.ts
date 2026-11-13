/**
 * Reports rows to chart data. Pure functions, no Apps Script calls, so they run
 * the same in Apps Script (where every file shares one global scope) and under
 * Node in the tests, which evaluate the compiled file and pick the functions out.
 *
 * The Reports "flows" tab has one row per period, level, source and target
 * (see flows.py). A Sankey chart wants [source, target, amount] rows with
 * positive amounts, each pair once, and no cycles (Google's Sankey refuses a
 * chart with one).
 */

interface FlowRow {
  period: string;
  level: string;
  source: string;
  target: string;
  amount: number;
}

type SankeyRow = [string, string, number];

const FLOW_COLUMNS = ["period", "level", "source", "target", "amount"];

/** The tab's values (header first) to rows; rows with a missing field are skipped. */
function parseRows(values: unknown[][]): FlowRow[] {
  if (!values.length) return [];
  const header = values[0].map((h) => String(h).trim());
  const at = FLOW_COLUMNS.map((c) => header.indexOf(c));
  if (at.some((i) => i < 0)) {
    throw new Error(`flows tab is missing columns: ${FLOW_COLUMNS.filter((_, n) => at[n] < 0).join(", ")}`);
  }
  const rows: FlowRow[] = [];
  for (const v of values.slice(1)) {
    const [period, level, source, target] = at.slice(0, 4).map((i) => String(v[i] ?? "").trim());
    const amount = Number(v[at[4]]);
    if (period && level && source && target) rows.push({ period, level, source, target, amount });
  }
  return rows;
}

/**
 * Periods present, newest year first; within a year: the year, its monthly average, then its months
 * newest first ("2026", "2026 monthly average", "2026-10", "2026-09", ...).
 */
function periods(rows: FlowRow[]): string[] {
  const rank = (p: string): number => (p.length === 4 ? 0 : p.indexOf(" ") > 0 ? 1 : 2);
  return Array.from(new Set(rows.map((r) => r.period))).sort((a, b) => {
    const year = b.slice(0, 4).localeCompare(a.slice(0, 4));
    if (year !== 0) return year;
    const r = rank(a) - rank(b);
    return r !== 0 ? r : b.localeCompare(a);
  });
}

function roundCents(x: number): number {
  return Math.round(x * 100) / 100;
}

/** Whether target already reaches source through the edges kept so far. */
function reaches(edges: Map<string, Set<string>>, from: string, to: string): boolean {
  const seen = new Set<string>();
  const stack = [from];
  while (stack.length) {
    const node = stack.pop() as string;
    if (node === to) return true;
    if (seen.has(node)) continue;
    seen.add(node);
    for (const next of edges.get(node) ?? []) stack.push(next);
  }
  return false;
}

/**
 * The Sankey rows for one period and level: amounts summed per pair, rounded to
 * cents, zero, negative and self-loop rows dropped, biggest first. An edge that
 * would close a cycle is dropped (the smaller one, since edges are added
 * biggest first); dropped lists them so the page can say so.
 */
function toSankeyRows(rows: FlowRow[], period: string, level: string): { rows: SankeyRow[]; dropped: SankeyRow[] } {
  const sums = new Map<string, number>();
  for (const r of rows) {
    if (r.period !== period || r.level !== level || r.source === r.target) continue;
    if (!Number.isFinite(r.amount)) continue;
    const key = JSON.stringify([r.source, r.target]);
    sums.set(key, (sums.get(key) ?? 0) + r.amount);
  }
  const candidates: SankeyRow[] = [];
  for (const [key, total] of sums) {
    const amount = roundCents(total);
    if (amount > 0) {
      const [source, target] = JSON.parse(key) as [string, string];
      candidates.push([source, target, amount]);
    }
  }
  candidates.sort((a, b) => b[2] - a[2] || a[0].localeCompare(b[0]) || a[1].localeCompare(b[1]));
  const edges = new Map<string, Set<string>>();
  const kept: SankeyRow[] = [];
  const dropped: SankeyRow[] = [];
  for (const row of candidates) {
    const [source, target] = row;
    if (reaches(edges, target, source)) {
      dropped.push(row);
      continue;
    }
    if (!edges.has(source)) edges.set(source, new Set());
    (edges.get(source) as Set<string>).add(target);
    kept.push(row);
  }
  return { rows: kept, dropped };
}

interface NodeTotal {
  name: string;
  into: number;
  out: number;
  total: number;
}

/**
 * Each node's money in and out across the chart's rows, biggest first. A node's total is the larger
 * of the two: everything a source sent, everything a sink received, what passed through a node in the
 * middle (in and out differ there only by rounding).
 */
function nodeTotals(rows: SankeyRow[]): NodeTotal[] {
  const by = new Map<string, NodeTotal>();
  const at = (name: string): NodeTotal => {
    let t = by.get(name);
    if (!t) {
      t = { name, into: 0, out: 0, total: 0 };
      by.set(name, t);
    }
    return t;
  };
  for (const [source, target, amount] of rows) {
    at(source).out += amount;
    at(target).into += amount;
  }
  const out = Array.from(by.values()).map((t) => {
    const into = roundCents(t.into);
    const sent = roundCents(t.out);
    return { name: t.name, into, out: sent, total: Math.max(into, sent) };
  });
  return out.sort((a, b) => b.total - a.total || a.name.localeCompare(b.name));
}

// ---------------------------------------------------------------------------------------------
// Budget against actuals. The Reports "budget_vs_actual" tab has one row per month and category:
// target and actual (spending, positive), with the category's group and the line's priority (see
// budget.py). The "categorized" tab has one row per transaction, a split charge carrying its parts
// in splits ("household -20.00; food -5.50").

interface BudgetRow {
  month: string;
  category: string;
  group: string;
  priority: string;
  target: number;
  actual: number;
}

/** An item an order history says a charge bought: its name and price. */
interface Item {
  name: string;
  amount: number;
}

interface Txn {
  id: string; // the line's txn_id, for correcting it in the ledger
  date: string;
  account: string;
  payee: string;
  category: string;
  amount: number;
  split: string; // a split charge's parts ("household -30.00; food -20.00"); "" for a whole one
  items: Item[];
  key: string; // the merchant it is answered under in review ("" in reports from before it)
  note: string; // the line's note in the ledger
  one_off: boolean; // marked one-off: left out of recurring charges and budget averages
}

const BUDGET_COLUMNS = ["month", "category", "group", "priority", "target", "actual"];
const TXN_COLUMNS = ["date", "account_id", "amount", "payee_raw", "payee_text", "category", "splits", "txn_id"];
const PRIORITY_RANK: Record<string, number> = { high: 0, medium: 1, low: 2 };

function columnsAt(values: unknown[][], wanted: string[], tab: string): number[] {
  const header = values[0].map((h) => String(h).trim());
  const at = wanted.map((c) => header.indexOf(c));
  if (at.some((i) => i < 0)) {
    throw new Error(`${tab} tab is missing columns: ${wanted.filter((_, n) => at[n] < 0).join(", ")}`);
  }
  return at;
}

function parseBudget(values: unknown[][]): BudgetRow[] {
  if (!values.length) return [];
  const at = columnsAt(values, BUDGET_COLUMNS, "budget_vs_actual");
  const rows: BudgetRow[] = [];
  for (const v of values.slice(1)) {
    const [month, category, group, priority] = at.slice(0, 4).map((i) => String(v[i] ?? "").trim());
    const target = Number(v[at[4]]) || 0;
    const actual = Number(v[at[5]]) || 0;
    if (month && category) rows.push({ month, category, group, priority, target, actual });
  }
  return rows;
}

/** The items column: JSON [[category, name, amount, item_id], ...] (reports before item ids have three), or
 * nothing (a line no order history explains). */
function parseItems(text: string): [string, string, number, string][] {
  if (!text) return [];
  try {
    const parsed = JSON.parse(text);
    return Array.isArray(parsed)
      ? parsed
          .filter((i) => Array.isArray(i) && i.length >= 3)
          .map((i) => [String(i[0]), String(i[1]), Number(i[2]) || 0, String(i[3] ?? "")] as [string, string, number, string])
      : [];
  } catch {
    return [];
  }
}

/**
 * Transactions as one row per category: a split charge becomes one row per part. A charge an order history
 * explains carries what it bought, each part only the items in its own category.
 */
function parseTransactions(values: unknown[][]): Txn[] {
  if (!values.length) return [];
  const at = columnsAt(values, TXN_COLUMNS, "categorized");
  const header = values[0].map((h) => String(h).trim());
  const itemsAt = header.indexOf("items"); // reports written before it: none
  const optional = (v: unknown[], name: string): string => {
    const i = header.indexOf(name);
    return i < 0 ? "" : String(v[i] ?? "").trim();
  };
  const out: Txn[] = [];
  for (const v of values.slice(1)) {
    const [date, account, amount, raw, named, category, splits, id] = at.map((i) => String(v[i] ?? "").trim());
    const bought = parseItems(itemsAt < 0 ? "" : String(v[itemsAt] ?? "").trim());
    const itemsIn = (c: string | null): Item[] =>
      bought.filter((i) => c === null || i[0] === c).map((i) => ({ name: i[1], amount: i[2] }));
    const base = {
      id,
      date: date.slice(0, 10),
      account,
      payee: named || raw,
      split: splits,
      key: optional(v, "key"),
      note: optional(v, "note"),
      one_off: optional(v, "one_off").toUpperCase() === "TRUE",
    };
    if (splits) {
      for (const piece of splits.split("; ")) {
        const cut = piece.lastIndexOf(" ");
        const part = piece.slice(0, cut);
        out.push({ ...base, category: part, amount: Number(piece.slice(cut + 1)) || 0, items: itemsIn(part) });
      }
    } else {
      out.push({ ...base, category, amount: Number(amount) || 0, items: itemsIn(null) });
    }
  }
  return out;
}

/** The period's transactions in these categories, biggest spending first. */
// The Budget page's book (budgetview.ts shapes it, in the page and in tests).
/** The BudgetBook from the Reports tabs budget_vs_actual, daily_actuals, subscription_months and editor_categories. */
function budgetBook(
  budgetValues: unknown[][],
  dailyValues: unknown[][],
  recurringValues: unknown[][],
  categoryValues: unknown[][],
  lineValues: unknown[][] = [],
  flowValues: unknown[][] = [],
  givingValues: unknown[][] = [],
  fundValues: unknown[][] = [],
): BudgetBook {
  const book: BudgetBook = { targets: [], days: [], groups: {}, recurring: [], lineTargets: [], income: [] };
  for (const r of parseBudget(budgetValues)) {
    if (r.group) book.groups[r.category] = r.group;
    if (r.target) book.targets.push({ month: r.month, category: r.category, priority: r.priority, target: r.target });
  }
  if (categoryValues.length) {
    const [category, group] = columnsAt(categoryValues, ["category", "group"], "editor_categories");
    for (const v of categoryValues.slice(1)) {
      const c = String(v[category] ?? "").trim();
      if (c && String(v[group] ?? "").trim()) book.groups[c] = String(v[group]).trim();
    }
    const loan = categoryValues[0].indexOf("loan"); // a Reports tab from before 0.60 has no loan column
    if (loan >= 0) {
      book.loans = {};
      for (const v of categoryValues.slice(1)) {
        const c = String(v[category] ?? "").trim();
        if (c && String(v[loan] ?? "").trim()) book.loans[c] = String(v[loan]).trim();
      }
    }
  }
  if (dailyValues.length) {
    const [date, category, spend] = columnsAt(dailyValues, ["date", "category", "spend"], "daily_actuals");
    for (const v of dailyValues.slice(1)) {
      const d = String(v[date] ?? "").slice(0, 10),
        c = String(v[category] ?? "").trim(),
        n = Number(v[spend]) || 0;
      if (/^\d{4}-\d{2}-\d{2}$/.test(d) && c && n) book.days.push([d, c, n]);
    }
  }
  if (recurringValues.length) {
    const [month, key, category, expected, actual] = columnsAt(
      recurringValues,
      ["month", "key", "category", "expected", "actual"],
      "subscription_months",
    );
    for (const v of recurringValues.slice(1)) {
      const want = String(v[expected] ?? "");
      book.recurring.push({
        month: String(v[month] ?? ""),
        key: String(v[key] ?? ""),
        category: String(v[category] ?? ""),
        expected: want === "" ? null : Number(want),
        actual: Number(v[actual]) || 0,
      });
    }
  }
  if (flowValues.length) {
    // what came in each month, from the flows report's group level: net pay, and other income
    const [period, level, source, target, amount] = columnsAt(
      flowValues,
      ["period", "level", "source", "target", "amount"],
      "flows",
    );
    const byMonth = new Map<string, { month: string; pay: number; other: number }>();
    for (const v of flowValues.slice(1)) {
      const month = String(v[period] ?? "");
      if (!/^\d{4}-\d{2}$/.test(month) || v[level] !== "group" || v[target] !== "accounts") continue;
      const kind = v[source] === "net pay" ? "pay" : v[source] === "other income" ? "other" : "";
      if (!kind) continue;
      if (!byMonth.has(month)) byMonth.set(month, { month, pay: 0, other: 0 });
      byMonth.get(month)![kind] += Number(v[amount]) || 0;
    }
    book.income = Array.from(byMonth.values());
  }
  if (lineValues.length) {
    const [month, line, category, target] = columnsAt(lineValues, ["month", "line", "category", "target"], "budget_targets");
    for (const v of lineValues.slice(1)) {
      const n = Number(v[target]) || 0;
      if (n)
        book.lineTargets!.push({
          month: String(v[month] ?? ""),
          line: String(v[line] ?? ""),
          category: String(v[category] ?? ""),
          target: n,
        });
    }
  }
  if (fundValues.length) {
    // each annual (sinking fund) line's running balance at each month's end (budget.sinking_funds)
    const [month, line, category, balance] = columnsAt(fundValues, ["month", "line", "category", "balance"], "sinking_funds");
    const head = fundValues[0].map(String);
    const setAside = head.indexOf("set_aside"),
      spentAt = head.indexOf("spent"); // older Reports have the balance only
    book.funds = [];
    for (const v of fundValues.slice(1)) {
      const m = String(v[month] ?? "");
      if (/^\d{4}-\d{2}$/.test(m))
        book.funds.push({
          month: m,
          line: String(v[line] ?? ""),
          category: String(v[category] ?? ""),
          balance: Number(v[balance]) || 0,
          ...(setAside >= 0 ? { set_aside: Number(v[setAside]) || 0 } : {}),
          ...(spentAt >= 0 ? { spent: Number(v[spentAt]) || 0 } : {}),
        });
    }
  }
  if (givingValues.length) {
    // each month's gifts by recipient (giving.py), and whether the recipient is deductible
    const [month, key, name, category, given, gifts, deductible] = columnsAt(
      givingValues,
      ["month", "key", "name", "category", "given", "gifts", "deductible"],
      "giving",
    );
    book.giving = [];
    for (const v of givingValues.slice(1)) {
      const n = Number(v[given]) || 0;
      if (!n) continue;
      book.giving.push({
        month: String(v[month] ?? ""),
        key: String(v[key] ?? ""),
        name: String(v[name] ?? "") || String(v[key] ?? ""),
        category: String(v[category] ?? ""),
        given: n,
        gifts: Number(v[gifts]) || 0,
        deductible: String(v[deductible] ?? ""),
      });
    }
  }
  return book;
}

/** A period's transactions in these categories, from its first day through `through` ("" for its last). */
function transactionsFor(txns: Txn[], period: string, categories: string[], through = ""): Txn[] {
  const [from, end] = periodDays(period);
  return linesBetween(txns, from, through || end, categories);
}

/** The lines of these categories from one day to another, both included: the most spent first. */
function linesBetween(txns: Txn[], from: string, to: string, categories: string[]): Txn[] {
  const wanted = new Set(categories);
  return txns
    .filter((t) => wanted.has(t.category) && t.date >= from && t.date <= to)
    .sort((a, b) => a.amount - b.amount || b.date.localeCompare(a.date));
}

/** The categories of these groups (editor_categories: category, group), for a group's lines. */
function categoriesOf(values: unknown[][], groups: string[]): string[] {
  if (!values.length || !groups.length) return [];
  const [c, g] = columnsAt(values, ["category", "group"], "editor_categories");
  const wanted = new Set(groups);
  return values
    .slice(1)
    .filter((v) => wanted.has(String(v[g] ?? "").trim()))
    .map((v) => String(v[c] ?? "").trim())
    .filter(Boolean);
}

/**
 * The status page: the latest run of each stage (ingest, compute) from the Reports tabs run_log (its
 * summary, as JSON) and run_notes (every line it printed), and what in them needs the owner.
 */
interface StageRun {
  stage: string;
  runId: string;
  at: string;
  summary: Record<string, unknown>;
  lines: string[];
  levels: string[]; // each line's level (INFO, WARN, ERROR), "INFO" where Reports kept none
}

interface AttentionItem {
  title: string;
  detail: string;
  link?: string; // where it opens: "budget:<category>" for a line item's drawer, "charge:<key>" for a charge's
}

interface Attention {
  key: string; // the summary count it comes from ("inbox_unread" for inbox files)
  label: string; // what the count counts, in words ("lines need a category")
  count: number;
  note: string; // one sentence more, from the reports ("" when there is nothing to add)
  items: AttentionItem[]; // the things themselves, when the reports list them (each download due)
  detail: string[]; // the log lines that say more, for a check with no items
  flags: string[]; // the log lines about it, marked in the run's log
  action: string; // the Edit tab that answers it, or ""
}

interface Breakdown {
  label: string;
  count: number;
  kind: string; // "decided", "waiting" or "open"
}

interface Status {
  stages: StageRun[];
  attention: Attention[];
  stale: boolean; // compute has not run since the last ingest
  projection: [string, number][]; // scenario, owe (positive) or refund (negative)
  projectionYear: string;
  wiProjection: number | null; // Wisconsin, owed (positive) or refund (negative)
  categories: { since: string; total: number; segments: Breakdown[] } | null; // how lines were categorized
}

const RUN_LOG_COLUMNS = ["run_id", "computed_at", "stage", "detail"];
const RUN_NOTES_COLUMNS = ["run_id", "stage", "at", "n", "message"];
// a compute summary count that needs the owner when it is not zero: key, what it counts (for one, then for more),
// the line that says more, the Edit tab that answers it
const CHECKS: [string, string, string, string, string][] = [
  ["review_open_lines", "line needs a category", "lines need a category", "categories since", "review"],
  ["downloads_due", "download due", "downloads due", "coverage:", ""],
  ["balances_off", "balance disagrees with the ledger", "balances disagree with the ledger", "balance_check:", ""],
  ["stubs_missing", "pay deposit has no stub", "pay deposits have no stub", "paychecks:", ""],
  ["pay_not_deposited", "pay stub has no deposit", "pay stubs have no deposit", "paychecks:", ""],
  [
    "possible_duplicates",
    "transaction may be a duplicate",
    "transactions may be duplicates",
    "possible_duplicates:",
    "duplicates",
  ],
  ["tax_check_mismatches", "filed tax line disagrees", "filed tax lines disagree", "tax_check:", ""],
  [
    "pay_mismatches",
    "run of paychecks disagrees with your jobs",
    "runs of paychecks disagree with your jobs",
    "pay_check:",
    "view:jobs",
  ],
  [
    "subscription_price_changes",
    "recurring charge changed price",
    "recurring charges changed price",
    "recurring charge: ",
    "view:recurring",
  ],
  ["tax_sources_unfiled", "tax value has no source PDF filed", "tax values have no source PDF filed", "tax_sources:", ""],
  [
    "withholding_unexplained",
    "federal withholding stub is unexplained",
    "federal withholding stubs are unexplained",
    "withholding_backtest:",
    "withholding",
  ],
  [
    "wi_withholding_unexplained",
    "Wisconsin withholding stub is unexplained",
    "Wisconsin withholding stubs are unexplained",
    "wi_withholding_backtest:",
    "withholding",
  ],
  [
    "pa_withholding_unexplained",
    "Pennsylvania withholding stub is unexplained",
    "Pennsylvania withholding stubs are unexplained",
    "pa_withholding_backtest:",
    "",
  ],
  ["mismatches", "legacy month disagrees", "legacy months disagree", "legacy check", ""],
  ["legacy_uncategorized", "legacy line has no category", "legacy lines have no category", "legacy check", ""],
  ["transfers_unmatched", "transfer has no partner", "transfers have no partner", "transfers:", "review"],
];
const UNREAD = ["error", "unrecognized", "failed"]; // inbox file statuses that leave a file in the inbox
// who decided a line (categorize.py's decided_by), in the breakdown's words; any other is an order history
const DECIDED_BY: Record<string, string> = {
  "your decision": "Your answers",
  you: "Your answers",
  "you, split": "Your answers",
  rule: "Payee rules",
  "transfer pair": "Transfers",
  model: "Model",
  "old books": "Old books",
};
const DUE = ["due", "never downloaded"];

function plural(n: number, one: string, many: string): string {
  return `${n.toLocaleString("en-US")} ${n === 1 ? one : many}`;
}

function sumOf(counts: unknown): number {
  return counts && typeof counts === "object"
    ? Object.values(counts as Record<string, unknown>).reduce((a: number, v) => a + (Number(v) || 0), 0)
    : 0;
}

/** Lines since REVIEW_FROM by how they were decided, waiting for a download, and open, biggest first. */
function categoryBreakdown(summary: Record<string, unknown>): Status["categories"] {
  const c = summary.categories as
    { since?: string; decided?: Record<string, number>; waiting?: Record<string, number>; open_lines?: number } | undefined;
  if (!c) return null;
  const by = new Map<string, number>();
  for (const [who, n] of Object.entries(c.decided || {})) {
    const label = DECIDED_BY[who] ?? "Receipts and order histories";
    by.set(label, (by.get(label) || 0) + Number(n));
  }
  const segments: Breakdown[] = Array.from(by.entries())
    .map(([label, count]) => ({ label, count, kind: "decided" }))
    .sort((a, b) => b.count - a.count);
  const waiting = sumOf(c.waiting);
  if (waiting) segments.push({ label: "Waiting for a download", count: waiting, kind: "waiting" });
  if (c.open_lines) segments.push({ label: "Waiting for you", count: Number(c.open_lines), kind: "open" });
  return { since: String(c.since || ""), total: segments.reduce((a, s) => a + s.count, 0), segments };
}

/** The downloads due, from Reports coverage: each source with how far it reaches and what waits for it. */
const CHANGED_KEY = "subscription_price_changes";
const CHANGED_PREFIX = "recurring charge: ";

/** Each recurring charge that changed price lately, from compute's notes ("recurring charge: <key> now <change>"),
 * opening its own drawer, where the line that set the new price can be marked not part of it. */
function priceChangeItems(lines: string[]): AttentionItem[] {
  return lines
    .filter((l) => l.startsWith(CHANGED_PREFIX))
    .map((l) => {
      const rest = l.slice(CHANGED_PREFIX.length);
      const at = rest.indexOf(" now ");
      const key = (at >= 0 ? rest.slice(0, at) : rest).trim();
      return { title: key, detail: at >= 0 ? "Now " + rest.slice(at + 5).trim() : "", link: "charge:" + key };
    })
    .filter((i) => i.title);
}

function downloadsDue(coverage: unknown[][]): AttentionItem[] {
  if (!coverage.length) return [];
  const header = coverage[0].map((h) => String(h).trim());
  const col = (v: unknown[], name: string): string => String(v[header.indexOf(name)] ?? "").trim();
  return coverage
    .slice(1)
    .filter((v) => DUE.indexOf(col(v, "status")) >= 0)
    .map((v) => {
      const newest = col(v, "newest").slice(0, 10);
      const age = Number(col(v, "age_days"));
      const waiting = Number(col(v, "waiting_lines")) || 0;
      const reach = newest
        ? `Has everything through ${newest}` + (age ? `, ${plural(age, "day", "days")} ago` : "")
        : "Never downloaded";
      const source = col(v, "source");
      return {
        title: source.charAt(0).toUpperCase() + source.slice(1),
        detail: reach + (waiting ? `; ${plural(waiting, "line waits", "lines wait")} for it` : ""),
      };
    });
}

/** The review queue's questions in words: how many, and how many are whole merchants. */
function reviewNote(review: unknown[][]): string {
  if (!review.length) return "";
  const kind = review[0].map((h) => String(h).trim()).indexOf("kind");
  const rows = review.slice(1);
  if (!rows.length) return "";
  const merchants = kind < 0 ? 0 : rows.filter((v) => String(v[kind]) === "payee").length;
  return (
    `${plural(rows.length, "question", "questions")}: ${plural(merchants, "merchant", "merchants")} answered once for all their lines, ` +
    `${plural(rows.length - merchants, "single line", "single lines")}.`
  );
}

/**
 * The status page from run_log and run_notes, and, when given, Reports coverage (for the downloads due) and
 * editor_review (for how the review questions split).
 */
function parseStatus(
  runLog: unknown[][],
  notes: unknown[][],
  coverage: unknown[][] = [],
  review: unknown[][] = [],
  checks: unknown[][] = [],
): Status {
  const runs: StageRun[] = [];
  if (runLog.length) {
    const [id, at, stage, detail] = columnsAt(runLog, RUN_LOG_COLUMNS, "run_log");
    for (const v of runLog.slice(1)) {
      const name = String(v[stage] ?? "").trim();
      if (name !== "ingest" && name !== "compute" && name !== "run") continue; // an older run_log row ("versions")
      let summary: Record<string, unknown> = {};
      try {
        summary = JSON.parse(String(v[detail] ?? "")) as Record<string, unknown>;
      } catch (e) {
        summary = {}; // a summary from before the status page: no counts to show
      }
      runs.push({ stage: name, runId: String(v[id] ?? ""), at: String(v[at] ?? ""), summary, lines: [], levels: [] });
    }
  }
  if (notes.length) {
    const [id, stage, , n, message] = columnsAt(notes, RUN_NOTES_COLUMNS, "run_notes");
    const level = notes[0].map((h) => String(h).trim()).indexOf("level"); // kept since 0.40
    const sorted = notes
      .slice(1)
      .slice()
      .sort((a, b) => Number(a[n]) - Number(b[n]));
    for (const v of sorted) {
      const run = runs.find((r) => r.stage === String(v[stage] ?? "").trim() && r.runId === String(v[id] ?? ""));
      if (run) {
        run.lines.push(String(v[message] ?? ""));
        run.levels.push(level >= 0 && String(v[level] ?? "").trim() ? String(v[level]).trim() : "INFO");
      }
    }
  }
  const order: Record<string, number> = { run: 0, ingest: 1, compute: 2 };
  runs.sort((a, b) => order[a.stage] - order[b.stage]);
  const whole = runs.find((r) => r.stage === "run");
  const ingest = runs.find((r) => r.stage === "ingest");
  const compute = runs.find((r) => r.stage === "compute");
  const attention: Attention[] = [];
  const failed = whole
    ? ((whole.summary.stages as { stage?: string; status?: string; result?: string }[] | undefined) || []).filter(
        (s) => s.status === "failed",
      )
    : [];
  if (failed.length) {
    attention.push({
      key: "stages_failed",
      label: failed.length === 1 ? "engine stage failed" : "engine stages failed",
      count: failed.length,
      note: "The Engine log says where.",
      detail: [],
      action: "",
      flags: whole ? whole.lines.filter((l, i) => whole.levels[i] === "ERROR") : [],
      items: failed.map((s) => ({ title: String(s.stage), detail: String(s.result || "") })),
    });
  }
  if (ingest) {
    const files = (ingest.summary.files as { file?: string; status?: string; reason?: string }[] | undefined) || [];
    const unread = files.filter((f) => UNREAD.indexOf(String(f.status)) >= 0);
    if (unread.length) {
      attention.push({
        key: "inbox_unread",
        label: unread.length === 1 ? "inbox file was not read" : "inbox files were not read",
        count: unread.length,
        note: "",
        detail: [],
        flags: ingest.lines.filter((l) => unread.some((f) => f.file && l.indexOf(String(f.file)) >= 0)),
        items: unread.map((f) => ({ title: String(f.file), detail: f.reason || String(f.status) })),
        action: "",
      });
    }
  }
  if (compute) {
    for (const [key, one, many, prefix, action] of CHECKS) {
      const count = Number(compute.summary[key] ?? 0);
      if (!count) continue;
      const items = key === "downloads_due" ? downloadsDue(coverage) : key === CHANGED_KEY ? priceChangeItems(compute.lines) : [];
      let note = "";
      if (key === "review_open_lines") {
        const waiting = sumOf((compute.summary.categories as { waiting?: unknown } | undefined)?.waiting);
        note = [reviewNote(review), waiting ? `${plural(waiting, "more line waits", "more lines wait")} for a download.` : ""]
          .filter(Boolean)
          .join(" ");
      }
      const structured = items.length || key === "review_open_lines";
      const lines = compute.lines.filter((l) => l.startsWith(prefix));
      attention.push({
        key,
        label: count === 1 ? one : many,
        count,
        note,
        items,
        action,
        flags: lines,
        detail: structured ? [] : lines,
      });
    }
  }
  const budgetCard = checksAttention(budgetChecks(checks));
  if (budgetCard) attention.push(budgetCard);
  const projection =
    compute && compute.summary.projection && typeof compute.summary.projection === "object"
      ? Object.entries(compute.summary.projection as Record<string, number>).map(([k, v]) => [k, Number(v)] as [string, number])
      : [];
  return {
    stages: runs,
    attention,
    stale: !!(ingest && compute && ingest.at > compute.at),
    projection,
    projectionYear: compute && compute.summary.projection_year ? String(compute.summary.projection_year) : "",
    wiProjection: compute && typeof compute.summary.wi_projection === "number" ? Number(compute.summary.wi_projection) : null,
    categories: compute ? categoryBreakdown(compute.summary) : null,
  };
}

/** One whole line from the categorized tab, for the transaction page: a split charge with all its parts. */
interface Line {
  id: string;
  date: string;
  account: string;
  payee: string; // the name an explanation gave it, else the bank's
  payeeRaw: string;
  amount: number; // the whole line, signed as the ledger has it (money out is negative)
  category: string;
  parts: [string, number][]; // a split's parts, signed; [] for a whole line
  items: { category: string; name: string; amount: number; id: string }[]; // id "" in reports before item ids
  decidedBy: string;
  status: string;
  note: string;
  key: string;
  oneOff: boolean;
}

function findLine(values: unknown[][], id: string): Line | null {
  if (!values.length || !id) return null;
  const header = values[0].map((h) => String(h).trim());
  const at = (name: string): number => header.indexOf(name);
  if (at("txn_id") < 0) throw new Error("categorized tab is missing columns: txn_id");
  const row = values.slice(1).find((v) => String(v[at("txn_id")] ?? "").trim() === id);
  if (!row) return null;
  const col = (name: string): string => (at(name) < 0 ? "" : String(row[at(name)] ?? "").trim());
  const parts: [string, number][] = col("splits")
    ? col("splits")
        .split("; ")
        .map((piece) => {
          const cut = piece.lastIndexOf(" ");
          return [piece.slice(0, cut), Number(piece.slice(cut + 1)) || 0] as [string, number];
        })
    : [];
  return {
    id,
    date: col("date").slice(0, 10),
    account: col("account_id"),
    payee: col("payee_text") || col("payee_raw"),
    payeeRaw: col("payee_raw"),
    amount: Number(col("amount")) || 0,
    category: col("category"),
    parts,
    items: parseItems(col("items")).map((i) => ({ category: i[0], name: i[1], amount: i[2], id: i[3] })),
    decidedBy: col("decided_by"),
    status: col("status"),
    note: col("note"),
    key: col("key"),
    oneOff: col("one_off").toUpperCase() === "TRUE",
  };
}

/** One open line a merchant question in review covers, for answering it on its own. */
interface ReviewLine {
  id: string;
  date: string;
  account: string;
  payee: string; // what an email or download named it, else the bank's
  amount: number;
}

/**
 * The open lines a merchant question covers, newest first: open lines whose key is the question's key or starts
 * with it (a question can gather keys that begin the same), leaving out the ones in skip (asked on their own).
 */
function reviewLines(values: unknown[][], key: string, skip: string[] = []): ReviewLine[] {
  if (!values.length || !key) return [];
  const header = values[0].map((h) => String(h).trim());
  const at = (name: string): number => header.indexOf(name);
  for (const name of ["txn_id", "key", "status"]) {
    if (at(name) < 0) throw new Error(`categorized tab is missing columns: ${name}`);
  }
  const col = (v: unknown[], name: string): string => (at(name) < 0 ? "" : String(v[at(name)] ?? "").trim());
  return values
    .slice(1)
    .filter(
      (v) =>
        col(v, "status") === "open" &&
        skip.indexOf(col(v, "txn_id")) < 0 &&
        (col(v, "key") === key || col(v, "key").startsWith(key)),
    )
    .map((v) => ({
      id: col(v, "txn_id"),
      date: col(v, "date").slice(0, 10),
      account: col(v, "account_id"),
      payee: col(v, "payee_text") || col(v, "payee_raw"),
      amount: Number(col(v, "amount")) || 0,
    }))
    .sort((a, b) => (a.date < b.date ? 1 : a.date > b.date ? -1 : 0));
}

/** The Recurring page: the charges you track with their yearly cost, and the total of the ones still charged. */
interface SubscriptionsPage {
  subscriptions: Record<string, string>[];
  total: number;
  computedAt: string;
}

function subscriptionsPage(values: unknown[][]): SubscriptionsPage {
  if (!values.length) return { subscriptions: [], total: 0, computedAt: "" };
  const header = values[0].map((h) => String(h).trim());
  const rows = values
    .slice(1)
    .map((v) => {
      const r: Record<string, string> = {};
      header.forEach((h, i) => {
        if (h && h !== "run_id") r[h] = String(v[i] ?? "").trim();
      });
      return r;
    })
    .filter((r) => r.key);
  const total = rows.filter((r) => r.status !== "stopped").reduce((t, r) => t + (Number(r.yearly) || 0), 0);
  return { subscriptions: rows, total: Math.round(total * 100) / 100, computedAt: "" };
}

interface GiftLine {
  txn_id: string;
  month: string;
  key: string; // the recipient's key, as giving.py sums it
  category: string;
}

/** Each gift line's recipient (the Reports gift_lines tab), so a recipient opens to its gifts. */
function giftLines(values: unknown[][]): GiftLine[] {
  if (!values.length) return [];
  const at = columnsAt(values, ["txn_id", "month", "key", "category"], "gift_lines");
  return values
    .slice(1)
    .map((v) => {
      const [txn, month, key, category] = at.map((i) => String(v[i] ?? "").trim());
      return { txn_id: txn, month: month.slice(0, 7), key, category };
    })
    .filter((g) => g.txn_id);
}

interface TargetInput {
  month: string; // the budget month whose target this works out
  line: string;
  category: string;
  rule: string; // trailing, lookback or seasonal
  input: string; // a month its average takes in
  spend: number; // what the category spent there, less the lines marked one-off
  left_out: number; // what those one-off lines spent
  mean: number;
  spread: number; // the months' standard deviation
  sigma: number; // how many spreads are added to the mean
  value: number; // the target the working gives
}

const TARGET_INPUT_COLUMNS = [
  "month",
  "line",
  "category",
  "rule",
  "input",
  "spend",
  "left_out",
  "mean",
  "spread",
  "sigma",
  "value",
];

/** How each floating target was worked out (the Reports target_inputs tab), or null before a run writes it. */
function targetInputs(values: unknown[][]): TargetInput[] | null {
  if (!values.length) return null;
  const at = columnsAt(values, TARGET_INPUT_COLUMNS, "target_inputs");
  return values.slice(1).map((v) => {
    const [month, line, category, rule, input, spend, leftOut, mean, spread, sigma, value] = at.map((i) =>
      String(v[i] ?? "").trim(),
    );
    return {
      month: month.slice(0, 7),
      line,
      category,
      rule,
      input: input.slice(0, 7),
      spend: Number(spend) || 0,
      left_out: Number(leftOut) || 0,
      mean: Number(mean) || 0,
      spread: Number(spread) || 0,
      sigma: Number(sigma) || 0,
      value: Number(value) || 0,
    };
  });
}

interface BudgetCheck {
  check: string; // its fingerprint: what "Looks right" settles (budget_checks.py)
  kind: string; // price_changed or target_high
  about: string; // the charge's key, or the budget line
  category: string; // the line item it sits under
  label: string; // a few words for the status column
  detail: string; // what it rests on, in a sentence or two
  open: string; // what to look at: "charge:<key>", "txn:<id>" or "line:<category>"
}

const BUDGET_CHECK_COLUMNS = ["check", "kind", "about", "category", "label", "detail", "open"];

/** What looks off in the budget (the Reports budget_checks tab), or null before a run has written it. */
function budgetChecks(values: unknown[][]): BudgetCheck[] | null {
  if (!values.length) return null;
  const at = columnsAt(values, BUDGET_CHECK_COLUMNS, "budget_checks");
  return values
    .slice(1)
    .map((v) => {
      const [check, kind, about, category, label, detail, open] = at.map((i) => String(v[i] ?? "").trim());
      return { check, kind, about, category, label, detail, open };
    })
    .filter((c) => c.check && c.category);
}

/** The Status page's card for the budget's checks: each one, opening its line item on the Budget page. */
function checksAttention(checks: BudgetCheck[] | null): Attention | null {
  if (!checks || !checks.length) return null;
  return {
    key: "budget_checks",
    label: checks.length === 1 ? "thing to check on the Budget page" : "things to check on the Budget page",
    count: checks.length,
    note: "Each is a number that may rest on a wrong input; its line item's drawer shows what to look at.",
    detail: [],
    flags: [],
    action: "view:budget",
    items: checks.map((c) => ({ title: c.label, detail: c.detail, link: "budget:" + c.category })),
  };
}

interface ChargeLine {
  key: string; // the recurring charge's merchant key
  txn_id: string;
  date: string;
  spend: number; // what the line spent, positive
  payee: string;
  counts: boolean; // whether it counts toward the charge's price and cadence
  why: string; // why it does not count, or that it sets the latest price; "" otherwise
}

const CHARGE_COLUMNS = ["key", "txn_id", "date", "spend", "payee", "counts", "why"];

/** Every line each recurring charge was matched with, newest first (the Reports recurring_charges tab). */
function chargeLines(values: unknown[][]): ChargeLine[] {
  if (!values.length) return [];
  const at = columnsAt(values, CHARGE_COLUMNS, "recurring_charges");
  return values
    .slice(1)
    .map((v) => {
      const [key, txn, date, spend, payee, counts, why] = at.map((i) => String(v[i] ?? "").trim());
      return {
        key,
        txn_id: txn,
        date: date.slice(0, 10),
        spend: Number(spend) || 0,
        payee,
        counts: counts.toLowerCase() === "yes",
        why,
      };
    })
    .filter((r) => r.key && r.txn_id);
}

/**
 * The flow chart's nodes by what they are to the household, for the summary beside the chart:
 *   income   what was earned: pay (vested shares at their value included), other income, and the net gain on
 *            shares sold, or the net loss as a negative line
 *   other    money in that wasn't earned: shares sold and other money drawn from savings (their value came in
 *            as income when they vested), money back, cash already in the accounts
 *   taxes    taxes and the deductions taken from pay
 *   went     where it ended up: each category, savings, the shares that vested, cash left in the accounts
 *   through  the stops on the way (net pay, accounts), each with the amount that passed through
 * A source only sends money on and an end only receives it, so a node's direction says which it is.
 */
interface FlowItem {
  name: string;
  amount: number;
}

interface FlowSummary {
  income: FlowItem[];
  other: FlowItem[];
  taxes: FlowItem[];
  went: FlowItem[];
  through: FlowItem[];
}

const TAX_NODES = [
  "federal tax",
  "state tax",
  "social security",
  "medicare",
  "taxes",
  "pre-tax deductions",
  "post-tax deductions",
];
const FROM_SAVINGS = "from savings";
const LOSSES = "capital losses";

function isTaxOrDeduction(name: string): boolean {
  return TAX_NODES.indexOf(name) >= 0 || name.indexOf("pre-tax: ") === 0 || name.indexOf("post-tax: ") === 0;
}

function notEarned(name: string): boolean {
  return (
    name === FROM_SAVINGS ||
    name.indexOf(FROM_SAVINGS + ": ") === 0 ||
    name === "money back" ||
    / \(money back\)$/.test(name) ||
    name === "from cash balances"
  );
}

function flowSummary(totals: NodeTotal[]): FlowSummary {
  const out: FlowSummary = { income: [], other: [], taxes: [], went: [], through: [] };
  for (const t of totals) {
    if (t.name === LOSSES)
      out.income.push({ name: t.name, amount: -roundCents(t.into) }); // what sales lost
    else if (t.name === FROM_SAVINGS + ": " + LOSSES)
      continue; // the shares' value the loss took, not money in
    else if (t.into > 0 && t.out > 0) out.through.push({ name: t.name, amount: roundCents(Math.max(t.into, t.out)) });
    else if (t.out > 0) (notEarned(t.name) ? out.other : out.income).push({ name: t.name, amount: roundCents(t.out) });
    else if (t.into > 0) (isTaxOrDeduction(t.name) ? out.taxes : out.went).push({ name: t.name, amount: roundCents(t.into) });
  }
  const biggest = (a: FlowItem, b: FlowItem): number => b.amount - a.amount || a.name.localeCompare(b.name);
  out.income.sort(biggest);
  out.other.sort(biggest);
  out.taxes.sort(biggest);
  out.went.sort(biggest);
  out.through.sort(biggest);
  return out;
}
