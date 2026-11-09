"""Fidelity account activity: a brokerage, IRA or HSA account's history download (CSV), and a workplace
plan's history in Quicken's investment format (QIF).

Each row says what happened to the account, in one vocabulary for both formats (KINDS):

  shares   the change in shares of the row's fund: positive bought or received, negative sold or paid out
           (0 for a row that moves only cash)
  value    the dollars of those shares, signed like them
  amount   the money crossing the account's edge: in when positive (a contribution, a conversion or
           transfer in, a dividend or interest), out when negative (a conversion or transfer out, a
           withdrawal, a fee). Buying, selling, exchanging and reinvesting stay inside the account: 0.

So an account's amounts added up by year are what went in and came out of it, and its shares added up
by fund are what it holds (where the history reaches back to its start).

The history CSV names the account only in its file name (History_for_Account_<number>.csv): the inbox
matches the number's last four to the accounts table. A plan's QIF names no account at all, so its file
name must carry the account_id (m_401k_old.qif). Neither format has ids: the CSV's running cash balance
makes each row distinct across overlapping downloads; a QIF row is keyed on all its fields, with a count
for identical rows. The inbox hashes each key with the account (finish_investment_rows).
"""

import csv
import hashlib
import io
import re
from datetime import datetime

import pandas as pd

from .common import ParseError, Result

NAME = "fidelity"
KIND = "investment_activity"
COLUMNS = ["activity_id", "account_id", "date", "kind", "action", "symbol", "description", "shares", "price",
           "value", "amount", "cash_balance", "source"]
KINDS = ("contribution", "buy", "sell", "exchange", "reinvest", "dividend", "interest", "conversion_in",
         "conversion_out", "transfer_in", "transfer_out", "withdrawal", "fee", "adjustment", "other")
CSV_HEADER = "Run Date,Action,Symbol,Description,Type,Price ($),Quantity,"


def sniff(text):
    head = text.lstrip("﻿")[:3000]
    return CSV_HEADER in head or head.lstrip().startswith("!Type:Invst")


def _num(text):
    text = str(text or "").replace(",", "").replace("$", "").strip()
    try:
        return float(text) if text else 0.0
    except ValueError as e:
        raise ParseError(f"{NAME}: not a number: {text!r}") from e


def _date(text):
    try:
        return datetime.strptime(str(text).strip(), "%m/%d/%Y").date().isoformat()
    except ValueError as e:
        raise ParseError(f"{NAME}: not a date: {text!r}") from e


def history_kind(action, amount):
    """The KINDS word for a history CSV's Action text; the sign of the amount decides in from out."""
    a = " ".join(str(action).upper().split())
    if a.startswith("REINVESTMENT"):
        return "reinvest"
    if a.startswith("DIVIDEND RECEIVED") or a.startswith("LONG-TERM CAP GAIN") or a.startswith("SHORT-TERM CAP GAIN"):
        return "dividend"
    if a.startswith("YOU BOUGHT"):
        return "buy"
    if a.startswith("YOU SOLD"):
        return "sell"
    if "INTEREST" in a:
        return "interest"
    if a.startswith("CONV") or " CONV " in f" {a} " or "CONVERSION" in a:
        return "conversion_in" if amount > 0 else "conversion_out"
    if "CONTRIBUTION" in a or "CONTR " in a or a.startswith("PARTIC CONTR"):
        return "contribution"
    if "TRANSFER" in a or "ROLLOVER" in a or "JOURNALED" in a:
        return "transfer_in" if amount > 0 else "transfer_out"
    if "FEE" in a:
        return "fee"
    if "DISTRIBUTION" in a or "WITHDRAWAL" in a or "DEBIT" in a:
        return "withdrawal"
    return "other"


INSIDE = ("buy", "sell", "exchange", "reinvest", "adjustment")  # kinds whose money stays in the account


def parse_history(text):
    lines = text.lstrip("﻿").splitlines()
    start = next((i for i, line in enumerate(lines) if line.strip().startswith(CSV_HEADER)), None)
    if start is None:
        raise ParseError(f"{NAME}: no history header")
    body = []
    for line in lines[start:]:
        if not line.strip():
            break  # the rows end at the first blank line; Fidelity's disclaimer follows
        body.append(line.rstrip())
    rows = []
    for r in csv.DictReader(io.StringIO("\n".join(body))):
        r = {k.strip(): (v or "").strip() for k, v in r.items() if k}
        amount = round(_num(r.get("Amount ($)")), 2)
        kind = history_kind(r["Action"], amount)
        shares = _num(r.get("Quantity"))
        rows.append({"date": _date(r["Run Date"]), "kind": kind, "action": " ".join(r["Action"].split()),
                     "symbol": r.get("Symbol", ""), "description": r.get("Description", ""),
                     "shares": round(shares, 6) if kind not in ("dividend", "interest") else 0.0,
                     "price": round(_num(r.get("Price ($)")), 6),
                     "value": round(-amount, 2) if kind in INSIDE else 0.0,
                     "amount": 0.0 if kind in INSIDE else amount,
                     "cash_balance": r.get("Cash Balance ($)", ""), "source": ""})
    return rows


