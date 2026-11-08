"""Read the legacy investments workbook: its list of funds and each account's dated readings.

The funds tab (Symbol, Title, ER, Type) becomes `securities`: the symbol, its name, the expense ratio and the
type as its asset class, with the symbol as its quote unless it can't be one (cash, a name).

An account tab has the date in column A and the account's total in B, then a group of columns per fund: the
fund's symbol (and its type, beside it) in the first row, and in the second what each column holds:

  Shares, Val   shares held and their value
  Per, Val      a share of a target-date fund the plan held, and that share's value (the fund itself is
                named in a Fund column); the value is kept, with no shares, under the underlying fund
  <label>, Val  a balance under a label (Safety-Net cash), kept as a value
  Val           a value alone, under a name in the first row (one per child in an education tab)

Each dated row becomes `holdings` rows (as_of, account_id, symbol, shares, value), one per fund with a value.
A first-row name that isn't a symbol (a child's name, say) is kept with a blank symbol, so the account's
total is right. Several tabs can map to one account (a 401(k)'s pre-tax and rollover money); their readings
on the same date add up. Rows are read unformatted, so dates are serial day counts and money plain numbers.
"""

import re

import pandas as pd

from .legacy import parse_date, parse_money

LEGACY_DOC = "legacy:investments"
SYMBOL = re.compile(r"^[A-Z][A-Z0-9]{1,9}$")
COLUMNS = ["as_of", "account_id", "symbol", "shares", "value", "doc_id"]


def _text(v):
    return str(v).strip() if v is not None else ""


def _symbol(text):
    t = _text(text)
    if t.lower() == "cash":
        return "cash"
    return t if SYMBOL.match(t) else ""


def securities(rows):
    """The funds tab's rows (header first) to securities rows, and notes about rows left out."""
    if not rows:
        return pd.DataFrame(columns=["symbol", "name", "asset_class", "expense_ratio", "quote", "wash_group"]), []
    header = [_text(h).lower() for h in rows[0]]
    at = {k: header.index(k) for k in ("symbol", "title", "er", "type") if k in header}
    if "symbol" not in at:
        raise ValueError("funds tab: no Symbol column")
    out, notes, seen = [], [], set()
    for row in rows[1:]:
        row = list(row) + [""] * (len(header) - len(row))
        symbol = _symbol(row[at["symbol"]])
        if not symbol:
            if _text(row[at["symbol"]]):
                notes.append(f"funds: {_text(row[at['symbol']])!r} is not a symbol, left out")
            continue
        if symbol in seen:
            notes.append(f"funds: {symbol} listed twice, the first kept")
            continue
        seen.add(symbol)
        er = row[at["er"]] if "er" in at else ""
        if isinstance(er, (int, float)) and not isinstance(er, bool):
            er = round(float(er), 6)  # unformatted, 0.11% reads as 0.0011
        elif not re.match(r"^\d+(\.\d+)?%?$", _text(er)):
            er = ""  # #N/A and the like
        out.append({"symbol": symbol, "name": _text(row[at["title"]]) if "title" in at else "",
                    "asset_class": _text(row[at["type"]]) if "type" in at else "", "expense_ratio": er,
                    "quote": "" if symbol == "cash" else symbol, "wash_group": ""})
    return pd.DataFrame(out, columns=["symbol", "name", "asset_class", "expense_ratio", "quote", "wash_group"]), notes


def _roles(second):
    roles = []
    for i, v in enumerate(second):
        t = _text(v).lower()
        if i < 2:
            roles.append("")  # Date, Value
        elif t in ("shares", "per", "val", "fund"):
            roles.append(t)
        else:
            roles.append("label" if t else "")
    return roles


def groups(first, second):
    """[(value column, shares column or None, symbol, name)] for an account tab's two header rows."""
    width = max(len(first), len(second))
    first = list(first) + [""] * (width - len(first))
    roles = _roles(list(second) + [""] * (width - len(second)))
    out = []
    for j, role in enumerate(roles):
        if role != "val":
            continue
        before = roles[j - 1] if j >= 3 else ""
        start = j - 1 if before in ("shares", "per", "label") else j
        name = _text(first[start])
        out.append((j, j - 1 if before == "shares" else None, _symbol(name), name))
    return out


def holdings(rows, account_id):
    """An account tab's rows to holdings rows for account_id, and notes about rows left out."""
    if len(rows) < 3:
        return pd.DataFrame(columns=COLUMNS), []
    cols = groups(rows[0], rows[1])
    out, notes = [], []
    for n, row in enumerate(rows[2:], start=3):
        row = list(row)
        if not row or _text(row[0]) == "":
            continue
        try:
            as_of = parse_date(row[0]).isoformat()
        except ValueError:
            notes.append(f"{account_id}: row {n}: {row[0]!r} is not a date, left out")
            continue
        for value_at, shares_at, symbol, _ in cols:
            cell = row[value_at] if value_at < len(row) else ""
            if _text(cell) == "":
                continue
            value = round(parse_money(cell), 2)
            shares = ""
            if shares_at is not None and shares_at < len(row) and _text(row[shares_at]) != "":
                shares = round(parse_money(row[shares_at]), 6)
            if value == 0 and shares in ("", 0):
                continue
            out.append({"as_of": as_of, "account_id": account_id, "symbol": symbol, "shares": shares, "value": value})
    return pd.DataFrame(out, columns=COLUMNS[:-1]), notes


def merge(frames):
    """Holdings from several tabs as one table: readings of one fund in one account on one date add up."""
    frames = [f for f in frames if f is not None and len(f)]
    if not frames:
        return pd.DataFrame(columns=COLUMNS)
    df = pd.concat(frames, ignore_index=True)
    df["shares"] = pd.to_numeric(df["shares"], errors="coerce")
    df["value"] = pd.to_numeric(df["value"], errors="coerce")
    out = (df.groupby(["as_of", "account_id", "symbol"], sort=True, dropna=False)
             .agg(shares=("shares", lambda s: round(s.sum(), 6) if s.notna().any() else ""),
                  value=("value", lambda s: round(s.sum(), 2)))
             .reset_index())
    out["doc_id"] = LEGACY_DOC
    return out[COLUMNS]
