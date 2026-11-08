"""Fidelity activity: an account's history CSV and a plan's QIF, on made-up rows."""

import pandas as pd
import pytest

from household import ingest, investments
from household.parsers import ParseError, fidelity, identify, paypal_qif

HEAD = '﻿  \n  \nRun Date,Action,Symbol,Description,Type,Price ($),Quantity,Commission ($),Fees ($),Accrued Interest ($),Amount ($),Cash Balance ($),Settlement Date  \n'
TAIL = '  \nThe data and information in this spreadsheet is provided to you solely for your use.  \n  \nDate downloaded 01/02/2001 9:00 pm'


def history(*rows):
    return HEAD + "".join(r + "  \n" for r in rows) + TAIL


IRA = history(
    '03/10/2001,REINVESTMENT FIDELITY GOVERNMENT MONEY MARKET (SPAXX) (Cash),SPAXX,FIDELITY GOVERNMENT MONEY MARKET,Cash,1,0.5,"","","","-0.5",1.5,""',
    '03/10/2001,DIVIDEND RECEIVED FIDELITY GOVERNMENT MONEY MARKET (SPAXX) (Cash),SPAXX,FIDELITY GOVERNMENT MONEY MARKET,Cash,"",0,"","","",0.5,1.5,""',
    '02/22/2001,CONV TO ROTH IRA VS 000-000000-1 (Cash),"",No Description,Cash,"",0,"","","","-70",1,""',
    '02/19/2001,CASH CONTRIBUTION CURRENT YEAR (Cash),"",No Description,Cash,"",0,"","","",70,71,""',
    '02/02/2001,YOU BOUGHT ACME INDEX FUND (ACMEX) (Cash),ACMEX,ACME INDEX FUND,Cash,20,3,"","","","-60",1,02/03/2001',
    '01/24/2001,TRANSFER OF ASSETS CHECK RECEIVED WILLOW ST (Cash),"",No Description,Cash,"",0,"","","",61,61,""',
)


def test_a_history_download_reads_each_row_as_money_in_or_out_and_shares_moved():
    r = fidelity.parse(IRA)
    df = r.tables["investment_activity"].set_index("kind")
    assert list(r.tables["investment_activity"]["kind"]) == ["reinvest", "dividend", "conversion_out", "contribution", "buy", "transfer_in"]
    assert (df.at["contribution", "amount"], df.at["conversion_out", "amount"], df.at["transfer_in", "amount"]) == (70, -70, 61)
    assert (df.at["buy", "shares"], df.at["buy", "value"], df.at["buy", "amount"]) == (3, 60, 0)  # bought inside the account
    assert (df.at["dividend", "amount"], df.at["reinvest", "shares"], df.at["reinvest", "amount"]) == (0.5, 0.5, 0)
    assert (r.covers_from, r.covers_to) == ("2001-01-24", "2001-03-10")
    assert identify(IRA) is fidelity


def test_overlapping_downloads_give_shared_rows_one_id_and_twin_rows_two():
    twins = history('04/30/2001,DIVIDEND RECEIVED ACME INDEX FUND (ACMEX) (Cash),ACMEX,ACME INDEX FUND,Cash,"",0,"","","",5,6,""',
                    '04/30/2001,DIVIDEND RECEIVED ACME INDEX FUND (ACMEX) (Cash),ACMEX,ACME INDEX FUND,Cash,"",0,"","","",5,1,""')
    a = fidelity.finish(fidelity.parse(IRA), "m_ira").tables["investment_activity"]
    b = fidelity.finish(fidelity.parse(IRA.replace("01/24/2001,TRANSFER", "01/25/2001,TRANSFER")), "m_ira").tables["investment_activity"]
    assert len(set(a["activity_id"]) & set(b["activity_id"])) == 5 and a["account_id"].eq("m_ira").all()
    other = fidelity.finish(fidelity.parse(IRA), "m_roth").tables["investment_activity"]
    assert not set(a["activity_id"]) & set(other["activity_id"])  # the same rows in another account are other rows
    assert fidelity.parse(twins).tables["investment_activity"]["activity_id"].nunique() == 2


