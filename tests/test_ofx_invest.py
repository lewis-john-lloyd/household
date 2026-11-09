"""Investment accounts in OFX: several accounts to a file, a plan's money sources, made-up rows."""

import re

import pandas as pd
import pytest

from household import ingest, investments
from household.parsers import ParseError, identify, ofx, ofx_invest

HEAD = """OFXHEADER:100
DATA:OFXSGML
VERSION:102

<OFX><SIGNONMSGSRSV1><SONRS><STATUS><CODE>0<SEVERITY>INFO</STATUS><DTSERVER>2001-06-01</SONRS></SIGNONMSGSRSV1>
<INVSTMTMSGSRSV1>"""
TAIL = """</INVSTMTMSGSRSV1><SECLISTMSGSRSV1><SECLIST>
<MFINFO><SECINFO><SECID><UNIQUEID>CUSIP0001<UNIQUEIDTYPE>CUSIP</SECID><SECNAME>ACME INDEX ADMIRAL<TICKER>ACMEX<UNITPRICE>11.0<DTASOF>2001-06-01</SECINFO></MFINFO>
<MFINFO><SECINFO><SECID><UNIQUEID>CUSIP0002<UNIQUEIDTYPE>CUSIP</SECID><SECNAME>WILLOW MONEY MARKET<TICKER>WILMM<UNITPRICE>1.0<DTASOF>2001-06-01</SECINFO></MFINFO>
<MFINFO><SECINFO><SECID><UNIQUEID>TRUST0001<UNIQUEIDTYPE>CUSIP</SECID><SECNAME>Quill 500 Index Trust<TICKER>TRUST0001<UNITPRICE>20.0<DTASOF>2001-06-01</SECINFO></MFINFO>
</SECLIST></SECLISTMSGSRSV1></OFX>"""


def statement(number, body, positions="", plan=False):
    return (
        f"<INVSTMTTRNRS><TRNUID>0<STATUS><CODE>0<SEVERITY>INFO</STATUS><INVSTMTRS><DTASOF>2001-06-01<CURDEF>USD"
        f"<INVACCTFROM><BROKERID>example.com<ACCTID>{number}</INVACCTFROM>"
        f"<INVTRANLIST><DTSTART>2001-01-01<DTEND>2001-06-01{body}</INVTRANLIST>"
        f"<INVPOSLIST>{positions}</INVPOSLIST>"
        + ("<INV401K><EMPLOYERNAME>ACME WIDGETS</INV401K>" if plan else "")
        + "</INVSTMTRS></INVSTMTTRNRS>"
    )


def buy(fitid, day, sec, units, price, total, memo="Buy", source=""):
    src = f"<INV401KSOURCE>{source}" if source else ""
    return (
        f"<BUYMF><INVBUY><INVTRAN><FITID>{fitid}<DTTRADE>{day}<MEMO>{memo}</INVTRAN><SECID><UNIQUEID>{sec}"
        f"<UNIQUEIDTYPE>CUSIP</SECID><UNITS>{units}<UNITPRICE>{price}<TOTAL>{total}<SUBACCTSEC>OTHER"
        f"<SUBACCTFUND>OTHER{src}</INVBUY><BUYTYPE>BUY</BUYMF>"
    )


def sell(fitid, day, sec, units, price, total, memo="Sell"):
    return (
        f"<SELLMF><INVSELL><INVTRAN><FITID>{fitid}<DTTRADE>{day}<MEMO>{memo}</INVTRAN><SECID><UNIQUEID>{sec}"
        f"<UNIQUEIDTYPE>CUSIP</SECID><UNITS>-{units}<UNITPRICE>{price}<TOTAL>{total}<SUBACCTSEC>OTHER"
        f"<SUBACCTFUND>OTHER</INVSELL><SELLTYPE>SELL</SELLMF>"
    )


def bank(fitid, day, amount):
    return (
        f"<INVBANKTRAN><STMTTRN><TRNTYPE>OTHER<DTPOSTED>{day}<TRNAMT>{amount}<FITID>{fitid}</STMTTRN>"
        f"<SUBACCTFUND>OTHER</INVBANKTRAN>"
    )


def position(sec, units, price, value, held="CASH"):
    return (
        f"<POSMF><INVPOS><SECID><UNIQUEID>{sec}<UNIQUEIDTYPE>CUSIP</SECID><HELDINACCT>{held}<POSTYPE>LONG"
        f"<UNITS>{units}<UNITPRICE>{price}<MKTVAL>{value}<DTPRICEASOF>2001-06-01</INVPOS></POSMF>"
    )


