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
  [
    "date",
    "account_id",
    "owner",
    "wrapper",
    "bucket",
    "symbol",
    "source",
    "asset_class",
    "shares",
    "price",
    "value",
    "basis",
    "run_id",
  ],
  ["2001-03-31", "a_plan", "alex", "401k", "401k_pretax", "ACMEX", "pretax", "SDT", 1, 10, 10, "priced", "r"],
  ["2001-04-10", "a_plan", "alex", "401k", "401k_pretax", "ACMEX", "pretax", "SDT", 3, 10, 30, "priced", "r"],
  ["2001-04-10", "a_plan", "alex", "401k", "401k_roth", "ACMEX", "roth", "SDT", 1, 10, 10, "priced", "r"],
  ["2001-04-10", "s_ira", "sam", "ira", "ira_roth", "WILLX", "", "BT", 2, 10, 20, "carried", "r"],
  ["2001-04-10", "k_529", "kid", "529", "529", "", "", "", "", "", 40, "carried", "r"],
];
const NET: unknown[][] = [
  ["date", "part", "owner", "value", "run_id"],
  ["2001-03-31", "401k_pretax", "alex", 10, "r"],
  ["2001-04-10", "401k_pretax", "alex", 30, "r"],
  ["2001-04-10", "home_equity", "alex", 50, "r"],
  ["2001-04-10", "home_equity", "sam", 50, "r"],
  ["2001-04-10", "529", "kids", 40, "r"],
  ["2001-04-10", "other_debt", "sam", -5, "r"],
];

test("the investments page sums today's values by bucket, account and fund, in the buckets' order", () => {
  const page = wealth.wealthPage({ investment_values: VALUES, net_worth: NET });
  assert.equal(page.asOf, "2001-04-10");
  assert.equal(page.total, 100);
  assert.deepEqual(
    page.buckets.map((b) => [b.key, b.value]),
    [
      ["401k_pretax", 30],
      ["401k_roth", 10],
      ["ira_roth", 20],
      ["529", 40],
    ],
  );
  assert.deepEqual(
    page.funds.map((f) => [f.symbol, f.value]),
    [
      ["ACMEX", 40],
      ["(k_529 total)", 40],
      ["WILLX", 20],
    ],
  );
  assert.equal(page.accounts.find((a) => a.account === "s_ira")?.carried, true);
});

test("net worth by owner lists the adults before the kids, and its history splits investments, homes and debt", () => {
  const page = wealth.wealthPage({ investment_values: VALUES, net_worth: NET });
  assert.deepEqual(
    page.owners.map((o) => [o.owner, o.total]),
    [
      ["alex", 80],
      ["sam", 45],
      ["kids", 40],
    ],
  );
  assert.deepEqual(
    page.history.map((p) => [p.date, p.total, p.invested, p.homes, p.debt]),
    [
      ["2001-03-31", 10, 10, 0, 0],
      ["2001-04-10", 165, 70, 100, -5],
    ],
  );
});

test("over time offers the totals, each owner, each tax bucket and each account, each line by date", () => {
  const page = wealth.wealthPage({ investment_values: VALUES, net_worth: NET });
  assert.deepEqual(
    page.lines.map((l) => l.key),
    [
      "net_worth",
      "invested",
      "home_equity",
      "other_debt",
      "owner:alex",
      "owner:sam",
      "owner:kids",
      "bucket:401k_pretax",
      "bucket:401k_roth",
      "bucket:ira_roth",
      "bucket:529",
      "account:a_plan",
      "account:k_529",
      "account:s_ira",
    ],
  );
  const line = (k: string) => page.lines.find((l) => l.key === k)!;
  assert.deepEqual(line("account:a_plan").points, [
    ["2001-03-31", 10],
    ["2001-04-10", 40],
  ]); // its pre-tax and Roth parts together
  assert.deepEqual(line("home_equity").points, [["2001-04-10", 100]]); // no home before it was bought
  assert.deepEqual(line("owner:alex").points, [
    ["2001-03-31", 10],
    ["2001-04-10", 80],
  ]);
  assert.equal(line("account:s_ira").owner, "sam");
});

