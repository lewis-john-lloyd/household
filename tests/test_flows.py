import pandas as pd

from household import flows

CATEGORIES = pd.DataFrame(
    [
        {"category": "food", "group": "food", "priority": "high"},
        {"category": "restaurant", "group": "food", "priority": "medium"},
        {"category": "household", "group": "household", "priority": "medium"},
        {"category": "savings_529", "group": "savings", "priority": "medium"},
        {"category": "interest", "group": "income", "priority": ""},
        {"category": "pay", "group": "income", "priority": ""},
        {"category": "transfer", "group": "transfer", "priority": ""},
    ]
)


def stub(date, person, items):
    return [
        {
            "pay_date": date,
            "person": person,
            "employer": "Acme",
            "stub_id": f"{person}{date}",
            "line_item": name,
            "kind": kind,
            "amount": amount,
        }
        for name, kind, amount in items
    ]


PAY = stub(
    "2026-03-13",
    "alex",
    [
        ("Regular Pay", "income", 4000.0),
        ("Group Term Life", "income", 10.0),
        ("Federal Income Tax", "tax", 500.0),
        ("Social Security Employee Tax", "tax", 200.0),
        ("Employee Medicare", "tax", 50.0),
        ("XX State Income Tax", "tax", 150.0),
        ("401K Pretax", "pre_tax", 400.0),
        ("401K Pretax", "employer", 200.0),
        ("Group Term Life", "post_tax", 10.0),
        ("Internet Reim", "post_tax", -12.5),
        ("Net Pay", "memo", 2702.5),
    ],
)


def txn(i, date, amount, category, splits="", era="ledger", payee=""):
    return {
        "txn_id": f"t{i}",
        "date": date,
        "amount": amount,
        "category": category,
        "splits": splits,
        "era": era,
        "payee_raw": payee,
    }


def frame(rows):
    return pd.DataFrame(rows)


def edges(df, period, level="category"):
    sel = df[(df["period"] == period) & (df["level"] == level)]
    return {(s, t): a for s, t, a in zip(sel["source"], sel["target"], sel["amount"])}


def test_pay_splits_into_taxes_deductions_and_net_pay():
    txns = frame([txn(1, "2026-03-14", 2702.5, "pay", payee="ACME PAYROLL"), txn(2, "2026-03-20", -1000.0, "food")])
    e = edges(flows.build(pd.DataFrame(PAY), txns, CATEGORIES), "2026-03")
    gross = "gross pay (alex)"
    assert e[(gross, "federal tax")] == 500.0
    assert e[(gross, "social security")] == 200.0
    assert e[(gross, "medicare")] == 50.0
    assert e[(gross, "state tax")] == 150.0
    assert e[(gross, "pre-tax: 401K Pretax")] == 400.0
    assert e[(gross, "post-tax: Group Term Life")] == 10.0
    assert e[(gross, "net pay")] == 2712.5  # the reimbursement rides on the check
    assert e[("net pay", "accounts")] == 2712.5
    assert ("pay", "accounts") not in e  # the deposit is the net pay, counted once
    assert e[("accounts", "food")] == 1000.0
    assert e[("accounts", "to cash balances")] == 1712.5


def test_other_income_savings_splits_and_balancing():
    txns = frame(
        [
            txn(1, "2026-04-02", 12.0, "interest"),
            txn(2, "2026-04-03", -300.0, "savings_529"),
            txn(3, "2026-04-04", -50.0, "household", splits="household -30.00; food -20.00"),
            txn(4, "2026-04-05", -500.0, "transfer"),
            txn(5, "2026-04-06", 8.0, "food"),  # a refund nets against its category
            txn(6, "2019-04-06", -99.0, "food", era="legacy"),
        ]
    )
    out = flows.build(pd.DataFrame(), txns, CATEGORIES)
    e = edges(out, "2026-04")
    assert e[("interest", "accounts")] == 12.0
    assert e[("accounts", "savings_529")] == 300.0
    assert e[("accounts", "household")] == 30.0
    assert e[("accounts", "food")] == 12.0
    assert ("accounts", "transfer") not in e
    assert e[("from cash balances", "accounts")] == 330.0
    assert set(out["period"]) == {"2026-04", "2026", "2026 monthly average"}

    g = edges(out, "2026-04", "group")
    assert g[("other income", "accounts")] == 12.0
    assert g[("accounts", "savings")] == 300.0
    assert g[("accounts", "household")] == 30.0 and g[("accounts", "food")] == 12.0


