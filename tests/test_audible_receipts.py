"""Audible order emails, as the exporter writes them, and how they name the card lines. Made-up orders in
the layouts' shapes: the plain one (each title, then its price or credit on the next line, or both on one)
and the one reduced from a table (cells kept on one line)."""

import json
import re
from pathlib import Path

import pandas as pd

from household import categorize, explain, recurring
from household.parsers import email_receipts, identify

FILE = (Path(__file__).parent / "fixtures" / "audible_receipts.txt").read_text()


def only(*ids):
    """The fixture's orders with these message ids."""
    parts = re.split(r"(?m)^(?====== RECEIPT )", FILE)
    return "".join(p for p in parts if any(p.startswith(f"===== RECEIPT {i} =====") for i in ids))


def test_audible_orders_read_their_priced_titles_with_tax_and_credits_charge_nothing():
    assert identify(FILE) is email_receipts
    result = email_receipts.parse(FILE)  # m4 is m3 sent again
    df = result.tables["order_items"].drop_duplicates("item_id")
    assert df[["order_id", "item", "amount", "date", "department"]].values.tolist() == [
        ["D01-3333333-4444444", "Audible Premium Plus - 2 Credits", 21.00, "2026-03-02", "audiobooks"],
        ["D01-5555555-6666666", "Paper Lanterns", 6.30, "2026-03-20", "audiobooks"],
    ]  # the order sent twice is one order; the title paid with a credit is no row
    assert "receipts: 1 that charged nothing (a return, a free order) left out" in result.notes


def test_an_audible_order_that_does_not_add_up_is_left_out_with_its_id():
    result = email_receipts.parse(re.sub(r"(Paper Lanterns \S)6", r"\g<1>5", only("m3")))
    assert "order_items" not in result.tables
    assert any("audible m3: the titles add to 5.00, Subtotal is 6.00" in n for n in result.notes)


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


def test_an_audible_purchase_is_media_by_its_order_and_a_renewal_waits_for_nothing():
    items = email_receipts.parse(only("m2", "m3")).tables["order_items"]
    lines = pd.DataFrame(
        [
            line(1, "2026-03-03", "Audible*AB12CD34", -21.00),
            line(2, "2026-03-21", "Audible*EF56GH78", -6.30),
            line(3, "2026-04-03", "Audible*IJ90KL12", -21.00),
        ]
    )
    contents = {}
    split = explain.match_all_orders(lines, items, [], contents)
    assert split == {"t1": [("media", -21.00)], "t2": [("media", -6.30)]}  # the renewal has no order email
    assert [i[1] for i in contents["t2"]] == ["Paper Lanterns"]
    assert categorize._waiting("AUDIBLE*IJ90KL12") == ""  # no longer held for Amazon's history


def test_a_purchase_its_order_itemizes_is_not_a_renewal_but_a_membership_is():
    def charge(i, date, amount, items=""):
        return {**line(i, date, "Audible*X", amount), "key": "AUDIBLE", "category": "media", "items": items}

    book = json.dumps([["media", "Paper Lanterns", 6.30, "x1"]])
    plan = json.dumps([["media", "Audible Premium Plus - 2 Credits", 21.00, "x2"]])
    df = pd.DataFrame(
        [charge(1, "2026-03-03", -21.00, plan)]
        + [charge(i, f"2026-0{i}-03", -21.00) for i in range(4, 8)]
        + [charge(9, "2026-07-10", -6.30, book)]
    )
    assert recurring._charges(df)["txn_id"].tolist() == ["t1", "t4", "t5", "t6", "t7"]
    subs = recurring.discover(df, today="2026-07-20")
    assert subs[["key", "latest", "status"]].values.tolist() == [["AUDIBLE", 21.00, "active"]]
