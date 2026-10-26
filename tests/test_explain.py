"""Amazon's export, explanation matching, and how matches reach categorization."""

import io
import zipfile

import pandas as pd
import pytest

from household import categorize, explain, inbox, schema
from household.parsers import amazon
from test_inbox import rows

ORDER_HEADER = ("ASIN,Billing Address,Department,Order Date,Order ID,Order Status,Payment Method Type,"
                "Product Name,Ship Date,Shipping Address,Total Amount,Website")


def order_line(order, day, ship, amount, name, department="Kitchen", card="Visa - 1111", status="Closed",
               website="Amazon.com"):
    return (f"B0X,1 Elm St Springfield,{department},{day}T10:00:00Z,{order},{status},{card},{name},"
            f"{ship}T10:00:00Z,1 Elm St Springfield,{amount},{website}")


ORDERS = "\n".join([
    ORDER_HEADER,
    order_line("111-1", "2025-08-01", "2025-08-02", "20.00", "Spatula"),
    order_line("111-1", "2025-08-01", "2025-08-02", "10.00", "Socks", department="Apparel"),
    order_line("111-1", "2025-08-01", "2025-08-05", "5.50", "Whisk"),
    order_line("222-2", "2025-08-03", "2025-08-04", "12.00", "Bananas", department="", website="wholefoods.com",
               card="Not Available"),
    order_line("333-3", "2025-08-03", "2025-08-04", "99.00", "Work monitor", card="Visa - 9999"),
    order_line("444-4", "2025-08-06", "2025-08-07", "8.00", "Gone", status="Cancelled"),
]) + "\n"
REFUNDS = ("Creation Date,Currency,Order ID,Refund Amount,Refund Date,Reversal Reason,Reversal Status,Website\n"
           "2025-08-20T00:00:00Z,USD,111-1,10.00,2025-08-21T00:00:00Z,Unwanted,Completed,Amazon.com\n")
DIGITAL = "ASIN,Digital Order Item ID,Order ID,Product Name,Order Date\nB1,X1,D01-1,A Book,2025-08-01\n"


def items():
    return pd.concat([amazon.parse(ORDERS).tables["order_items"], amazon.parse(REFUNDS).tables["order_items"]],
                     ignore_index=True)


def line(i, date, payee, amount, account="card"):
    return {"txn_id": f"t{i}", "date": date, "account_id": account, "amount": amount, "payee_raw": payee,
            "category": "", "note": "", "era": "ledger", "source": "ofx"}


def test_order_history_keeps_only_what_matching_needs():
    result = amazon.parse(ORDERS)
    df = result.tables["order_items"]
    assert len(df) == 5  # the cancelled order is gone
    assert set(df["card"]) == {"1111", "", "9999"}
    assert "Address" not in result.record and "Elm St" not in result.record
    again = amazon.parse(result.record).tables["order_items"]  # the trimmed record reads back the same
    assert list(again["item_id"]) == list(df["item_id"])
    assert df["item_id"].is_unique


def test_refunds_and_unused_files():
    refund = amazon.parse(REFUNDS).tables["order_items"].iloc[0]
    assert (refund["kind"], refund["amount"], refund["date"]) == ("refund", 10.0, "2025-08-21")
    digital = amazon.parse(DIGITAL)
    assert digital.tables == {} and digital.record == ""


def test_shipments_orders_refunds_and_a_foreign_card():
    lines = pd.DataFrame([
        line(1, "2025-08-03", "AMAZON MKTPL*AB12", -30.00),   # shipment of spatula and socks: split
        line(2, "2025-08-06", "Amazon.com*CD34", -5.50),      # second shipment
        line(3, "2025-08-05", "Amazon.com*EF56", -12.00),     # Whole Foods
        line(4, "2025-08-05", "AMAZON MKTPL*GH78", -99.00),   # paid with a card that is not ours
        line(5, "2025-08-22", "AMAZON MKTPLACE PMTS", 10.00),  # refund
    ])
    found = explain.match_amazon(lines, items(), cards={"1111"})
    assert found["t1"] == [("clothing", -10.0), ("household", -20.0)]
    assert found["t2"] == [("household", -5.5)]
    assert found["t3"] == [("food", -12.0)]
    assert "t4" not in found
    assert found["t5"] == [("clothing", 10.0)]  # the socks came back


def test_a_whole_order_matches_when_no_shipment_does():
    lines = pd.DataFrame([line(1, "2025-08-06", "AMAZON MKTPL*AB12", -35.50)])
    assert explain.match_amazon(lines, items(), cards={"1111"})["t1"][-1][0] == "household"


def test_paypal_names_the_line_within_the_window():
    details = pd.DataFrame([
        {"detail_id": "d1", "date": "2025-08-01", "amount": "-25.00", "service": "paypal", "kind": "Express",
         "counterparty": "Lantern Cafe", "memo": "Dinner", "doc_id": ""},
        {"detail_id": "d2", "date": "2025-07-01", "amount": "-40.00", "service": "paypal", "kind": "Express",
         "counterparty": "Too Early", "memo": "", "doc_id": ""}])
    lines = pd.DataFrame([line(1, "2025-08-03", "PAYPAL *LANTERN", -25.00), line(2, "2025-08-03", "PAYPAL *X", -40.00),
                          line(3, "2025-08-03", "GROCER", -25.00)])
    assert explain.match_paypal(lines, details) == {"t1": "Lantern Cafe Dinner"}


