"""Target purchases: the household's Target account, store trips and online orders, item by item.

Target offers no export, so the file is made from the account's purchase
history (read in a browser signed in to it): one row per item, with columns
target_order_id, date, store, order_total, item, price, qty, returned. Each
order becomes order_items rows of service "target". The price is before tax
and discounts, so each item's amount is its share of the order's total (price
times quantity, scaled so the items add to the total, the cents left over on
the biggest), which is what the card was charged. Clothes and shoes say so in
their department (CLOTHES, SHOES), so they map to clothing the way Amazon's
departments do; everything else takes the default.

Only purchases linked to the account appear: online orders, and store trips
paid with a card or the Target Circle number the account knows. An online
order's invoices are its charges, so a file can list each invoice as its own
order (its id and date, the invoice total, and each item's total as the price),
and each charge then matches to the cent. An order that cost nothing is left out.
"""

import csv
import hashlib
import io
import re

import pandas as pd

from .amazon import COLUMNS
from .common import ParseError, Result

NAME = "target"
KIND = "target_purchases"
SERVICE = "target"
HEADER = ["target_order_id", "date", "store", "order_total", "item", "price", "qty", "returned"]
CLOTHES = re.compile(r"\b(jeans|pants|shorts|skort|shirt|t-shirt|tee|top|dress|pajama|socks|sports bra|hipster|"
                     r"boxer briefs|underwear|jacket|hoodie|sweater|hat|earmuff|rash guard|leggings|swim)\b", re.I)
SHOES = re.compile(r"\b(boots|sneakers|shoes|clogs|sandals|slippers)\b", re.I)


def sniff(text):
    first = text.lstrip("﻿").splitlines()[0] if text.strip() else ""
    return [c.strip() for c in next(csv.reader([first]))] == HEADER if first else False


def _department(item):
    return "shoes" if SHOES.search(item) else "apparel" if CLOTHES.search(item) else ""


def _shares(prices, total):
    """Each item's part of the order total, in cents, adding to it exactly."""
    whole = sum(prices)
    if whole <= 0:
        raise ParseError(f"{NAME}: an order with nothing priced")
    cents = [round(p * total / whole) for p in prices]
    cents[cents.index(max(cents))] += total - sum(cents)
    return cents


def parse(text):
    raw = pd.DataFrame(list(csv.DictReader(io.StringIO(text.lstrip("﻿")))), dtype=str).fillna("")
    rows = []
    for order_id, order in raw.groupby("target_order_id", sort=False):
        try:
            total = round(float(order["order_total"].iloc[0]) * 100)
            prices = [round(float(p) * 100) * int(q) for p, q in zip(order["price"], order["qty"])]
        except ValueError as e:
            raise ParseError(f"{NAME}: order {order_id}: bad amount") from e
        if total <= 0 or sum(prices) <= 0:  # free (a replacement, a gift card at no cost): no charge to explain
            continue
        for n, (r, cents) in enumerate(zip(order.to_dict("records"), _shares(prices, total))):
            item = " ".join(r["item"].split())
            if int(r["qty"]) > 1:
                item = f"{item} (x{r['qty']})"
            rows.append({"item_id": hashlib.sha256(f"{SERVICE}|{order_id}|{n}".encode()).hexdigest()[:16],
                         "service": SERVICE, "order_id": order_id, "kind": "item", "order_date": r["date"],
                         "date": r["date"], "amount": cents / 100, "card": "", "department": _department(item),
                         "item": item, "website": r["store"] or "target.com"})
    df = pd.DataFrame(rows, columns=COLUMNS)
    dates = sorted(d for d in df["date"] if d)
    return Result(kind=KIND, parser=NAME, label=f"target_purchases_{dates[-1] if dates else 'none'}",
                  covers_from=dates[0] if dates else "", covers_to=dates[-1] if dates else "",
                  tables={"order_items": df} if len(df) else {}, keys={"order_items": ["item_id"]},
                  notes=[f"Target: {df['order_id'].nunique()} purchases, {len(df)} items"])
