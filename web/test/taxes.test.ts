import { test } from "node:test";
import * as assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";

const compiled = readFileSync(join(__dirname, "..", "src", "taxes.js"), "utf8");
const taxes = new Function(`${compiled}\nreturn { taxPage };`)() as { taxPage: (tabs: Record<string, unknown[][]>) => TaxPage };

const LINES: unknown[][] = [
  ["year", "scenario", "order", "form", "line", "key", "label", "value", "filed", "run_id"],
  [2001, "as is", 1, "Form 1040", "11", "11a", "Adjusted gross income", 90000, "", "r2"],
  [2001, "as is", 0, "Form 1040", "1z", "1z", "Wages (W-2 box 1)", 90000, "", "r2"],
  [2001, "as is", 2, "Wisconsin Form 1", "", "wi_net_tax", "Net tax", 4000, "", "r2"],
  [2001, "stock 20% lower", 0, "Form 1040", "1z", "1z", "Wages (W-2 box 1)", 80000, "", "r2"],
  [2000, "as is", 0, "Form 1040", "24", "24", "Total tax", 9000, 9100, "r2"],
];

test("the tax page groups each year's lines by form, in order, with filed values", () => {
  const page = taxes.taxPage({ tax_lines: LINES });
  assert.deepEqual(page.years.map((y) => [y.year, y.filed]), [["2001", false], ["2000", true]]);
  assert.deepEqual(page.years[0].forms["Form 1040"].map((l) => l.line), ["1z", "11"]);
  assert.equal(page.years[0].forms["Wisconsin Form 1"][0].value, 4000);
  assert.equal(page.years[1].forms["Form 1040"][0].filed, 9100);
  assert.equal(page.years[0].forms["Form 1040"][0].filed, null);
});

test("the forms show a year as it stands, whatever scenarios an older compute left", () => {
  const page = taxes.taxPage({ tax_lines: LINES });
  assert.deepEqual(page.years[0].forms["Form 1040"].map((l) => l.value), [90000, 90000]);
  assert.equal("scenarios" in page.years[0], false);
});

test("the plan, accounts and notes come from the latest year and the latest compute", () => {
  const page = taxes.taxPage({
    withholding_plan: [["year", "return", "suggested_extra", "run_id"], [2001, "federal", 50, "r2"], [2000, "federal", 9, "r1"]],
    elections: [["year", "person", "plan", "ytd", "run_id"], [2001, "alex", "401(k) elective deferral", 900, "r2"]],
    projection: [["scenario", "owe_or_refund", "wi_owe_or_refund", "run_id"], ["as is", -1500, 200, "r2"]],
    run_log: [["run_id", "computed_at", "stage", "detail"], ["r1", "2001-01-01T00:00:00Z", "compute", "{}"],
              ["r2", "2001-02-01T00:00:00Z", "compute", "{}"]],
    run_notes: [["run_id", "stage", "at", "n", "message"],
                ["r2", "compute", "", 2, "projection 2001: alex/Acme Widgets: 3 more monthly vests of about 900 at 35%"],
                ["r2", "compute", "", 1, "budget: 3 lines"], ["r1", "compute", "", 1, "projection 2001: an older run"]],
  });
  assert.deepEqual(page.plan.map((p) => p.suggested_extra), ["50"]);
  assert.equal(page.accounts[0].ytd, "900");
  assert.deepEqual(page.totals, [{ scenario: "as is", federal: -1500, wisconsin: 200 }]);
  assert.deepEqual(page.notes, ["alex/Acme Widgets: 3 more monthly vests of about 900 at 35%"]);
});

test("the tax history comes oldest year first, each split's parts in order", () => {
  const page = taxes.taxPage({ tax_lines: LINES, tax_history: [
    ["year", "jurisdiction", "split", "part", "order", "value", "total", "run_id"],
    ["2001", "federal", "went", "Federal income tax", 4, 9000, 90000, "r2"],
    ["2001", "federal", "went", "Kept after income tax", 0, 81000, 90000, "r2"],
    ["2000", "federal", "came", "Capital gains", 2, -3000, 77000, "r2"],
    ["2000", "federal", "came", "Wages, alex", 0, 80000, 77000, "r2"],
  ] });
  assert.deepEqual(page.history.map((h) => [h.year, h.part, h.value]),
                   [["2000", "Wages, alex", 80000], ["2000", "Capital gains", -3000],
                    ["2001", "Kept after income tax", 81000], ["2001", "Federal income tax", 9000]]);
  assert.equal(taxes.taxPage({ tax_lines: LINES }).history.length, 0);
});

test("any filed year's lines come form by form in order, and a projected year in the history says so", () => {
  const page = taxes.taxPage({
    filed_lines: [["year", "form", "order", "line", "key", "label", "value", "run_id"],
                  [2019, "Form 1040", 24, "24", "24", "Total tax", 16000, "r"], [2019, "Form 1040", 0, "1z", "1z", "Wages", 150000, "r"]],
    tax_history: [["year", "jurisdiction", "split", "part", "order", "value", "total", "basis", "run_id"],
                  [2019, "federal", "went", "Federal income tax", 4, 16000, 150000, "filed", "r"],
                  [2020, "federal", "went", "Federal income tax", 4, 17000, 160000, "projected", "r"]],
  });
  assert.deepEqual(page.filed["2019"]["Form 1040"].map((l) => l.line), ["1z", "24"]);
  assert.deepEqual(page.history.map((h) => [h.year, h.projected]), [["2019", false], ["2020", true]]);
});