SWEEP_IN, SWEEP_OUT = "Sweep Into Settlement Fund", "Sweep Out Of Settlement Fund"
# a traditional IRA: a check deposited (seen only as a sweep into the money market), then moved to the Roth IRA
CONDUIT = statement(
    "X-2222",
    buy("c1", "2001-01-10", "CUSIP0002", 70.0, 1.0, -70.0, SWEEP_IN)
    + sell("c2", "2001-01-12", "CUSIP0002", 70.0, 1.0, 70.0, SWEEP_OUT)
    + bank("c3", "2001-01-12", "-70.0"),
)
# the Roth IRA receives it, sweeps it into the money market, and buys a fund with it
ROTH = statement(
    "X-4444",
    bank("r1", "2001-01-12", "70.0")
    + buy("r2", "2001-01-15", "CUSIP0002", 70.0, 1.0, -70.0, SWEEP_IN)
    + buy("r3", "2001-01-16", "CUSIP0001", 6.0, 10.0, -60.0)
    + sell("r4", "2001-01-17", "CUSIP0002", 60.0, 1.0, 60.0, SWEEP_OUT)
    + "<REINVEST><INVTRAN><FITID>r5<DTTRADE>2001-03-01<MEMO>Dividend Reinvestment</INVTRAN><SECID><UNIQUEID>CUSIP0001"
    "<UNIQUEIDTYPE>CUSIP</SECID><INCOMETYPE>DIV<TOTAL>5.5<SUBACCTSEC>OTHER<UNITS>0.5<UNITPRICE>11.0</REINVEST>",
    position("CUSIP0001", 6.5, 11.0, 71.5) + position("CUSIP0002", 10.0, 1.0, 10.0),
)
TRUST_BUY = buy("p2", "2001-02-15", "TRUST0001", 2.0, 20.0, 40.0, "Price as of date", "AFTERTAX")
# a plan: payroll buys by source, the same trust purchase listed twice, an in-plan move, a fee
PLAN = statement(
    "X-6666",
    buy("p1", "2001-02-15", "CUSIP0001", 3.0, 10.0, 30.0, "Price as of date", "PRETAX")
    + TRUST_BUY
    + TRUST_BUY
    + "<TRANSFER><INVTRAN><FITID>p3<DTTRADE>2001-02-20</INVTRAN><SECID><UNIQUEID>TRUST0001<UNIQUEIDTYPE>CUSIP</SECID>"
    "<SUBACCTSEC>CASH<UNITS>2.0<TFERACTION>OUT<POSTYPE>LONG<UNITPRICE>20.0<INV401KSOURCE>AFTERTAX</TRANSFER>"
    + "<TRANSFER><INVTRAN><FITID>p4<DTTRADE>2001-02-20</INVTRAN><SECID><UNIQUEID>TRUST0001<UNIQUEIDTYPE>CUSIP</SECID>"
    "<SUBACCTSEC>CASH<UNITS>2.0<TFERACTION>IN<POSTYPE>LONG<UNITPRICE>20.0<INV401KSOURCE>AFTERTAX</TRANSFER>"
    + "<INVEXPENSE><INVTRAN><FITID>p5<DTTRADE>2001-03-01</INVTRAN><SECID><UNIQUEID>CUSIP0001<UNIQUEIDTYPE>CUSIP</SECID>"
    "<TOTAL>-0.25<SUBACCTSEC>CASH<SUBACCTFUND>OTHER</INVEXPENSE>",
    position("CUSIP0001", 3.0, 11.0, 33.0, "OTHER") + position("TRUST0001", 2.0, 20.0, 40.0, "OTHER"),
    plan=True,
)
FILE = re.sub(r"(\d{4})-(\d{2})-(\d{2})", r"\1\2\3", HEAD + CONDUIT + ROTH + PLAN + TAIL)  # OFX dates: YYYYMMDD
ACCOUNTS = pd.DataFrame(
    [
        {"account_id": "a_conduit", "last4": "2222", "tax_treatment": "pre_tax"},
        {"account_id": "a_roth", "last4": "4444", "tax_treatment": "roth"},
        {"account_id": "a_401k", "last4": "6666", "tax_treatment": "pre_tax"},
    ]
)


def finished():
    return ingest.finish_investment_rows(ofx_invest.parse(FILE), "download.qfx", ACCOUNTS)


def test_an_investment_download_is_read_by_this_reader_and_not_the_bank_reader():
    assert identify(FILE) is ofx_invest and not ofx.sniff(FILE)


