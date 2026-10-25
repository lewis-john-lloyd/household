"""Bank and card downloads: OFX/QFX (SGML and XML), USAA CSV, and matching files to accounts.

Everything here is synthetic. Dates are built from parts so no line carries an
eight-digit run.
"""

import pandas as pd
import pytest

from household import inbox, ingest
from household.parsers import ParseError, identify, ofx, usaa_csv
from test_inbox import rows, setup  # noqa: F401  (pytest fixture)


def d(month, day, year=2001):
    return f"{year}{month:02d}{day:02d}"


def sgml(transactions, balance="512.34", acct="99-1234", status="0"):
    body = "".join(
        f"<STMTTRN>\r\n<TRNTYPE>{kind}\r\n<DTPOSTED>{d(*when)}120000.000[0:GMT]\r\n<TRNAMT>{amount}\r\n"
        f"<FITID>{fitid}\r\n<NAME>{name}\r\n<MEMO>\r\n</STMTTRN>\r\n"
        for kind, when, amount, fitid, name in transactions)
    return ("OFXHEADER:100\r\nDATA:OFXSGML\r\nVERSION:102\r\nCHARSET:1252\r\n\r\n<OFX>\r\n<SIGNONMSGSRSV1>"
            f"\r\n<SONRS>\r\n<STATUS>\r\n<CODE>{status}\r\n<SEVERITY>INFO\r\n</STATUS>\r\n<FI>\r\n<ORG>Example CU"
            "\r\n</FI>\r\n</SONRS>\r\n</SIGNONMSGSRSV1>\r\n<BANKMSGSRSV1>\r\n<STMTTRNRS>\r\n<STMTRS>\r\n"
            f"<CURDEF>USD\r\n<BANKACCTFROM>\r\n<BANKID>1\r\n<ACCTID>{acct}\r\n<ACCTTYPE>CHECKING\r\n"
            f"</BANKACCTFROM>\r\n<BANKTRANLIST>\r\n<DTSTART>{d(1, 1)}\r\n<DTEND>{d(1, 31)}\r\n{body}"
            f"</BANKTRANLIST>\r\n<LEDGERBAL>\r\n<BALAMT>{balance}\r\n<DTASOF>{d(2, 1)}\r\n</LEDGERBAL>\r\n"
            "</STMTRS>\r\n</STMTTRNRS>\r\n</BANKMSGSRSV1>\r\n</OFX>\r\n")


def xml_card(transactions, balance="-42.10"):
    body = "".join(
        f"<STMTTRN><TRNTYPE>{kind}</TRNTYPE><DTPOSTED>{d(*when)}000000.000</DTPOSTED>"
        f"<TRNAMT>{amount}</TRNAMT><FITID>{fitid}</FITID><NAME>{name}</NAME>"
        f"<CCACCTTO><ACCTID>7777</ACCTID></CCACCTTO><MEMO>{memo}</MEMO></STMTTRN>"
        for kind, when, amount, fitid, name, memo in transactions)
    return ('<?xml version="1.0" encoding="utf-8" ?><?OFX OFXHEADER="200" VERSION="202"?><OFX>'
            "<SIGNONMSGSRSV1><SONRS><STATUS><CODE>0</CODE></STATUS><FI><ORG>Example Card</ORG></FI></SONRS>"
            "</SIGNONMSGSRSV1><CREDITCARDMSGSRSV1><CCSTMTTRNRS><STATUS><CODE>0</CODE></STATUS><CCSTMTRS>"
            "<CURDEF>USD</CURDEF><CCACCTFROM><ACCTID>0042</ACCTID></CCACCTFROM><BANKTRANLIST>"
            f"<DTSTART>{d(1, 1)}</DTSTART><DTEND>{d(1, 31)}</DTEND>{body}</BANKTRANLIST>"
            f"<LEDGERBAL><BALAMT>{balance}</BALAMT><DTASOF>{d(2, 1)}</DTASOF></LEDGERBAL>"
            "</CCSTMTRS></CCSTMTTRNRS></CREDITCARDMSGSRSV1></OFX>")


