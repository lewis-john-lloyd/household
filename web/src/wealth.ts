/**
 * The Investments and Home pages' data from Reports: what each investment account is worth by tax bucket and
 * owner (investment_values, net_worth), the allocation against its targets and the proposed rebalance
 * (allocation, rebalance), net worth over time, tax lots and their sales (open_lots, form_8949), and each home's numbers, estimates, improvements and
 * amortization (property_summary, home_values, home_improvements, mortgage). Pure functions, tested under Node.
 */

interface Amount {
  key: string;
  label: string;
  value: number;
}

interface AccountValue {
  account: string;
  owner: string;
  wrapper: string;
  bucket: string;
  value: number;
  carried: boolean;
  tracked: boolean;
}

interface FundValue {
  symbol: string;
  assetClass: string;
  value: number;
  accounts: string[];
  security: Record<string, string> | null; // its row in the ledger's securities (editor_securities), or null
}

interface OwnerRow {
  owner: string;
  parts: Record<string, number>;
  total: number;
}

interface WorthPoint {
  date: string;
  total: number;
  invested: number;
  homes: number;
  debt: number;
}

/** One line the Over time part can draw: a total, an owner's net worth, a tax bucket, or one account. */
interface ValueLine {
  key: string;
  group: "total" | "owner" | "bucket" | "account";
  label: string;
  owner: string;
  points: [string, number][];
}

interface AllocationRow {
  node: string;
  parent: string;
  depth: number;
  label: string;
  leaf: boolean;
  targetShare: number;
  currentShare: number;
  targetValue: number;
  currentValue: number;
  deltaValue: number;
  proposedValue: number | null;
  proposedShare: number | null;
}

interface Trade {
  account: string;
  bucket: string;
  symbol: string;
  assetClass: string;
  current: number;
  proposed: number;
  trade: number;
}

interface OpenLot {
  lot: string;
  account: string;
  symbol: string;
  acquired: string;
  holdingFrom: string;
  shares: number;
  basis: number;
  washAdded: number;
  value: number | null;
  gain: number | null;
  term: string;
}

interface LotSale {
  year: string;
  saleDate: string;
  account: string;
  symbol: string;
  shares: number;
  acquired: string;
  holdingFrom: string;
  term: string;
  proceeds: number;
  basis: number;
  code: string;
  adjustment: number;
  gain: number;
  note: string;
}

interface LotYear {
  year: string;
  shortTerm: number;
  longTerm: number;
  disallowed: number;
  sales: number;
}

interface WealthPage {
  asOf: string;
  total: number; // every investment account today, 529s included
  buckets: Amount[]; // in BUCKET_ORDER
  accounts: AccountValue[];
  funds: FundValue[];
  owners: OwnerRow[];
  history: WorthPoint[];
  lines: ValueLine[]; // every line the Over time part offers, totals first, then owners, buckets and accounts
  allocation: AllocationRow[];
  trades: Trade[];
  notes: string[];
  openLots: OpenLot[]; // what each lot in a taxable account holds today (open_lots), oldest first
  lotSales: LotSale[]; // each lot part sold, as Form 8949 has it (form_8949), newest first
  lotYears: LotYear[]; // each year's gains and wash sales, newest first
  lotNotes: string[]; // the latest compute's lines about lots
}

interface HomeLine {
  key: string;
  label: string;
  value: number | string;
  asOf: string;
  note: string;
}
interface Home {
  id: string;
  lines: HomeLine[];
}
interface Estimate {
  property: string;
  asOf: string;
  source: string;
  value: number;
  note: string;
}
interface Improvement {
  property: string;
  date: string;
  description: string;
  kind: string;
  cost: number;
  credit: number;
  basisAdded: number;
  receipt: string;
  note: string;
}
interface Payment {
  month: string;
  loan: string;
  payment: number;
  interest: number;
  principal: number;
  escrow: number;
  extra: number;
  balance: number;
}
interface LoanYear {
  year: string;
  loan: string;
  payments: number;
  interest: number;
  principal: number;
  extra: number;
  escrow: number;
  endBalance: number;
  projected: boolean;
}

interface HomeTax {
  property: string;
  year: string;
  assessed: number;
  millRate: number;
  totalTaxes: number;
  credits: number;
  specials: number;
  due: number;
  estimated: boolean;
}

interface LoanDetail {
  loan: string;
  key: string;
  label: string;
  value: string;
  kind: string;
  asOf: string;
  note: string;
}

interface HomePage {
  loans: LoanDetail[];
  homes: Home[];
  estimates: Estimate[];
  improvements: Improvement[];
  schedule: Payment[];
  years: LoanYear[];
  taxes: HomeTax[];
  month: string;
  notes: string[];
}