PLAN = """!Type:Invst
D01/16/2001
NBuy
YACME INDEX(OJU1)
I10.00000
Q5.000
U50.00
T50.00
MContribution
^
D01/30/2001
NShrsOut
YACME INDEX(OJU1)
I10.00000
Q1.000
U10.00
T10.00
MFees
^
D02/01/2001
NSell
YACME INDEX(OJU1)
I10.00000
Q4.000
U40.00
T40.00
MExchange Out
^
D02/01/2001
NBuy
YQUILL BOND INDEX(OJU6)
I20.00000
Q2.000
U40.00
T40.00
MExchange In
^
D03/01/2001
NShrsIn
YQUILL BOND INDEX(OJU6)
I20.00000
Q3.000
U60.00
T60.00
MTransfers
^
D04/01/2001
NSell
YQUILL BOND INDEX(OJU6)
I20.00000
Q1.000
U20.00
T20.00
MWithdrawals
^
D04/02/2001
Nnull
YQUILL BOND INDEX(OJU6)
I20.00000
Q0.010
U0.00
T0.00
MChange in Market Value
^
"""


def test_a_plan_history_reads_contributions_exchanges_fees_and_withdrawals():
    r = fidelity.parse(PLAN)
    df = r.tables["investment_activity"]
    assert df[["kind", "symbol", "shares", "value", "amount"]].values.tolist() == [
        ["contribution", "OJU1", 5, 50, 50], ["fee", "OJU1", -1, -10, -10], ["exchange", "OJU1", -4, -40, 0],
        ["exchange", "OJU6", 2, 40, 0], ["transfer_in", "OJU6", 3, 60, 60], ["withdrawal", "OJU6", -1, -20, -20],
        ["adjustment", "OJU6", 0.01, 0, 0]]
    # what is held: 0 of the first fund, 4.01 of the second
    held = df.groupby("symbol")["shares"].sum().round(6).to_dict()
    assert held == {"OJU1": 0.0, "OJU6": 4.01}
    assert identify(PLAN) is fidelity and not paypal_qif.sniff(PLAN)


def test_the_account_comes_from_the_number_in_a_history_file_name_or_an_account_id_in_a_plan_file_name():
    accounts = pd.DataFrame([{"account_id": "m_ira", "last4": "3456"}, {"account_id": "m_roth", "last4": "7788"},
                             {"account_id": "m_401k_old", "last4": ""}])
    assert ingest.resolve_account("", "History_for_Account_123456 (2).csv", accounts)["account_id"] == "m_ira"
    assert ingest.resolve_account("", "m_401k_old_plan.qif", accounts)["account_id"] == "m_401k_old"
    with pytest.raises(ParseError, match="rename it to include its account_id"):
        ingest.resolve_account("", "plan_history.qif", accounts)


def test_an_empty_history_is_refused():
    with pytest.raises(ParseError, match="no activity"):
        fidelity.parse(history())


def test_flows_sum_each_accounts_money_in_and_out_by_year_and_kind():
    a = fidelity.finish(fidelity.parse(IRA), "m_ira").tables["investment_activity"]
    p = fidelity.finish(fidelity.parse(PLAN), "m_plan").tables["investment_activity"]
    out = investments.flows(pd.concat([a, p]))
    assert out[["account_id", "kind", "amount"]].values.tolist() == [
        ["m_ira", "contribution", 70], ["m_ira", "conversion_out", -70], ["m_ira", "dividend", 0.5],
        ["m_ira", "transfer_in", 61], ["m_plan", "contribution", 50], ["m_plan", "fee", -10],
        ["m_plan", "transfer_in", 60], ["m_plan", "withdrawal", -20]]
