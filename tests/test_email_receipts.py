"""Store receipts from email, as the exporter writes them (made-up receipts in tests/fixtures)."""

import re
from pathlib import Path

import pandas as pd
import pytest

from household import categorize, explain
from household.parsers import ParseError, email_receipts, identify

RECEIPTS = (Path(__file__).parent / "fixtures" / "receipts.txt").read_text()


def line(i, date, payee, amount):
    return {
        "txn_id": f"t{i}",
        "date": date,
        "account_id": "card",
        "amount": amount,
        "payee_raw": payee,
        "category": "",
        "note": "",
        "era": "ledger",
        "source": "ofx",
    }


def test_a_co_op_receipt_reads_item_by_item_with_tax_and_coupon_shared_out():
    assert identify(RECEIPTS) is email_receipts
    result = email_receipts.parse(RECEIPTS)
    df = result.tables["order_items"]
    assert result.notes == [
        "receipts: 5 read, 18 items",
        "receipts: 1 from example.com left for a reader not written yet",
        "receipts: 1 bookseller charges for several items without prices left as one charge",
    ]
    df = df[df["order_id"] == "m001"]
    assert round(df["amount"].sum(), 2) == 54.50  # what the card was charged
    by = dict(zip(df["item"], df["department"]))
    assert (by["Amber Ale 6x12 fl oz"], by["Jigsaw Puzzle 500 pc"], by["Beeswax Candle"]) == (
        "alcohol",
        "toys & games",
        "",
    )
    assert (by["Rolled Oats 32 oz"], by["Dark Chocolate Bar 3 oz"], by["NA Ginger Brew 4x12 fl oz"]) == (
        "grocery",
        "grocery",
        "",
    )
    oats = df[df["item"] == "Rolled Oats 32 oz"]["amount"].item()
    ale = df[df["item"] == "Amber Ale 6x12 fl oz"]["amount"].item()
    assert ale / oats > 10.0 / 4.0  # taxed items carry the tax, untaxed ones don't
    assert set(df["date"]) == {"2026-03-04"} and set(df["website"]) == {"Willow St Grocery Cooperative"}


def test_a_receipt_that_does_not_add_up_is_refused():
    with pytest.raises(ParseError, match="Net Sales"):
        email_receipts.parse(re.sub(r"(Net Sales \S)53\.50", r"\g<1>43.50", RECEIPTS))  # past the coupons


def test_a_co_op_charge_is_split_into_food_booze_family_and_household():
    orders = email_receipts.parse(RECEIPTS).tables["order_items"]
    lines = pd.DataFrame([line(1, "2026-03-05", "WILLOW ST CO-OP", -54.50)])
    parameters = pd.DataFrame(
        [{"jurisdiction": "household", "year": "", "key": "order_payee:co_op", "value": "WILLOW ST"}]
    )
    services = explain.order_services(parameters)
    assert services["co_op"] == "WILLOW ST" and "amazon" in services
    explained = explain.explain(lines, items=orders, services=services)
    out = categorize.categorize(lines, explanations=explained).set_index("txn_id")
    parts = dict(p.rsplit(" ", 1) for p in out.loc["t1", "splits"].split("; "))
    assert set(parts) == {"booze", "family", "food", "household"}
    assert round(sum(float(v) for v in parts.values()), 2) == -54.50
    assert out.loc["t1", "decided_by"] == "co_op"
    assert "t1" not in explain.explain(lines, items=orders)["split"]  # without the parameter, no store to match


def bookseller(df):
    return df[df["service"] == "bookseller"].set_index("item")