CHECKING = [("DEBIT", (1, 5), "-12.50", "A1", "COFFEE SHOP"), ("CREDIT", (1, 9), "100.00", "A2", "PAYROLL"),
            ("DEBIT", (1, 9), "-12.50", "A3", "COFFEE SHOP")]
CARD = [("DEBIT", (1, 3), "-30.00", "C1", "GROCER", "GROCER"),
        ("CREDIT", (1, 20), "25.00", "C2", "PAYMENT THANK YOU", "AUTOPAY")]

ACCOUNTS = pd.DataFrame([
    {"account_id": "bills", "type": "checking", "last4": "1234"},
    {"account_id": "card", "type": "credit_card", "last4": "42"},  # a Sheet drops the leading zeros
    {"account_id": "savings", "type": "savings", "last4": ""},
    {"account_id": "usaa_card", "type": "credit_card", "last4": ""},
])

USAA = ("Date,Description,Original Description,Category,Amount,Status\n"
        '2001-01-05,"Grocer","GROCER   STORE 12  ANYTOWN",Groceries,30.00,Posted\n'
        '2001-01-05,"Grocer","GROCER   STORE 12  ANYTOWN",Groceries,30.00,Posted\n'
        '2001-01-07,"Card Payment","CARD PAYMENT",Credit Card Payment,-55.00,Posted\n'
        '2001-01-08,"Cafe","CAFE",Category Pending,4.00,Pending\n')


def test_sgml_download_gives_transactions_balance_and_last_four():
    text = sgml(CHECKING)
    assert identify(text) is ofx
    r = ofx.parse(text)
    t = r.tables["transactions"]
    assert list(t["amount"]) == [-12.5, 100.0, -12.5]
    assert list(t["date"]) == ["2001-01-05", "2001-01-09", "2001-01-09"]
    assert list(t["payee_raw"]) == ["COFFEE SHOP", "PAYROLL", "COFFEE SHOP"]
    assert set(t["source"]) == {"ofx"} and (t["note"] == "").all()  # the empty SGML <MEMO> is no aggregate
    assert r.account_hint == "1234" and (r.covers_from, r.covers_to) == ("2001-01-01", "2001-01-31")
    assert r.tables["balances"].to_dict("records") == [{"as_of": "2001-02-01", "account_id": "",
                                                        "balance": 512.34}]


def test_xml_card_download_keeps_memo_and_cardholder():
    r = ofx.parse(xml_card(CARD))
    t = r.tables["transactions"]
    assert list(t["amount"]) == [-30.0, 25.0]  # OFX: a charge is money out, as in the ledger
    assert list(t["note"]) == ["card 7777", "AUTOPAY; card 7777"]
    assert r.account_hint == "0042" and r.tables["balances"]["balance"][0] == -42.10


@pytest.mark.parametrize("change, message", [
    (lambda t: t.replace("<TRNAMT>-12.50", "<TRNAMT>12.50", 1), "DEBIT"),
    (lambda t: t.replace("<FITID>A3", "<FITID>A1"), "repeated FITID"),
    (lambda t: t.replace("<CODE>0", "<CODE>2000"), "status"),
    (lambda t: t.replace("<CURDEF>USD", "<CURDEF>CAD"), "currency"),
])
def test_ofx_checks(change, message):
    with pytest.raises(ParseError, match=message):
        ofx.parse(change(sgml(CHECKING)))


def test_usaa_csv_leaves_out_pending_and_keeps_bank_hints():
    assert identify(USAA) is usaa_csv
    r = usaa_csv.parse(USAA)
    t = r.tables["transactions"]
    assert len(t) == 3 and r.notes == ["USAA: 3 posted, 1 pending left out"]
    assert t["payee_raw"][0] == "GROCER STORE 12 ANYTOWN" and t["note"][0] == "Grocer | Groceries"
    assert r.card_charges_positive and r.account_hint == ""