def test_a_deposit_seen_only_as_a_sweep_is_a_contribution_and_a_move_to_the_roth_ira_a_conversion():
    a = finished().tables["investment_activity"]
    money = a[a["amount"] != 0].sort_values(["account_id", "date"])
    assert money[["account_id", "kind", "amount"]].values.tolist() == [
        ["a_401k", "contribution", 30],
        ["a_401k", "contribution", 40],
        ["a_401k", "fee", -0.25],
        ["a_conduit", "contribution", 70],
        ["a_conduit", "conversion_out", -70],
        ["a_roth", "conversion_in", 70],
        ["a_roth", "reinvest", 5.5],
    ]
    roth = a[a["account_id"] == "a_roth"]
    assert roth.groupby("symbol")["shares"].sum().round(6).to_dict() == {"": 0.0, "ACMEX": 6.5, "WILMM": 10.0}


def test_a_plans_rows_keep_their_source_and_a_repeated_transaction_is_read_once():
    r = finished()
    plan = r.tables["investment_activity"].query("account_id == 'a_401k'")
    assert sorted(plan["source"]) == ["", "aftertax", "aftertax", "aftertax", "pretax"]
    assert plan.query("symbol == 'TRUST0001'")["shares"].sum() == 2  # one purchase, an exchange out and back in
    assert plan.query("kind == 'exchange'")["amount"].tolist() == [0, 0]
    assert (
        "a_401k (plan): 2 contribution, 2 exchange, 1 fee; 2 positions on 2001-06-01; 1 transaction(s) listed twice, read once"
        in r.notes
    )
    assert r.tables["investment_activity"]["activity_id"].is_unique


def test_positions_are_holdings_and_every_stated_price_is_kept():
    r = finished()
    held = r.tables["holdings"]
    assert held[["account_id", "symbol", "shares", "value"]].values.tolist() == [
        ["a_roth", "ACMEX", 6.5, 71.5],
        ["a_roth", "WILMM", 10.0, 10.0],
        ["a_401k", "ACMEX", 3.0, 33.0],
        ["a_401k", "TRUST0001", 2.0, 40.0],
    ]
    prices = r.tables["prices"].set_index(["symbol", "date"])["close"]
    assert (
        prices[("TRUST0001", "2001-02-15")] == 20 and prices[("ACMEX", "2001-06-01")] == 11
    )  # a trade's, a position's
    assert set(r.tables["prices"]["source"]) == {"ofx"}
    flows = investments.flows(r.tables["investment_activity"])
    assert flows.query("account_id == 'a_roth'")["kind"].tolist() == ["conversion_in", "reinvest"]


def test_each_statement_needs_an_account_the_ledger_knows_by_its_last_four():
    with pytest.raises(ParseError, match="put 6666 in that account's last4 column"):
        ingest.finish_investment_rows(ofx_invest.parse(FILE), "download.qfx", ACCOUNTS.iloc[:2])


def test_a_failed_server_status_refuses_the_file():
    with pytest.raises(ParseError, match="server status 2000"):
        ofx_invest.parse(
            FILE.replace(
                "<CODE>0<SEVERITY>INFO</STATUS><INVSTMTRS>", "<CODE>2000<SEVERITY>ERROR</STATUS><INVSTMTRS>", 1
            )
        )


def test_a_plan_known_only_by_its_balance_block_moves_shares_between_funds_inside_it():
    body = (
        "<TRANSFER><INVTRAN><FITID>e1<DTTRADE>2001-02-01</INVTRAN><SECID><UNIQUEID>CUSIP0001<UNIQUEIDTYPE>CUSIP</SECID>"
        "<SUBACCTSEC>OTHER<UNITS>-2.0<TFERACTION>OUT<POSTYPE>LONG<UNITPRICE>10.0</TRANSFER>"
        "<TRANSFER><INVTRAN><FITID>e2<DTTRADE>2001-02-01</INVTRAN><SECID><UNIQUEID>TRUST0001<UNIQUEIDTYPE>CUSIP</SECID>"
        "<SUBACCTSEC>OTHER<UNITS>1.0<TFERACTION>IN<POSTYPE>LONG<UNITPRICE>20.0</TRANSFER>"
    )
    one = statement("X-6666", body).replace("</INVSTMTRS>", "<INV401KBAL><TOTAL>40.0</INV401KBAL></INVSTMTRS>")
    text = re.sub(r"(\d{4})-(\d{2})-(\d{2})", r"\1\2\3", HEAD + one + TAIL)
    a = ofx_invest.parse(text).tables["investment_activity"]
    assert a[["kind", "shares", "amount"]].values.tolist() == [["exchange", -2, 0], ["exchange", 1, 0]]
