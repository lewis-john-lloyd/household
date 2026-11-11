// The Budget page's periods and views. This file runs in two places: compiled as a server file like the
// others, and inlined into the page by scripts/copy.mjs, so the page shapes every lens and comparison
// itself without asking the server. It uses nothing from the other files.

const BUDGET_RANK: Record<string, number> = { high: 0, medium: 1, low: 2 };

function toCents(n: number): number {
  return Math.round(n * 100) / 100;
}

// The Budget page: any month, quarter or calendar year beside any other of the same kind. The page loads
// one BudgetBook (each month's targets per category, every day's spending per category, and the recurring
// charges' months) and shapes every view from it, so switching lenses asks the server nothing.
// Periods are keys: "2026-10" a month, "2026-Q4" a quarter, "2026" a year.

interface BudgetBook {
  targets: { month: string; category: string; priority: string; target: number }[];
  lineTargets?: { month: string; line: string; category: string; target: number }[]; // each budget line's (budget_targets)
  income?: { month: string; pay: number; other: number }[]; // what came in each month: take-home pay, other income (flows)
  giving?: Gift[]; // each month's gifts by recipient (the giving report)
  funds?: { month: string; line: string; category: string; balance: number }[]; // annual lines' balances at each month's end (sinking_funds)
  days: [string, string, number][]; // [date, category, spend]
  groups: Record<string, string>; // category -> group
  recurring: { month: string; key: string; category: string; expected: number | null; actual: number }[];
}

interface Gift {
  month: string;
  key: string;
  name: string;
  category: string;
  given: number;
  gifts: number;
  deductible: string; // "yes", "no", or "" when not said
}

interface Recipient {
  key: string;
  name: string;
  given: number;
  gifts: number;
  deductible: string;
}

interface RecurringInLine {
  key: string;
  expected: number | null;
  actual: number;
  compared: number;
}

interface ViewLine {
  category: string;
  group: string;
  priority: string;
  target: number;
  actual: number;
  compared: number;
  recurring: RecurringInLine[];
  byLine: { line: string; target: number }[]; // each budget line's part of the target, when there are two or more
  recipients: Recipient[]; // a giving line item's gifts in the period, by recipient
  balance: number | null; // an annual line item's fund: what is set aside and not spent, at the period's last month so far
}

interface ViewGroup {
  name: string;
  target: number;
  actual: number;
  compared: number;
  lines: ViewLine[];
}

interface BudgetView {
  lens: string;
  period: string;
  compare: string;
  from: string; // the days summed: the period's, cut at today when it is not over and sameWeeks is on
  through: string;
  compareFrom: string;
  compareThrough: string;
  partial: boolean; // the period is not over
  sameWeeks: boolean; // only its days so far, beside the same days of the other period
  target: number;
  actual: number;
  compared: number;
  recurring: number; // what the recurring charges in it spent
  pay: number; // take-home pay in the period's months (both paychecks, after tax and payroll deductions)
  otherIncome: number; // other money in: interest, rewards, refunds
  deductible: { year: string; given: number; unsaid: number } | null; // gifts to deductible recipients in the period's last year, through `through`, and the recipients not marked yet
  groups: ViewGroup[];
}

const DAY_MS = 24 * 60 * 60 * 1000;

function isoDay(t: number): string {
  return new Date(t).toISOString().slice(0, 10);
}
function dayMs(date: string): number {
  return Date.parse(date + "T00:00:00Z");
}

/** The period of a lens ("month", "quarter" or "year") a date falls in. */
function periodOf(date: string, lens: string): string {
  if (lens === "year") return date.slice(0, 4);
  if (lens === "quarter") return date.slice(0, 4) + "-Q" + (Math.floor((Number(date.slice(5, 7)) - 1) / 3) + 1);
  return date.slice(0, 7);
}

function lensOf(period: string): string {
  return period.length === 4 ? "year" : period.indexOf("Q") >= 0 ? "quarter" : "month";
}

/** A period's first and last day. */
function periodDays(period: string): [string, string] {
  const year = Number(period.slice(0, 4));
  let first = 0,
    months = 12;
  if (lensOf(period) === "quarter") {
    first = (Number(period.slice(6)) - 1) * 3;
    months = 3;
  } else if (lensOf(period) === "month") {
    first = Number(period.slice(5, 7)) - 1;
    months = 1;
  }
  return [isoDay(Date.UTC(year, first, 1)), isoDay(Date.UTC(year, first + months, 1) - DAY_MS)];
}

