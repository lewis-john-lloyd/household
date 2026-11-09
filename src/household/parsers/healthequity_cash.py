"""HealthEquity's HSA cash history: the "Completed Transactions" download (TransactionHistory.xls, which is an
HTML table, one file a year): Date, Transaction, Amount, HSA Cash Balance.

The cash side of the HSA, which the investment download doesn't have:

  contribution   Employee or Employer Contribution: money in
  interest       Interest for the month: money in
  fee            an Investment Admin Fee or other fee: money out
  withdrawal     a claim, payment or distribution paid out of the HSA: money out
  exchange       Investment: <fund>, cash moved into the fund (the investment download has the purchase), or
                 a sale of a fund back to cash: inside the account, no money in or out
  (left out)     Balance Forward, the year's opening balance

Each row is cash (symbol "cash", a share a dollar). The rows come newest first; read oldest first, each
row's balance must be the one before it plus its amount, or the file is refused. The newest balance is a
holdings reading of the HSA's cash on its date.

The file names no account: the inbox finds it by its file name (l_hsa_cash.xls) or, failing that, as the one
HealthEquity account in the accounts table (account_hint "institution:HealthEquity").
"""

import re
from datetime import datetime

import pandas as pd

from . import ofx
from .common import ParseError, Result
from .fidelity import COLUMNS

NAME = "healthequity_cash"
KIND = "investment_activity"
HEADER = ["Date", "Transaction", "Amount", "HSA Cash Balance"]


def sniff(text):
    head = text[:3000]
    return "Completed Transactions" in head and "HSA Cash Balance" in head and "<table" in head.lower()


def _cells(row):
    return [
        " ".join(ofx.unescape(re.sub(r"<[^>]+>", " ", c)).split())
        for c in re.findall(r"<td[^>]*>(.*?)</td>", row, re.S | re.I)
    ]


def _money(text, what):
    t = text.replace("$", "").replace(",", "").strip()
    negative = t.startswith("(") and t.endswith(")") or t.startswith("-")
    try:
        n = float(t.strip("()-") or 0)
    except ValueError as e:
        raise ParseError(f"{NAME}: {what} is not an amount: {text!r}") from e
    return round(-n if negative else n, 2)


def kind_of(transaction, amount):
    t = transaction.lower()
    if t.startswith("balance forward"):
        return None
    if "contribution" in t:
        return "contribution"
    if t.startswith("interest"):
        return "interest"
    if "fee" in t:
        return "fee"
    if t.startswith("investment") or "liquidation" in t or "sell" in t or "sale" in t:
        return "exchange"
    if any(w in t for w in ("claim", "payment", "distribution", "withdrawal", "reimburse", "debit card", "card")):
        return "withdrawal"
    if "transfer" in t or "rollover" in t:
        return "transfer_in" if amount > 0 else "transfer_out"
    return "other"


def parse(text):
    rows = [_cells(r) for r in re.findall(r"<tr[^>]*>(.*?)</tr>", text, re.S | re.I)]
    at = next((i for i, r in enumerate(rows) if r[:4] == HEADER), None)
    if at is None:
        raise ParseError(f"{NAME}: no Date, Transaction, Amount, HSA Cash Balance header")
    lines = []
    for r in rows[at + 1 :]:
        if len(r) < 4 or not r[0]:
            continue
        try:
            # a deposit not yet available reads "1/26/2001 (Available 1/28/01)": the date it was made counts
            day = datetime.strptime(r[0].split("(")[0].strip(), "%m/%d/%Y").date().isoformat()
        except ValueError as e:
            raise ParseError(f"{NAME}: not a date: {r[0]!r}") from e
        lines.append((day, r[1], _money(r[2], "Amount"), _money(r[3], "HSA Cash Balance")))
    if not lines:
        raise ParseError(f"{NAME}: no transactions")
    lines.reverse()  # newest first in the file
    acts, balance = [], None
    for day, what, amount, after in lines:
        kind = kind_of(what, amount)
        if kind is None:  # the year's opening balance
            balance = after
            continue
        if balance is not None and abs(round(balance + amount, 2) - after) > 0.005:
            raise ParseError(f"{NAME}: {day} {what}: {balance:,.2f} and {amount:,.2f} is not the {after:,.2f} it shows")
        balance = after
        acts.append(
            {
                "date": day,
                "kind": kind,
                "action": what,
                "symbol": "cash",
                "description": what,
                "shares": amount,
                "price": 1.0,
                "value": amount,
                "amount": 0.0 if kind == "exchange" else amount,
                "cash_balance": after,
                "source": "",
            }
        )
    df = pd.DataFrame(acts, columns=COLUMNS[2:])
    key = df[["date", "action", "amount", "value", "cash_balance"]].astype(str).agg("|".join, axis=1)
    df.insert(0, "account_id", "")
    df.insert(0, "activity_id", [f"key:{k}|{i}" for k, i in zip(key, key.groupby(key).cumcount())])
    newest = lines[-1]
    held = pd.DataFrame(
        [
            {
                "as_of": newest[0],
                "account_id": "",
                "symbol": "cash",
                "source": "",
                "shares": newest[3],
                "value": newest[3],
            }
        ],
        columns=["as_of", "account_id", "symbol", "source", "shares", "value"],
    )
    counts = df["kind"].value_counts()
    return Result(
        kind=KIND,
        parser=NAME,
        label=f"healthequity_cash_{newest[0]}",
        covers_from=lines[0][0],
        covers_to=newest[0],
        account_hint="institution:HealthEquity",
        tables={KIND: df, "holdings": held},
        keys={KIND: ["activity_id"], "holdings": ["as_of", "account_id", "source", "symbol"]},
        notes=[
            f"HealthEquity cash {lines[0][0]} to {newest[0]}: "
            + ", ".join(f"{n} {k}" for k, n in counts.items())
            + f"; cash {newest[3]:,.2f} on {newest[0]}"
        ],
    )
