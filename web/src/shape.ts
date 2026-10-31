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

/** Periods present, newest first; a month (YYYY-MM) sorts after its year (YYYY). */
function periods(rows: FlowRow[]): string[] {
  return Array.from(new Set(rows.map((r) => r.period))).sort((a, b) => (a < b ? 1 : a > b ? -1 : 0));
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
