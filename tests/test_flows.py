import pandas as pd

from household import flows

CATEGORIES = pd.DataFrame([
    {"category": "food", "group": "food", "priority": "high"},
    {"category": "restaurant", "group": "food", "priority": "medium"},
    {"category": "household", "group": "household", "priority": "medium"},
    {"category": "savings_529", "group": "savings", "priority": "medium"},
    {"category": "interest", "group": "income", "priority": ""},
    {"category": "pay", "group": "income", "priority": ""},
    {"category": "transfer", "group": "transfer", "priority": ""},
])


def stub(date, person, items):
    return [{"pay_date": date, "person": person, "employer": "Acme", "stub_id": f"{person}{date}",
             "line_item": name, "kind": kind, "amount": amount} for name, kind, amount in items]


PAY = stub("2026-03-13", "alex", [
    ("Regular Pay", "income", 4000.0), ("Group Term Life", "income", 10.0),
    ("Federal Income Tax", "tax", 500.0), ("Social Security Employee Tax", "tax", 200.0),
    ("Employee Medicare", "tax", 50.0), ("XX State Income Tax", "tax", 150.0),
    ("401K Pretax", "pre_tax", 400.0), ("401K Pretax", "employer", 200.0),
    ("Group Term Life", "post_tax", 10.0), ("Internet Reim", "post_tax", -12.5), ("Net Pay", "memo", 2702.5)])


def txn(i, date, amount, category, splits="", era="ledger"):
    return {"txn_id": f"t{i}", "date": date, "amount": amount, "category": category, "splits": splits, "era": era}


def frame(rows):
    return pd.DataFrame(rows)


def edges(df, period, level="category"):
    sel = df[(df["period"] == period) & (df["level"] == level)]
    return {(s, t): a for s, t, a in zip(sel["source"], sel["target"], sel["amount"])}


def test_pay_splits_into_taxes_deductions_and_net_pay():
    txns = frame([txn(1, "2026-03-14", 2702.5, "pay"), txn(2, "2026-03-20", -1000.0, "food")])
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
    assert e[("accounts", "unspent")] == 1712.5


def test_other_income_savings_splits_and_balancing():
    txns = frame([
        txn(1, "2026-04-02", 12.0, "interest"),
        txn(2, "2026-04-03", -300.0, "savings_529"),
        txn(3, "2026-04-04", -50.0, "household", splits="household -30.00; food -20.00"),
        txn(4, "2026-04-05", -500.0, "transfer"),
        txn(5, "2026-04-06", 8.0, "food"),             # a refund nets against its category
        txn(6, "2019-04-06", -99.0, "food", era="legacy"),
    ])
    out = flows.build(pd.DataFrame(), txns, CATEGORIES)
    e = edges(out, "2026-04")
    assert e[("interest", "accounts")] == 12.0
    assert e[("accounts", "savings_529")] == 300.0
    assert e[("accounts", "household")] == 30.0
    assert e[("accounts", "food")] == 12.0
    assert ("accounts", "transfer") not in e
    assert e[("savings and balances", "accounts")] == 330.0
    assert set(out["period"]) == {"2026-04", "2026"}

    g = edges(out, "2026-04", "group")
    assert g[("other income", "accounts")] == 12.0
    assert g[("accounts", "savings")] == 300.0
    assert g[("accounts", "household")] == 30.0 and g[("accounts", "food")] == 12.0


def test_years_add_up_their_months():
    txns = frame([txn(1, "2026-01-05", -10.0, "food"), txn(2, "2026-02-05", -15.0, "food")])
    out = flows.build(pd.DataFrame(), txns, CATEGORIES)
    assert edges(out, "2026")[("accounts", "food")] == 25.0
    assert edges(out, "2026")[("savings and balances", "accounts")] == 25.0


def test_tax_nodes():
    assert [flows.tax_node(n) for n in ("FITW", "SS", "MED", "OASDI", "WI State Tax", "Federal Income Tax")] == [
        "federal tax", "social security", "medicare", "social security", "state tax", "federal tax"]