def test_coverage_spans_each_service():
    details = pd.DataFrame({"service": ["paypal", "paypal"], "date": ["2025-01-05", "2025-09-30"]})
    assert explain.coverage(details, items()) == {"paypal": ("2025-01-05", "2025-09-30"),
                                                  "amazon": ("2025-08-02", "2025-08-21")}


def test_categorization_uses_what_was_matched():
    history = [dict(line(f"h{n}", "2023-05-01", f"GROCER {n}", -50.0), category="food", era="legacy")
               for n in range(5)] + [dict(line(f"k{n}", "2023-05-01", f"PUMP {n}", -30.0), category="petrol",
                                          era="legacy") for n in range(5)]
    lines = pd.DataFrame(history + [
        line(1, "2025-08-03", "AMAZON MKTPL*AB12", -30.00),
        line(2, "2025-08-03", "PAYPAL *LANTERN", -25.00),
        line(3, "2025-09-30", "AMAZON MKTPL*ZZ99", -7.00),    # after the export: waits
        line(4, "2025-08-10", "AMAZON MKTPL*YY88", -7.00),    # inside it, unmatched: asked like any line
        line(5, "2025-08-04", "APPLE.COM/BILL", -2.99),       # no Apple export yet: waits
    ])
    explained = {"payee": {"t2": "Lantern Cafe Dinner"},
                 "split": {"t1": [("clothing", -10.0), ("household", -20.0)]},
                 "coverage": {"amazon": ("2025-08-02", "2025-08-21")}}
    decisions = pd.DataFrame([{"proposal_key": "payee:LANTERN CAFE DINNER", "kind": "category",
                               "decided_at": "2026-01-01", "decision": "approve", "note": "restaurant"}])
    out = categorize.categorize(lines, decisions=decisions, explanations=explained).set_index("txn_id")
    assert out.loc["t1", ["category", "decided_by", "splits"]].tolist() == [
        "household", "amazon", "clothing -10.00; household -20.00"]
    assert out.loc["t2", ["category", "decided_by", "key"]].tolist() == ["restaurant", "your decision",
                                                                         "LANTERN CAFE DINNER"]
    assert out.loc["t3", "status"] == "waiting"
    assert out.loc["t4", "status"] == "open"
    assert out.loc["t5", ["status", "decided_by"]].tolist() == ["waiting", "waiting for apple"]

    actual = categorize.actual_rows(out.reset_index())
    t1 = actual[actual["txn_id"] == "t1"].sort_values("category")
    assert t1[["category", "amount"]].values.tolist() == [["clothing", -10.0], ["household", -20.0]]


def export_zip():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as z:
        z.writestr("Your Orders/Order History.csv", ORDERS)
        z.writestr("Your Orders/Refund Details.csv", REFUNDS)
        z.writestr("Your Orders/Digital Content Orders.csv", DIGITAL)
        z.writestr("Your Addresses/Addresses.csv", "Name,Address\nSomeone,1 Elm St\n")
    return buffer.getvalue()


def test_the_export_zip_is_trimmed_filed_and_trashed(setup):
    drive, ledger, box, record = setup
    data = export_zip()
    drive.add("Your Orders.zip", box, "application/zip", data)
    [summary] = inbox.process(ledger, drive, drive.root)
    assert summary["status"] == "ingested", summary
    got = rows(ledger, "order_items")
    assert len(got) == 6 and set(got["kind"]) == {"item", "refund"}
    [filed] = [f for f in drive.items.values() if f["parent"] == record]
    assert filed["name"].endswith(".csv") and b"Elm St" not in drive.content[filed["id"]]
    assert [f["name"] for f in drive.items.values() if f["parent"] == "trash"] == ["Your Orders.zip"]
    doc = rows(ledger, "source_documents").iloc[0]
    assert doc["drive_link"].endswith(filed["id"]) and doc["parser"] == "amazon"

    drive.add("Your Orders (1).zip", box, "application/zip", data)  # sent again
    [again] = inbox.process(ledger, drive, drive.root)
    assert again["status"] == "duplicate"
    assert sorted(f["name"] for f in drive.items.values() if f["parent"] == "trash") == [
        "Your Orders (1).zip", "Your Orders.zip"]


def test_a_loose_order_history_is_trimmed_too(setup):
    drive, ledger, box, record = setup
    drive.add("Order History.csv", box, "text/csv", ORDERS.encode())
    [summary] = inbox.process(ledger, drive, drive.root)
    assert summary["status"] == "ingested"
    assert [f["name"] for f in drive.items.values() if f["parent"] == "trash"] == ["Order History.csv"]


def test_a_zip_nobody_reads_stays(setup):
    drive, ledger, box, record = setup
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as z:
        z.writestr("notes.csv", "a,b\n1,2\n")
    drive.add("other.zip", box, "application/zip", buffer.getvalue())
    [summary] = inbox.process(ledger, drive, drive.root)
    assert summary["status"] == "unrecognized"


@pytest.fixture
def setup(monkeypatch):
    from test_inbox import FakeDrive
    from test_sheets import FakeSpreadsheet
    from household import sheets

    drive, ledger = FakeDrive(), FakeSpreadsheet("ledger")
    sheets.ensure_tables(ledger, schema.LEDGER)
    root = drive.folder("household_finances")
    box = drive.folder("inbox", root)
    record = drive.folder("record_store", root)
    monkeypatch.setattr(inbox, "download", lambda d, fid: d.content[fid])
    drive.root = root
    return drive, ledger, box, record
