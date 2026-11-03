"""Amazon's export, explanation matching, and how matches reach categorization."""

import io
import json
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


def test_what_a_matched_charge_bought_goes_along():
    lines = pd.DataFrame([line(1, "2025-08-03", "AMAZON MKTPL*AB12", -30.00),
                          line(5, "2025-08-22", "AMAZON MKTPLACE PMTS", 10.00)])
    contents = {}
    explain.match_amazon(lines, items(), cards={"1111"}, contents=contents)
    assert sorted(c for c, _, _ in contents["t1"]) == ["clothing", "household"]
    assert sum(a for _, _, a in contents["t1"]) == 30.0
    assert [(c, a) for c, _, a in contents["t5"]] == [("clothing", 10.0)]
    out = explain.explain(lines, items=items(), cards={"1111"})
    assert json.loads(out["items"]["t1"]) == [list(i) for i in contents["t1"]]


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
                 "coverage": {"amazon": ("2025-08-02", "2025-08-21")},
                 "items": {"t1": '[["household", "Spatula", 20.0], ["clothing", "Socks", 10.0]]'}}
    decisions = pd.DataFrame([{"proposal_key": "payee:LANTERN CAFE DINNER", "kind": "category",
                               "decided_at": "2026-01-01", "decision": "approve", "note": "restaurant"}])
    out = categorize.categorize(lines, decisions=decisions, explanations=explained).set_index("txn_id")
    assert out.loc["t1", ["category", "decided_by", "splits"]].tolist() == [
        "household", "amazon", "clothing -10.00; household -20.00"]
    assert out.loc["t2", ["category", "decided_by", "key"]].tolist() == ["restaurant", "your decision",
                                                                         "LANTERN CAFE DINNER"]
    assert out.loc["t3", "status"] == "waiting"
    assert out.loc["t4", ["status", "ask", "decided_by"]].tolist() == ["open", "line", "amazon, no matching order"]
    assert out.loc["t5", ["status", "decided_by"]].tolist() == ["waiting", "waiting for apple"]
    assert json.loads(out.loc["t1", "items"])[0] == ["household", "Spatula", 20.0]
    assert out.loc["t2", "items"] == ""

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


GOOGLE = ("Time,Transaction ID,Description,Product,Payment method,Status,Amount\n"
          '"Oct 21, 2025, 10:51 PM",GPA.1..6,Pro Plan (Lantern Notes),Google Play Apps,'
          '"Visa •••• 1111",Complete,USD 21.10\n'
          '"Oct 17, 2025, 12:03 PM",GPA.2,Trail Map Peak (Trails: Hike),Google Play Apps,'
          '"Visa •••• 1111",Complete,USD 84.39\n'
          '"Oct 18, 2025, 9:00 AM",GPA.2,Trail Map Peak (Trails: Hike),Google Play Apps,'
          '"Visa •••• 1111",Complete,−USD 84.39\n'
          '"Oct 1, 2025, 9:00 AM",GPA.3,Declined thing,Google Play Apps,,Declined,USD 5.00\n')


def test_google_purchases_and_matching():
    from household.parsers import google_takeout, identify
    assert identify(GOOGLE) is google_takeout
    result = google_takeout.parse(GOOGLE)
    items = result.tables["order_items"]
    assert items[["kind", "amount", "card", "item"]].values.tolist() == [
        ["item", 21.1, "1111", "Lantern Notes: Pro Plan"],
        ["item", 84.39, "1111", "Trails: Hike: Trail Map Peak"],
        ["refund", 84.39, "1111", "Trails: Hike: Trail Map Peak"]]
    assert (result.covers_from, result.covers_to) == ("2025-10-17", "2025-10-21")
    lines = pd.DataFrame([line(1, "2025-10-22", "GOOGLE *Lantern", -21.10), line(2, "2025-10-20", "GOOGLE *Trails", 84.39),
                          line(3, "2025-10-22", "GOOGLE LLC PAYROLL", 21.10)])
    assert explain.match_google(lines, items) == {"t1": "Lantern Notes: Pro Plan",
                                                  "t2": "Trails: Hike: Trail Map Peak"}


def takeout_zip(members):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as z:
        for name, text in members.items():
            z.writestr(name, text)
    return buffer.getvalue()


def test_takeout_parts_are_trimmed_and_trashed(setup):
    drive, ledger, box, record = setup
    drive.add("takeout-001.zip", box, "application/zip", takeout_zip({
        "Takeout/Google Pay/Google transactions/transactions_1.csv": GOOGLE,
        "Takeout/Google Play Store/Order History.json": '[{"billingContact": "1 Elm St"}]'}))
    drive.add("takeout-1-001.zip", box, "application/zip", takeout_zip({"Takeout/archive_browser.html": "<html/>"}))
    summaries = inbox.process(ledger, drive, drive.root)
    assert [s["status"] for s in summaries] == ["ingested", "ingested"], summaries
    assert len(rows(ledger, "order_items")) == 3
    assert sorted(f["name"] for f in drive.items.values() if f["parent"] == "trash") == [
        "takeout-001.zip", "takeout-1-001.zip"]
    [filed] = [f for f in drive.items.values() if f["parent"] == record]
    assert b"Elm St" not in drive.content[filed["id"]]


