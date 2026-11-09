"""Google Takeout: Google Pay's list of Google purchases (Play apps and subscriptions, Google One, Fi,
the Google Store).

Takeout arrives as one or more zips holding far more than the household uses
(addresses, IP addresses, install history, an HTML index). One file is read:
"Google Pay/Google transactions/transactions_<n>.csv", with columns Time,
Transaction ID, Description, Product, Payment method, Status, Amount. Each
completed transaction becomes an order_items row of service "google" (a
negative amount, a refund or credit, becomes kind "refund"); the trimmed rows
are the record, and the inbox trashes the zips. A Takeout zip with nothing this
parser reads (the HTML index alone, say) is recognized and trashed as well.

The card charge for a purchase says only GOOGLE *...; matching names it with
the item, so "Claude Pro Subscription (Claude by Anthropic)" is what review
asks about.
"""

import csv
import hashlib
import io
import re
from datetime import datetime

import pandas as pd

from .amazon import COLUMNS
from .common import ParseError, Result

NAME = "google"
KIND = "google_purchases"
SERVICE = "google"
HEADER = ["Time", "Transaction ID", "Description", "Product", "Payment method", "Status", "Amount"]
ZIP_PREFIX = "Takeout/"  # a Takeout zip is this parser's even when nothing in it is read
CARD = re.compile(r"(\d{4})")
SPACES = re.compile(r"[   ]")


def sniff(text: str) -> bool:
    first = text.lstrip("﻿").splitlines()[0] if text.strip() else ""
    return [c.strip() for c in next(csv.reader([first]))] == HEADER if first else False


def _date(value):
    text = SPACES.sub(" ", value).strip()
    try:
        return datetime.strptime(text, "%b %d, %Y, %I:%M %p").date().isoformat()
    except ValueError as e:
        raise ParseError(f"{NAME}: bad time {value!r}") from e


def _amount(value):
    text = SPACES.sub(" ", value).replace("−", "-").replace(",", "").strip()
    found = re.fullmatch(r"(-?)\s*USD\s*([\d.]+)", text)
    if not found:
        raise ParseError(f"{NAME}: bad amount {value!r}")
    return round(float(found.group(2)) * (-1 if found.group(1) else 1), 2)


def _card(method):
    found = CARD.search(SPACES.sub(" ", str(method)))
    return found.group(1) if found else ""


def _item(description, product):
    """The app or service first ("Claude by Anthropic: Claude Pro Subscription"), so purchases group by app."""
    description = description.strip()
    inner = re.search(r"\(([^()]*)\)\s*$", description)
    if inner:
        return f"{inner.group(1).strip()}: {description[: inner.start()].strip()}"
    return (
        f"{product.strip()}: {description}" if product.strip() and product.strip() not in description else description
    )


def parse(text: str) -> Result:
    raw = pd.DataFrame(list(csv.DictReader(io.StringIO(text.lstrip("﻿")))), dtype=str).fillna("")
    raw = raw[raw["Status"].str.strip() == "Complete"]
    rows = []
    for r in raw.to_dict("records"):
        amount, day = _amount(r["Amount"]), _date(r["Time"])
        rows.append(
            {
                "service": SERVICE,
                "order_id": r["Transaction ID"].strip(),
                "kind": "refund" if amount < 0 else "item",
                "order_date": day,
                "date": day,
                "amount": abs(amount),
                "card": _card(r["Payment method"]),
                "department": r["Product"].strip(),
                "item": _item(r["Description"], r["Product"]),
                "website": "",
            }
        )
    df = pd.DataFrame(rows, columns=[c for c in COLUMNS if c != "item_id"])
    df.insert(
        0,
        "item_id",
        [hashlib.sha256(f"{SERVICE}|{o}|{k}".encode()).hexdigest()[:16] for o, k in zip(df["order_id"], df["kind"])],
    )
    if df["item_id"].duplicated().any():
        raise ParseError(f"{NAME}: repeated transaction id")
    return _result(df, [f"Google: {len(df)} purchases"])


def _result(df, notes):
    dates = sorted(d for d in df["date"] if d)
    return Result(
        kind=KIND,
        parser=NAME,
        label=f"google_purchases_{dates[-1] if dates else 'none'}",
        covers_from=dates[0] if dates else "",
        covers_to=dates[-1] if dates else "",
        tables={"order_items": df} if len(df) else {},
        keys={"order_items": ["item_id"]},
        record="" if df.empty else df.to_csv(index=False),
        notes=notes,
    )


def combine(results):
    """One result for the files read out of one Takeout zip (none, for a zip of other things)."""
    frames = [r.tables["order_items"] for r in results if "order_items" in r.tables]
    df = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=COLUMNS)
    notes = [n for r in results for n in r.notes] or ["Google Takeout: nothing used in this part"]
    return _result(df.drop_duplicates("item_id"), notes)
