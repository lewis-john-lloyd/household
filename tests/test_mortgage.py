import pandas as pd
import pytest

from household import mortgage
from household.parsers import seed

LOAN = {
    "loan_id": "home",
    "lender": "Lender",
    "originated": "2019-12-15",
    "principal": "200000",
    "rate": "6",
    "term_months": "360",
    "escrow": "",
}


def events(*rows):
    return pd.DataFrame(
        [{"date": d, "loan_id": "home", "kind": k, "amount": a, "doc_id": ""} for d, k, a in rows],
        columns=["date", "loan_id", "kind", "amount", "doc_id"],
    )


def run(loan=LOAN, ev=None, through="2050-12"):
    rows, notes = mortgage.amortize(loan, ev, through)
    assert notes == []
    return rows.set_index("month")


def test_first_payment_is_the_second_month_after_closing():
    assert run(dict(LOAN, originated="2025-05-07")).index[0] == "2025-07"


def test_textbook_thirty_year_schedule():
    assert mortgage.payment(200000, 0.06, 360) == pytest.approx(1199.10, abs=0.005)
    s = run()
    assert len(s) == 360 and s.index[0] == "2020-02" and s.index[-1] == "2050-01"
    assert s.loc["2020-02", ["interest", "principal"]].tolist() == [1000.0, 199.1]
    assert s["balance"].iloc[-1] == 0.0
    assert s["principal"].sum() + s["extra_principal"].sum() == pytest.approx(200000, abs=0.05)


def test_rate_given_as_a_fraction_too():
    assert run(dict(LOAN, rate="0.06")).loc["2020-02", "interest"] == 1000.0


def test_a_prepayment_lowers_the_balance():
    base = run()
    early = run(ev=events(("2021-06-10", "prepayment", "10000")))
    assert early.loc["2021-06", "extra_principal"] == 10000.0
    assert early.loc["2021-06", "balance"] == pytest.approx(base.loc["2021-06", "balance"] - 10000, abs=0.01)
    assert len(early) < len(base)  # same payment, paid off sooner


def test_a_recast_lowers_the_payment_and_keeps_the_term():
    s = run(ev=events(("2021-01-20", "recast", "50000")))
    assert s.loc["2021-01", "extra_principal"] == 50000.0
    assert s.loc["2021-02", "payment"] < s.loc["2020-12", "payment"]
    assert s.index[-1] == "2050-01" and s["balance"].iloc[-1] == 0.0


def test_a_rate_change_reamortizes():
    s = run(ev=events(("2022-01-01", "rate_change", "4.5")))
    assert s.loc["2022-01", "interest"] == pytest.approx(s.loc["2021-12", "balance"] * 0.045 / 12, abs=0.01)
    assert s.loc["2022-01", "payment"] < s.loc["2021-12", "payment"]
    assert s.index[-1] == "2050-01"


def test_escrow_comes_off_first_and_overpayment_is_extra_principal():
    loan = dict(LOAN, escrow="300")
    s = run(loan, events(("2020-02-01", "payment", "1499.10"), ("2020-03-01", "payment", "1599.10")), "2020-04")
    assert s.loc["2020-02", ["payment", "escrow", "interest", "principal", "extra_principal"]].tolist() == [
        1499.10,
        300.0,
        1000.0,
        199.1,
        0.0,
    ]
    assert s.loc["2020-03", "extra_principal"] == 100.0
    assert s.loc["2020-04", "payment"] == 1499.10  # no record that month: the scheduled payment


def test_missing_loan_data_is_a_note_not_an_error():
    rows, notes = mortgage.amortize(dict(LOAN, rate=""), None, "2021-01")
    assert rows.empty and "skipped" in notes[0]
    rows, notes = mortgage.schedule(pd.DataFrame(), None, None, "2021-01")
    assert rows.empty and notes


