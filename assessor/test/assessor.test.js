// The county check (assessor/Code.js) under Node, on a made-up parcel page. Code.js is an Apps Script file:
// its functions are globals, so it runs in a fresh context per test.
const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const CODE = fs.readFileSync(path.join(__dirname, "..", "Code.js"), "utf8");

function load(extra = {}) {
  const context = vm.createContext({ console, ...extra });
  vm.runInContext(CODE, context);
  return context;
}

// a made-up parcel page with the shape of the county's: the valuation list, the assessment detail with a
// column per year, and one tax bill per year
const PAGE = `<html><head><script>var x = "Total Value";</script></head><body>
<h3>Valuations by Assessment Year</h3>
<table><tr><th>Year</th><th>Land Value</th><th>Improved Value</th><th>Total Value</th></tr>
<tr><td>2002</td><td>¤90</td><td>¤310</td><td>¤400</td></tr></table>
<h3>Assessment Detail</h3>
<table><tr><th>Assessment Year</th><th>2002</th><th>2001</th><th>2000</th></tr>
<tr><td>Land Value</td><td>¤90.00</td><td>¤80.00</td><td>¤70.00</td></tr>
<tr><td>Improved Value</td><td>¤310.00</td><td>¤270.00</td><td>¤230.00</td></tr>
<tr><td>Total Value</td><td>¤400.00</td><td>¤350.00</td><td>¤300.00</td></tr>
<tr><td>Estimated Fair Market Value</td><td>N/A</td><td>¤360</td><td>¤320</td></tr></table>
<h4>Tax Year 2001</h4>
<table><tr><td>Net Assessed Value Rate (mill rate):</td><td>0.02</td></tr>
<tr><td>School Levy Tax Credit:</td><td>¤1.00</td></tr>
<tr><td>Total Taxes:</td><td>¤7.00</td></tr>
<tr><td>LOTTERY CREDIT</td><td>¤0.50</td></tr><tr><td>FIRST DOLLAR CREDIT</td><td>¤0.25</td></tr>
<tr><td>Specials</td><td>¤2.00</td></tr>
<tr><td>Total Taxes Less Credits:</td><td>¤6.25</td></tr><tr><td>Total Amount Due:</td><td>¤8.25</td></tr></table>
<h4>Tax Year 2000</h4>
<table><tr><td>Net Assessed Value Rate (mill rate):</td><td>0.021</td></tr><tr><td>Total Taxes:</td><td>¤6.30</td></tr></table>
</body></html>`.replace(/¤/g, "$");  // money written as ¤ above, so no line of this file reads as an amount
const HOME = { property_id: "willow", parcel: "066/0101-011-0101-0", since: "2001" };

test("a parcel page gives each year's assessment, its fair market estimate and each tax bill", () => {
  const c = load();
  const page = c.parsePage(PAGE);
  assert.deepEqual(JSON.parse(JSON.stringify(page.assessments["2002"])), { land: 90, improved: 310, total: 400, fmv: null });
  assert.equal(page.assessments["2001"].fmv, 360);
  assert.deepEqual(JSON.parse(JSON.stringify(page.bills["2001"])), {
    mill_rate: 0.02, school_levy_credit: 1, total_taxes: 7, lottery_credit: 0.5, first_dollar_credit: 0.25, specials: 2,
    net_taxes: 6.25, amount_due: 8.25 });
});

test("only the years since the home was bought become rows, each kept under the key ingest uses", () => {
  const c = load();
  const rows = JSON.parse(JSON.stringify(c.rowsFor(HOME, c.parsePage(PAGE))));  // out of the script's realm
  assert.deepEqual(rows.values.map((r) => [r.as_of, r.source, r.value]), [
    ["2001-01-01", "county_assessed", 350], ["2001-01-01", "county_fair_market", 360], ["2002-01-01", "county_assessed", 400]]);
  assert.deepEqual(rows.taxes.map((r) => [r.tax_year, r.assessed, r.mill_rate, r.amount_due]), [["2001", 350, 0.02, 8.25]]);
  assert.equal(c.keyOf(rows.taxes[0]), "tax|2001|willow");
  const text = c.csv(["as_of", "property_id", "source", "value", "note", "doc_id"], rows.values);
  assert.match(text, /^as_of,property_id,source,value,note,doc_id\n2001-01-01,willow,county_assessed,350,AccessDane/);
});

test("the page address is the parcel number's twelve digits, and anything else is refused", () => {
  const c = load();
  assert.equal(c.pageUrl("066/0101-011-0101-0"), "https://accessdane.danecounty.gov/" + "010101" + "101010");
  assert.throws(() => c.pageUrl("12-34"), /not a Dane County parcel number/);
  assert.deepEqual(JSON.parse(JSON.stringify(c.readParcels('{"version":1,"parcels":[{"property_id":"willow"}]}'))), [{ property_id: "willow" }]);
  assert.deepEqual(JSON.parse(JSON.stringify(c.readParcels(""))), []);
});
