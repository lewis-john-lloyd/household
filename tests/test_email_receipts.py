"""Store receipts from email, as the exporter writes them (made-up receipts in tests/fixtures)."""

import re
from pathlib import Path

import pandas as pd
import pytest

from household import categorize, explain
from household.parsers import ParseError, email_receipts, identify

RECEIPTS = (Path(__file__).parent / "fixtures" / "receipts.txt").read_text()


def line(i, date, payee, amount):
    return {"txn_id": f"t{i}", "date": date, "account_id": "card", "amount": amount, "payee_raw": payee,
            "category": "", "note": "", "era": "ledger", "source": "ofx"}


def test_a_co_op_receipt_reads_item_by_item_with_tax_and_coupon_shared_out():
    assert identify(RECEIPTS) is email_receipts
    result = email_receipts.parse(RECEIPTS)
    df = result.tables["order_items"]
    assert result.notes == ["receipts: 1 read, 8 items",
                            "receipts: 1 from example.com left for a reader not written yet"]
    assert round(df["amount"].sum(), 2) == 54.50  # what the card was charged
    by = dict(zip(df["item"], df["department"]))
    assert (by["Amber Ale 6x12 fl oz"], by["Jigsaw Puzzle 500 pc"], by["Beeswax Candle"]) == (
        "alcohol", "toys & games", "")
    assert (by["Rolled Oats 32 oz"], by["Dark Chocolate Bar 3 oz"], by["NA Ginger Brew 4x12 fl oz"]) == (
        "grocery", "grocery", "")
    oats = df[df["item"] == "Rolled Oats 32 oz"]["amount"].item()
    ale = df[df["item"] == "Amber Ale 6x12 fl oz"]["amount"].item()
    assert ale / oats > 10.0 / 4.0  # taxed items carry the tax, untaxed ones don't
    assert set(df["date"]) == {"2026-03-04"} and set(df["website"]) == {"Willow St Grocery Cooperative"}


def test_a_receipt_that_does_not_add_up_is_refused():
    with pytest.raises(ParseError, match="Net Sales"):
        email_receipts.parse(re.sub(r"(Net Sales \S)53\.50", r"\g<1>53.00", RECEIPTS))


def test_a_co_op_charge_is_split_into_food_booze_family_and_household():
    orders = email_receipts.parse(RECEIPTS).tables["order_items"]
    lines = pd.DataFrame([line(1, "2026-03-05", "WILLOW ST CO-OP", -54.50)])
    parameters = pd.DataFrame([{"jurisdiction": "household", "year": "", "key": "order_payee:co_op",
                                "value": "WILLOW ST"}])
    services = explain.order_services(parameters)
    assert services["co_op"] == "WILLOW ST" and "amazon" in services
    explained = explain.explain(lines, items=orders, services=services)
    out = categorize.categorize(lines, explanations=explained).set_index("txn_id")
    parts = dict(p.rsplit(" ", 1) for p in out.loc["t1", "splits"].split("; "))
    assert set(parts) == {"booze", "family", "food", "household"}
    assert round(sum(float(v) for v in parts.values()), 2) == -54.50
    assert out.loc["t1", "decided_by"] == "co_op"
    assert "t1" not in explain.explain(lines, items=orders)["split"]  # without the parameter, no store to match
