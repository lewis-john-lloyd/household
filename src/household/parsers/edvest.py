"""Edvest (Wisconsin's 529 plan) account download, a CSV of two parts: the account's positions (Fund Account
Number, Fund Name, Price, Shares, Total Value), then its transactions (Account Number, Trade Date, Process
Date, Transaction Type, Transaction Description, Investment Name, Share Price, Shares, Gross Amount, Net
Amount).

  investment_activity  a contribution (recurring or one-time) is money in; a rollover contribution is
                       money moved in from another plan (transfer_in); a withdrawal (a K-12 or college
                       withdrawal to the owner or a school) is money out; an exchange between portfolios
                       stays inside the account
  holdings             each portfolio's shares and value as the download shows them; the file carries no
                       date for them, so they are dated from the download's name (ofxdownload_MMDDYYYY.csv)
  prices               each portfolio's price on each trade date and on the download date

A portfolio has no ticker: its name is its symbol. The account is found by the last four digits of the
account number (X0001-02 -> 0102) against the accounts table's last4.
"""

import csv
import io
import re
from datetime import datetime

import pandas as pd

from .common import ParseError, Result
from .fidelity import COLUMNS

NAME = "edvest"
KIND = "investment_activity"
POSITIONS = "Fund Account Number,Fund Name,Price,Shares,Total Value"
TRANSACTIONS = "Account Number,Trade Date,Process Date,Transaction Type,Transaction Description,Investment Name,Share Price,Shares,Gross Amount,Net Amount"
HOLDINGS = ["as_of", "account_id", "symbol", "source", "shares", "value"]


def sniff(text: str) -> bool:
    head = text.lstrip("﻿")[:400]
    return head.startswith(POSITIONS) and TRANSACTIONS in text


def _num(text, what):
    t = str(text or "").replace("$", "").replace(",", "").strip()
    try:
        return float(t or 0)
    except ValueError as e:
        raise ParseError(f"{NAME}: {what} is not a number: {text!r}") from e


def kind_of(kind, description):
    k = f"{kind} {description}".lower()
    if "rollover" in k:
        return "transfer_in"
    if "contribution" in k:
        return "contribution"
    if "w/d" in k or "withdraw" in k or "distribution" in k:
        return "withdrawal"
    if "exchange" in k:
        return "exchange"
    if "fee" in k:
        return "fee"
    return "other"


def parse(text: str) -> Result:
    body = text.lstrip("﻿")
    first, _, rest = body.partition("\n\n") if "\n\n" in body else body.partition("\r\n\r\n")
    at = body.find(TRANSACTIONS)
    if at < 0:
        raise ParseError(f"{NAME}: no transactions header")
    numbers, held, prices, acts = set(), [], [], []
    for r in csv.DictReader(io.StringIO(body[:at])):
        if not (r.get("Fund Name") or "").strip():
            continue
        numbers.add(r["Fund Account Number"].strip())
        held.append(
            {
                "as_of": "",
                "account_id": "",
                "symbol": r["Fund Name"].strip(),
                "source": "",
                "shares": round(_num(r["Shares"], "Shares"), 6),
                "value": round(_num(r["Total Value"], "Total Value"), 2),
                "price": round(_num(r["Price"], "Price"), 6),
            }
        )
    for r in csv.DictReader(io.StringIO(body[at:])):
        if not (r.get("Trade Date") or "").strip():
            continue
        numbers.add(r["Account Number"].strip())
        try:
            day = datetime.strptime(r["Trade Date"].strip(), "%m/%d/%Y").date().isoformat()
        except ValueError as e:
            raise ParseError(f"{NAME}: not a date: {r['Trade Date']!r}") from e
        kind = kind_of(r["Transaction Type"], r["Transaction Description"])
        amount, shares, price = (
            _num(r["Net Amount"], "Net Amount"),
            _num(r["Shares"], "Shares"),
            _num(r["Share Price"], "Share Price"),
        )
        symbol = r["Investment Name"].strip()
        acts.append(
            {
                "date": day,
                "kind": kind,
                "action": " ".join(r["Transaction Description"].split()),
                "symbol": symbol,
                "description": r["Transaction Type"].strip(),
                "shares": round(shares, 6),
                "price": round(price, 6),
                "value": round(_num(r["Gross Amount"], "Gross Amount"), 2),
                "amount": 0.0 if kind == "exchange" else round(amount, 2),
                "cash_balance": "",
                "source": "",
            }
        )
        if price:
            prices.append({"symbol": symbol, "date": day, "priced_date": day, "close": round(price, 6), "source": NAME})
    if len(numbers) != 1:
        raise ParseError(f"{NAME}: expected one account, found {len(numbers)}")
    if not acts and not held:
        raise ParseError(f"{NAME}: no positions or transactions")
    number = numbers.pop()
    df = pd.DataFrame(acts, columns=COLUMNS[2:])
    key = df[["date", "kind", "symbol", "shares", "amount"]].astype(str).agg("|".join, axis=1)
    df.insert(0, "account_id", "")
    df.insert(0, "activity_id", [f"key:{k}|{i}" for k, i in zip(key, key.groupby(key).cumcount())])
    dates = sorted(df["date"]) or [""]
    result = Result(
        kind=KIND,
        parser=NAME,
        label=f"edvest_{dates[-1]}",
        covers_from=dates[0],
        covers_to=dates[-1],
        account_hint=re.sub(r"\D", "", number)[-4:],
        tables={
            KIND: df,
            "holdings": pd.DataFrame(held, columns=HOLDINGS + ["price"]),
            "prices": pd.DataFrame(prices, columns=["symbol", "date", "priced_date", "close", "source"]),
        },
        keys={
            KIND: ["activity_id"],
            "holdings": ["as_of", "account_id", "source", "symbol"],
            "prices": ["symbol", "date"],
        },
        notes=[
            f"Edvest {dates[0]} to {dates[-1]}: "
            + ", ".join(f"{n} {k}" for k, n in df["kind"].value_counts().items())
            + "; "
            + ", ".join(f"{h['symbol']} {h['shares']:g} shares" for h in held)
        ],
    )
    return result


def date_positions(result, file_name):
    """Date the positions (and their prices) from the download's name, ofxdownload_MMDDYYYY.csv."""
    held = result.tables.get("holdings")
    if held is None or held.empty:
        return result
    m = re.search(r"(\d{2})(\d{2})(\d{4})", file_name)
    if not m:
        raise ParseError(f"{NAME}: the positions carry no date: keep the download's name (ofxdownload_MMDDYYYY.csv)")
    day = f"{m.group(3)}-{m.group(1)}-{m.group(2)}"
    try:
        datetime.strptime(day, "%Y-%m-%d")
    except ValueError as e:
        raise ParseError(f"{NAME}: {file_name}: no download date in the name") from e
    prices = [
        {"symbol": s, "date": day, "priced_date": day, "close": p, "source": NAME}
        for s, p in zip(held["symbol"], held["price"])
        if p
    ]
    result.tables["holdings"] = held.assign(as_of=day)[HOLDINGS]
    result.tables["prices"] = pd.concat(
        [result.tables["prices"], pd.DataFrame(prices)], ignore_index=True
    ).drop_duplicates(["symbol", "date"], keep="last")
    return result