/** The period n periods after this one (n negative for before). */
function shiftPeriod(period: string, n: number): string {
  const lens = lensOf(period);
  if (lens === "year") return String(Number(period) + n);
  const [start] = periodDays(period);
  const d = new Date(dayMs(start));
  d.setUTCMonth(d.getUTCMonth() + n * (lens === "quarter" ? 3 : 1));
  return periodOf(isoDay(d.getTime()), lens);
}

/** In words: "October 2026", "Q4 2026", "2026". */
function periodName(period: string): string {
  const lens = lensOf(period);
  if (lens === "year") return period;
  if (lens === "quarter") return period.slice(5) + " " + period.slice(0, 4);
  const names = [
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
  ];
  return names[Number(period.slice(5, 7)) - 1] + " " + period.slice(0, 4);
}

/** Every period of a lens from the book's first spending or target to today's, newest first. */
function lensPeriods(book: BudgetBook, lens: string, today: string): string[] {
  let first = today;
  for (const d of book.days) if (d[0] < first) first = d[0];
  for (const t of book.targets) if (t.month + "-01" < first) first = t.month + "-01";
  const out: string[] = [];
  const last = periodOf(first, lens);
  for (let p = periodOf(today, lens); out.length < 1000; p = shiftPeriod(p, -1)) {
    out.push(p);
    if (p === last) break;
  }
  return out;
}

/** The comparisons offered first: the period before, and the same period a year earlier (for a year, only the one). */
function quickPicks(period: string): string[] {
  const lens = lensOf(period);
  return lens === "year"
    ? [shiftPeriod(period, -1)]
    : [shiftPeriod(period, -1), shiftPeriod(period, lens === "quarter" ? -4 : -12)];
}

/**
 * The budget for one period beside another, by group and category. The target is always the whole period's
 * (a bill due on the 1st makes a pro rata target wrong), so a period not over shows how much of it is used
 * so far. With sameWeeks, a period that is not over (today falls in it) counts its spending through today
 * and the other period's over as many days from its start; otherwise both are whole. Recurring charges sit
 * under their category with what they spent in each.
 */
