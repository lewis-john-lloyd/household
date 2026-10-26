"""Amazon's order history export ("Request your data" > Your Orders).

The export is a zip of many files, most of which the household has no use for
(addresses, tracking numbers, gift messages). Two are read:

  Order History.csv   one row per item per shipment: order id and date, ship
                      date, the item's Total Amount (price, tax and shipping,
                      less discounts), department, product name, website
                      (Amazon.com or wholefoods.com) and the card it was paid
                      with ("Visa - 1234")
  Refund Details.csv  money returned: order id, refund date and amount

Each becomes order_items rows, and the trimmed rows (only these columns) are
what is filed as the record: the inbox files `record` in place of the
original and moves the original to the trash. A trimmed file dropped in the
inbox again reads back the same rows, so it is a duplicate.

The other files in the export (digital orders and returns among them) are
recognized and left out.

Cancelled orders are dropped. The card column keeps the last four digits only;
matching skips orders paid with a card that is not one of the household's.
"""

import csv
import hashlib
import io
import re

import pandas as pd

from .common import ParseError, Result

NAME = "amazon"
KIND = "amazon_orders"
SERVICE = "amazon"
COLUMNS = ["item_id", "service", "order_id", "kind", "order_date", "date", "amount", "card", "department",
           "item", "website"]
ORDERS = {"Order ID", "Order Date", "Ship Date", "Total Amount", "Payment Method Type", "Product Name",
          "Website", "Order Status", "Department"}
REFUNDS = {"Order ID", "Refund Amount", "Refund Date", "Reversal Status"}
DIGITAL = {"Digital Order Item ID", "Order ID", "Product Name"}  # digital orders and returns: not used
CARD = re.compile(r"(\d{4})\s*$")


def _header(text):
    first = text.lstrip("﻿").splitlines()[0] if text.strip() else ""
    return [c.strip().strip('"') for c in next(csv.reader([first]))] if first else []


def _shape(text):
    header = _header(text)
    columns = set(header)
    if header == COLUMNS:
        return "trimmed"
    if ORDERS <= columns:
        return "orders"
    if REFUNDS <= columns:
        return "refunds"
    if DIGITAL <= columns:
        return "digital"
    return None


def sniff(text):
    return _shape(text) is not None


def _rows(text):
    return pd.DataFrame(list(csv.DictReader(io.StringIO(text.lstrip("﻿")))), dtype=str).fillna("")


def _money(series, what):
    values = pd.to_numeric(series.str.replace(",", "", regex=False), errors="coerce")
    if values.isna().any():
        raise ParseError(f"{NAME}: {what} not a number: {series[values.isna()].iloc[0]!r}")
    return values.round(2)


def _card(method):
    found = CARD.search(str(method))
    if found:
        return found.group(1)
    return "gift" if "gift" in str(method).lower() else ""


def _ids(df):
    key = df["order_id"] + "|" + df["kind"] + "|" + df["date"] + "|" + df["item"] + "|" + df["amount"].map("{:.2f}".format)
    n = key.groupby(key).cumcount().astype(str)
    return [hashlib.sha256(f"{k}|{i}".encode()).hexdigest()[:16] for k, i in zip(key, n)]


def _orders(raw):
    raw = raw[raw["Order Status"].str.strip().str.lower() != "cancelled"]
    ship = raw["Ship Date"].str[:10]
    order_date = raw["Order Date"].str[:10]
    df = pd.DataFrame({
        "service": SERVICE, "order_id": raw["Order ID"].str.strip(), "kind": "item",
        "order_date": order_date,
        "date": ship.where(ship.str.match(r"\d{4}-\d{2}-\d{2}"), order_date),
        "amount": _money(raw["Total Amount"], "Total Amount"),
        "card": raw["Payment Method Type"].map(_card),
        "department": raw["Department"].str.strip().replace({"Not Available": ""}),
        "item": raw["Product Name"].str.strip(), "website": raw["Website"].str.strip(),
    })
    return df


def _refunds(raw):
    raw = raw[raw["Reversal Status"].str.strip().str.lower().isin(["completed", ""])]
    return pd.DataFrame({
        "service": SERVICE, "order_id": raw["Order ID"].str.strip(), "kind": "refund",
        "order_date": "", "date": raw["Refund Date"].str[:10],
        "amount": _money(raw["Refund Amount"], "Refund Amount"), "card": "", "department": "",
        "item": raw.get("Reversal Reason", pd.Series("", index=raw.index)).str.strip(),
        "website": raw.get("Website", pd.Series("", index=raw.index)).str.strip(),
    })


def parse(text):
    shape = _shape(text)
    raw = _rows(text)
    if shape == "trimmed":
        df = raw.copy()
        df["amount"] = _money(df["amount"], "amount")
    elif shape == "orders":
        df = _orders(raw)
    elif shape == "refunds":
        df = _refunds(raw)
    else:
        df = pd.DataFrame(columns=COLUMNS)
    if shape != "trimmed" and not df.empty:
        df["item_id"] = _ids(df)
    df = df.reindex(columns=COLUMNS).reset_index(drop=True)
    if df["item_id"].duplicated().any():
        raise ParseError(f"{NAME}: repeated item id")
    dates = sorted(d for d in df["date"] if d)
    record = "" if df.empty else df.to_csv(index=False)
    what = {"orders": "order items", "refunds": "refunds", "trimmed": "order rows",
            "digital": "digital orders (not used)"}[shape]
    return Result(kind=KIND, parser=NAME, label=f"amazon_{shape}_{dates[-1] if dates else 'none'}",
                  covers_from=dates[0] if dates else "", covers_to=dates[-1] if dates else "",
                  tables={"order_items": df} if len(df) else {}, keys={"order_items": ["item_id"]},
                  record=record, notes=[f"Amazon: {len(df)} {what}"])


def combine(results):
    """One result for the files read out of one export zip."""
    frames = [r.tables["order_items"] for r in results if "order_items" in r.tables]
    df = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=COLUMNS)
    dates = sorted(d for d in df["date"] if d)
    return Result(kind=KIND, parser=NAME, label=f"amazon_export_{dates[-1] if dates else 'none'}",
                  covers_from=dates[0] if dates else "", covers_to=dates[-1] if dates else "",
                  tables={"order_items": df} if len(df) else {}, keys={"order_items": ["item_id"]},
                  record="" if df.empty else df.to_csv(index=False),
                  notes=[n for r in results for n in r.notes])