def qif_records(text):
    for block in text.replace("\r\n", "\n").split("\n^"):
        r = {}
        for line in block.strip().split("\n"):
            if line and not line.startswith("!") and line[0] not in r:
                r[line[0]] = line[1:].strip()
        if r:
            yield r


def plan_kind(action, memo):
    """The KINDS word for a plan QIF record (N is the action, M the plan's reason for it)."""
    a, m = action.lower(), " ".join(memo.lower().split())
    if "contribution" in m:
        return "contribution"
    if "exchange" in m:
        return "exchange"
    if "fee" in m or a in ("miscexp",):
        return "fee"
    if "withdrawal" in m or "distribution" in m:
        return "withdrawal"
    if "transfer" in m or "rollover" in m:
        return "transfer_in" if a in ("shrsin", "buy", "xin") else "transfer_out"
    if "market value" in m or a == "null":
        return "adjustment"
    if a.startswith("reinv"):
        return "reinvest"
    if a in ("div", "intinc", "cgshort", "cglong"):
        return "interest" if a == "intinc" else "dividend"
    if a == "buy":
        return "buy"
    if a == "sell":
        return "sell"
    return "other"


def parse_plan(text):
    rows = []
    for r in qif_records(text.lstrip("﻿")):
        if "D" not in r:
            raise ParseError(f"{NAME}: QIF record without a date: {r}")
        action, memo = r.get("N", ""), r.get("M", "")
        kind = plan_kind(action, memo)
        out = action.lower() in ("sell", "shrsout", "xout", "miscexp") or kind in ("fee", "withdrawal", "transfer_out")
        sign = -1 if out else 1
        shares, value = sign * _num(r.get("Q")), sign * _num(r.get("T") or r.get("U"))
        # a reinvested dividend is income that buys shares: it comes in as money and as shares
        amount = 0.0 if kind in ("buy", "sell", "exchange", "adjustment") else value
        rows.append({"date": _date(r["D"]), "kind": kind, "action": (action + (" " + " ".join(memo.split()) if memo else "")).strip(),
                     "symbol": _qif_symbol(r.get("Y", "")), "description": " ".join(r.get("Y", "").split()),
                     "shares": round(shares, 6), "price": round(_num(r.get("I")), 6), "value": round(value, 2),
                     "amount": round(amount, 2), "cash_balance": "", "source": ""})
    return rows


def _qif_symbol(security):
    """A plan fund's ticker or fund code: "S&P 500 INDEX(OJU1)" -> OJU1; the name when it has none."""
    m = re.search(r"\(([A-Z0-9]{3,8})\)\s*$", security)
    return m.group(1) if m else " ".join(security.split())


def parse(text):
    stripped = text.lstrip("﻿")
    rows = parse_plan(stripped) if stripped.lstrip().startswith("!Type:Invst") else parse_history(stripped)
    plan = stripped.lstrip().startswith("!Type:Invst")
    df = pd.DataFrame(rows, columns=COLUMNS[2:])
    if df.empty:
        raise ParseError(f"{NAME}: no activity in the file")
    key_cols = ["date", "action", "symbol", "shares", "amount", "value", "cash_balance"]
    key = df[key_cols].astype(str).agg("|".join, axis=1)
    n = key.groupby(key).cumcount()
    df.insert(0, "account_id", "")
    df.insert(0, "activity_id", [f"key:{k}|{i}" for k, i in zip(key, n)])  # hashed with the account on ingest
    dates = sorted(df["date"])
    counts = df["kind"].value_counts()
    what = ", ".join(f"{counts[k]} {k}" for k in KINDS if k in counts)
    return Result(kind=KIND, parser=NAME, label=f"fidelity_{dates[-1]}", covers_from=dates[0], covers_to=dates[-1],
                  tables={KIND: df}, keys={KIND: ["activity_id"]},
                  notes=[f"Fidelity {'plan history' if plan else 'account history'} {dates[0]} to {dates[-1]}: {what}"])


def finish(result, account_id):
    """The rows with their account and ids: each key hashed with the account."""
    for name, rows in result.tables.items():  # holdings too, when the file has them
        if name != KIND and "account_id" in rows:
            result.tables[name] = rows.assign(account_id=account_id)
    df = result.tables[KIND].copy()
    df["account_id"] = account_id
    df["activity_id"] = [hashlib.sha256(f"{account_id}|{k}".encode()).hexdigest()[:16] for k in df["activity_id"]]
    result.tables[KIND] = df
    result.account_id = account_id
    result.label = f"{account_id}_{result.covers_to or 'undated'}"
    return result
