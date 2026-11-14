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
  // annual lines' balances at each month's end, with that month's amount set aside and spending (sinking_funds)
  funds?: { month: string; line: string; category: string; balance: number; set_aside?: number; spent?: number }[];
  days: [string, string, number][]; // [date, category, spend]
  oneOffs?: [string, string, number][]; // [date, category, the part of that spend from lines marked one-off]
  groups: Record<string, string>; // category -> group
  loans?: Record<string, string>; // category -> the loan whose payment splits into it (editor_categories)
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
  recipients: Recipient[]; // a giving line item's gifts in the period's last year so far, by recipient
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
  // A giving line item's recipients are the year's so far (the period's last year, through `through`), whatever
  // the period: whether a gift is deductible is a question of the tax year, so a month with no gifts still lists
  // who was given to this year and lets each be marked.
  const giftYear = through.slice(0, 4);
  for (const gift of book.giving || []) {
    if (gift.month.slice(0, 4) !== giftYear || gift.month + "-01" > through) continue;
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
    if (!l.target && !l.actual && !l.compared && !l.recurring.length && !l.recipients.length) continue; // a giving line item shows all year
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

/**
 * A floating target from its months' spending: the mean plus sigma standard deviations (n - 1), as
 * budget.history works it out. Checked against the same fixture as the engine (tests/fixtures/floating_targets.json),
 * so the page's "after the next run, about" can't drift from it.
 */
function floatingValue(values: number[], sigma: number): number {
  if (!values.length) return 0;
  const mean = values.reduce((t, x) => t + x, 0) / values.length;
  const spread = values.length > 1 ? Math.sqrt(values.reduce((t, x) => t + (x - mean) * (x - mean), 0) / (values.length - 1)) : 0;
  return mean + sigma * spread;
}

const PRICE_STEP = 0.03; // recurring.PRICE_STEP
const RAISE_MIN = 0.5; // recurring.RAISE_MIN

/**
 * Whether a charge's latest price differs from the one before enough to say "price changed", as
 * recurring.price_changed decides it. Checked against the same fixture as the engine
 * (tests/fixtures/charge_prices.json), so the page's preview can't drift from it.
 */
function priceChanged(latest: number, previous: number | null): boolean {
  return previous !== null && Math.abs(latest - previous) > Math.max(PRICE_STEP * previous, RAISE_MIN);
}

// recurring.CADENCES, GAP_TOLERANCE and SHARE: the cadences a charge's gaps are held to, shortest first
const CHARGE_CADENCES: [string, number][] = [
  ["weekly", 7],
  ["biweekly", 14],
  ["monthly", 30.4],
  ["quarterly", 91.3],
  ["yearly", 365.25],
];
const GAP_TOLERANCE = 0.2;
const CADENCE_SHARE = 0.7;

/** The cadence a charge's dates fall in, or null with fewer than two or no steady one: the engine's rule
 * (recurring.cadence_of), both checked against tests/fixtures/charge_cadences.json. */
function chargeCadence(dates: string[]): string | null {
  if (dates.length < 2) return null;
  const days = dates.map((d) => d.slice(0, 10)).sort();
  const gaps: number[] = [];
  for (let i = 1; i < days.length; i++) gaps.push(Math.round((Date.parse(days[i]) - Date.parse(days[i - 1])) / 864e5));
  for (const [name, every] of CHARGE_CADENCES) {
    const on = gaps.filter((g) => Math.abs(g - every) <= GAP_TOLERANCE * every).length;
    if (on / gaps.length >= CADENCE_SHARE) return name;
  }
  return null;
}

// ---- equity (equity.ts), shared with the page ----

const EQUITY_STEPS_DEFAULT = [5, 10, 20];
const OPTION_TYPES = ["OPTION", "ISO", "NSO", "NQ"]; // equity.OPTIONS

/** What one share of a grant is worth at a price: an option's spread over its grant price, else the price
 * (equity.per_share). */
function sharePrice(awardType: string, price: number, grantPrice: number | null): number {
  return OPTION_TYPES.indexOf(awardType.toUpperCase()) >= 0 ? Math.max(price - (grantPrice || 0), 0) : price;
}

/** A grant as a setting names it, grant_price:<this> (settings.symbol_key). */
function grantKey(grantId: string): string {
  return grantId
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "_")
    .replace(/^_+|_+$/g, "");
}

/** The equity_price_steps setting as dollar steps: positive numbers, smallest first, at most six; the default
 * when it is unset or has none. */
function equitySteps(text: string | null): number[] {
  const steps = String(text || "")
    .split(",")
    .map((s) => Number(s.replace(/[$\s]/g, "")))
    .filter((n) => isFinite(n) && n > 0);
  const unique = Array.from(new Set(steps)).sort((a, b) => a - b);
  return unique.length ? unique.slice(0, 6) : EQUITY_STEPS_DEFAULT.slice();
}

