"""TreasuryDirect's Current Holdings table, pasted as text: one reading of a savings bond account, on made-up bonds."""

import pandas as pd
import pytest

from household import ingest
from household import valuation as v
from household.parsers import ParseError, identify, treasury_direct

# made-up bonds, tab separated as a browser copies the table
PAGE = (
    "\tConfirm #\tIssue Date\tInterest Rate\tStatus\tAmount\tCurrent Value\n"
    "\tIXXXB\t02-01-2001\t3.00%\t \t$40.00\t$45.00\n"  # leak-guard: allow (made up)
    "\tIXXXA\t01-01-2001\t3.00%\t \t$30.00\t$35.00\n"  # leak-guard: allow (made up)
    " \t \t \t \tTotals:\t$70.00\t$80.00\n"  # leak-guard: allow (made up)
)
ACCOUNTS = pd.DataFrame(
    [
        {
            "account_id": "a_bonds",
            "institution": "TreasuryDirect",
            "owner": "alex",
            "type": "savings_bonds",
            "last4": "",
        },
        {
            "account_id": "s_bonds",
            "institution": "TreasuryDirect",
            "owner": "sam",
            "type": "savings_bonds",
            "last4": "",
        },
    ]
)
NO_SETTINGS = pd.DataFrame(columns=["jurisdiction", "key", "value"])


def read(file_name, received="2001-03-05T10:00:00+00:00"):
    result = identify(PAGE).parse(PAGE)
    return ingest.finish_investment_rows(result, file_name, ingest.named_accounts(ACCOUNTS, NO_SETTINGS), received)


def test_the_page_is_one_reading_of_the_account_its_file_names():
    assert identify(PAGE) is treasury_direct
    result = read("s_bonds.txt")
    held = result.tables["holdings"]
    assert held[["as_of", "account_id", "symbol", "shares", "value"]].values.tolist() == [
        ["2001-03-05", "s_bonds", "IBOND", "", 80.0]
    ]
    assert "2 bonds issued 2001-01-01 to 2001-02-01, bought for 70.00, worth 80.00" in result.notes[0]
    assert any("dated 2001-03-05, the day read" in n for n in result.notes)


def test_a_date_in_the_name_dates_the_reading():
    held = read("a_bonds_2001-02-28.txt").tables["holdings"]
    assert held[["as_of", "account_id"]].values.tolist() == [["2001-02-28", "a_bonds"]]


def test_a_name_that_picks_no_account_is_refused_when_there_are_two():
    with pytest.raises(ParseError, match="2 TreasuryDirect accounts"):
        read("bonds.txt")


def test_bonds_that_dont_add_to_the_totals_are_refused():
    off = PAGE.replace("$80.00", "$90.00")  # leak-guard: allow (made up)
    with pytest.raises(ParseError, match="the Totals row says"):
        treasury_direct.parse(off)
    with pytest.raises(ParseError, match="no Totals row"):
        treasury_direct.parse(PAGE.rsplit(" \t", 4)[0])


def test_the_reading_values_the_account_from_its_date_and_ends_an_older_symbol():
    accounts = pd.DataFrame(
        [{"account_id": "s_bonds", "owner": "sam", "type": "savings_bonds", "tax_treatment": "taxable", "closed": ""}]
    )
    older = {"as_of": "2001-01-15", "account_id": "s_bonds", "symbol": "OLDB", "source": "", "shares": "", "value": 60}
    newer = {**read("s_bonds.txt").tables["holdings"].iloc[0].to_dict(), "doc_id": "d2"}
    holdings = pd.DataFrame([{**older, "doc_id": "d1"}, newer])
    out, _ = v.values(
        accounts,
        holdings,
        pd.DataFrame(columns=["account_id", "date", "symbol", "source", "shares"]),
        pd.DataFrame(columns=["symbol", "date", "priced_date", "close"]),
        pd.DataFrame(columns=["symbol", "asset_class"]),
        "2001-03-10",
    )
    by_day = {d: g[["symbol", "value", "basis"]].values.tolist() for d, g in out.groupby("date")}
    [(symbol, value, basis)] = by_day["2001-02-28"]  # between the readings: bridged from one to the next
    assert symbol == "OLDB" and 60 < value < 80 and basis == "bridged"
    assert [r[:2] for r in by_day["2001-03-10"]] == [["IBOND", 80.0]]  # the new reading alone from its date
    assert set(out["bucket"]) == {"ibonds"}