def test_the_household_payments_sit_beside_the_schedule():
    categorized = pd.DataFrame(
        [
            {"date": "2020-02-03", "amount": -1199.10, "category": "mortgage", "era": "ledger"},
            {"date": "2020-02-20", "amount": -50.00, "category": "food", "era": "ledger"},
            {"date": "2020-02-03", "amount": -900.00, "category": "mortgage", "era": "legacy"},
        ]
    )
    s, _ = mortgage.schedule(pd.DataFrame([LOAN]), None, categorized, "2020-03")
    assert s["paid_from_ledger"].tolist() == [1199.10, 0.0]
    assert mortgage.summary(s) == ["mortgage: home balance 199,600.80 after 2 payments (through 2020-03)"]


def test_loans_can_be_seeded():
    text = "loan_id,lender,originated,principal,rate,term_months,escrow\nhome,Lender,2020-01-15,200000,6,360,300\n"
    result = seed.parse(text)
    assert result.keys == {"loans": ["loan_id"]} and len(result.tables["loans"]) == 1


SPLIT = "mortgage_interest=interest, mortgage_principal=principal, pmi=50, property_tax=200, home_insurance=50"


def test_a_loan_split_reads_its_parts_and_refuses_what_it_cannot_use():
    assert mortgage.split_parts(SPLIT) == [
        ("mortgage_interest", "interest"),
        ("mortgage_principal", "principal"),
        ("pmi", 50.0),
        ("property_tax", 200.0),
        ("home_insurance", 50.0),
    ]
    assert mortgage.split_parts("pmi=50, property_tax=200") == []  # no interest or principal
    assert mortgage.split_parts("mortgage_interest=interest, mortgage_principal=principal, pmi=fifty") == []
    params = pd.DataFrame(
        [
            {"jurisdiction": "household", "key": "loan_split:home", "value": SPLIT},
            {"jurisdiction": "household", "key": "loan_split:old", "value": "nonsense"},
        ]
    )
    assert list(mortgage.split_settings(params)) == ["home"]


def payment_lines(*rows):
    return pd.DataFrame(
        [
            {"txn_id": t, "date": d, "amount": a, "category": c, "era": "ledger", "splits": "", "decided_by": "rule"}
            for t, d, a, c in rows
        ]
    )


def test_a_payment_splits_by_the_schedule_and_the_escrow_parts_share_what_is_left():
    loan = dict(LOAN, escrow="300")
    sched, _ = mortgage.schedule(pd.DataFrame([loan]), None, None, "2020-03")
    lines = payment_lines(
        ("t1", "2020-02-03", -1499.10, "mortgage"),  # schedule: interest 1000, principal 199.10, escrow 300
        ("t2", "2020-03-03", -1493.10, "mortgage"),  # the escrow came down by 6
        ("t3", "2020-03-09", -20.00, "food"),
    )
    out = mortgage.split_payments(lines, sched, mortgage.split_settings(_params(SPLIT))).set_index("txn_id")
    assert out.loc["t1", "splits"] == (
        "mortgage_interest -1000.00; mortgage_principal -199.10; pmi -50.00; property_tax -200.00; home_insurance -50.00"
    )
    assert out.loc["t1", "category"] == "mortgage_interest"
    assert out.loc["t1", "decided_by"] == "rule, split by the loan schedule"
    march = dict(piece.rsplit(" ", 1) for piece in out.loc["t2", "splits"].split("; "))
    assert [march["pmi"], march["property_tax"], march["home_insurance"]] == ["-49.00", "-196.00", "-49.00"]
    assert sum(float(v) for v in march.values()) == pytest.approx(-1493.10)
    assert out.loc["t3", "splits"] == ""
    # the payments still count toward the mortgage beside the schedule
    assert mortgage.ledger_payments(out.reset_index().assign(era="ledger")) == {"2020-02": 1499.10, "2020-03": 1493.10}


def test_a_payment_short_of_principal_and_interest_is_left_whole():
    sched, _ = mortgage.schedule(pd.DataFrame([dict(LOAN, escrow="300")]), None, None, "2020-02")
    lines = payment_lines(("t1", "2020-02-03", -900.00, "mortgage"))
    out = mortgage.split_payments(lines, sched, mortgage.split_settings(_params(SPLIT)))
    assert out["splits"].tolist() == [""] and out["category"].tolist() == ["mortgage"]


def _params(value):
    return pd.DataFrame([{"jurisdiction": "household", "key": "loan_split:home", "value": value}])
