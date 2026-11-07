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
  key: string;  // the merchant it is answered under in review ("" in reports from before it)
  note: string;  // the line's note in the ledger
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

/** The items column: JSON [[category, name, amount, item_id], ...] (reports before item ids have three), or
 * nothing (a line no order history explains). */
function parseItems(text: string): [string, string, number, string][] {
  if (!text) return [];
  try {
    const parsed = JSON.parse(text);
    return Array.isArray(parsed)
      ? parsed.filter((i) => Array.isArray(i) && i.length >= 3)
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
  const itemsAt = header.indexOf("items");  // reports written before it: none
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
    const base = { id, date: date.slice(0, 10), account, payee: named || raw, split: splits, key: optional(v, "key"),
                   note: optional(v, "note") };
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

interface AttentionItem {
  title: string;
  detail: string;
}

interface Attention {
  key: string;  // the summary count it comes from ("inbox_unread" for inbox files)
  label: string;  // what the count counts, in words ("lines need a category")
  count: number;
  note: string;  // one sentence more, from the reports ("" when there is nothing to add)
  items: AttentionItem[];  // the things themselves, when the reports list them (each download due)
  detail: string[];  // the log lines that say more, for a check with no items
  flags: string[];  // the log lines about it, marked in the run's log
  action: string;  // the Edit tab that answers it, or ""
}

interface Breakdown {
  label: string;
  count: number;
  kind: string;  // "decided", "waiting" or "open"
}

interface Status {
  stages: StageRun[];
  attention: Attention[];
  stale: boolean;  // compute has not run since the last ingest
  projection: [string, number][];  // scenario, owe (positive) or refund (negative)
  projectionYear: string;
  wiProjection: number | null;  // Wisconsin, owed (positive) or refund (negative)
  categories: { since: string; total: number; segments: Breakdown[] } | null;  // how lines were categorized
}

const RUN_LOG_COLUMNS = ["run_id", "computed_at", "stage", "detail"];
const RUN_NOTES_COLUMNS = ["run_id", "stage", "at", "n", "message"];
// a compute summary count that needs the owner when it is not zero: key, what it counts, the line that says
// more, the Edit tab that answers it
const CHECKS: [string, string, string, string][] = [
  ["review_open_lines", "lines need a category", "categories since", "review"],
  ["downloads_due", "downloads due", "coverage:", ""],
  ["balances_off", "balances disagree with the ledger", "balance_check:", ""],
  ["stubs_missing", "pay deposits have no stub", "paychecks:", ""],
  ["pay_not_deposited", "pay stubs have no deposit", "paychecks:", ""],
  ["possible_duplicates", "transactions may be duplicates", "possible_duplicates:", "duplicates"],
  ["tax_check_mismatches", "filed tax lines disagree", "tax_check:", ""],
  ["pay_mismatches", "runs of paychecks disagree with your jobs", "pay_check:", "jobs"],
  ["tax_sources_unfiled", "tax values have no source PDF filed", "tax_sources:", ""],
  ["withholding_unexplained", "federal withholding stubs are unexplained", "withholding_backtest:", "withholding"],
  ["wi_withholding_unexplained", "Wisconsin withholding stubs are unexplained", "wi_withholding_backtest:", "withholding"],
  ["pa_withholding_unexplained", "Pennsylvania withholding stubs are unexplained", "pa_withholding_backtest:", ""],
  ["mismatches", "legacy months disagree", "legacy check", ""],
  ["legacy_uncategorized", "legacy lines have no category", "legacy check", ""],
  ["transfers_unmatched", "transfers have no partner", "transfers:", "review"],
];
const UNREAD = ["error", "unrecognized", "failed"];  // inbox file statuses that leave a file in the inbox
// who decided a line (categorize.py's decided_by), in the breakdown's words; any other is an order history
const DECIDED_BY: Record<string, string> = {
  "your decision": "Your answers", "you": "Your answers", "you, split": "Your answers", "rule": "Payee rules",
  "transfer pair": "Transfers", "model": "Model", "old books": "Old books",
};
const DUE = ["due", "never downloaded"];

function plural(n: number, one: string, many: string): string {
  return `${n.toLocaleString("en-US")} ${n === 1 ? one : many}`;
}

function sumOf(counts: unknown): number {
  return counts && typeof counts === "object"
    ? Object.values(counts as Record<string, unknown>).reduce((a: number, v) => a + (Number(v) || 0), 0) : 0;
}

/** Lines since REVIEW_FROM by how they were decided, waiting for a download, and open, biggest first. */
function categoryBreakdown(summary: Record<string, unknown>): Status["categories"] {
  const c = summary.categories as { since?: string; decided?: Record<string, number>; waiting?: Record<string, number>;
                                    open_lines?: number } | undefined;
  if (!c) return null;
  const by = new Map<string, number>();
  for (const [who, n] of Object.entries(c.decided || {})) {
    const label = DECIDED_BY[who] ?? "Receipts and order histories";
    by.set(label, (by.get(label) || 0) + Number(n));
  }
  const segments: Breakdown[] = Array.from(by.entries()).map(([label, count]) => ({ label, count, kind: "decided" }))
    .sort((a, b) => b.count - a.count);
  const waiting = sumOf(c.waiting);
  if (waiting) segments.push({ label: "Waiting for a download", count: waiting, kind: "waiting" });
  if (c.open_lines) segments.push({ label: "Waiting for you", count: Number(c.open_lines), kind: "open" });
  return { since: String(c.since || ""), total: segments.reduce((a, s) => a + s.count, 0), segments };
}

/** The downloads due, from Reports coverage: each source with how far it reaches and what waits for it. */
function downloadsDue(coverage: unknown[][]): AttentionItem[] {
  if (!coverage.length) return [];
  const header = coverage[0].map((h) => String(h).trim());
  const col = (v: unknown[], name: string): string => String(v[header.indexOf(name)] ?? "").trim();
  return coverage.slice(1).filter((v) => DUE.indexOf(col(v, "status")) >= 0).map((v) => {
    const newest = col(v, "newest").slice(0, 10);
    const age = Number(col(v, "age_days"));
    const waiting = Number(col(v, "waiting_lines")) || 0;
    const reach = newest ? `Has everything through ${newest}` + (age ? `, ${plural(age, "day", "days")} ago` : "") : "Never downloaded";
    const source = col(v, "source");
    return { title: source.charAt(0).toUpperCase() + source.slice(1), detail: reach + (waiting ? `; ${plural(waiting, "line waits", "lines wait")} for it` : "") };
  });
}

/** The review queue's questions in words: how many, and how many are whole merchants. */
function reviewNote(review: unknown[][]): string {
  if (!review.length) return "";
  const kind = review[0].map((h) => String(h).trim()).indexOf("kind");
  const rows = review.slice(1);
  if (!rows.length) return "";
  const merchants = kind < 0 ? 0 : rows.filter((v) => String(v[kind]) === "payee").length;
  return `${plural(rows.length, "question", "questions")}: ${plural(merchants, "merchant", "merchants")} answered once for all their lines, `
    + `${plural(rows.length - merchants, "single line", "single lines")}.`;
}

/**
 * The status page from run_log and run_notes, and, when given, Reports coverage (for the downloads due) and
 * editor_review (for how the review questions split).
 */
function parseStatus(runLog: unknown[][], notes: unknown[][], coverage: unknown[][] = [], review: unknown[][] = []): Status {
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
      attention.push({ key: "inbox_unread", label: "inbox files were not read", count: unread.length, note: "", detail: [],
                       flags: ingest.lines.filter((l) => unread.some((f) => f.file && l.indexOf(String(f.file)) >= 0)),
                       items: unread.map((f) => ({ title: String(f.file), detail: f.reason || String(f.status) })), action: "" });
    }
  }
  if (compute) {
    for (const [key, label, prefix, action] of CHECKS) {
      const count = Number(compute.summary[key] ?? 0);
      if (!count) continue;
      const items = key === "downloads_due" ? downloadsDue(coverage) : [];
      let note = "";
      if (key === "review_open_lines") {
        const waiting = sumOf((compute.summary.categories as { waiting?: unknown } | undefined)?.waiting);
        note = [reviewNote(review), waiting ? `${plural(waiting, "more line waits", "more lines wait")} for a download.` : ""]
          .filter(Boolean).join(" ");
      }
      const structured = items.length || key === "review_open_lines";
      const lines = compute.lines.filter((l) => l.startsWith(prefix));
      attention.push({ key, label, count, note, items, action, flags: lines, detail: structured ? [] : lines });
    }
  }
  const projection = compute && compute.summary.projection && typeof compute.summary.projection === "object"
    ? Object.entries(compute.summary.projection as Record<string, number>).map(([k, v]) => [k, Number(v)] as [string, number])
    : [];
  return { stages: runs, attention, stale: !!(ingest && compute && ingest.at > compute.at), projection,
           projectionYear: compute && compute.summary.projection_year ? String(compute.summary.projection_year) : "",
           wiProjection: compute && typeof compute.summary.wi_projection === "number" ? Number(compute.summary.wi_projection) : null,
           categories: compute ? categoryBreakdown(compute.summary) : null };
}

