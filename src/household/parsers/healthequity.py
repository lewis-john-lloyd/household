"""HealthEquity's investment history: an HSA's "All Investment Transactions" download (.xlsx, read as CSV).

Date, Fund, Category, Description, Price, Amount, Shares, Total Shares, Total Value: one row per purchase
or reinvested dividend, with the fund's running share count and value after it. So the file gives:

  investment_activity  a Buy moves money from the HSA's cash into the fund (a buy: no money crosses the
                       account's edge); a Dividend is reinvested (income in, shares bought); a Sell goes
                       back to cash; a Fee is money out
  holdings             each fund's last Total Shares and Total Value, on the date of its last row
  prices               each row's price on its date

The HSA's cash (contributions, claims paid) is not in this download. The file names no account, so its
file name carries the account_id (l_hsa_healthequity.xlsx), as for a plan's QIF.
"""

import csv
import io
import re
from datetime import date, datetime, timedelta

import pandas as pd

from .common import ParseError, Result
from .fidelity import COLUMNS

NAME = "healthequity"
KIND = "investment_activity"
HEADER = ["Date", "Fund", "Category", "Description", "Price", "Amount", "Shares", "Total Shares", "Total Value"]
KINDS = {"buy": "buy", "dividend": "reinvest", "sell": "sell", "fee": "fee"}


def sniff(text):
    head = text.lstrip("﻿")[:3000]
    return "All Investment Transactions" in head and ",".join(HEADER) in head


def _num(text, what):
    t = str(text or "").replace("$", "").replace(",", "").strip()
    negative = t.startswith("(") and t.endswith(")")
    try:
        n = float(t.strip("()") or 0)
    except ValueError as e:
        raise ParseError(f"{NAME}: {what} is not a number: {text!r}") from e
    return -n if negative else n


def _date(text):
    t = str(text).strip()
    if re.fullmatch(r"\d+(\.0+)?", t):  # an Excel serial day count
        return (date(1899, 12, 30) + timedelta(days=int(float(t)))).isoformat()
    try:
        return datetime.strptime(t, "%m/%d/%Y").date().isoformat()
    except ValueError as e:
        raise ParseError(f"{NAME}: not a date: {text!r}") from e


def parse(text):
    lines = list(csv.reader(io.StringIO(text.lstrip("﻿"))))
    start = next((i for i, r in enumerate(lines) if [c.strip() for c in r[:len(HEADER)]] == HEADER), None)
    if start is None:
        raise ParseError(f"{NAME}: no transaction header")
    acts, prices, last, running = [], [], {}, {}
    for r in lines[start + 1:]:
        if not any(c.strip() for c in r):
            continue
        r = (r + [""] * len(HEADER))[:len(HEADER)]
        day, fund, category = _date(r[0]), r[1].strip(), r[2].strip()
        price, amount, shares = _num(r[4], "Price"), _num(r[5], "Amount"), _num(r[6], "Shares")
        kind = KINDS.get(category.lower(), "other")
        sign = -1 if kind in ("sell", "fee") else 1
        acts.append({"date": day, "kind": kind, "action": f"{category} {r[3].strip()}".strip(), "symbol": fund,
                     "description": r[3].strip(), "shares": round(sign * abs(shares), 6), "price": round(price, 6),
                     "value": round(sign * abs(amount), 2),
                     "amount": {"reinvest": round(abs(amount), 2), "fee": -round(abs(amount), 2)}.get(kind, 0.0),
                     "cash_balance": "", "source": ""})
        if price:
            prices.append({"symbol": fund, "date": day, "priced_date": day, "close": round(price, 6), "source": NAME})
        if r[7].strip():
            total = _num(r[7], "Total Shares")
            if fund in running and abs(running[fund] + sign * abs(shares) - total) > 0.01:
                raise ParseError(f"{NAME}: {fund} on {day}: {running[fund]} shares and {shares} more is not the {total} it says")
            running[fund] = total
            last[fund] = {"as_of": day, "account_id": "", "symbol": fund, "source": "",
                          "shares": round(total, 6), "value": round(_num(r[8], "Total Value"), 2)}
    if not acts:
        raise ParseError(f"{NAME}: no transactions in the file")
    df = pd.DataFrame(acts, columns=COLUMNS[2:])
    key = df[["date", "kind", "symbol", "shares", "value"]].astype(str).agg("|".join, axis=1)
    df.insert(0, "account_id", "")
    df.insert(0, "activity_id", [f"key:{k}|{i}" for k, i in zip(key, key.groupby(key).cumcount())])
    dates = sorted(df["date"])
    counts = df["kind"].value_counts()
    held = pd.DataFrame(list(last.values()), columns=["as_of", "account_id", "symbol", "source", "shares", "value"])
    return Result(kind=KIND, parser=NAME, label=f"healthequity_{dates[-1]}", covers_from=dates[0], covers_to=dates[-1],
                  tables={KIND: df, "holdings": held,
                          "prices": pd.DataFrame(prices).drop_duplicates(["symbol", "date"], keep="last")},
                  keys={KIND: ["activity_id"], "holdings": ["as_of", "account_id", "source", "symbol"], "prices": ["symbol", "date"]},
                  notes=[f"HealthEquity investments {dates[0]} to {dates[-1]}: "
                         + ", ".join(f"{n} {k}" for k, n in counts.items())
                         + "; " + ", ".join(f"{f} {h['shares']} shares on {h['as_of']}" for f, h in last.items())])