function budgetView(book: BudgetBook, period: string, compare: string, sameWeeks: boolean, today: string): BudgetView {
  const [from, end] = periodDays(period);
  const [compareFrom, compareEnd] = periodDays(compare);
  const partial = today >= from && today < end;
  const cut = partial && sameWeeks;
  const through = cut ? today : end;
  const days = Math.round((dayMs(through) - dayMs(from)) / DAY_MS);
  const compareThrough = cut ? [isoDay(dayMs(compareFrom) + days * DAY_MS), compareEnd].sort()[0] : compareEnd;
  const lines = new Map<string, ViewLine>();
  const line = (category: string): ViewLine => {
    let l = lines.get(category);
    if (!l) {
      l = {
        category,
        group: book.groups[category] || "",
        priority: "",
        target: 0,
        actual: 0,
        compared: 0,
        recurring: [],
        byLine: [],
        recipients: [],
        balance: null,
      };
      lines.set(category, l);
    }
    return l;
  };
  for (const t of book.targets) {
    const [mFrom, mEnd] = periodDays(t.month);
    if (mEnd < from || mFrom > end) continue;
    const l = line(t.category);
    l.target += t.target;
    if ((BUDGET_RANK[t.priority] ?? 3) < (BUDGET_RANK[l.priority] ?? 3)) l.priority = t.priority;
  }
  const parts = new Map<string, Map<string, number>>(); // category -> line -> its targets in the period
  for (const t of book.lineTargets || []) {
    const [mFrom, mEnd] = periodDays(t.month);
    if (mEnd < from || mFrom > end) continue;
    if (!parts.has(t.category)) parts.set(t.category, new Map());
    const byLine = parts.get(t.category)!;
    byLine.set(t.line, (byLine.get(t.line) || 0) + t.target);
  }
  for (const [date, category, spend] of book.days) {
    if (date >= from && date <= through) line(category).actual += spend;
    else if (date >= compareFrom && date <= compareThrough) line(category).compared += spend;
  }
  const inMonths = (month: string, a: string, b: string): boolean => month + "-01" <= b && periodDays(month)[1] >= a;
  for (const r of book.recurring) {
    const now = inMonths(r.month, from, through),
      then = inMonths(r.month, compareFrom, compareThrough);
    if (!now && !then) continue;
    const l = line(r.category);
    let e = l.recurring.find((x) => x.key === r.key);
    if (!e) {
      e = { key: r.key, expected: null, actual: 0, compared: 0 };
      l.recurring.push(e);
    }
    if (now) {
      if (r.expected !== null) e.expected = (e.expected ?? 0) + r.expected;
      e.actual += r.actual;
    }
    if (then) e.compared += r.actual;
  }
  for (const gift of book.giving || []) {
    const [mFrom, mEnd] = periodDays(gift.month);
    if (mEnd < from || mFrom > through) continue;
    const l = line(gift.category);
    let r = l.recipients.find((x) => x.key === gift.key);
    if (!r) {
      r = { key: gift.key, name: gift.name, given: 0, gifts: 0, deductible: gift.deductible };
      l.recipients.push(r);
    }
    r.given += gift.given;
    r.gifts += gift.gifts;
  }
  // each annual line's balance at the latest month it has on or before the period's last month so far
  const latestFund = new Map<string, { month: string; balance: number }>(); // line -> its latest month
  const lastMonth = through.slice(0, 7);
  const fundCategory = new Map<string, string>();
  for (const f of book.funds || []) {
    if (f.month > lastMonth) continue;
    const had = latestFund.get(f.line);
    if (!had || f.month > had.month) latestFund.set(f.line, { month: f.month, balance: f.balance });
    fundCategory.set(f.line, f.category);
  }
  for (const [fundLine, f] of latestFund) {
    const l = lines.get(fundCategory.get(fundLine) || "");
    if (l) l.balance = toCents((l.balance ?? 0) + f.balance);
  }
  let deductible: BudgetView["deductible"] = null;
  if (book.giving && book.giving.length) {
    const year = through.slice(0, 4);
    const keys = new Set<string>(),
      unsaid = new Set<string>();
    let given = 0;
    for (const gift of book.giving) {
      if (gift.month.slice(0, 4) !== year || gift.month + "-01" > through) continue;
      keys.add(gift.key);
      if (gift.deductible === "yes") given += gift.given;
      if (!gift.deductible) unsaid.add(gift.key);
    }
    if (keys.size) deductible = { year, given: toCents(given), unsaid: unsaid.size };
  }
  const groups = new Map<string, ViewGroup>();
  for (const l of lines.values()) {
    const byLine = parts.get(l.category);
    if (byLine && byLine.size > 1)
      l.byLine = Array.from(byLine, ([line, target]) => ({ line, target: toCents(target) })).sort(
        (a, b) => b.target - a.target || a.line.localeCompare(b.line),
      );
    l.target = toCents(l.target);
    l.actual = toCents(l.actual);
    l.compared = toCents(l.compared);
    l.recurring = l.recurring
      .map((e) => ({
        ...e,
        expected: e.expected === null ? null : toCents(e.expected),
        actual: toCents(e.actual),
        compared: toCents(e.compared),
      }))
      .filter((e) => e.actual || e.expected || e.compared)
      .sort((a, b) => b.actual - a.actual || a.key.localeCompare(b.key));
    l.recipients = l.recipients
      .map((r) => ({ ...r, given: toCents(r.given) }))
      .sort((a, b) => b.given - a.given || a.name.localeCompare(b.name));
    if (!l.target && !l.actual && !l.compared && !l.recurring.length && !l.recipients.length) continue;
    const name = l.group || "Other";
    let g = groups.get(name);
    if (!g) {
      g = { name, target: 0, actual: 0, compared: 0, lines: [] };
      groups.set(name, g);
    }
    g.target += l.target;
    g.actual += l.actual;
    g.compared += l.compared;
    g.lines.push(l);
  }
  const sorted = Array.from(groups.values())
    .map((g) => ({
      ...g,
      target: toCents(g.target),
      actual: toCents(g.actual),
      compared: toCents(g.compared),
      lines: g.lines.sort((a, b) => b.target - a.target || b.actual - a.actual || a.category.localeCompare(b.category)),
    }))
    .sort(
      (a, b) =>
        (a.name === "Other" ? 1 : 0) - (b.name === "Other" ? 1 : 0) ||
        b.target - a.target ||
        b.actual - a.actual ||
        a.name.localeCompare(b.name),
    );
  const sum = (f: (g: ViewGroup) => number): number => toCents(sorted.reduce((t, g) => t + f(g), 0));
  let recurring = 0;
  for (const g of sorted) for (const l of g.lines) for (const e of l.recurring) recurring += e.actual;
  let pay = 0,
    otherIncome = 0;
  for (const i of book.income || []) {
    const [mFrom, mEnd] = periodDays(i.month);
    if (mEnd < from || mFrom > end) continue;
    pay += i.pay;
    otherIncome += i.other;
  }
  return {
    lens: lensOf(period),
    period,
    compare,
    from,
    through,
    compareFrom,
    compareThrough,
    partial,
    sameWeeks: cut,
    target: sum((g) => g.target),
    actual: sum((g) => g.actual),
    compared: sum((g) => g.compared),
    recurring: toCents(recurring),
    pay: toCents(pay),
    otherIncome: toCents(otherIncome),
    deductible,
    groups: sorted,
  };
}