/** One whole line from the categorized tab, for the transaction page: a split charge with all its parts. */
interface Line {
  id: string;
  date: string;
  account: string;
  payee: string;  // the name an explanation gave it, else the bank's
  payeeRaw: string;
  amount: number;  // the whole line, signed as the ledger has it (money out is negative)
  category: string;
  parts: [string, number][];  // a split's parts, signed; [] for a whole line
  items: { category: string; name: string; amount: number; id: string }[];  // id "" in reports before item ids
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
  const parts: [string, number][] = col("splits") ? col("splits").split("; ").map((piece) => {
    const cut = piece.lastIndexOf(" ");
    return [piece.slice(0, cut), Number(piece.slice(cut + 1)) || 0] as [string, number];
  }) : [];
  return {
    id, date: col("date").slice(0, 10), account: col("account_id"), payee: col("payee_text") || col("payee_raw"),
    payeeRaw: col("payee_raw"), amount: Number(col("amount")) || 0, category: col("category"), parts,
    items: parseItems(col("items")).map((i) => ({ category: i[0], name: i[1], amount: i[2], id: i[3] })),
    decidedBy: col("decided_by"), status: col("status"), note: col("note"), key: col("key"),
    oneOff: col("one_off").toUpperCase() === "TRUE",
  };
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

const TAX_NODES = ["federal tax", "state tax", "social security", "medicare", "taxes", "pre-tax deductions",
                   "post-tax deductions"];
const FROM_SAVINGS = "from savings";
const LOSSES = "capital losses";

function isTaxOrDeduction(name: string): boolean {
  return TAX_NODES.indexOf(name) >= 0 || name.indexOf("pre-tax: ") === 0 || name.indexOf("post-tax: ") === 0;
}

function notEarned(name: string): boolean {
  return name === FROM_SAVINGS || name.indexOf(FROM_SAVINGS + ": ") === 0 || name === "money back"
    || / \(money back\)$/.test(name) || name === "from cash balances";
}

function flowSummary(totals: NodeTotal[]): FlowSummary {
  const out: FlowSummary = { income: [], other: [], taxes: [], went: [], through: [] };
  for (const t of totals) {
    if (t.name === LOSSES) out.income.push({ name: t.name, amount: -roundCents(t.into) });  // what sales lost
    else if (t.name === FROM_SAVINGS + ": " + LOSSES) continue;  // the shares' value the loss took, not money in
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
