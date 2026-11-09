"""Venmo's emails, as the exporter writes them, and the bank lines they name. Made-up emails in the layouts' shapes."""

from pathlib import Path

import pandas as pd

from household import categorize, explain
from household.parsers import email_receipts, identify


FILE = (Path(__file__).parent / "fixtures" / "venmo.txt").read_text()


def line(i, date, payee, amount):
    return {
        "txn_id": f"t{i}",
        "date": date,
        "account_id": "checking",
        "amount": amount,
        "payee_raw": payee,
        "category": "",
        "note": "",
        "era": "ledger",
        "source": "ofx",
    }


def test_payments_sent_and_received_and_transfers_read_as_payment_details():
    assert identify(FILE) is email_receipts
    result = email_receipts.parse(FILE)
    d = result.tables["payment_details"].set_index("detail_id")
    rows = d.sort_values("date")[["date", "amount", "service", "kind", "counterparty", "memo"]].values.tolist()
    assert rows == [
        ["2026-08-01", 10.0, "venmo", "Transfer", "", ""],
        ["2026-09-01", -42.5, "venmo", "Payment", "sam", "Pizza night"],
        ["2026-09-03", 30.0, "venmo", "Payment Received", "partner", "Concert tickets"],
        ["2026-09-05", 20.0, "venmo", "Payment Received", "sam", ""],  # no note
        ["2026-09-06", 50.0, "venmo", "Transfer", "", ""],
    ]
    assert "Venmo: 5 payments and transfers" in result.notes
    assert "order_items" not in result.tables


def test_a_payment_line_takes_who_and_why_and_a_cash_out_takes_what_it_carried():
    details = email_receipts.parse(FILE).tables["payment_details"]
    lines = pd.DataFrame(
        [
            line(1, "2026-09-02", "VENMO PAYMENT 1234", -42.50),
            line(2, "2026-09-08", "VENMO CASHOUT 5678", 50.00),
            line(3, "2026-09-09", "VENMO PAYMENT 4321", -15.00),
        ]
    )
    found = explain.explain(lines, details=details)
    # the cash-out carried what was received after the August transfer, up to its own
    assert found["payee"] == {"t1": "sam Pizza night", "t2": "Venmo transfer: partner (Concert tickets); sam"}
    assert found["coverage"]["venmo"] == ("2026-08-01", "2026-09-06")
    # a payment after the latest email waits for the next; the named ones don't
    status = categorize.categorize(lines, pd.DataFrame(), pd.DataFrame(), explanations=found).set_index("txn_id")
    assert status.at["t3", "status"] == "waiting" and "waiting" not in set(status.loc[["t1", "t2"], "status"])
    # each cash-out is its own question, even after an answer for its merchant
    answered = pd.DataFrame(
        [
            {
                "proposal_key": "payee:VENMO TRANSFER",
                "kind": "category",
                "decided_at": "2026-09-01",
                "decision": "approve",
                "note": "gifts",
            }
        ]
    )
    again = categorize.categorize(lines, pd.DataFrame(), answered, explanations=found).set_index("txn_id")
    assert (again.at["t2", "ask"], again.at["t2", "decided_by"]) == ("line", "venmo transfer, asked each time")


def test_a_line_before_the_first_email_is_asked_about_and_one_after_the_last_waits():
    details = email_receipts.parse(FILE).tables["payment_details"]  # Venmo's emails run 2026-08-01 to 2026-09-06
    lines = pd.DataFrame(
        [line(1, "2026-07-15", "VENMO PAYMENT 1111", -25.00), line(2, "2026-09-20", "VENMO PAYMENT 2222", -25.00)]
    )
    found = explain.explain(lines, details=details)
    status = categorize.categorize(lines, pd.DataFrame(), pd.DataFrame(), explanations=found).set_index("txn_id")
    assert status.at["t1", "status"] != "waiting" and status.at["t1", "ask"] in ("payee", "line")
    assert status.at["t2", "status"] == "waiting"


def test_venmo_and_apple_lines_nothing_names_are_asked_about_one_by_one():
    details = email_receipts.parse(FILE).tables["payment_details"]  # Venmo's emails run 2026-08-01 to 2026-09-06
    lines = pd.DataFrame(
        [
            line(1, "2026-07-15", "VENMO PAYMENT 1111", -25.00),
            line(2, "2026-07-20", "VENMO PAYMENT 2222", -40.00),
            line(3, "2026-07-21", "APPLE.COM/BILL", -2.99),
            line(4, "2026-07-22", "WILLOW ST", -12.00),
            line(5, "2026-07-23", "WILLOW ST", -14.00),
        ]
    )
    found = explain.explain(lines, details=details)
    found["coverage"]["apple"] = ("2026-08-01", "2026-09-30")  # Apple's downloads start after these lines too
    df = categorize.categorize(lines, pd.DataFrame(), pd.DataFrame(), explanations=found)
    status = df.set_index("txn_id")
    assert list(status.loc[["t1", "t2", "t3"], "ask"]) == ["line"] * 3
    assert list(status.loc[["t4", "t5"], "ask"]) == ["payee"] * 2  # an ordinary merchant is still one question
    review = categorize.queue(df)
    assert sorted(review.loc[review["kind"] == "line", "key"]) == ["t1", "t2", "t3"]
    assert review.loc[review["kind"] == "payee", "lines"].tolist() == [2]
