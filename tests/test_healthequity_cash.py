"""HealthEquity's HSA cash history (an HTML table saved as .xls), on made-up rows."""

import pandas as pd
import pytest

from household import inbox, ingest
from household.parsers import ParseError, healthequity_cash, identify

ATTACHMENT = '<div id="parentDiv"><div class="thumbnail" style="display: none;"></div></div>'


def table(*rows):
    cells = "".join(
        f"<tr>{''.join(f'<td nowrap class=text>{c}</td>' for c in r)}<td>{ATTACHMENT}</td></tr>" for r in rows
    )
    return (
        "<style> .text { mso-number-format:\\@; } </style> <table><tr><td colspan=5><center><b>Completed Transactions"
        "</b></center></td></tr><tr><td><b>Date</b></td><td><b>Transaction</b></td><td><b>Amount</b></td>"
        "<td><b>HSA Cash Balance</b></td><td><b>Attachments</b></td></tr>" + cells + "</table>"
    ).replace("¤", "$")


# newest first, as the download lists them; money written as ¤ so no line of this file reads as an amount
YEAR = table(
    ["3/2/2001", "Investment: ACMEX", "(¤40.00)", "¤25.00"],
    ["3/1/2001", "Payment to Willow St Clinic", "(¤20.00)", "¤65.00"],
    ["2/28/2001", "Interest for Feb-01", "¤0.10", "¤85.00"],
    [
        "2/27/2001",
        "Investment Admin Fee February 2001. Average HealthEquity investments of ¤90.00",
        "(¤0.10)",
        "¤84.90",
    ],
    ["1/2/2001", "Employee Contribution (Tax year: 2001)", "¤50.00", "¤85.00"],
    ["1/2/2001", "Balance Forward", "¤35.00", "¤35.00"],
)
ACCOUNTS = pd.DataFrame(
    [
        {"account_id": "a_hsa", "institution": "HealthEquity", "last4": ""},
        {"account_id": "b_hsa", "institution": "Quill & Ink", "last4": ""},
    ]
)


def test_the_cash_history_reads_oldest_first_with_its_running_balance_and_finds_its_account():
    text = inbox.to_text("TransactionHistory.xls", "application/vnd.ms-excel", YEAR.encode())
    assert identify(text) is healthequity_cash
    r = ingest.finish_investment_rows(healthequity_cash.parse(text), "TransactionHistory.xls", ACCOUNTS)
    a = r.tables["investment_activity"]
    assert a[["date", "kind", "amount", "shares"]].values.tolist() == [
        ["2001-01-02", "contribution", 50, 50],
        ["2001-02-27", "fee", -0.1, -0.1],
        ["2001-02-28", "interest", 0.1, 0.1],
        ["2001-03-01", "withdrawal", -20, -20],
        ["2001-03-02", "exchange", 0, -40],
    ]
    assert set(a["account_id"]) == {"a_hsa"} and set(a["symbol"]) == {"cash"}
    assert r.tables["holdings"][["as_of", "account_id", "symbol", "value"]].values.tolist() == [
        ["2001-03-02", "a_hsa", "cash", 25]
    ]


def test_a_balance_that_doesnt_follow_from_the_row_before_refuses_the_file():
    wrong = YEAR.replace(">$65.00<", ">$66.00<")  # leak-guard: allow (made up)
    with pytest.raises(ParseError, match="2001-03-01 Payment to Willow St Clinic"):
        healthequity_cash.parse(wrong)


def test_two_accounts_at_the_custodian_need_the_account_id_in_the_file_name():
    two = pd.concat([ACCOUNTS, pd.DataFrame([{"account_id": "c_hsa", "institution": "HealthEquity", "last4": ""}])])
    with pytest.raises(ParseError, match="2 HealthEquity accounts"):
        ingest.finish_investment_rows(healthequity_cash.parse(YEAR), "TransactionHistory.xls", two)
    r = ingest.finish_investment_rows(healthequity_cash.parse(YEAR), "c_hsa_TransactionHistory.xls", two)
    assert set(r.tables["investment_activity"]["account_id"]) == {"c_hsa"}


def test_a_deposit_not_yet_available_counts_on_the_day_it_was_made():
    pending = YEAR.replace(">1/2/2001<", ">1/2/2001 (Available 1/4/01)<", 1)
    a = healthequity_cash.parse(pending).tables["investment_activity"]
    assert a["date"].iloc[0] == "2001-01-02"