def test_years_add_up_their_months():
    txns = frame([txn(1, "2026-01-05", -10.0, "food"), txn(2, "2026-02-05", -15.0, "food")])
    out = flows.build(pd.DataFrame(), txns, CATEGORIES)
    assert edges(out, "2026")[("accounts", "food")] == 25.0
    assert edges(out, "2026")[("from cash balances", "accounts")] == 25.0
    assert edges(out, "2026 monthly average")[("accounts", "food")] == 12.5  # two months of data


def test_tax_nodes():
    assert [flows.tax_node(n) for n in ("FITW", "SS", "MED", "OASDI", "WI State Tax", "Federal Income Tax")] == [
        "federal tax",
        "social security",
        "medicare",
        "social security",
        "state tax",
        "federal tax",
    ]


def ytd_stub(date, person, employer, stub_id, items):
    return [
        {
            "pay_date": date,
            "person": person,
            "employer": employer,
            "stub_id": stub_id,
            "line_item": name,
            "kind": kind,
            "amount": amount,
            "ytd_amount": ytd,
        }
        for name, kind, amount, ytd in items
    ]


def test_year_takes_year_to_date_from_the_last_stub_so_missing_stubs_still_count():
    # only January's and October's stubs are on file; October's year-to-date covers the months between
    stubs = pd.DataFrame(
        ytd_stub(
            "2026-01-09",
            "sam",
            "Acme Inc",
            "1",
            [
                ("Salary", "income", 1000.0, "1000"),
                ("FITW", "tax", 100.0, "100"),
                ("Stipend", "post_tax", -10.0, "-10"),
            ],
        )
        + ytd_stub(
            "2026-10-09",
            "sam",
            "Acme Inc",
            "2",
            [
                ("Salary", "income", 1000.0, "10,000"),
                ("Bonus", "income", 0.0, "500"),
                ("FITW", "tax", 100.0, "1050"),
                ("Stipend", "post_tax", -10.0, "-100"),
            ],
        )
    )
    txns = frame(
        [txn(i, f"2026-{m:02d}-10", 910.0, "pay", payee="ACME INC PAYROLL") for i, m in enumerate(range(1, 11))]
        + [txn(98, "2026-05-15", 40.0, "pay", payee="STATE REFUND"), txn(99, "2026-10-20", -9000.0, "food")]
    )
    out = flows.build(stubs, txns, CATEGORIES)
    year = edges(out, "2026")
    assert year[("gross pay (sam)", "federal tax")] == 1050.0
    assert year[("gross pay (sam)", "net pay")] == 9550.0  # 10,500 gross, less 1,050 tax, plus the 100 stipend
    assert ("pay without a stub (sam)", "accounts") not in year  # 9,100 deposited, all within the stubs' net pay
    assert year[("pay without a stub", "accounts")] == 40.0  # no stub names that payer
    assert edges(out, "2026 monthly average")[("gross pay (sam)", "federal tax")] == 105.0

    may = edges(out, "2026-05")
    assert may[("pay without a stub (sam)", "accounts")] == 910.0  # no stub this month: the deposit still counts
    assert ("gross pay (sam)", "net pay") not in may
    assert ("pay without a stub (sam)", "accounts") not in edges(out, "2026-01")


def test_employer_word():
    assert flows.employer_word("Acme Widgets, LLC") == "ACME"
    assert flows.payers(pd.DataFrame([{"employer": "Acme Inc", "person": "sam"}])) == {"ACME": "sam"}


def test_year_to_date_restarts_with_a_new_employer_and_partial_years_add_up_stubs():
    stubs = pd.DataFrame(
        ytd_stub("2025-03-07", "sam", "Old Co", "1", [("Salary", "income", 2000.0, "6000")])
        + ytd_stub("2025-11-07", "sam", "New Co", "2", [("Salary", "income", 3000.0, "3000")])
    )
    txns = frame(
        [txn(1, "2025-01-05", -1.0, "food"), txn(2, "2025-03-07", 2000.0, "pay"), txn(3, "2025-11-07", 3000.0, "pay")]
    )
    assert edges(flows.build(stubs, txns, CATEGORIES), "2025")[("gross pay (sam)", "net pay")] == 9000.0

    late = frame([txn(2, "2025-03-07", 2000.0, "pay"), txn(3, "2025-11-07", 3000.0, "pay")])
    # the ledger starts in March: January and February's pay in the year-to-date is not in the ledger
    assert edges(flows.build(stubs, late, CATEGORIES), "2025")[("gross pay (sam)", "net pay")] == 5000.0


