"""USAA account and card downloads (CSV).

Header: Date,Description,Original Description,Category,Amount,Status. The file
names no account, so the inbox takes the account from the file name, which
must contain an account_id from the accounts table (for example
"expenses_usaa.csv" for the account "expenses").

Signs: on a checking or savings download money out is negative, as in the
ledger. On a card download a charge is positive and a payment or refund
negative, so the inbox flips card amounts (CARD_CHARGES_POSITIVE).

Pending rows are left out: their description, category and sometimes date
change when they post, and the posted row arrives in a later download.
Posted rows carry no id, so the inbox keys them on account, date, amount and
original description, with an occurrence count for identical rows on one day.

The cleaned Description and USAA's Category go in the note, as hints for
categorizing; the ledger's category stays empty for the household's own rules.
"""

import csv
import io

import pandas as pd

from .common import ParseError, Result

NAME = "usaa_csv"
KIND = "bank_download"
HEADER = ("Date", "Description", "Original Description", "Category", "Amount", "Status")
CARD_CHARGES_POSITIVE = True
STATUSES = {"Posted", "Pending"}


def sniff(text):
    first = text.lstrip("﻿").splitlines()[0] if text.strip() else ""
    return tuple(c.strip().strip('"') for c in first.split(",")) == HEADER


def parse(text):
    rows = list(csv.DictReader(io.StringIO(text.lstrip("﻿"))))
    bad = sorted({r["Status"] for r in rows} - STATUSES)
    if bad:
        raise ParseError(f"{NAME}: unknown status {bad}")
    posted = [r for r in rows if r["Status"] == "Posted"]
    out = []
    for r in posted:
        try:
            amount = round(float(r["Amount"].replace(",", "")), 2)
            date = pd.Timestamp(r["Date"]).date().isoformat()
        except ValueError as e:
            raise ParseError(f"{NAME}: bad row {r['Date']} {r['Amount']!r}") from e
        hints = [h for h in (" ".join(r["Description"].split()), r["Category"].strip()) if h]
        out.append({"txn_id": "", "date": date, "account_id": "", "amount": amount,
                    "payee_raw": " ".join(r["Original Description"].split()), "category": "",
                    "one_off": "FALSE", "note": " | ".join(hints), "source": "csv", "era": "ledger",
                    "recorded_at": ""})
    txns = pd.DataFrame(out, columns=["txn_id", "date", "account_id", "amount", "payee_raw", "category",
                                      "one_off", "note", "source", "era", "recorded_at"])
    dates = sorted(txns["date"])
    pending = len(rows) - len(posted)
    return Result(kind=KIND, parser=NAME, label=f"usaa_{dates[-1] if dates else 'empty'}",
                  covers_from=dates[0] if dates else "", covers_to=dates[-1] if dates else "",
                  tables={"transactions": txns}, keys={"transactions": ["txn_id"]},
                  card_charges_positive=CARD_CHARGES_POSITIVE,
                  notes=[f"USAA: {len(txns)} posted" + (f", {pending} pending left out" if pending else "")])
