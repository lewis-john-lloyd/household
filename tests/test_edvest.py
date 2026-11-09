"""Edvest's 529 account download, on made-up rows."""

import pandas as pd
import pytest

from household import ingest
from household.parsers import ParseError, edvest, identify

FILE = """Fund Account Number,Fund Name,Price,Shares,Total Value
X0001-02,Acme Stock Index Portfolio,¤12.00,7.5,¤90.00
X0001-02,Willow Bond Portfolio,¤10.00,1,¤10.00

Account Number,Trade Date,Process Date,Transaction Type,Transaction Description,Investment Name,Share Price,Shares,Gross Amount,Net Amount
X0001-02,03/16/2001,03/16/2001,K-12 W/D to Account Owner,K-12 W/D to Account Owner ACH,Acme Stock Index Portfolio,¤10.00,-2.5,¤-25,¤-25
X0001-02,02/16/2001,02/16/2001,Recurring Contribution,Recurring Contribution,Willow Bond Portfolio,¤10.00,1,¤10,¤10
X0001-02,02/16/2001,02/16/2001,Recurring Contribution,Recurring Contribution,Acme Stock Index Portfolio,¤10.00,5,¤50,¤50
X0001-02,01/12/2001,01/12/2001,Rollover Contribution,Rollover Contribution,Acme Stock Index Portfolio,¤10.00,5,¤50,¤50
""".replace("¤", "$")  # money written as ¤ above, so no line of this file reads as an amount
ACCOUNTS = pd.DataFrame([{"account_id": "a_529", "last4": "0102"}, {"account_id": "b_529", "last4": "0103"}])


def test_the_download_reads_as_contributions_a_rollover_a_withdrawal_and_dated_positions():
    assert identify(FILE) is edvest
    r = ingest.finish_investment_rows(edvest.parse(FILE), "ofxdownload_06012001.csv", ACCOUNTS)
    a = r.tables["investment_activity"].sort_values("date")
    assert a[["date", "kind", "shares", "amount"]].values.tolist() == [
        ["2001-01-12", "transfer_in", 5, 50], ["2001-02-16", "contribution", 1, 10], ["2001-02-16", "contribution", 5, 50],
        ["2001-03-16", "withdrawal", -2.5, -25]]
    assert set(a["account_id"]) == {"a_529"} and a["activity_id"].is_unique
    held = r.tables["holdings"]
    assert held[["as_of", "account_id", "symbol", "shares", "value"]].values.tolist() == [
        ["2001-06-01", "a_529", "Acme Stock Index Portfolio", 7.5, 90], ["2001-06-01", "a_529", "Willow Bond Portfolio", 1, 10]]
    # the transactions take each portfolio from nothing to the shares the positions show
    assert a.groupby("symbol")["shares"].sum().to_dict() == dict(zip(held["symbol"], held["shares"]))
    assert r.tables["prices"].set_index(["symbol", "date"])["close"][("Acme Stock Index Portfolio", "2001-06-01")] == 12


def test_positions_need_the_download_date_in_the_file_name():
    with pytest.raises(ParseError, match="keep the download's name"):
        ingest.finish_investment_rows(edvest.parse(FILE), "sam_529.csv", ACCOUNTS)
