/**
 * The tax page's data from Reports: both returns line by line per year as things stand (tax_lines), the
 * withholding that lands the year near the target refund (withholding_plan), retirement accounts so far
 * and by year end (elections), each scenario's bottom lines (projection), and compute's notes about
 * them. Pure functions, tested under Node like shape.ts.
 */

interface TaxLine {
  form: string;
  line: string;
  key: string;
  label: string;
  value: number;
  filed: number | null; // the filed return's value, once it is in return_facts
}

interface TaxPart {
  person: string;
  employer: string;
  part: string; // so far, paychecks, vests, short-term, long-term or estimate
  amount: number;
  detail: string; // where it comes from, in words
}

interface TaxYear {
  year: string;
  forms: Record<string, TaxLine[]>; // form -> lines in order, the year as it stands
  filed: boolean; // whether any line has a filed value
}

// The forms show each year as it stands: a past year is as it happened, and this year's scenarios differ
// only in their totals (projection). Rows for any other scenario, left by an older compute, are ignored.
const AS_IS = "as is";

interface ScenarioTotals {
  scenario: string;
  federal: number; // owed (positive) or refund (negative)
  wisconsin: number;
}

/** One part of one year's income split (taxhistory.py): where it came from, how it was taxed, where it went. */
interface HistoryPart {
  year: string;
  jurisdiction: string; // federal, pa, wi
  split: string; // came, taxed, went
  part: string;
  order: number;
  value: number;
  total: number; // the whole the split's parts add up to
  projected: boolean; // this year's, from the projection, until its return is filed
}

interface TaxPage {
  history: HistoryPart[];
  filed: Record<string, Record<string, TaxLine[]>>; // year -> form -> its lines as filed, for any filed year
  years: TaxYear[];
  totals: ScenarioTotals[]; // the current year's scenarios
  plan: Record<string, string>[];
  accounts: Record<string, string>[];
  notes: string[];
  law: Record<string, string>[]; // the latest year's tax law values, each with its document, page and quote
  parts: Record<string, TaxPart[]>; // "year|key": what a projected line is made of (tax_inputs)
}

function rowsOf(values: unknown[][]): Record<string, string>[] {
  if (!values.length) return [];
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

function numberOr(text: string, otherwise: number | null): number | null {
  return text === "" || isNaN(Number(text)) ? otherwise : Number(text);
}

function taxPage(tabs: Record<string, unknown[][]>): TaxPage {
  const years = new Map<string, TaxYear>();
  const lines = rowsOf(tabs.tax_lines || []).sort((a, b) => Number(a.order) - Number(b.order));
  for (const r of lines) {
    if ((r.scenario || AS_IS) !== AS_IS) continue;
    let y = years.get(r.year);
    if (!y) {
      y = { year: r.year, forms: {}, filed: false };
      years.set(r.year, y);
    }
    const forms = y.forms;
    const filed = numberOr(r.filed, null);
    if (filed !== null) y.filed = true;
    (forms[r.form] || (forms[r.form] = [])).push({
      form: r.form,
      line: r.line,
      key: r.key,
      label: r.label,
      value: Number(r.value) || 0,
      filed,
    });
  }
  const totals = rowsOf(tabs.projection || []).map((r) => ({
    scenario: r.scenario,
    federal: Number(r.owe_or_refund) || 0,
    wisconsin: Number(r.wi_owe_or_refund) || 0,
  }));
  const latest = (rows: Record<string, string>[]): Record<string, string>[] => {
    const top = rows.reduce((a, r) => (r.year > a ? r.year : a), "");
    return rows.filter((r) => !r.year || r.year === top);
  };
  const runs = rowsOf(tabs.run_log || []).filter((r) => r.stage === "compute");
  const run = runs.length ? runs.sort((a, b) => a.computed_at.localeCompare(b.computed_at))[runs.length - 1].run_id : "";
  const notes = rowsOf(tabs.run_notes || [])
    .filter((r) => r.stage === "compute" && r.run_id === run && /^projection \d{4}: /.test(r.message))
    .sort((a, b) => Number(a.n) - Number(b.n))
    .map((r) => r.message.replace(/^projection \d{4}: /, ""));
  const history = rowsOf(tabs.tax_history || [])
    .filter((r) => r.year && r.part)
    .map((r) => ({
      year: r.year,
      jurisdiction: r.jurisdiction,
      split: r.split,
      part: r.part,
      order: Number(r.order) || 0,
      value: Number(r.value) || 0,
      total: Number(r.total) || 0,
      projected: r.basis === "projected",
    }))
    .sort((a, b) => a.year.localeCompare(b.year) || a.order - b.order);
  const filed: Record<string, Record<string, TaxLine[]>> = {};
  for (const r of rowsOf(tabs.filed_lines || []).sort((a, b) => Number(a.order) - Number(b.order))) {
    const forms = filed[r.year] || (filed[r.year] = {});
    (forms[r.form] || (forms[r.form] = [])).push({
      form: r.form,
      line: r.line,
      key: r.key,
      label: r.label,
      value: Number(r.value) || 0,
      filed: Number(r.value) || 0,
    });
  }
  return {
    history,
    filed,
    years: Array.from(years.values()).sort((a, b) => b.year.localeCompare(a.year)),
    totals,
    plan: latest(rowsOf(tabs.withholding_plan || [])),
    accounts: latest(rowsOf(tabs.elections || [])),
    notes,
    law: latest(rowsOf(tabs.tax_sources || [])).filter((r) => r.item && r.document),
    parts: taxParts(rowsOf(tabs.tax_inputs || [])),
  };
}

/** What each projected line is made of (tax_inputs), by "year|key", in the order compute wrote them. */
function taxParts(rows: Record<string, string>[]): Record<string, TaxPart[]> {
  const out: Record<string, TaxPart[]> = {};
  for (const r of rows) {
    if (!r.year || !r.key) continue;
    (out[r.year + "|" + r.key] = out[r.year + "|" + r.key] || []).push({
      person: r.person || "",
      employer: r.employer || "",
      part: r.part || "",
      amount: Number(r.amount) || 0,
      detail: r.detail || "",
    });
  }
  return out;
}
