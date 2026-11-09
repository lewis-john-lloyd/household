/**
 * The Taxes page's Form 8949 part: each year's sales as the form lays them out (form_8949, from lots.py), Part I
 * short-term and Part II long-term, each with its totals as they carry to Schedule D, beside the return as filed
 * (filed_lines) when it is on file. Pure functions, tested under Node.
 */

interface F8949Row {
  saleDate: string;
  acquired: string;
  holdingFrom: string;
  account: string;
  symbol: string;
  shares: number;
  proceeds: number;
  basis: number;
  code: string;
  adjustment: number;
  gain: number;
  washCarried: number;
  reportedBasis: number | null;
  note: string;
}

interface F8949Part {
  term: string; // short_term (Part I) or long_term (Part II)
  rows: F8949Row[];
  proceeds: number;
  basis: number;
  adjustment: number;
  gain: number;
  filed: number | null; // the return's Schedule D line 7 or 15, as filed, when it is on file
}

interface F8949Year {
  year: string;
  parts: F8949Part[];
  disallowed: number;
  filedOnFile: boolean;
}

interface F8949Page {
  years: F8949Year[];
}

function f8949Rows(values: unknown[][]): Record<string, string>[] {
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

function f8949Num(text: string | undefined): number {
  const n = Number(text);
  return text === undefined || text === "" || isNaN(n) ? 0 : n;
}

const F8949_FILED_KEYS: Record<string, string> = { short_term: "capital_gain_short", long_term: "capital_gain_long" };

function form8949Page(tabs: Record<string, unknown[][]>): F8949Page {
  const byYear = new Map<string, Map<string, F8949Row[]>>();
  for (const r of f8949Rows(tabs.form_8949 || [])) {
    const term = r.term === "long_term" ? "long_term" : "short_term";
    const parts = byYear.get(r.year) || new Map<string, F8949Row[]>();
    const rows = parts.get(term) || [];
    const reported =
      r.reported_basis === "" || r.reported_basis === undefined || isNaN(Number(r.reported_basis))
        ? null
        : Number(r.reported_basis);
    rows.push({
      saleDate: r.sale_date,
      acquired: r.acquired,
      holdingFrom: r.holding_from,
      account: r.account_id,
      symbol: r.symbol,
      shares: f8949Num(r.shares),
      proceeds: f8949Num(r.proceeds),
      basis: f8949Num(r.basis),
      code: r.code,
      adjustment: f8949Num(r.adjustment),
      gain: f8949Num(r.gain),
      washCarried: f8949Num(r.wash_carried),
      reportedBasis: reported,
      note: r.note,
    });
    parts.set(term, rows);
    byYear.set(r.year, parts);
  }
  // the filed return's net short- and long-term gains (Schedule D lines 7 and 15), by year
  const filed = new Map<string, Record<string, number>>();
  for (const r of f8949Rows(tabs.filed_lines || [])) {
    if (r.key !== "capital_gain_short" && r.key !== "capital_gain_long") continue;
    const f = filed.get(r.year) || {};
    f[r.key] = f8949Num(r.value);
    filed.set(r.year, f);
  }
  const years: F8949Year[] = [];
  byYear.forEach((parts, year) => {
    const onFile = filed.get(year);
    const out: F8949Part[] = ["short_term", "long_term"]
      .filter((t) => parts.has(t))
      .map((term) => {
        const rows = (parts.get(term) || []).sort(
          (a, b) =>
            a.saleDate.localeCompare(b.saleDate) || a.acquired.localeCompare(b.acquired) || a.symbol.localeCompare(b.symbol),
        );
        const sum = (k: "proceeds" | "basis" | "adjustment" | "gain") =>
          Math.round(rows.reduce((s, x) => s + x[k], 0) * 100) / 100;
        const filedValue = onFile && F8949_FILED_KEYS[term] in onFile ? onFile[F8949_FILED_KEYS[term]] : null;
        return {
          term,
          rows,
          proceeds: sum("proceeds"),
          basis: sum("basis"),
          adjustment: sum("adjustment"),
          gain: sum("gain"),
          filed: filedValue,
        };
      });
    years.push({ year, parts: out, disallowed: out.reduce((s, p) => s + p.adjustment, 0), filedOnFile: !!onFile });
  });
  years.sort((a, b) => b.year.localeCompare(a.year));
  return { years };
}
