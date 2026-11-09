/**
 * The Retirement page's data from Reports (retirement.py writes the tables): each retirement year under the three
 * return models, each strategy at the plan's year, the plan year by year for every strategy, Social Security by
 * person with the earnings it counts, and the assumptions. Pure functions, tested under Node.
 */

interface RetireYear {
  year: number; ages: string; chosen: boolean;
  monteCarlo: number | null; historical: number | null;
  fixed: { scenario: string; success: boolean; shortFrom: number | null; end: number }[];
  atRetirement: number; firstSpending: number; withdrawalRate: number; medianEnd: number; p10End: number;
}

interface RetireStrategy {
  key: string; label: string; order: string; convertTo: number; rule: string; sepp: boolean; claims: string; chosen: boolean;
  historical: number | null; monteCarlo: number | null; afterTax: number; afterTaxP10: number; tax: number; irmaa: number;
  penalty: number; converted: number; spent: number; lowestSpending: number;
}

interface PlanYear {
  year: number; ages: string; phase: string; contributions: number; spending: number; health: number; socialSecurity: number;
  rmd: number; fromTaxable: number; fromPreTax: number; fromRoth: number; fromHsa: number; converted: number;
  ordinaryIncome: number; capitalGains: number; federalTax: number; stateTax: number; irmaa: number; penalty: number; short: number;
  taxable: number; preTax: number; roth: number; hsa: number; total: number; p10: number; p50: number; p90: number;
}

interface Benefit {
  person: string; claimAge: number; claimYear: number; statement: number | null; computed: number; plan: number; planYearly: number;
  aime: number; pia: number; note: string;
}

interface Earning { person: string; year: number; earnings: number; indexed: number; counted: boolean; projected: boolean }

interface Assumption { section: string; key: string; label: string; value: string; source: string; setting: string }

/** One line of the spending the plan uses: a group, a category in it, a line a setting adds, or a loan's end.
 * firstYear and atRetirement are null for a category its group's setting covers. */
interface BudgetLine {
  line: string; group: string; kind: string; now: number; firstYear: number | null; atRetirement: number | null;
  retireYear: number; setting: string; isSet: boolean; lumpy: number | null; note: string;
}

interface RetirementPage {
  chosenYear: number | null; chosenStrategy: string;
  years: RetireYear[]; strategies: RetireStrategy[]; plans: Record<string, PlanYear[]>;
  benefits: Benefit[]; earnings: Earning[]; assumptions: Assumption[]; budget: BudgetLine[]; notes: string[];
}

function retireRows(values: unknown[][]): Record<string, string>[] {
  if (!values || !values.length) return [];
  const header = values[0].map((h) => String(h).trim());
  return values.slice(1).map((v) => {
    const row: Record<string, string> = {};
    header.forEach((h, i) => { if (h) row[h] = String(v[i] ?? "").trim(); });
    return row;
  }).filter((r) => Object.values(r).some((x) => x !== ""));
}

function retireNum(text: string | undefined): number {
  const n = Number(text);
  return text === undefined || text === "" || isNaN(n) ? 0 : n;
}

function retireNumOrNull(text: string | undefined): number | null {
  return text === undefined || text === "" || isNaN(Number(text)) ? null : Number(text);
}

function retireYes(text: string | undefined): boolean {
  return ["yes", "true"].indexOf(String(text || "").toLowerCase()) >= 0;
}