def test_savings_flow_both_ways_and_money_back_nets_over_the_period():
    cats = pd.concat(
        [
            CATEGORIES,
            pd.DataFrame([{"category": "stock_sales", "group": "savings"}, {"category": "mortgage", "group": "home"}]),
        ]
    )
    txns = frame(
        [
            txn(1, "2026-01-05", 5000.0, "stock_sales"),  # shares sold: drawn from savings
            txn(2, "2026-01-06", -300.0, "savings_529"),
            txn(3, "2026-02-01", -1000.0, "mortgage"),
            txn(4, "2026-03-01", 4000.0, "mortgage"),  # a sale's proceeds
            txn(5, "2026-02-03", -50.0, "food"),
            txn(6, "2026-03-03", 20.0, "food"),
        ]
    )
    out = flows.build(pd.DataFrame(), txns, cats)
    year = edges(out, "2026")
    assert year[("from savings: stock_sales", "accounts")] == 5000.0
    assert year[("accounts", "savings_529")] == 300.0
    assert year[("mortgage (money back)", "accounts")] == 3000.0  # netted over the year
    assert year[("accounts", "food")] == 30.0
    assert edges(out, "2026-02")[("accounts", "mortgage")] == 1000.0
    assert edges(out, "2026-03")[("mortgage (money back)", "accounts")] == 4000.0
    group = edges(out, "2026", "group")
    assert group[("from savings", "accounts")] == 5000.0 and group[("money back", "accounts")] == 3000.0


def test_vested_shares_go_to_savings_not_a_deduction():
    vest = stub(
        "2026-03-13",
        "alex",
        [
            ("Regular Pay", "income", 4000.0),
            ("Stock Units", "income", 10000.0),
            ("Federal Income Tax", "tax", 3000.0),
            ("Stock Offset", "post_tax", 7000.0),
        ],
    )
    txns = frame([txn(1, "2026-03-14", 4000.0, "pay", payee="ACME PAYROLL")])
    e = edges(flows.build(pd.DataFrame(vest), txns, CATEGORIES), "2026-03")
    assert e[("gross pay (alex)", "vested shares (to savings)")] == 7000.0
    assert e[("gross pay (alex)", "net pay")] == 4000.0
    g = edges(flows.build(pd.DataFrame(vest), txns, CATEGORIES), "2026-03", "group")
    assert g[("gross pay (alex)", "savings")] == 7000.0


def test_a_share_sale_adds_only_its_gain_or_loss_and_the_proceeds_come_from_savings():
    cats = pd.concat([CATEGORIES, pd.DataFrame([{"category": "stock_sales", "group": "savings"}])])
    # proceeds reach the bank a month after the sales: the gain stays in the month of the sale
    txns = frame([txn(1, "2026-06-02", -40.0, "food"), txn(2, "2026-07-01", 900.0, "stock_sales")])
    sales = pd.DataFrame(
        [
            {"sale_date": "2026-06-29", "grant_id": "g1", "proceeds": "500", "basis": "520", "gain": "-20"},
            {"sale_date": "2026-06-29", "grant_id": "g2", "proceeds": "400", "basis": "395", "gain": "5"},
            {
                "sale_date": "2026-05-20",
                "grant_id": "g1",
                "proceeds": "60",
                "basis": "50",
                "gain": "10",
            },  # no ledger month
            {
                "sale_date": "2025-12-30",
                "grant_id": "g1",
                "proceeds": "60",
                "basis": "50",
                "gain": "10",
            },  # no ledger year
        ]
    )
    out = flows.build(pd.DataFrame(), txns, cats, sales)
    june, july, year = edges(out, "2026-06"), edges(out, "2026-07"), edges(out, "2026")
    assert june[("from savings: capital losses", "capital losses")] == 15.0  # -20 + 5, net
    assert ("capital gains", "capital gains (to savings)") not in june
    assert july[("from savings: stock_sales", "accounts")] == 900.0  # the cash, drawn from savings
    assert year[("from savings: capital losses", "capital losses")] == 5.0  # -20 + 5 + 10 (May), net for the year
    # neither touches the accounts, so each period still balances to the cent
    for period in ("2026-06", "2026-07", "2026"):
        e = edges(out, period)
        assert round(sum(a for (s, t), a in e.items() if t == "accounts"), 2) == round(
            sum(a for (s, t), a in e.items() if s == "accounts"), 2
        )
    group = edges(out, "2026-06", "group")
    assert group[("from savings", "capital losses")] == 15.0
    gains = flows.build(pd.DataFrame(), txns, cats, sales.assign(gain=["20", "5", "0", "0"]))
    assert edges(gains, "2026-06")[("capital gains", "capital gains (to savings)")] == 25.0
    assert edges(gains, "2026-06", "group")[("capital gains", "savings")] == 25.0