// The withholding plan's two knobs, previewed on the Taxes and Equity pages before a run: future vests
// withheld at rate, and extra withheld a paycheck (W-4 Step 4(c), or WT-4's additional amount). Both only
// move withholding, never the tax, so a plan row's projection shifts in a straight line. The same rule as
// projection.plan_preview and plan_extra (tests/fixtures/withholding_preview.json).
interface PlanBasis {
  return: string; // federal or wisconsin; the vest rate is federal, so it leaves Wisconsin alone
  projected: number; // refund positive, owed negative, with vests at vest_rate and the current extra
  vest_rate: number | null;
  vest_wages_left: number; // vest income still to come this year
  current_extra: number;
  paychecks_left: number;
  withheld: number | null; // federal: this year's withholding, lines 25a and 25c
  total_tax: number | null; // federal line 24
  safe_harbor: number | null; // from last year's return, when it is on file
}

const PENALTY_FREE_BALANCE = 1000; // tax.PENALTY_FREE_BALANCE
const CURRENT_YEAR_SHARE = 0.9; // tax.CURRENT_YEAR_SHARE

function planShift(row: PlanBasis, rate: number, extra: number): number {
  var vestRate = row.vest_rate === null ? rate : row.vest_rate;
  return (
    (row.return === "federal" ? (rate - vestRate) * row.vest_wages_left : 0) + (extra - row.current_extra) * row.paychecks_left
  );
}

function planPreview(row: PlanBasis, rate: number, extra: number): { lands: number; penaltyFree: boolean | null } {
  var shift = planShift(row, rate, extra);
  var lands = Math.round((row.projected + shift) * 100) / 100;
  if (row.return !== "federal") return { lands: lands, penaltyFree: null };
  var owe = -lands,
    total = row.total_tax || 0;
  var needed = row.safe_harbor === null ? CURRENT_YEAR_SHARE * total : Math.min(CURRENT_YEAR_SHARE * total, row.safe_harbor);
  return { lands: lands, penaltyFree: owe < PENALTY_FREE_BALANCE || (row.withheld || 0) + shift >= needed };
}

function planExtra(row: PlanBasis, rate: number, goal: number): number {
  var projected = row.projected + planShift(row, rate, row.current_extra);
  return Math.round(Math.max(row.current_extra + (goal - projected) / row.paychecks_left, 0) * 100) / 100;
}

// Spending month by month, by group or by category (the Over time part of Where the money went): what was spent,
// from daily_actuals, never what was budgeted. Keys come in order of their spending over all the months the book
// holds, whatever the range shown, so a key keeps its place (and its color) when the range or the filter changes.
// A month's total is its spending in every key; percents are of that total. One-offs can be left out. Spending a
// refund pushes below zero in a month counts as nothing in that month's stack, and is told in `below`.
interface SpendOverTime {
  months: string[]; // YYYY-MM, every month from the first to the last
  keys: string[]; // every group (or category) with spending, most spent first over the whole book
  values: Record<string, number[]>; // key -> spending per month (0 when below zero)
  totals: number[]; // per month, of every key
  below: Record<string, number[]>; // key -> what refunds took below zero, per month
}

function spendingOverTime(
  book: BudgetBook,
  by: "group" | "category",
  opts: { from?: string | undefined; to?: string | undefined; withoutOneOffs?: boolean; group?: string | undefined },
): SpendOverTime {
  var keyOf = function (category: string): string | null {
    var g = book.groups[category] || "Ungrouped";
    if (opts.group && g !== opts.group) return null;
    return by === "group" ? g : category;
  };
  var net: Record<string, Record<string, number>> = {}; // key -> month -> cents
  var everCents: Record<string, number> = {};
  var add = function (date: string, category: string, cents: number) {
    var key = keyOf(category);
    if (key === null) return;
    var month = date.slice(0, 7);
    (net[key] = net[key] || {})[month] = (net[key][month] || 0) + cents;
    everCents[key] = (everCents[key] || 0) + cents;
  };
  book.days.forEach(function (d) {
    add(d[0], d[1], Math.round(d[2] * 100));
  });
  if (opts.withoutOneOffs)
    (book.oneOffs || []).forEach(function (d) {
      add(d[0], d[1], -Math.round(d[2] * 100));
    });
  var keys = Object.keys(net)
    .filter(function (k) {
      return everCents[k] > 0;
    })
    .sort(function (a, b) {
      return everCents[b] - everCents[a] || (a < b ? -1 : 1);
    });
  var all: string[] = [];
  keys.forEach(function (k) {
    Object.keys(net[k]).forEach(function (m) {
      all.push(m);
    });
  });
  all.sort();
  var months: string[] = [];
  var first = opts.from && all.length && opts.from > all[0] ? opts.from : all[0];
  var last = opts.to && all.length && opts.to < all[all.length - 1] ? opts.to : all[all.length - 1];
  for (var m = first; all.length && m <= last; m = shiftPeriod(m, 1)) months.push(m);
  var out: SpendOverTime = { months: months, keys: keys, values: {}, totals: months.map(() => 0), below: {} };
  keys.forEach(function (k) {
    out.values[k] = months.map(function (month, i) {
      var c = net[k][month] || 0;
      if (c < 0) (out.below[k] = out.below[k] || months.map(() => 0))[i] = -c / 100;
      out.totals[i] += Math.max(c, 0);
      return Math.max(c, 0) / 100;
    });
  });
  out.totals = out.totals.map(function (c) {
    return c / 100;
  });
  return out;
}
