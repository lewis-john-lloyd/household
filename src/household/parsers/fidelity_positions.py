"""Fidelity's Positions download (Portfolio_Positions_<date>.csv): every account's holdings on the day it was
downloaded, brokerage and workplace plans alike.

Account number, Account name, Symbol, Description, Quantity, Last price, ..., Current value, ...: one row per
holding. A money market held as the account's cash (SPAXX**, FDRXX**) has a value and no quantity; it is kept
as its symbol at a share a dollar. A plan's fund comes as NON40<code> (NON40OJU1); the code alone is its
symbol, as a plan's QIF names it.

  holdings  each account's holdings on the download date (the "Date downloaded" line)
  prices    each fund's last price on that date

Each account is found by the last four digits of its number in the accounts table's last4 (a plan's short
number, 12345, by 2345).
"""

import csv
import io
import re
from datetime import datetime

import pandas as pd

from .common import ParseError, Result
from .fidelity import COLUMNS

NAME = "fidelity_positions"
HEADER = "Account number,Account name,Symbol,Description,Quantity,Last price"
HOLDINGS = ["as_of", "account_id", "symbol", "source", "shares", "value"]


def sniff(text: str) -> bool:
    return text.lstrip("﻿").startswith(HEADER)


def _num(text, what):
    t = str(text or "").replace("$", "").replace(",", "").replace("+", "").strip()
    try:
        return float(t) if t else None
    except ValueError as e:
        raise ParseError(f"{NAME}: {what} is not a number: {text!r}") from e


def symbol_of(text):
    s = text.strip().rstrip("*")
    m = re.match(r"^NON\d{2}([A-Z0-9]+)$", s)
    return m.group(1) if m else s


def parse(text: str) -> Result:
    body = text.lstrip("﻿")
    when = re.search(r"Date downloaded\s+([A-Za-z]{3}-\d{2}-\d{4})", body)
    if not when:
        raise ParseError(f"{NAME}: no Date downloaded line")
    day = datetime.strptime(when.group(1), "%b-%d-%Y").date().isoformat()
    held, prices, accounts = [], [], {}
    for r in csv.DictReader(io.StringIO(body)):
        number = (r.get("Account number") or "").strip()
        if not number or not (r.get("Symbol") or "").strip() or not re.search(r"\d{4}", number):
            continue
        symbol = symbol_of(r["Symbol"])
        value = _num(r.get("Current value"), "Current value")
        if value is None:
            continue
        shares = _num(r.get("Quantity"), "Quantity")
        price = _num(r.get("Last price"), "Last price")
        if shares is None:  # cash in a money market: a share a dollar
            shares, price = value, 1.0
        account = f"acct:{re.sub(r'[^0-9]', '', number)[-4:]}"
        accounts[account] = r.get("Account name", "").strip()
        held.append(
            {
                "as_of": day,
                "account_id": account,
                "symbol": symbol,
                "source": "",
                "shares": round(shares, 6),
                "value": round(value, 2),
            }
        )
        if price:
            prices.append(
                {"symbol": symbol, "date": day, "priced_date": day, "close": round(price, 6), "source": "fidelity"}
            )
    if not held:
        raise ParseError(f"{NAME}: no positions")
    df = pd.DataFrame(held, columns=HOLDINGS)
    df = df.groupby(["as_of", "account_id", "symbol", "source"], as_index=False).agg(
        shares=("shares", "sum"), value=("value", "sum")
    )
    totals = df.groupby("account_id")["value"].sum()
    return Result(
        kind="positions",
        parser=NAME,
        label=f"fidelity_positions_{day}",
        covers_from=day,
        covers_to=day,
        tables={
            "investment_activity": pd.DataFrame(columns=COLUMNS),
            "holdings": df[HOLDINGS],
            "prices": pd.DataFrame(prices).drop_duplicates(["symbol", "date"], keep="last"),
        },
        keys={
            "investment_activity": ["activity_id"],
            "holdings": ["as_of", "account_id", "source", "symbol"],
            "prices": ["symbol", "date"],
        },
        notes=[
            f"account ..{a[5:]}: {accounts[a]}, {int((df['account_id'] == a).sum())} holdings worth "
            f"{totals[a]:,.2f} on {day}"
            for a in accounts
        ],
    )