const BUCKET_ORDER = ["401k_pretax", "401k_roth", "ira_pretax", "ira_roth", "hsa", "taxable", "ibonds", "529", "other"];
const BUCKET_LABELS: Record<string, string> = {
  "401k_pretax": "401(k) pre-tax",
  "401k_roth": "401(k) Roth",
  ira_pretax: "IRA pre-tax",
  ira_roth: "IRA Roth",
  hsa: "HSA",
  taxable: "Taxable",
  ibonds: "I-Bonds",
  "529": "529",
  other: "Other",
  home_equity: "Home equity",
  other_debt: "Other debt",
};
const NOTE_PREFIXES = /^(investments|valuation|rebalance|net worth|house|mortgage):/;

function wealthRows(values: unknown[][]): Record<string, string>[] {
  if (!values || !values.length) return [];
  const header = values[0].map((h) => String(h).trim());
  return values
    .slice(1)
    .map((v) => {
      const row: Record<string, string> = {};
      header.forEach((h, i) => {
        if (h) row[h] = String(v[i] ?? "").trim();
      });
      return row;
    })
    .filter((r) => Object.values(r).some((x) => x !== ""));
}

function num(text: string | undefined): number {
  const n = Number(text);
  return text === undefined || text === "" || isNaN(n) ? 0 : n;
}

function numOrNull(text: string | undefined): number | null {
  return text === undefined || text === "" || isNaN(Number(text)) ? null : Number(text);
}

function latestNotes(tabs: Record<string, unknown[][]>, keep: RegExp): string[] {
  const runs = wealthRows(tabs.run_log || []).filter((r) => r.stage === "compute");
  const run = runs.length ? runs.sort((a, b) => a.computed_at.localeCompare(b.computed_at))[runs.length - 1].run_id : "";
  return wealthRows(tabs.run_notes || [])
    .filter((r) => r.stage === "compute" && r.run_id === run && keep.test(r.message))
    .sort((a, b) => num(a.n) - num(b.n))
    .map((r) => r.message);
}

/** Each date's sum of value by key, as [date, value] in date order. */
function summed(rows: Record<string, string>[], key: (r: Record<string, string>) => string): Map<string, [string, number][]> {
  const by = new Map<string, Map<string, number>>();
  for (const r of rows) {
    const k = key(r);
    if (!k) continue;
    const dates = by.get(k) || new Map<string, number>();
    dates.set(r.date, (dates.get(r.date) || 0) + num(r.value));
    by.set(k, dates);
  }
  const out = new Map<string, [string, number][]>();
  by.forEach((dates, k) =>
    out.set(
      k,
      Array.from(dates.entries()).sort((a, b) => a[0].localeCompare(b[0])),
    ),
  );
  return out;
}

function valueLines(
  vals: Record<string, string>[],
  net: Record<string, string>[],
  history: WorthPoint[],
  ownerOrder: (o: string) => number,
): ValueLine[] {
  const lines: ValueLine[] = [];
  const total = (key: string, label: string, pick: (p: WorthPoint) => number) => {
    const points = history.filter((p) => pick(p) !== 0 || key === "net_worth").map((p) => [p.date, pick(p)] as [string, number]);
    if (points.length) lines.push({ key, group: "total", label, owner: "", points });
  };
  total("net_worth", "Net worth", (p) => p.total);
  total("invested", "Investments", (p) => p.invested);
  total("home_equity", "Home equity", (p) => p.homes);
  total("other_debt", "Other debt", (p) => p.debt);
  const owners = summed(net, (r) => r.owner);
  Array.from(owners.keys())
    .sort((a, b) => ownerOrder(a) - ownerOrder(b) || a.localeCompare(b))
    .forEach((o) => {
      lines.push({ key: "owner:" + o, group: "owner", label: o, owner: o, points: owners.get(o) || [] });
    });
  const buckets = summed(vals, (r) => r.bucket);
  BUCKET_ORDER.concat(Array.from(buckets.keys()).filter((b) => BUCKET_ORDER.indexOf(b) < 0)).forEach((b) => {
    if (buckets.has(b))
      lines.push({ key: "bucket:" + b, group: "bucket", label: BUCKET_LABELS[b] || b, owner: "", points: buckets.get(b) || [] });
  });
  const owner: Record<string, string> = {};
  vals.forEach((r) => {
    owner[r.account_id] = r.owner;
  });
  const accounts = summed(vals, (r) => r.account_id);
  Array.from(accounts.keys())
    .sort()
    .forEach((a) => {
      lines.push({ key: "account:" + a, group: "account", label: a, owner: owner[a] || "", points: accounts.get(a) || [] });
    });
  return lines;
}

