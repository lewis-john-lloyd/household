import { test } from "node:test";
import * as assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";

const compiled = readFileSync(join(__dirname, "..", "src", "wealth.js"), "utf8");
const wealth = new Function(`${compiled}\nreturn { wealthPage, homePage };`)() as {
  wealthPage: (tabs: Record<string, unknown[][]>) => WealthPage;
  homePage: (tabs: Record<string, unknown[][]>, month: string) => HomePage;
};

const VALUES: unknown[][] = [
  ["date", "account_id", "owner", "wrapper", "bucket", "symbol", "source", "asset_class", "shares", "price", "value", "basis", "run_id"],
  ["2001-03-31", "a_plan", "alex", "401k", "401k_pretax", "ACMEX", "pretax", "SDT", 1, 10, 10, "priced", "r"],
  ["2001-04-10", "a_plan", "alex", "401k", "401k_pretax", "ACMEX", "pretax", "SDT", 3, 10, 30, "priced", "r"],
  ["2001-04-10", "a_plan", "alex", "401k", "401k_roth", "ACMEX", "roth", "SDT", 1, 10, 10, "priced", "r"],
  ["2001-04-10", "s_ira", "sam", "ira", "ira_roth", "WILLX", "", "BT", 2, 10, 20, "carried", "r"],
  ["2001-04-10", "k_529", "kid", "529", "529", "", "", "", "", "", 40, "carried", "r"],
];
const NET: unknown[][] = [
  ["date", "part", "owner", "value", "run_id"],
  ["2001-03-31", "401k_pretax", "alex", 10, "r"],
  ["2001-04-10", "401k_pretax", "alex", 30, "r"], ["2001-04-10", "home_equity", "alex", 50, "r"],
  ["2001-04-10", "home_equity", "sam", 50, "r"], ["2001-04-10", "529", "kids", 40, "r"],
  ["2001-04-10", "other_debt", "sam", -5, "r"],
];

test("the investments page sums today's values by bucket, account and fund, in the buckets' order", () => {
  const page = wealth.wealthPage({ investment_values: VALUES, net_worth: NET });
  assert.equal(page.asOf, "2001-04-10");
  assert.equal(page.total, 100);
  assert.deepEqual(page.buckets.map((b) => [b.key, b.value]), [["401k_pretax", 30], ["401k_roth", 10], ["ira_roth", 20], ["529", 40]]);
  assert.deepEqual(page.funds.map((f) => [f.symbol, f.value]), [["ACMEX", 40], ["(k_529 total)", 40], ["WILLX", 20]]);
  assert.equal(page.accounts.find((a) => a.account === "s_ira")?.carried, true);
});

test("net worth by owner lists the adults before the kids, and its history splits investments, homes and debt", () => {
  const page = wealth.wealthPage({ investment_values: VALUES, net_worth: NET });
  assert.deepEqual(page.owners.map((o) => [o.owner, o.total]), [["alex", 80], ["sam", 45], ["kids", 40]]);
  assert.deepEqual(page.history.map((p) => [p.date, p.total, p.invested, p.homes, p.debt]),
                   [["2001-03-31", 10, 10, 0, 0], ["2001-04-10", 165, 70, 100, -5]]);
});

test("the home page groups each home's lines and sums the amortization by year, marking projected years", () => {
  const page = wealth.homePage({
    property_summary: [["property_id", "order", "key", "label", "value", "as_of", "note", "run_id"],
                       ["willow", 2, "equity", "Equity", 60, "2001-09-15", "", "r"],
                       ["willow", 1, "purchase_price", "Purchase price", 300, "2001-05-01", "", "r"]],
    mortgage: [["month", "loan_id", "payment", "interest", "principal", "escrow", "extra_principal", "balance", "paid_from_ledger", "run_id"],
               ["2001-11", "acme", 5, 2, 1, 2, 0, 99, "", "r"], ["2001-12", "acme", 5, 2, 1, 2, 0, 98, "", "r"],
               ["2002-01", "acme", 5, 2, 1, 2, 0, 97, "", "r"]],
  }, "2001-12");
  assert.deepEqual(page.homes[0].lines.map((l) => l.key), ["purchase_price", "equity"]);
  assert.deepEqual(page.years.map((y) => [y.year, y.payments, y.interest, y.endBalance, y.projected]),
                   [["2001", 2, 4, 98, false], ["2002", 1, 2, 97, true]]);
});
