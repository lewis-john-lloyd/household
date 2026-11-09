// The views' page code (the built web/dist files) run on Reports tabs given as JSON on stdin; prints what each
// page would show, as JSON. tests/test_end_to_end.py drives it, after ingest and compute on made-up files.
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const DIST = path.join(__dirname, "..", "web", "dist");
const code = ["shape.js", "budgetview.js", "taxes.js", "wealth.js", "retirement.js", "form8949.js"]
  .map((f) => fs.readFileSync(path.join(DIST, f), "utf8")).join("\n");
const context = { console };
vm.createContext(context);
vm.runInContext(code, context);
const tabs = JSON.parse(fs.readFileSync(0, "utf8"));
context.tabs = tabs;
const out = vm.runInContext(`(() => {
  const flowRows = parseRows(tabs.flows || []);
  const period = periods(flowRows)[0] || "";
  const sankey = toSankeyRows(flowRows, period, "group");
  return {
    flows: { periods: periods(flowRows), period, rows: sankey.rows, summary: flowSummary(nodeTotals(sankey.rows)) },
    status: parseStatus(tabs.run_log || [], tabs.run_notes || [], tabs.coverage || [], tabs.review || []),
    budget: budgetBook(tabs.budget_vs_actual || [], tabs.daily_actuals || [], tabs.subscription_months || [], tabs.editor_categories || []),
    transactions: parseTransactions(tabs.categorized || []),
    investments: wealthPage(tabs),
    retirement: retirementPage(tabs),
    taxes: taxPage(tabs),
    form8949: form8949Page(tabs),
  };
})()`, context);
process.stdout.write(JSON.stringify(out));
