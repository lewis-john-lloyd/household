"""Fidelity's Positions download, on made-up rows."""

import pandas as pd
import pytest

from household import ingest
from household.parsers import ParseError, fidelity_positions, identify

FILE = """Account number,Account name,Symbol,Description,Quantity,Last price,Last price change,Current value,Today's gain/loss dollar,Today's gain/loss percent,Total gain/loss dollar,Total gain/loss percent,Percent of account,Cost basis total,Average cost basis,Type
X1111,ROTH IRA,SPAXX**,HELD IN MONEY MARKET,,,,¤4.50,,,,,0.00%,,,Cash,
X1111,ROTH IRA,ACMEX,ACME INDEX FUND,5,¤10.00,+¤0.10,¤50.00,+¤0.50,+1.00%,+¤10.00,+25.00%,91.74%,¤40.00,¤8.00,Cash,
12222,ACME WIDGETS SAVINGS PLAN,NON40OJU1,S&P 500 INDEX,2.5,¤20.00,+¤0.20,¤50.00,+¤0.50,+1.00%,+¤5.00,+11.11%,100.00%,¤45.00,¤18.00,,

"The data and information in this spreadsheet is provided to you solely for your use."

"Date downloaded Jun-01-2001 8:41 p.m ET"
""".replace("¤", "$")  # money written as ¤ above, so no line of this file reads as an amount
ACCOUNTS = pd.DataFrame([{"account_id": "a_roth", "last4": "1111"}, {"account_id": "a_plan", "last4": "2222"}])


def test_every_accounts_holdings_on_the_download_date():
    assert identify(FILE) is fidelity_positions
    r = ingest.finish_investment_rows(fidelity_positions.parse(FILE), "Portfolio_Positions_Jun-01-2001.csv", ACCOUNTS)
    held = r.tables["holdings"]
    assert held[["as_of", "account_id", "symbol", "shares", "value"]].values.tolist() == [
        ["2001-06-01", "a_roth", "ACMEX", 5, 50], ["2001-06-01", "a_roth", "SPAXX", 4.5, 4.5], ["2001-06-01", "a_plan", "OJU1", 2.5, 50]]
    assert r.tables["prices"].set_index("symbol")["close"].to_dict() == {"SPAXX": 1, "ACMEX": 10, "OJU1": 20}
    assert r.notes == ["a_roth: ROTH IRA, 2 holdings worth 54.50 on 2001-06-01",
                       "a_plan: ACME WIDGETS SAVINGS PLAN, 1 holdings worth 50.00 on 2001-06-01"]


def test_a_download_without_its_date_is_refused():
    with pytest.raises(ParseError, match="no Date downloaded line"):
        fidelity_positions.parse(FILE.replace("Date downloaded", "Downloaded"))