def test_accounts_match_by_last_four_then_by_file_name():
    assert ingest.resolve_account("1234", "download.ofx", ACCOUNTS)["account_id"] == "bills"
    assert ingest.resolve_account("0042", "x.qfx", ACCOUNTS)["account_id"] == "card"
    assert ingest.resolve_account("", "savings_usaa.csv", ACCOUNTS)["account_id"] == "savings"
    assert ingest.resolve_account("9999", "2001_savings.qfx", ACCOUNTS)["account_id"] == "savings"
    with pytest.raises(ParseError, match="last4"):
        ingest.resolve_account("9999", "download.qfx", ACCOUNTS)
    with pytest.raises(ParseError, match="rename it to include its account_id"):
        ingest.resolve_account("", "visa.csv", ACCOUNTS)
    with pytest.raises(ParseError, match="several"):
        ingest.resolve_account("", "bills_and_savings.csv", ACCOUNTS)


def test_card_csv_is_flipped_and_identical_rows_stay_distinct():
    r = ingest.finish_bank_rows(usaa_csv.parse(USAA), "usaa_card.csv", ACCOUNTS, "now")
    t = r.tables["transactions"]
    assert list(t["amount"]) == [-30.0, -30.0, 55.0] and set(t["account_id"]) == {"usaa_card"}
    assert t["txn_id"].nunique() == 3 and r.label == "usaa_card_2001-01-07"
    same = ingest.finish_bank_rows(usaa_csv.parse(USAA), "savings_usaa.csv", ACCOUNTS, "now")
    assert list(same.tables["transactions"]["amount"]) == [30.0, 30.0, -55.0]  # not a card: as given


def test_fitid_ids_are_stable_across_downloads_and_belong_to_the_account():
    one = ingest.finish_bank_rows(ofx.parse(sgml(CHECKING[:2])), "a.ofx", ACCOUNTS, "now")
    two = ingest.finish_bank_rows(ofx.parse(sgml(CHECKING)), "b.ofx", ACCOUNTS, "now")
    assert list(one.tables["transactions"]["txn_id"]) == list(two.tables["transactions"]["txn_id"][:2])
    assert set(two.tables["balances"]["account_id"]) == {"bills"} and two.account_id == "bills"


SEED = ("account_id,institution,owner,type,tax_treatment,opened,closed,last4,expected_docs,max_gap_days\n"
        "bills,Example Bank,joint,checking,none,,,1234,monthly_statement,45\n"
        "usaa_card,Example Bank,joint,credit_card,none,,,,monthly_statement,45\n")


def test_inbox_files_downloads_by_account_and_adds_overlaps_once(setup):  # noqa: F811
    drive, ledger, box, record = setup
    drive.add("accounts.csv", box, "text/csv", SEED.encode())
    inbox.process(ledger, drive, "root")
    drive.add("Bank_Export.ofx", box, "application/octet-stream", sgml(CHECKING[:2]).encode("cp1252"))
    drive.add("usaa_card.csv", box, "text/csv", USAA.encode())
    drive.add("visa.csv", box, "text/csv", USAA.replace("CAFE", "CAFE 2").encode())
    out = {s["file"]: s for s in inbox.process(ledger, drive, "root")}
    assert out["Bank_Export.ofx"]["appended"] == {"transactions": 2, "balances": 1}
    assert out["usaa_card.csv"]["appended"] == {"transactions": 3}
    assert out["visa.csv"]["status"] == "failed" and "account_id" in out["visa.csv"]["reason"]
    assert drive.names_in(box) == ["visa.csv"]
    assert any(n.startswith("bills_2001-01-31_") for n in drive.names_in(record))

    drive.add("Bank_Export (1).ofx", box, "application/octet-stream", sgml(CHECKING, balance="499.84").encode())
    [later] = [s for s in inbox.process(ledger, drive, "root") if s["file"].startswith("Bank")]
    assert later["appended"] == {"transactions": 1, "balances": 0}  # same as-of day: first balance kept
    txns = rows(ledger, "transactions")
    assert len(txns) == 6 and set(txns["account_id"]) == {"bills", "usaa_card"}
    docs = rows(ledger, "source_documents").set_index("original_name")
    assert docs.loc["usaa_card.csv", "account_id"] == "usaa_card"