function retirementPage(tabs: Record<string, unknown[][]>): RetirementPage {
  const byYear = new Map<number, RetireYear>();
  for (const r of retireRows(tabs.retirement_years || [])) {
    const year = retireNum(r.retire_year);
    const y = byYear.get(year) || { year, ages: r.ages, chosen: false, monteCarlo: null, historical: null, fixed: [],
                                     atRetirement: 0, firstSpending: 0, withdrawalRate: 0, medianEnd: 0, p10End: 0 };
    y.chosen = y.chosen || retireYes(r.chosen);
    if (r.model === "monte carlo") {
      y.monteCarlo = retireNum(r.success);
      y.atRetirement = retireNum(r.at_retirement);
      y.firstSpending = retireNum(r.first_spending);
      y.withdrawalRate = retireNum(r.withdrawal_rate);
      y.medianEnd = retireNum(r.median_end);
      y.p10End = retireNum(r.p10_end);
    } else if (r.model === "historical") {
      y.historical = retireNum(r.success);
    } else if (r.model === "fixed") {
      y.fixed.push({ scenario: r.scenario, success: retireNum(r.success) >= 1, shortFrom: retireNumOrNull(r.short_from),
                     end: retireNum(r.median_end) });
    }
    byYear.set(year, y);
  }
  const years = Array.from(byYear.values()).sort((a, b) => a.year - b.year);
  years.forEach((y) => y.fixed.sort((a, b) => parseFloat(a.scenario) - parseFloat(b.scenario)));

  const byKey = new Map<string, RetireStrategy>();
  for (const r of retireRows(tabs.retirement_strategies || [])) {
    const s = byKey.get(r.strategy) || {
      key: r.strategy, label: r.label, order: r.order, convertTo: retireNum(r.convert_to), rule: r.rule, sepp: retireYes(r.sepp),
      claims: r.claims, chosen: false, historical: null, monteCarlo: null, afterTax: 0, afterTaxP10: 0, tax: 0, irmaa: 0, penalty: 0,
      converted: 0, spent: 0, lowestSpending: 1 };
    s.chosen = s.chosen || retireYes(r.chosen);
    if (r.model === "historical") s.historical = retireNum(r.success);
    if (r.model === "monte carlo") {
      s.monteCarlo = retireNum(r.success);
      s.afterTax = retireNum(r.median_after_tax);
      s.afterTaxP10 = retireNum(r.p10_after_tax);
      s.tax = retireNum(r.median_tax);
      s.irmaa = retireNum(r.median_irmaa);
      s.penalty = retireNum(r.median_penalty);
      s.converted = retireNum(r.median_converted);
      s.spent = retireNum(r.median_spent);
      s.lowestSpending = retireNum(r.lowest_spending);
    }
    byKey.set(r.strategy, s);
  }
  const strategies = Array.from(byKey.values());

  const plans: Record<string, PlanYear[]> = {};
  for (const r of retireRows(tabs.retirement_plan || [])) {
    (plans[r.strategy] = plans[r.strategy] || []).push({
      year: retireNum(r.year), ages: r.ages, phase: r.phase, contributions: retireNum(r.contributions), spending: retireNum(r.spending),
      health: retireNum(r.health), socialSecurity: retireNum(r.social_security), rmd: retireNum(r.rmd),
      fromTaxable: retireNum(r.from_taxable), fromPreTax: retireNum(r.from_pre_tax), fromRoth: retireNum(r.from_roth),
      fromHsa: retireNum(r.from_hsa), converted: retireNum(r.converted), ordinaryIncome: retireNum(r.ordinary_income),
      capitalGains: retireNum(r.capital_gains), federalTax: retireNum(r.federal_tax), stateTax: retireNum(r.state_tax),
      irmaa: retireNum(r.irmaa), penalty: retireNum(r.penalty), short: retireNum(r.short), taxable: retireNum(r.taxable),
      preTax: retireNum(r.pre_tax), roth: retireNum(r.roth), hsa: retireNum(r.hsa), total: retireNum(r.total),
      p10: retireNum(r.p10_total), p50: retireNum(r.p50_total), p90: retireNum(r.p90_total) });
  }
  Object.keys(plans).forEach((k) => plans[k].sort((a, b) => a.year - b.year));

  const benefits = retireRows(tabs.retirement_social_security || []).map((r) => ({
    person: r.person, claimAge: retireNum(r.claim_age), claimYear: retireNum(r.claim_year), statement: retireNumOrNull(r.statement_monthly),
    computed: retireNum(r.computed_monthly), plan: retireNum(r.plan_monthly), planYearly: retireNum(r.plan_yearly),
    aime: retireNum(r.aime), pia: retireNum(r.pia), note: r.note }));
  const earnings = retireRows(tabs.retirement_earnings || []).map((r) => ({
    person: r.person, year: retireNum(r.year), earnings: retireNum(r.fica_earnings), indexed: retireNum(r.indexed),
    counted: retireYes(r.counted), projected: r.basis === "projected" }));
  const assumptions = retireRows(tabs.retirement_assumptions || []).map((r) => ({
    section: r.section, key: r.key, label: r.label, value: r.value, source: r.source, setting: r.setting }));

  const budget = retireRows(tabs.retirement_budget || []).map((r) => ({
    line: r.line, group: r.group, kind: r.kind, now: retireNum(r.now), firstYear: retireNumOrNull(r.first_year),
    atRetirement: retireNumOrNull(r.at_retirement), retireYear: retireNum(r.retire_year), setting: r.setting, isSet: retireYes(r.is_set),
    lumpy: retireNumOrNull(r.lumpy), note: r.note }));

  const runs = retireRows(tabs.run_log || []).filter((r) => r.stage === "compute");
  const run = runs.length ? runs.sort((a, b) => a.computed_at.localeCompare(b.computed_at))[runs.length - 1].run_id : "";
  const notes = retireRows(tabs.run_notes || []).filter((r) => r.stage === "compute" && r.run_id === run && /^retirement:/.test(r.message))
    .sort((a, b) => retireNum(a.n) - retireNum(b.n)).map((r) => r.message.replace(/^retirement: /, ""));

  const chosen = years.filter((y) => y.chosen)[0];
  const pick = strategies.filter((s) => s.chosen)[0];
  return { chosenYear: chosen ? chosen.year : null, chosenStrategy: pick ? pick.key : "", years, strategies, plans, benefits,
           earnings, assumptions, budget, notes };
}
