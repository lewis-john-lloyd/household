import pandas as pd

from household import checks, recurring

ACCOUNTS = pd.DataFrame([
    {"account_id": "checking", "type": "checking", "expected_docs": "monthly_statement", "closed": "",
     "max_gap_days": "45"},
    {"account_id": "card", "type": "credit_card", "expected_docs": "monthly_statement", "closed": "",
     "max_gap_days": ""},
    {"account_id": "new", "type": "savings", "expected_docs": "monthly_statement", "closed": "", "max_gap_days": ""},
    {"account_id": "old", "type": "checking", "expected_docs": "monthly_statement", "closed": "2025-01-01",
     "max_gap_days": ""},
    {"account_id": "theirs", "type": "checking", "expected_docs": "none", "closed": "", "max_gap_days": ""},
    {"account_id": "spare", "type": "credit_card", "expected_docs": "when_used", "closed": "", "max_gap_days": ""},
    {"account_id": "broker", "type": "brokerage", "expected_docs": "quarterly_statement", "closed": "",
     "max_gap_days": "100"},
])


def txn(i, date, account, amount, payee="SHOP", category="household", key=None, era="ledger"):
    return {"txn_id": f"t{i}", "date": date, "account_id": account, "amount": amount, "payee_raw": payee,
            "category": category, "key": key or payee, "era": era, "status": "decided", "decided_by": "model"}


def test_coverage_lists_what_is_due():
    txns = pd.DataFrame([txn(1, "2026-10-20", "checking", -5.0), txn(2, "2026-08-01", "card", -5.0)])
    balances = pd.DataFrame(columns=["as_of", "account_id", "balance"])
    docs = pd.DataFrame([{"account_id": "card", "covers_to": "2026-08-15"}])
    categorized = pd.DataFrame([dict(txn(3, "2026-10-21", "card", -3.0), status="waiting",
                                     decided_by="waiting for venmo")])
    out = checks.coverage(ACCOUNTS, txns, balances, docs, categorized, {"paypal": ("2025-01-01", "2026-10-01")},
                          "2026-10-26").set_index("source")
    assert list(out.index) == ["checking", "card", "new", "spare", "paypal", "amazon", "venmo", "apple", "google"]
    assert out.loc["spare", "status"] == "download when used"
    assert out.loc["checking", "status"] == "ok"
    assert (out.loc["card", "newest"], out.loc["card", "status"]) == ("2026-08-15", "due")  # 72 days > 45
    assert out.loc["new", "status"] == "never downloaded"
    assert out.loc["paypal", "status"] == "ok"
    assert (out.loc["venmo", "waiting_lines"], out.loc["venmo", "status"]) == (1, "never downloaded")
    assert out.loc["amazon", "status"] == "not used"


def test_balances_must_imply_one_opening():
    txns = pd.DataFrame([txn(1, "2026-08-02", "checking", -50.0), txn(2, "2026-09-03", "checking", 200.0),
                         txn(3, "2026-09-20", "checking", -25.0), txn(4, "2026-09-21", "card", -9.0)])
    balances = pd.DataFrame([{"as_of": "2026-08-31", "account_id": "checking", "balance": "950.00"},
                             {"as_of": "2026-09-30", "account_id": "checking", "balance": "1125.00"},
                             {"as_of": "2026-10-31", "account_id": "checking", "balance": "1100.00"}])
    out = checks.balance_check(balances, txns)
    assert list(out["implied_opening"]) == [1000.0, 1000.0, 975.0]
    assert list(out["status"]) == ["baseline", "ok", "off by -25.00"]


def monthly(key, amounts, start="2026-01-05", category="subscriptions", days=30):
    first = pd.Timestamp(start)
    return [txn(f"{key}{n}", (first + pd.Timedelta(days=days * n)).date().isoformat(), "card", -a, key, category)
            for n, a in enumerate(amounts)]


def test_recurring_finds_subscriptions_and_their_status():
    rows = (monthly("STREAMER", [15.49] * 9)
            + monthly("NEWSPAPER", [4.0] * 6 + [8.0], start="2026-03-01")  # price went up
            + monthly("OLD GYM", [40.0] * 4, start="2025-06-01")    # stopped
            + monthly("GROCER", [80.0, 12.0, 150.0, 40.0, 95.0])    # amounts all over the place
            + monthly("CHECK", [100.0] * 5)
            + [txn("x", "2026-03-01", "card", -3.0, "ODD SHOP"), txn("y", "2026-05-17", "card", -3.0, "ODD SHOP"),
               txn("z", "2026-05-19", "card", -3.0, "ODD SHOP")])  # irregular
    out = recurring.discover(pd.DataFrame(rows), today="2026-09-20").set_index("key")
    assert set(out.index) == {"STREAMER", "NEWSPAPER", "OLD GYM"}
    assert out.loc["STREAMER", ["cadence", "typical", "status"]].tolist() == ["monthly", 15.49, "active"]
    assert out.loc["NEWSPAPER", ["latest", "status"]].tolist() == [8.0, "price changed"]
    assert out.loc["OLD GYM", "status"] == "stopped"
    assert list(out.index)[0] == "NEWSPAPER"  # changes first


def test_yearly_charges_count():
    rows = [txn(n, f"{2023 + n}-02-10", "card", -99.0, "ANNUAL CLUB") for n in range(3)]
    out = recurring.discover(pd.DataFrame(rows), today="2025-06-01")
    assert out[["key", "cadence", "status"]].values.tolist() == [["ANNUAL CLUB", "yearly", "active"]]