test("the home page groups each home's lines and sums the amortization by year, marking projected years", () => {
  const page = wealth.homePage(
    {
      property_summary: [
        ["property_id", "order", "key", "label", "value", "as_of", "note", "run_id"],
        ["willow", 2, "equity", "Equity", 60, "2001-09-15", "", "r"],
        ["willow", 1, "purchase_price", "Purchase price", 300, "2001-05-01", "", "r"],
      ],
      mortgage: [
        [
          "month",
          "loan_id",
          "payment",
          "interest",
          "principal",
          "escrow",
          "extra_principal",
          "balance",
          "paid_from_ledger",
          "run_id",
        ],
        ["2001-11", "acme", 5, 2, 1, 2, 0, 99, "", "r"],
        ["2001-12", "acme", 5, 2, 1, 2, 0, 98, "", "r"],
        ["2002-01", "acme", 5, 2, 1, 2, 0, 97, "", "r"],
      ],
    },
    "2001-12",
  );
  assert.deepEqual(
    page.homes[0].lines.map((l) => l.key),
    ["purchase_price", "equity"],
  );
  assert.deepEqual(
    page.years.map((y) => [y.year, y.payments, y.interest, y.endBalance, y.projected]),
    [
      ["2001", 2, 4, 98, false],
      ["2002", 1, 2, 97, true],
    ],
  );
});

test("the lots part lists open lots oldest first, sales newest first, and each year's gains and wash sales", () => {
  const open: unknown[][] = [
    [
      "lot_id",
      "piece",
      "account_id",
      "symbol",
      "acquired",
      "holding_from",
      "shares",
      "cost_per_share",
      "basis",
      "wash_added",
      "kind",
      "grant_id",
      "price",
      "value",
      "gain",
      "term_if_sold",
      "run_id",
    ],
    [
      "vest:G1:2001-03-25",
      1,
      "brk",
      "ACME",
      "2001-03-25",
      "2001-03-25",
      2,
      40,
      80,
      0,
      "vest",
      "G1",
      45,
      90,
      10,
      "short_term",
      "r",
    ],
    [
      "vest:G1:2001-02-25",
      2,
      "brk",
      "ACME",
      "2001-02-25",
      "2001-01-20",
      1,
      50,
      50,
      10,
      "vest",
      "G1",
      "",
      "",
      "",
      "short_term",
      "r",
    ],
  ];
  const form: unknown[][] = [
    [
      "year",
      "sale_date",
      "account_id",
      "symbol",
      "shares",
      "acquired",
      "holding_from",
      "term",
      "proceeds",
      "basis",
      "wash_carried",
      "code",
      "adjustment",
      "gain",
      "lot_id",
      "piece",
      "replaced_by",
      "reported_basis",
      "note",
      "run_id",
    ],
    [
      "2001",
      "2001-02-01",
      "brk",
      "ACME",
      1,
      "2001-01-10",
      "2001-01-10",
      "short_term",
      30,
      40,
      0,
      "W",
      10,
      0,
      "x",
      1,
      "",
      "",
      "",
      "r",
    ],
    [
      "2001",
      "2001-03-01",
      "brk",
      "ACME",
      1,
      "2001-01-10",
      "2001-01-10",
      "short_term",
      50,
      40,
      0,
      "",
      0,
      10,
      "x",
      2,
      "",
      "",
      "",
      "r",
    ],
    [
      "2000",
      "2000-12-01",
      "brk",
      "ACME",
      1,
      "1999-01-10",
      "1999-01-10",
      "long_term",
      50,
      20,
      0,
      "",
      0,
      30,
      "y",
      1,
      "",
      "",
      "",
      "r",
    ],
  ];
  const page = wealth.wealthPage({ investment_values: VALUES, net_worth: NET, open_lots: open, form_8949: form });
  assert.deepEqual(
    page.openLots.map((l) => [l.acquired, l.value, l.washAdded]),
    [
      ["2001-02-25", null, 10],
      ["2001-03-25", 90, 0],
    ],
  );
  assert.deepEqual(
    page.lotSales.map((x) => x.saleDate),
    ["2001-03-01", "2001-02-01", "2000-12-01"],
  );
  assert.deepEqual(
    page.lotYears.map((y) => [y.year, y.shortTerm, y.longTerm, y.disallowed, y.sales]),
    [
      ["2001", 10, 0, 10, 2],
      ["2000", 0, 30, 0, 1],
    ],
  );
});

test("a fund carries its row in the ledger's securities, for its asset class to be changed", () => {
  const page = wealth.wealthPage({
    investment_values: VALUES,
    net_worth: NET,
    editor_securities: [
      ["symbol", "name", "asset_class", "expense_ratio", "quote", "wash_group", "run_id"],
      ["ACMEX", "Acme Index", "SDT", "0.04", "ACMEX", "total", "r"],
    ],
  });
  const by = Object.fromEntries(page.funds.map((f) => [f.symbol, f.security]));
  assert.equal(by["ACMEX"]?.quote, "ACMEX");
  assert.equal(by["WILLX"], null); // not in securities: a new one if its class is set
  assert.equal(wealth.wealthPage({ investment_values: VALUES, net_worth: NET }).funds[0].security, null);
});
