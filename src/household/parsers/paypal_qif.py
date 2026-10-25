"""PayPal activity downloads in Quicken format (QIF).

PayPal is not counted as an account: a PayPal purchase is already on the card
or bank account that funded it ("PAYPAL *EBAY US"), and the download leaves
out bank-funded deposits, so its balance never reconciles. What it adds is the
other side of each payment: who it went to or came from, and the item. Those
rows go to payment_details, for naming and categorizing the card and bank
lines they match by amount and date.

Each record has D (date, MM/DD/YYYY), T (amount; money out negative), L (the
PayPal transaction type), P (counterparty) and sometimes M (item or note).
Kept: payments and refunds with a counterparty. Left out: funding moves
(General Card Deposit, withdrawals), authorization holds and their voids, and
"Other" rows, which PayPal uses for partial holds.

QIF carries no ids, so rows are keyed on date, amount, type, counterparty and
memo, with a count for identical rows.
"""

import hashlib
from datetime import datetime

import pandas as pd

from .common import ParseError, Result

NAME = "paypal_qif"
KIND = "payment_details"
KEPT = ("Express Checkout Payment", "PreApproved Payment Bill User Payment", "Website Payment",
        "BillPay transaction", "Mobile Payment", "General Payment", "Payment Refund")
KNOWN = KEPT + ("General Card Deposit", "General Card Withdrawal", "General Authorization",
                "Void of Authorization", "User Initiated Withdrawal", "General Withdrawal",
                "General Currency Conversion", "Other")
COLUMNS = ["detail_id", "date", "amount", "service", "kind", "counterparty", "memo"]


def sniff(text):
    head = text.lstrip("﻿")[:2000]
    return head.startswith("!Type:") and any(f"\nL{k}" in head.replace("\r", "") for k in KNOWN)


def records(text):
    for block in text.replace("\r\n", "\n").split("\n^"):
        r = {}
        for line in block.strip().split("\n"):
            if line and not line.startswith("!") and line[0] not in r:
                r[line[0]] = line[1:].strip()
        if r:
            yield r


def parse(text):
    rows = []
    for r in records(text.lstrip("﻿")):
        if "D" not in r or "T" not in r:
            raise ParseError(f"{NAME}: record without date or amount: {r}")
        kind = r.get("L", "")
        if kind not in KNOWN:
            raise ParseError(f"{NAME}: unknown PayPal type {kind!r}")
        if kind not in KEPT or not r.get("P"):
            continue
        try:
            date = datetime.strptime(r["D"], "%m/%d/%Y").date().isoformat()
            amount = round(float(r["T"].replace(",", "")), 2)
        except ValueError as e:
            raise ParseError(f"{NAME}: bad record {r['D']} {r['T']!r}") from e
        rows.append({"date": date, "amount": amount, "service": "paypal", "kind": kind,
                     "counterparty": " ".join(r["P"].split()), "memo": " ".join(r.get("M", "").split())})
    df = pd.DataFrame(rows, columns=COLUMNS[1:])
    key = df.astype(str).agg("|".join, axis=1) if not df.empty else pd.Series(dtype=str)
    n = key.groupby(key).cumcount() if not df.empty else []
    df.insert(0, "detail_id", [hashlib.sha256(f"{k}|{i}".encode()).hexdigest()[:16] for k, i in zip(key, n)])
    dates = sorted(df["date"])
    return Result(kind=KIND, parser=NAME, label=f"paypal_{dates[-1] if dates else 'empty'}",
                  covers_from=dates[0] if dates else "", covers_to=dates[-1] if dates else "",
                  tables={"payment_details": df}, keys={"payment_details": ["detail_id"]},
                  notes=[f"PayPal: {len(df)} payments with a counterparty"])