def test_unmatched_amazon_charges_get_hints():
    lines = pd.DataFrame([line(1, "2025-08-05", "AMAZON MKTPL*ZZ", -24.00), line(2, "2025-08-03", "AMAZON MKTPL*AB", -30.00)])
    hints = explain.amazon_hints(lines, items(), matched={"t2"})
    assert list(hints) == ["t1"]
    assert hints["t1"].startswith("maybe: Spatula (20.00, 2025-08-02)")


def test_any_store_with_an_order_history_is_itemized_like_amazon(monkeypatch):
    monkeypatch.setitem(explain.ORDER_SERVICES, "quill", "QUILL")
    quill = pd.DataFrame([
        {"item_id": "q1", "service": "quill", "order_id": "Q-1", "kind": "item", "order_date": "2025-08-01",
         "date": "2025-08-01", "amount": "8.00", "card": "", "department": "", "item": "Fountain pen", "website": "",
         "doc_id": ""},
        {"item_id": "q2", "service": "quill", "order_id": "Q-1", "kind": "item", "order_date": "2025-08-01",
         "date": "2025-08-01", "amount": "4.00", "card": "", "department": "grocery", "item": "Tea", "website": "",
         "doc_id": ""}])
    lines = pd.DataFrame([line(1, "2025-08-03", "QUILL & INK", -12.00),
                          line(2, "2025-08-03", "AMAZON MKTPL*AB12", -30.00)])
    out = explain.explain(lines, items=pd.concat([items(), quill], ignore_index=True), cards={"1111"})
    assert out["split"]["t1"] == [("food", -4.0), ("household", -8.0)]
    assert [i[1] for i in json.loads(out["items"]["t1"])] == ["Fountain pen", "Tea"]
    assert out["split"]["t2"] == [("clothing", -10.0), ("household", -20.0)]  # Amazon goes on as before


@pytest.mark.parametrize("ask", ["TRUE", "FALSE"])
def test_an_order_history_decides_ahead_of_any_payee_rule(ask):
    lines = pd.DataFrame([line(1, "2025-08-03", "QUILL & INK", -12.00), line(2, "2025-08-04", "QUILL & INK", -5.00)])
    rules = pd.DataFrame([{"pattern": "QUILL", "category": "household", "precedence": 10, "effective": "",
                           "ask": ask}])
    explained = {"split": {"t1": [("food", -4.0), ("household", -8.0)]}}
    out = categorize.categorize(lines, rules, explanations=explained).set_index("txn_id")
    assert out.loc["t1", ["status", "splits"]].tolist() == ["decided", "food -4.00; household -8.00"]
    # nothing explains the other line: the rule has it, asking each time or deciding
    expected = ["open", "line"] if ask == "TRUE" else ["decided", ""]
    assert out.loc["t2", ["status", "ask"]].tolist() == expected


TARGET = ("target_order_id,date,store,order_total,item,price,qty,returned\n"
          "A-1,2025-08-02,Willow St,21.80,Girls' Denim Jeans - Blue 14,12.00,1,\n"
          "A-1,2025-08-02,Willow St,21.80,Kids' Rain Boots - Red 1,4.00,1,\n"
          "A-1,2025-08-02,Willow St,21.80,Dish Soap - 16oz,2.00,2,\n"
          "B-2,2025-08-09,,9.00,Spatula,9.00,1,\n")


def test_a_target_purchase_list_reads_as_orders_with_tax_shared_out():
    from household.parsers import identify, target
    assert identify(TARGET) is target
    df = target.parse(TARGET).tables["order_items"]
    a = df[df["order_id"] == "A-1"]
    assert round(a["amount"].sum(), 2) == 21.80  # the order total, tax included
    assert a["department"].tolist() == ["apparel", "shoes", ""]
    assert a["item"].tolist()[-1] == "Dish Soap - 16oz (x2)"
    assert df[df["order_id"] == "B-2"]["website"].item() == "target.com"


def test_a_target_charge_is_itemized_ahead_of_its_payee_rule():
    from household.parsers import target
    orders = pd.concat([items(), target.parse(TARGET).tables["order_items"]], ignore_index=True)
    lines = pd.DataFrame([line(1, "2025-08-03", "TARGET 0042", -21.80),
                          line(2, "2025-08-20", "TARGET 0042", -7.00)])
    rules = pd.DataFrame([{"pattern": r"\bTARGET\b", "category": "household", "precedence": 10, "effective": "",
                           "ask": "FALSE"}])
    explained = explain.explain(lines, items=orders, cards={"1111"})
    out = categorize.categorize(lines, rules, explanations=explained).set_index("txn_id")
    assert out.loc["t1", ["decided_by", "category"]].tolist() == ["target", "clothing"]
    assert out.loc["t1", "splits"] == "clothing -17.44; household -4.36"  # 12 + 4 of 20, scaled to 21.80
    assert out.loc["t2", ["decided_by", "category"]].tolist() == ["rule", "household"]


def test_a_free_target_order_is_left_out():
    from household.parsers import target
    text = TARGET + "C-3,2025-08-10,,0.00,Replacement part,0.00,1,\n"
    assert set(target.parse(text).tables["order_items"]["order_id"]) == {"A-1", "B-2"}