function wealthPage(tabs: Record<string, unknown[][]>): WealthPage {
  const securities: Record<string, Record<string, string>> = {}; // each fund's row in force, by symbol
  for (const r of wealthRows(tabs.editor_securities || [])) if (r.symbol) securities[r.symbol] = r;
  const vals = wealthRows(tabs.investment_values || []);
  const asOf = vals.reduce((a, r) => (r.date > a ? r.date : a), "");
  const now = vals.filter((r) => r.date === asOf);
  const byBucket: Record<string, number> = {};
  const byAccount = new Map<string, AccountValue>();
  const byFund = new Map<string, FundValue>();
  for (const r of now) {
    const v = num(r.value);
    byBucket[r.bucket] = (byBucket[r.bucket] || 0) + v;
    const key = r.account_id + "\u0000" + r.bucket;
    const a = byAccount.get(key) || {
      account: r.account_id,
      owner: r.owner,
      wrapper: r.wrapper,
      bucket: r.bucket,
      value: 0,
      carried: false,
      tracked: false,
    };
    a.value += v;
    a.carried = a.carried || r.basis === "carried";
    a.tracked = a.tracked || r.basis === "tracked"; // an old reading moved with prices since
    byAccount.set(key, a);
    const symbol = r.symbol || "(" + r.account_id + " total)";
    const f = byFund.get(symbol) || { symbol, assetClass: r.asset_class, value: 0, accounts: [], security: null };
    f.value += v;
    if (f.accounts.indexOf(r.account_id) < 0) f.accounts.push(r.account_id);
    byFund.set(symbol, f);
  }
  const buckets = BUCKET_ORDER.filter((b) => byBucket[b])
    .map((b) => ({ key: b, label: BUCKET_LABELS[b] || b, value: byBucket[b] }))
    .concat(
      Object.keys(byBucket)
        .filter((b) => BUCKET_ORDER.indexOf(b) < 0)
        .map((b) => ({ key: b, label: b, value: byBucket[b] })),
    );
  const net = wealthRows(tabs.net_worth || []);
  const latest = net.reduce((a, r) => (r.date > a ? r.date : a), "");
  const owners = new Map<string, OwnerRow>();
  for (const r of net.filter((x) => x.date === latest)) {
    const o = owners.get(r.owner) || { owner: r.owner, parts: {}, total: 0 };
    o.parts[r.part] = (o.parts[r.part] || 0) + num(r.value);
    o.total += num(r.value);
    owners.set(r.owner, o);
  }
  const points = new Map<string, WorthPoint>();
  for (const r of net) {
    const p = points.get(r.date) || { date: r.date, total: 0, invested: 0, homes: 0, debt: 0 };
    const v = num(r.value);
    p.total += v;
    if (r.part === "home_equity") p.homes += v;
    else if (r.part === "other_debt") p.debt += v;
    else p.invested += v;
    points.set(r.date, p);
  }
  const allocation = wealthRows(tabs.allocation || [])
    .sort((a, b) => num(a.order) - num(b.order))
    .map((r) => ({
      node: r.node,
      parent: r.parent,
      depth: num(r.depth),
      label: r.label || r.node,
      leaf: r.leaf === "TRUE" || r.leaf === "true",
      targetShare: num(r.target_share),
      currentShare: num(r.current_share),
      targetValue: num(r.target_value),
      currentValue: num(r.current_value),
      deltaValue: num(r.delta_value),
      proposedValue: numOrNull(r.proposed_value),
      proposedShare: numOrNull(r.proposed_share),
    }));
  const trades = wealthRows(tabs.rebalance || []).map((r) => ({
    account: r.account_id,
    bucket: r.bucket,
    symbol: r.symbol,
    assetClass: r.asset_class,
    current: num(r.current),
    proposed: num(r.proposed),
    trade: num(r.trade),
  }));
  const openLots = wealthRows(tabs.open_lots || [])
    .map((r) => ({
      lot: r.lot_id,
      account: r.account_id,
      symbol: r.symbol,
      acquired: r.acquired,
      holdingFrom: r.holding_from,
      shares: num(r.shares),
      basis: num(r.basis),
      washAdded: num(r.wash_added),
      value: numOrNull(r.value),
      gain: numOrNull(r.gain),
      term: r.term_if_sold,
    }))
    .sort((a, b) => a.acquired.localeCompare(b.acquired) || a.holdingFrom.localeCompare(b.holdingFrom));
  const lotSales = wealthRows(tabs.form_8949 || [])
    .map((r) => ({
      year: r.year,
      saleDate: r.sale_date,
      account: r.account_id,
      symbol: r.symbol,
      shares: num(r.shares),
      acquired: r.acquired,
      holdingFrom: r.holding_from,
      term: r.term,
      proceeds: num(r.proceeds),
      basis: num(r.basis),
      code: r.code,
      adjustment: num(r.adjustment),
      gain: num(r.gain),
      note: r.note,
    }))
    .sort((a, b) => b.saleDate.localeCompare(a.saleDate) || a.acquired.localeCompare(b.acquired));
  const years = new Map<string, LotYear>();
  for (const x of lotSales) {
    const y = years.get(x.year) || { year: x.year, shortTerm: 0, longTerm: 0, disallowed: 0, sales: 0 };
    if (x.term === "long_term") y.longTerm += x.gain;
    else y.shortTerm += x.gain;
    y.disallowed += x.adjustment;
    y.sales += 1;
    years.set(x.year, y);
  }
  const ownerOrder = (o: string) => (o === "kids" ? 1 : 0);
  const history = Array.from(points.values()).sort((a, b) => a.date.localeCompare(b.date));
  return {
    asOf,
    total: now.reduce((s, r) => s + num(r.value), 0),
    buckets,
    accounts: Array.from(byAccount.values()).sort((a, b) => b.value - a.value),
    funds: Array.from(byFund.values())
      .map((f) => ({ ...f, security: securities[f.symbol] || null }))
      .sort((a, b) => b.value - a.value),
    owners: Array.from(owners.values()).sort(
      (a, b) => ownerOrder(a.owner) - ownerOrder(b.owner) || a.owner.localeCompare(b.owner),
    ),
    history,
    lines: valueLines(vals, net, history, ownerOrder),
    allocation,
    trades,
    notes: latestNotes(tabs, /^(valuation|rebalance):/),
    openLots,
    lotSales,
    lotYears: Array.from(years.values()).sort((a, b) => b.year.localeCompare(a.year)),
    lotNotes: latestNotes(tabs, /^lots\b/),
  };
}

