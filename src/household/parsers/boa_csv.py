"""Bank of America card statement downloads (CSV, one statement per file).

Header: Posted Date,Reference Number,Payee,Address,Amount. Amounts are signed
as in the ledger (a charge is negative, a payment positive). The file names no
account, but Bank of America names it with the card's last four
("April2026_1234.csv"), which the inbox matches to the last4 column.

Each row carries Bank of America's reference number, which keys it, so
statements that overlap add nothing twice.
"""

import csv
import io
from datetime import datetime

import pandas as pd

from .common import ParseError, Result

NAME = "boa_csv"
KIND = "card_statement"
HEADER = ("Posted Date", "Reference Number", "Payee", "Address", "Amount")


def sniff(text):
    first = text.lstrip("﻿").splitlines()[0] if text.strip() else ""
    return tuple(c.strip().strip('"') for c in first.split(",")) == HEADER


def parse(text):
    out = []
    for r in csv.DictReader(io.StringIO(text.lstrip("﻿"))):
        try:
            date = datetime.strptime(r["Posted Date"].strip(), "%m/%d/%Y").date().isoformat()
            amount = round(float(r["Amount"].replace(",", "")), 2)
        except ValueError as e:
            raise ParseError(f"{NAME}: bad row {r['Posted Date']} {r['Amount']!r}") from e
        ref = r["Reference Number"].strip()
        if not ref:
            raise ParseError(f"{NAME}: row without a reference number ({date})")
        out.append({"txn_id": f"ref:{ref}", "date": date, "account_id": "", "amount": amount,
                    "payee_raw": " ".join(r["Payee"].split()), "category": "", "one_off": "FALSE",
                    "note": " ".join(r["Address"].split()), "source": "csv", "era": "ledger",
                    "recorded_at": ""})
    txns = pd.DataFrame(out, columns=["txn_id", "date", "account_id", "amount", "payee_raw", "category",
                                      "one_off", "note", "source", "era", "recorded_at"])
    if txns["txn_id"].duplicated().any():
        raise ParseError(f"{NAME}: repeated reference number")
    dates = sorted(txns["date"])
    return Result(kind=KIND, parser=NAME, label=f"boa_{dates[-1] if dates else 'empty'}",
                  covers_from=dates[0] if dates else "", covers_to=dates[-1] if dates else "",
                  tables={"transactions": txns}, keys={"transactions": ["txn_id"]},
                  notes=[f"Bank of America: {len(txns)} transactions"])