def test_a_bookseller_register_receipt_reads_each_item_at_its_price_after_discounts_with_tax():
    df = bookseller(email_receipts.parse(RECEIPTS).tables["order_items"])
    store = df[df["order_id"] == "m003"]
    assert store["amount"].to_dict() == {
        "Quiet River: A Novel": 17.85,
        "Brick Castle Building Set": 37.80,
        "Ocean Jigsaw Puzzle 500pc": 15.75,
    }
    assert store["department"].to_dict() == {
        "Quiet River: A Novel": "books",
        "Brick Castle Building Set": "toys & games",
        "Ocean Jigsaw Puzzle 500pc": "toys & games",
    }
    assert set(store["date"]) == {"2026-03-07"} and set(store["website"]) == {"Page Turner Books #0042"}


def test_a_bookseller_billing_summary_in_html_reads_its_one_item_at_what_was_charged():
    df = bookseller(email_receipts.parse(RECEIPTS).tables["order_items"])
    online = df[df["order_id"] == "m004"]
    assert online["amount"].to_dict() == {"Garden Birds Field Guide": 31.50}
    assert online["department"].to_dict() == {"Garden Birds Field Guide": "books"}
    assert "m005" not in set(df["order_id"])  # several items, no prices: left as one charge


def test_a_bookseller_receipt_whose_items_do_not_add_up_is_refused():
    with pytest.raises(ParseError, match="Subtotal is 68.00"):
        email_receipts.parse(RECEIPTS.replace("(1 @ 15.00) 15.00", "(1 @ 15.00) 14.00"))


def test_a_bookseller_charge_splits_into_media_and_family():
    orders = email_receipts.parse(RECEIPTS).tables["order_items"]
    lines = pd.DataFrame([line(1, "2026-03-08", "PAGE TURNER BOOKS", -71.40)])
    parameters = pd.DataFrame(
        [{"jurisdiction": "household", "year": "", "key": "order_payee:bookseller", "value": "PAGE TURNER"}]
    )
    explained = explain.explain(lines, items=orders, services=explain.order_services(parameters))
    out = categorize.categorize(lines, explanations=explained).set_index("txn_id")
    parts = dict(p.rsplit(" ", 1) for p in out.loc["t1", "splits"].split("; "))
    assert parts == {"family": "-53.55", "media": "-17.85"}


def test_a_co_op_void_cancels_the_next_priced_entry_and_charity_is_its_own_item():
    df = email_receipts.parse(RECEIPTS).tables["order_items"]
    got = df[df["order_id"] == "m006"]
    # the void passes over the weight line and cancels one bunch of apples; the other void cancels a bag credit
    assert got["item"].tolist() == ["Popcorn 6 oz", "Apples bulk", "Coffee 12 fl oz", "CHIP", "Charity"]
    assert dict(zip(got["item"], got["department"]))["Charity"] == "charity"
    assert round(got["amount"].sum(), 2) == 11.54  # a store coupon off the totals is shared out, not refused


def test_an_online_summary_reads_a_name_and_quantity_on_separate_lines_and_a_membership():
    df = email_receipts.parse(RECEIPTS).tables["order_items"]
    got = df[df["order_id"] == "m007"]
    assert got[["item", "amount", "department"]].values.tolist() == [["Premium Membership", 42.0, "membership"]]
    assert explain.DEPARTMENTS["membership"] == "subscriptions"


def test_a_message_the_exporter_could_not_read_is_noted_and_reads_no_rows():
    text = (
        "===== RECEIPT m101 =====\nUnreadable: TypeError: Cannot read properties of null (reading 'headers')\n"
        "Date: 2026-03-01T10:00:00.000Z\n\n\n"
    )
    assert identify(text) is email_receipts
    result = email_receipts.parse(RECEIPTS + "\n" + text)
    assert len(result.tables["order_items"]) == len(email_receipts.parse(RECEIPTS).tables["order_items"])
    assert result.notes[-1] == (
        "receipts: 1 the exporter could not read, tried again on its every run (look in Gmail "
        "by id): m101 (TypeError: Cannot read properties of null (reading 'headers'))"
    )
    alone = email_receipts.parse(text)
    assert alone.tables == {} and "1 the exporter could not read" in alone.notes[-1]