function homePage(tabs: Record<string, unknown[][]>, month: string): HomePage {
  const homes = new Map<string, Home>();
  for (const r of wealthRows(tabs.property_summary || []).sort((a, b) => num(a.order) - num(b.order))) {
    const h = homes.get(r.property_id) || { id: r.property_id, lines: [] };
    h.lines.push({ key: r.key, label: r.label, value: numOrNull(r.value) ?? r.value, asOf: r.as_of, note: r.note });
    homes.set(r.property_id, h);
  }
  const estimates = wealthRows(tabs.home_values || [])
    .map((r) => ({
      property: r.property_id,
      asOf: r.as_of,
      source: r.source,
      value: num(r.value),
      note: r.note,
    }))
    .sort((a, b) => b.asOf.localeCompare(a.asOf));
  const improvements = wealthRows(tabs.home_improvements || []).map((r) => ({
    property: r.property_id,
    date: r.date,
    description: r.description,
    kind: r.kind,
    cost: num(r.cost),
    credit: num(r.credit),
    basisAdded: num(r.basis_added),
    receipt: r.receipt,
    note: r.note,
  }));
  const schedule = wealthRows(tabs.mortgage || [])
    .map((r) => ({
      month: r.month,
      loan: r.loan_id,
      payment: num(r.payment),
      interest: num(r.interest),
      principal: num(r.principal),
      escrow: num(r.escrow),
      extra: num(r.extra_principal),
      balance: num(r.balance),
    }))
    .sort((a, b) => a.loan.localeCompare(b.loan) || a.month.localeCompare(b.month));
  const years = new Map<string, LoanYear>();
  for (const p of schedule) {
    const key = p.loan + "\u0000" + p.month.slice(0, 4);
    const y = years.get(key) || {
      year: p.month.slice(0, 4),
      loan: p.loan,
      payments: 0,
      interest: 0,
      principal: 0,
      extra: 0,
      escrow: 0,
      endBalance: 0,
      projected: false,
    };
    y.payments += 1;
    y.interest += p.interest;
    y.principal += p.principal;
    y.extra += p.extra;
    y.escrow += p.escrow;
    y.endBalance = p.balance;
    y.projected = y.projected || p.month > month;
    years.set(key, y);
  }
  const taxes = wealthRows(tabs.home_taxes || [])
    .map((r) => ({
      property: r.property_id,
      year: r.tax_year,
      assessed: num(r.assessed),
      millRate: num(r.mill_rate),
      totalTaxes: num(r.total_taxes),
      credits: num(r.credits),
      specials: num(r.specials),
      due: num(r.amount_due),
      estimated: r.basis === "estimated",
    }))
    .sort((a, b) => b.year.localeCompare(a.year));
  const loans = wealthRows(tabs.loan_details || [])
    .sort((a, b) => a.loan_id.localeCompare(b.loan_id) || num(a.order) - num(b.order))
    .map((r) => ({
      loan: r.loan_id,
      key: r.key,
      label: r.label || r.key,
      value: r.value,
      kind: r.kind || "text",
      asOf: r.as_of,
      note: r.note,
    }));
  return {
    loans,
    homes: Array.from(homes.values()),
    estimates,
    improvements,
    schedule,
    years: Array.from(years.values()),
    taxes,
    month,
    notes: latestNotes(tabs, /^(house|mortgage):/),
  };
}
