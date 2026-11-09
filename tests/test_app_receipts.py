"""App store receipts from email (Apple's two layouts, Google Play), as the exporter writes them, and how
they name the card lines. Made-up receipts in the layouts' shapes."""

import re
from pathlib import Path

import pandas as pd

from household import categorize, explain
from household.parsers import email_receipts, google_takeout, identify


FILE = (Path(__file__).parent / "fixtures" / "app_receipts.txt").read_text()


def only(*ids):
    """The fixture's receipts with these message ids."""
    parts = re.split(r"(?m)^(?====== RECEIPT )", FILE)
    return "".join(p for p in parts if any(p.startswith(f"===== RECEIPT {i} =====") for i in ids))


def test_app_store_receipts_read_item_by_item_with_tax_shared_and_a_bad_one_left_out():
    assert identify(FILE) is email_receipts
    result = email_receipts.parse(FILE)
    df = result.tables["order_items"]
    old = df[(df["service"] == "apple") & (df["order_id"] == "AB1CD2EF3G")]
    # only the first copy is read; the tax goes to the items by amount, so they add to the charge
    assert old["item"].tolist() == ["Apple TV: Night Ferry", "Apple TV: Paper Moon"]
    assert (
        round(old["amount"].sum(), 2) == 21.40 and set(old["date"]) == {"2026-02-08"} and set(old["card"]) == {"1111"}
    )
    new = df[df["order_id"] == "HJ4KL5MN6P"]
    assert new[["item", "amount", "date", "card"]].values.tolist() == [
        ["Lantern Notes: Lantern Notes Pro (Monthly)", 5.25, "2026-03-02", "1111"]
    ]
    play = df[df["service"] == "google"].set_index("order_id")
    assert play.at["GPA.1..6", "item"] == (
        "Trails: Hike: Trail Map Peak Plus with Offline Maps and Weather Layers for Every Hike"
    )  # a name wrapped onto two lines, the app first
    assert (play.at["GPA.1..6", "amount"], play.at["GPA.1..6", "date"], play.at["GPA.1..6", "card"]) == (
        12.66,
        "2026-03-03",
        "1111",
    )
    assert play.at["SOP.2..0", "item"] == "Cloud Drive: Cloud Plus (2 TB)"  # "(by ...)" dropped
    # the third Apple receipt's item doesn't add to its Subtotal: it is left out with its id, the rest read
    offer = df[df["order_id"] == "XY1ZA2BC3D"]  # the Apple Card offer above the item is not the item
    assert offer[["item", "amount"]].values.tolist() == [["Apple One: Family (Monthly)", 21.00]]
    assert result.notes[0] == "receipts: 5 read, 6 items"
    assert any("a3: the items don't add to the Subtotal" in n for n in result.notes)


def test_a_play_receipt_and_takeout_key_one_purchase_alike_so_it_is_read_once():
    receipt = email_receipts.parse(only("g1")).tables["order_items"]
    takeout = google_takeout.parse(
        "Time,Transaction ID,Description,Product,Payment method,Status,Amount\n"
        '"Mar 3, 2026, 12:51 AM",GPA.1..6,Trail Map Peak (Trails: Hike),Google Play Apps,'
        '"Visa •••• 1111",Complete,USD 12.66\n'
    ).tables["order_items"]
    assert receipt["item_id"].tolist() == takeout["item_id"].tolist()


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


def test_an_apple_charge_is_named_by_its_orders_items_and_no_longer_waits():
    items = email_receipts.parse(only("a1", "a2")).tables["order_items"]
    lines = pd.DataFrame(
        [
            line(1, "2026-02-09", "APPLE.COM/BILL 866-712-7753 CA", -21.40),
            line(2, "2026-03-03", "APPLE.COM/BILL 866-712-7753 CA", -5.25),
            line(3, "2026-03-04", "APPLE.COM/BILL 866-712-7753 CA", -1.00),
        ]
    )
    found = explain.explain(lines, items=items)
    assert found["payee"] == {
        "t1": "Apple TV: Night Ferry; Paper Moon",
        "t2": "Lantern Notes: Lantern Notes Pro (Monthly)",
    }
    assert found["coverage"]["apple"] == ("2026-02-08", "2026-03-02")
    # inside the receipts' dates, a charge with no receipt is categorized like any other line, not kept waiting
    waits = categorize.categorize(lines.assign(category=""), pd.DataFrame(), pd.DataFrame(), explanations=found)
    assert "waiting" not in set(waits.loc[waits["txn_id"].isin(["t1", "t2"]), "status"])


def test_an_apple_charge_is_asked_about_as_apple_and_its_app_not_the_item():
    items = email_receipts.parse(only("a1", "a2", "a4")).tables["order_items"]
    # a name read before the offer was skipped is repaired when matched
    old = items.iloc[[0]].assign(
        item_id="old",
        order_id="OLD1",
        date="2026-04-20",
        amount=10.00,
        item="Save 3% on all your Apple purchases with Apple Card. 1 Apply and use in minutes. 2: Apple One",
    )
    items = pd.concat([items, old], ignore_index=True)
    lines = pd.DataFrame(
        [
            line(1, "2026-02-09", "APPLE.COM/BILL 866-712-7753 CA", -21.40),
            line(2, "2026-03-03", "APPLE.COM/BILL 866-712-7753 CA", -5.25),
            line(3, "2026-04-02", "APPLE.COM/BILL 866-712-7753 CA", -21.00),
            line(4, "2026-04-21", "APPLE.COM/BILL 866-712-7753 CA", -10.00),
        ]
    )
    found = explain.explain(lines, items=items)
    assert found["payee"]["t4"] == "Apple One"
    df = categorize.categorize(lines.assign(category=""), pd.DataFrame(), pd.DataFrame(), explanations=found)
    assert df.set_index("txn_id")["key"].to_dict() == {
        "t1": "APPLE TV",
        "t2": "APPLE LANTERN NOTES",
        "t3": "APPLE ONE",
        "t4": "APPLE ONE",
    }
    review = categorize.queue(df).set_index("key")
    assert review.at["APPLE ONE", "lines"] == 2
    assert review.at["APPLE TV", "example"] == "APPLE.COM/BILL 866-712-7753 CA (Apple TV: Night Ferry; Paper Moon)"
