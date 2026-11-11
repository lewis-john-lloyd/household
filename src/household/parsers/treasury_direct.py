"""TreasuryDirect's savings bonds, copied from its Current Holdings page: the table of bonds, pasted as text.

  Confirm #   Issue Date   Interest Rate   Status   Amount   Current Value
  (one row per bond)
                                             Totals:  <amount>  <current value>

The columns come tab separated, as a browser copies a table. Each bond's Amount is what it cost, and its Current
Value what it is worth this month (TreasuryDirect values a bond monthly, three months' interest held back in its
first five years). The reading is one holdings row for the account, symbol IBOND, valued at the total and with no
shares (a savings bond has no market price). The page carries no date, so the reading is dated by a date in the
file's name (a_bonds_2001-03-01.txt) or else the day ingest reads it. The account is the one the file's
name gives (a_bonds.txt), or TreasuryDirect's one account in the accounts table.

Checks: every bond's amount and value read, and they add to the Totals row.
"""

import re
from datetime import date, datetime

import pandas as pd

from .common import ParseError, Result
from .fidelity import COLUMNS

NAME = "treasury_direct"
SYMBOL = "IBOND"
HOLDINGS = ["as_of", "account_id", "symbol", "source", "shares", "value"]
HEADER = ("Confirm #", "Issue Date", "Current Value")
ISSUED = re.compile(r"^(\d{2})-(\d{2})-(\d{4})$")
NAMED_DAY = re.compile(r"(\d{4})-?(\d{2})-?(\d{2})")


def sniff(text: str) -> bool:
    head = text.lstrip("﻿").lstrip().split("\n", 1)[0]
    return all(h in head for h in HEADER)


def _money(text, what):
    t = str(text).replace("$", "").replace(",", "").strip()
    try:
        return round(float(t), 2)
    except ValueError as e:
        raise ParseError(f"{NAME}: {what} is not an amount: {text!r}") from e


def parse(text: str) -> Result:
    lines = [ln for ln in text.lstrip("﻿").splitlines() if ln.strip()]
    header = [c.strip() for c in lines[0].split("\t")]
    try:
        at = {k: header.index(k) for k in ("Confirm #", "Issue Date", "Amount", "Current Value")}
    except ValueError as e:
        raise ParseError(f"{NAME}: the header lacks a column ({e})") from e
    bonds, totals = [], None
    for line in lines[1:]:
        cells = [c.strip() for c in line.split("\t")]
        cells += [""] * (len(header) - len(cells))
        if any(c == "Totals:" for c in cells):
            money = [c for c in cells if c.startswith("$")]
            if len(money) != 2:
                raise ParseError(f"{NAME}: the Totals row has no amount and value: {line.strip()!r}")
            totals = (_money(money[0], "the total amount"), _money(money[1], "the total value"))
            continue
        issued = ISSUED.match(cells[at["Issue Date"]])
        if not cells[at["Confirm #"]] or not issued:
            raise ParseError(f"{NAME}: a row that is not a bond: {line.strip()!r}")
        bonds.append(
            {
                "issued": f"{issued.group(3)}-{issued.group(1)}-{issued.group(2)}",
                "amount": _money(cells[at["Amount"]], "a bond's amount"),
                "value": _money(cells[at["Current Value"]], "a bond's value"),
            }
        )
    if not bonds:
        raise ParseError(f"{NAME}: no bonds")
    amount = round(sum(b["amount"] for b in bonds), 2)
    value = round(sum(b["value"] for b in bonds), 2)
    if totals is None:
        raise ParseError(f"{NAME}: no Totals row to check the bonds against")
    if abs(amount - totals[0]) > 0.005 or abs(value - totals[1]) > 0.005:
        raise ParseError(
            f"{NAME}: the bonds add to {amount:,.2f} bought and {value:,.2f} worth, "
            f"the Totals row says {totals[0]:,.2f} and {totals[1]:,.2f}"
        )
    held = pd.DataFrame([{"as_of": "", "account_id": "", "symbol": SYMBOL, "source": "", "shares": "", "value": value}])
    dates = sorted(b["issued"] for b in bonds)
    return Result(
        kind="positions",
        parser=NAME,
        label=NAME,
        account_hint="institution:TreasuryDirect",
        tables={"investment_activity": pd.DataFrame(columns=COLUMNS), "holdings": held[HOLDINGS]},
        keys={"investment_activity": ["activity_id"], "holdings": ["as_of", "account_id", "source", "symbol"]},
        notes=[
            f"TreasuryDirect: {len(bonds)} bonds issued {dates[0]} to {dates[-1]}, "
            f"bought for {amount:,.2f}, worth {value:,.2f}"
        ],
    )


def date_positions(result, file_name, received_at=""):
    """Date the reading by a date in the file's name, else by the day ingest reads it (received_at)."""
    held = result.tables.get("holdings")
    if held is None or held.empty:
        return result
    m = NAMED_DAY.search(file_name)
    day = ""
    if m:
        try:
            day = date(int(m.group(1)), int(m.group(2)), int(m.group(3))).isoformat()
        except ValueError:
            day = ""
    if not day:
        day = str(received_at)[:10] or datetime.now().date().isoformat()
        result.notes.append(f"TreasuryDirect: no date in the file's name, so the reading is dated {day}, the day read")
    result.tables["holdings"] = held.assign(as_of=day)[HOLDINGS]
    result.covers_from = result.covers_to = day
    return result
