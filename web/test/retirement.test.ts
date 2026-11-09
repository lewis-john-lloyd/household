import { test } from "node:test";
import * as assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";

const compiled = readFileSync(join(__dirname, "..", "src", "retirement.js"), "utf8");
const page = new Function(`${compiled}\nreturn retirementPage;`)() as (tabs: Record<string, unknown[][]>) => RetirementPage;

const YEARS: unknown[][] = [
  ["retire_year", "ages", "model", "scenario", "success", "median_end", "p10_end", "short_from", "at_retirement", "first_spending",
   "withdrawal_rate", "chosen", "run_id"],
  [2030, "50/48", "fixed", "9%", 1, 90, 90, "", 50, 4, 0.05, "", "r"],
  [2030, "50/48", "fixed", "5%", 0, 0, 0, 2050, 40, 4, 0.06, "", "r"],
  [2030, "50/48", "historical", "", 0.8, 70, 1, "", 45, 4, 0.05, "", "r"],
  [2030, "50/48", "monte carlo", "", 0.85, 60, 0, "", 44, 4, 0.05, "", "r"],
  [2031, "51/49", "monte carlo", "", 0.91, 70, 5, "", 50, 4, 0.04, "yes", "r"],
];
const STRATEGIES: unknown[][] = [
  ["strategy", "label", "order", "convert_to", "rule", "sepp", "claims", "model", "success", "median_after_tax", "p10_after_tax",
   "median_tax", "median_irmaa", "median_penalty", "median_converted", "median_spent", "lowest_spending", "chosen", "run_id"],
  ["bracket/to22/constant", "Pre-tax to the 12% bracket", "bracket", 0.22, "constant", "False", "", "historical", 0.95, 80, 5, 9, 1, 0, 30, 90, 1, "yes", "r"],
  ["bracket/to22/constant", "Pre-tax to the 12% bracket", "bracket", 0.22, "constant", "False", "", "monte carlo", 0.91, 70, 4, 8, 1, 0, 30, 90, 1, "yes", "r"],
  ["bracket/to22/vpw", "VPW", "bracket", 0.22, "vpw", "False", "", "monte carlo", 1, 20, 0, 5, 0, 0, 10, 95, 0.4, "", "r"],
];
const PLAN: unknown[][] = [
  ["strategy", "year", "ages", "phase", "contributions", "spending", "health", "social_security", "rmd", "from_taxable", "from_pre_tax",
   "from_roth", "from_hsa", "converted", "ordinary_income", "capital_gains", "federal_tax", "state_tax", "irmaa", "penalty", "short",
   "taxable", "pre_tax", "roth", "hsa", "total", "p10_total", "p50_total", "p90_total", "run_id"],
  ["bracket/to22/constant", 2032, "52/50", "retired", "", 4, 1, 0, 0, 2, 3, 0, 0, 5, 8, 1, 1, 0.5, 0, 0, 0, 10, 20, 5, 1, 36, 20, 36, 60, "r"],
  ["bracket/to22/constant", 2031, "51/49", "working", 7, "", "", "", "", "", "", "", "", "", "", "", "", "", "", "", "", 9, 19, 4, 1, 33, 30, 33, 40, "r"],
];

test("each retirement year gathers its three return models, the fixed ones lowest return first", () => {
  const p = page({ retirement_years: YEARS, retirement_strategies: STRATEGIES, retirement_plan: PLAN });
  assert.equal(p.chosenYear, 2031);
  const y = p.years[0];
  assert.deepEqual([y.year, y.monteCarlo, y.historical], [2030, 0.85, 0.8]);
  assert.deepEqual(y.fixed.map((f) => [f.scenario, f.success, f.shortFrom]), [["5%", false, 2050], ["9%", true, null]]);
});

test("strategies join their models and the plan is year by year per strategy", () => {
  const p = page({ retirement_years: YEARS, retirement_strategies: STRATEGIES, retirement_plan: PLAN });
  assert.equal(p.chosenStrategy, "bracket/to22/constant");
  const s = p.strategies.find((x) => x.key === "bracket/to22/constant")!;
  assert.deepEqual([s.historical, s.monteCarlo, s.afterTax, s.chosen], [0.95, 0.91, 70, true]);
  assert.deepEqual(p.plans["bracket/to22/constant"].map((r) => [r.year, r.phase]), [[2031, "working"], [2032, "retired"]]);
});

test("the latest compute's retirement notes are kept without their topic", () => {
  const p = page({
    run_log: [["run_id", "computed_at", "stage", "detail"], ["r1", "2001-01-01", "compute", "{}"], ["r2", "2001-02-01", "compute", "{}"]],
    run_notes: [["run_id", "stage", "at", "n", "message", "level"], ["r1", "compute", "", 1, "retirement: old", "INFO"],
                ["r2", "compute", "", 2, "retirement: 2031 (51/49)", "INFO"], ["r2", "compute", "", 1, "lots: x", "INFO"]],
  });
  assert.deepEqual(p.notes, ["2031 (51/49)"]);
  assert.equal(p.chosenYear, null);
});
