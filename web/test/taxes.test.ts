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

test("the tax page groups lines by year, scenario and form, in order, with filed values", () => {
  const page = taxes.taxPage({ tax_lines: LINES });
  assert.deepEqual(page.years.map((y) => [y.year, y.scenarios, y.filed]),
                   [["2001", ["as is", "stock 20% lower"], false], ["2000", ["as is"], true]]);
  assert.deepEqual(page.years[0].forms["as is"]["Form 1040"].map((l) => l.line), ["1z", "11"]);
  assert.equal(page.years[0].forms["as is"]["Wisconsin Form 1"][0].value, 4000);
  assert.equal(page.years[1].forms["as is"]["Form 1040"][0].filed, 9100);
  assert.equal(page.years[0].forms["as is"]["Form 1040"][0].filed, null);
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
