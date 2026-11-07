"""A farm store's and an outfitter's emailed receipts, and PayPal's emails, as the exporter writes them, and the card
lines they explain. Made-up receipts in the layouts' shapes."""

import re
from pathlib import Path

import pandas as pd

from household import explain
from household.parsers import email_receipts, identify


FILE = (Path(__file__).parent / "fixtures" / "store_receipts.txt").read_text()


def only(*ids):
    """The fixture's receipts with these message ids."""
    parts = re.split(r"(?m)^(?====== RECEIPT )", FILE)
    return "".join(p for p in parts if any(p.startswith(f"===== RECEIPT {i} =====") for i in ids))


def test_store_receipts_read_item_by_item_and_paypal_emails_read_as_payment_details():
    assert identify(FILE) is email_receipts
    result = email_receipts.parse(FILE)
    items = result.tables["order_items"]
    farm = items[items["service"] == "farm_store"]
    # the price after the discount, the tax on the taxed item; untaxed is food, a donation is charity
    assert farm[["item", "amount", "department", "date", "website"]].values.tolist() == [
        ["Garden Hose", 21.0, "", "2026-05-02", "WILLOW ST"],
        ["Oats", 6.0, "grocery", "2026-05-02", "WILLOW ST"],
        ["Donation", 1.0, "charity", "2026-05-02", "WILLOW ST"]]
    store = items[items["order_id"] == "r1"]
    # rewards paid half: every item comes to half, adding to what the card was charged
    assert store[["item", "amount", "website"]].values.tolist() == [["Rain Jacket", 31.5, "ACME GROCER"],
                                                                    ["Wool Socks", 10.5, "ACME GROCER"]]
    online = items[items["order_id"] == "A100"]
    # an online order: the rewards come off the first item, as its parts are charged
    assert online[["item", "amount", "date"]].values.tolist() == [["Camp Stove (Blue)", 38.5, "2026-08-18"],
                                                                  ["Tent Stakes", 31.5, "2026-08-18"]]
    paid = result.tables["payment_details"].set_index("counterparty")
    # the merchant's receipt and its authorization are one payment (one transaction id)
    assert len(paid) == 4 and paid.at["QUILL & INK Co.", "amount"].tolist() == [-12.5, 12.5]
    assert paid.at["PAGE TURNER BOOKS", "memo"] == "Atlas of Rivers, Second Edition"
    assert (paid.at["sam", "amount"], paid.at["sam", "memo"], paid.at["sam", "date"]) == (
        40.0, "Half of the camping gear", "2026-09-12")
    assert result.notes[:2] == ["receipts: 3 read, 7 items", "PayPal: 4 payments and transfers"]
    # a return and an order paid in full with rewards charged no card
    assert "receipts: 2 that charged nothing (a return, a free order) left out" in result.notes


def line(i, date, payee, amount):
    return {"txn_id": f"t{i}", "date": date, "account_id": "card", "amount": amount, "payee_raw": payee,
            "category": "", "note": "", "era": "ledger", "source": "ofx"}


def test_the_card_lines_match_a_receipt_each_and_an_order_charged_in_parts_matches_item_by_item():
    result = email_receipts.parse(FILE)
    parameters = pd.DataFrame([{"jurisdiction": "household", "key": "order_payee:farm_store", "value": "WILLOW ST"},
                               {"jurisdiction": "household", "key": "order_payee:outfitter", "value": "ACME GROCER"}])
    services = explain.order_services(parameters)
    lines = pd.DataFrame([line(1, "2026-05-04", "WILLOW ST 01", -28.00),
                          line(2, "2026-06-08", "ACME GROCER #1", -42.00),
                          line(3, "2026-08-19", "ACME GROCER.COM", -38.50),
                          line(4, "2026-08-20", "ACME GROCER.COM", -31.50),
                          line(5, "2026-09-02", "PAYPAL *QUILLINK", -12.50),
                          line(6, "2026-09-11", "PAYPAL *QUILLINK", 12.50),
                          line(7, "2026-09-04", "PAYPAL *PAGETURNER", -30.00)])
    found = explain.explain(lines, details=result.tables["payment_details"], items=result.tables["order_items"],
                            services=services)
    assert found["split"]["t1"] == [("charity", -1.0), ("food", -6.0), ("household", -21.0)]
    assert found["split"]["t2"] == [("household", -42.0)]
    assert found["split"]["t3"] == [("household", -38.5)] and found["split"]["t4"] == [("household", -31.5)]
    assert "Camp Stove (Blue)" in found["items"]["t3"] and "Tent Stakes" in found["items"]["t4"]
    assert found["payee"] == {"t5": "QUILL & INK Co.", "t6": "QUILL & INK Co.",
                              "t7": "PAGE TURNER BOOKS Atlas of Rivers, Second Edition"}


def test_an_order_charged_whole_still_matches_as_one_and_its_items_are_not_matched_again():
    items = email_receipts.parse(only("r2")).tables["order_items"]
    lines = pd.DataFrame([line(1, "2026-08-19", "ACME GROCER.COM", -70.00),
                          line(2, "2026-08-20", "ACME GROCER.COM", -31.50)])
    found = explain.explain(lines, items=items, services={"outfitter": "ACME GROCER"})
    assert list(found["split"]) == ["t1"]
