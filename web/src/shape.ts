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

interface Txn {
  date: string;
  account: string;
  payee: string;
  category: string;
  amount: number;
}

const BUDGET_COLUMNS = ["month", "category", "group", "priority", "target", "actual"];
const TXN_COLUMNS = ["date", "account_id", "amount", "payee_raw", "payee_text", "category", "splits"];
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

/** Transactions as one row per category: a split charge becomes one row per part. */
function parseTransactions(values: unknown[][]): Txn[] {
  if (!values.length) return [];
  const at = columnsAt(values, TXN_COLUMNS, "categorized");
  const out: Txn[] = [];
  for (const v of values.slice(1)) {
    const [date, account, amount, raw, named, category, splits] = at.map((i) => String(v[i] ?? "").trim());
    const base = { date: date.slice(0, 10), account, payee: named || raw };
    if (splits) {
      for (const piece of splits.split("; ")) {
        const cut = piece.lastIndexOf(" ");
        out.push({ ...base, category: piece.slice(0, cut), amount: Number(piece.slice(cut + 1)) || 0 });
      }
    } else {
      out.push({ ...base, category, amount: Number(amount) || 0 });
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
