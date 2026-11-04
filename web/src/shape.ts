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

interface BudgetLine {
  name: string;
  group: string;
  priority: string;
  target: number;
  actual: number;
  remaining: number;
  categories: string[];
}

/** An item an order history says a charge bought: its name and price. */
interface Item {
  name: string;
  amount: number;
}

interface Txn {
  id: string;  // the line's txn_id, for correcting it in the ledger
  date: string;
  account: string;
  payee: string;
  category: string;
  amount: number;
  split: string;  // a split charge's parts ("household -30.00; food -20.00"); "" for a whole one
  items: Item[];
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

/** Years and months with budget rows, newest first: "2026" (the year so far), "2026-10", "2026-09", ... */
function budgetPeriods(rows: BudgetRow[]): string[] {
  const months = Array.from(new Set(rows.map((r) => r.month))).sort().reverse();
  const out: string[] = [];
  for (const m of months) {
    const year = m.slice(0, 4);
    if (!out.includes(year)) out.push(year);
    out.push(m);
  }
  return out;
}

function inPeriod(month: string, period: string): boolean {
  return period.length === 4 ? month.slice(0, 4) === period : month === period;
}

/**
 * One line per category (or per group) for a month, or for a year so far (its months added up): target,
 * actual and what remains, the highest priority among its lines. Sorted by priority, then by what remains,
 * least first, so what is over budget comes first within each priority.
 */
function budgetLines(rows: BudgetRow[], period: string, level: string): BudgetLine[] {
  const by = new Map<string, BudgetLine>();
  for (const r of rows) {
    if (!inPeriod(r.month, period)) continue;
    const name = level === "group" ? r.group || r.category : r.category;
    let line = by.get(name);
    if (!line) {
      line = { name, group: r.group, priority: "", target: 0, actual: 0, remaining: 0, categories: [] };
      by.set(name, line);
    }
    line.target += r.target;
    line.actual += r.actual;
    if (!line.categories.includes(r.category)) line.categories.push(r.category);
    const rank = (p: string): number => PRIORITY_RANK[p] ?? 3;
    if (rank(r.priority) < rank(line.priority)) line.priority = r.priority;
  }
  const out = Array.from(by.values()).map((l) => {
    const target = roundCents(l.target);
    const actual = roundCents(l.actual);
    return { ...l, target, actual, remaining: roundCents(target - actual), categories: l.categories.sort() };
  });
  return out.sort((a, b) => (PRIORITY_RANK[a.priority] ?? 3) - (PRIORITY_RANK[b.priority] ?? 3)
    || a.remaining - b.remaining || a.name.localeCompare(b.name));
}

/** The items column: JSON [[category, name, amount], ...], or nothing (a line no order history explains). */
function parseItems(text: string): [string, string, number][] {
  if (!text) return [];
  try {
    const parsed = JSON.parse(text);
    return Array.isArray(parsed)
      ? parsed.filter((i) => Array.isArray(i) && i.length === 3).map((i) => [String(i[0]), String(i[1]), Number(i[2]) || 0])
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
  const itemsAt = values[0].map((h) => String(h).trim()).indexOf("items");  // reports written before it: none
  const out: Txn[] = [];
  for (const v of values.slice(1)) {
    const [date, account, amount, raw, named, category, splits, id] = at.map((i) => String(v[i] ?? "").trim());
    const bought = parseItems(itemsAt < 0 ? "" : String(v[itemsAt] ?? "").trim());
    const itemsIn = (c: string | null): Item[] =>
      bought.filter((i) => c === null || i[0] === c).map((i) => ({ name: i[1], amount: i[2] }));
    const base = { id, date: date.slice(0, 10), account, payee: named || raw, split: splits };
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
function transactionsFor(txns: Txn[], period: string, categories: string[]): Txn[] {
  const wanted = new Set(categories);
  return txns.filter((t) => wanted.has(t.category) && inPeriod(t.date.slice(0, 7), period))
    .sort((a, b) => a.amount - b.amount || b.date.localeCompare(a.date));
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
}

interface Attention {
  label: string;
  count: number;
  detail: string[];  // the log lines that say more
}

interface Status {
  stages: StageRun[];
  attention: Attention[];
  stale: boolean;  // compute has not run since the last ingest
  projection: [string, number][];  // scenario, owe (positive) or refund (negative)
}

const RUN_LOG_COLUMNS = ["run_id", "computed_at", "stage", "detail"];
const RUN_NOTES_COLUMNS = ["run_id", "stage", "at", "n", "message"];
// a compute summary count that needs the owner when it is not zero: key, what it counts, the line that says more
const CHECKS: [string, string, string][] = [
  ["review_open_lines", "lines waiting in the review tab", "categories since"],
  ["downloads_due", "downloads due", "coverage:"],
  ["balances_off", "balances that disagree with the ledger", "balance_check:"],
  ["stubs_missing", "pay deposits with no stub", "paychecks:"],
  ["pay_not_deposited", "pay stubs with no deposit", "paychecks:"],
  ["possible_duplicates", "possible duplicate transactions", "possible_duplicates:"],
  ["tax_check_mismatches", "filed tax lines that disagree", "tax_check:"],
  ["tax_sources_unfiled", "tax values whose source PDF is not filed", "tax_sources:"],
  ["withholding_unexplained", "federal withholding stubs unexplained", "withholding_backtest:"],
  ["wi_withholding_unexplained", "state (wi) withholding stubs unexplained", "wi_withholding_backtest:"],
  ["pa_withholding_unexplained", "state (pa) withholding stubs unexplained", "pa_withholding_backtest:"],
  ["mismatches", "legacy check mismatches", "legacy check"],
  ["legacy_uncategorized", "legacy lines with no category", "legacy check"],
  ["transfers_unmatched", "transfer lines with no partner, not yet answered", "transfers:"],
];
const UNREAD = ["error", "unrecognized"];  // inbox file statuses that leave a file in the inbox

function parseStatus(runLog: unknown[][], notes: unknown[][]): Status {
  const runs: StageRun[] = [];
  if (runLog.length) {
    const [id, at, stage, detail] = columnsAt(runLog, RUN_LOG_COLUMNS, "run_log");
    for (const v of runLog.slice(1)) {
      const name = String(v[stage] ?? "").trim();
      if (name !== "ingest" && name !== "compute") continue;  // an older run_log row ("versions")
      let summary: Record<string, unknown> = {};
      try {
        summary = JSON.parse(String(v[detail] ?? "")) as Record<string, unknown>;
      } catch (e) {
        summary = {};  // a summary from before the status page: no counts to show
      }
      runs.push({ stage: name, runId: String(v[id] ?? ""), at: String(v[at] ?? ""), summary, lines: [] });
    }
  }
  if (notes.length) {
    const [id, stage, , n, message] = columnsAt(notes, RUN_NOTES_COLUMNS, "run_notes");
    const sorted = notes.slice(1).slice().sort((a, b) => Number(a[n]) - Number(b[n]));
    for (const v of sorted) {
      const run = runs.find((r) => r.stage === String(v[stage] ?? "").trim() && r.runId === String(v[id] ?? ""));
      if (run) run.lines.push(String(v[message] ?? ""));
    }
  }
  runs.sort((a, b) => (a.stage === "ingest" ? 0 : 1) - (b.stage === "ingest" ? 0 : 1));
  const ingest = runs.find((r) => r.stage === "ingest");
  const compute = runs.find((r) => r.stage === "compute");
  const attention: Attention[] = [];
  if (ingest) {
    const files = (ingest.summary.files as { file?: string; status?: string; reason?: string }[] | undefined) || [];
    const unread = files.filter((f) => UNREAD.indexOf(String(f.status)) >= 0);
    if (unread.length) {
      attention.push({ label: "inbox files not read", count: unread.length,
                       detail: unread.map((f) => `${f.file}: ${f.status}${f.reason ? " (" + f.reason + ")" : ""}`) });
    }
  }
  if (compute) {
    for (const [key, label, prefix] of CHECKS) {
      const count = Number(compute.summary[key] ?? 0);
      if (count) attention.push({ label, count, detail: compute.lines.filter((l) => l.startsWith(prefix)) });
    }
  }
  const projection = compute && compute.summary.projection && typeof compute.summary.projection === "object"
    ? Object.entries(compute.summary.projection as Record<string, number>).map(([k, v]) => [k, Number(v)] as [string, number])
    : [];
  return { stages: runs, attention, stale: !!(ingest && compute && ingest.at > compute.at), projection };
}
