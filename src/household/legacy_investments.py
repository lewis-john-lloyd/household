"""Read the legacy investments workbook: its list of funds and each account's dated readings.

The funds tab (Symbol, Title, ER, Type) becomes `securities`: the symbol, its name, the expense ratio and the
type as its asset class, with the symbol as its quote unless it can't be one (cash, a name).

An account tab has the date in column A and the account's total in B, then a group of columns per fund: the
fund's symbol (and its type, beside it) in the first row, and in the second what each column holds:

  Shares, Val   shares held and their value
  Per, Val      a share of a target-date fund the plan held, and that share's value (the fund itself is
                named in a Fund column); the value is kept, with no shares, under the underlying fund. A
                Per cell above 1 is a count of shares: later rows held the plan's funds directly and
                kept their shares in the same column
  <label>, Val  a balance under a label (Safety-Net cash), kept as a value
  Val           a value alone, under a name in the first row (one per child in an education tab)

Each dated row becomes `holdings` rows (as_of, account_id, symbol, source, shares, value), one per fund with a
value. A tab that is one money source of a workplace plan (its Roth money, say) names that source, so a plan
kept as one account keeps its parts apart.
A first-row name that isn't a symbol (a child's name, say) is kept with a blank symbol, so the account's
total is right. Several tabs can map to one account (a 401(k)'s pre-tax and rollover money); their readings
on the same date add up (a fund's readings in one source of one account on one date). Rows are read unformatted, so dates are serial day counts and money plain numbers.
"""

import re

import pandas as pd

from .legacy import parse_date, parse_money

LEGACY_DOC = "legacy:investments"
SYMBOL = re.compile(r"^[A-Z][A-Z0-9]{1,9}$")
COLUMNS = ["as_of", "account_id", "symbol", "source", "shares", "value", "doc_id"]


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
        out.append(
            {
                "symbol": symbol,
                "name": _text(row[at["title"]]) if "title" in at else "",
                "asset_class": _text(row[at["type"]]) if "type" in at else "",
                "expense_ratio": er,
                "quote": "" if symbol == "cash" else symbol,
                "wash_group": "",
            }
        )
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
    """[(value column, shares or share-of-fund column or None, its role, symbol, name)] for an account tab's two
    header rows."""
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
        out.append((j, j - 1 if before in ("shares", "per") else None, before, _symbol(name), name))
    return out


def holdings(rows, account_id, source=""):
    """An account tab's rows to holdings rows for account_id (the part of it source names, when the tab is one
    money source of a workplace plan), and notes about rows left out."""
    if len(rows) < 3:
        return pd.DataFrame(columns=COLUMNS[:-1]), []
    cols = groups(rows[0], rows[1])
    out, notes, dates = [], [], []
    for n, row in enumerate(rows[2:], start=3):
        row = list(row)
        if not row or _text(row[0]) == "":
            continue
        try:
            as_of = parse_date(row[0]).isoformat()
        except ValueError:
            notes.append(f"{account_id}: row {n}: {row[0]!r} is not a date, left out")
            continue
        if dates and as_of < dates[-1]:
            notes.append(
                f"{account_id}: row {n}: {as_of} comes before the row above it ({dates[-1]}): is its year a typo?"
            )
        dates.append(as_of)
        for value_at, shares_at, role, symbol, _ in cols:
            cell = row[value_at] if value_at < len(row) else ""
            if _text(cell) == "":
                continue
            value = round(parse_money(cell), 2)
            shares = ""
            if shares_at is not None and shares_at < len(row) and _text(row[shares_at]) != "":
                count = round(parse_money(row[shares_at]), 6)
                # a Per column holds a share of a target-date fund (0.54), until the plan's own funds were
                # held directly and the same column took their shares (5048.48)
                shares = count if role == "shares" or count > 1 else ""
            if value == 0 and shares in ("", 0):
                continue
            out.append(
                {
                    "as_of": as_of,
                    "account_id": account_id,
                    "symbol": symbol,
                    "source": source,
                    "shares": shares,
                    "value": value,
                }
            )
    df = pd.DataFrame(out, columns=COLUMNS[:-1])
    df.attrs["dates"] = sorted(set(dates))  # every date the tab has a row for, a row of zeros included
    return df, notes


def _carried(frame, dates):
    """frame's readings, plus its last reading carried to each of dates it has no row for that falls
    between its own first and last dates (another tab of the same account recorded that day; this one didn't)."""
    read = frame.attrs.get("dates") or sorted(set(frame["as_of"]))
    if not read:
        return frame
    extra = []
    for d in dates:
        if read[0] < d < read[-1] and d not in read:
            before = max(r for r in read if r < d)
            extra.append(frame[frame["as_of"] == before].assign(as_of=d))
    return pd.concat([frame] + extra, ignore_index=True) if extra else frame


def merge(frames):
    """Holdings from several tabs as one table: readings of one fund in one account on one date add up.

    Tabs of one account don't always share their dates: on a date one tab recorded and another skipped, the
    other's previous reading counts, so the account's total that day is whole.
    """
    frames = [f for f in frames if f is not None and len(f)]
    if not frames:
        return pd.DataFrame(columns=COLUMNS)
    by_account = {}
    for f in frames:
        for account in set(f["account_id"]):
            by_account.setdefault(account, set()).update(f.attrs.get("dates") or f["as_of"])
    frames = [_carried(f, sorted(by_account[f["account_id"].iloc[0]])) for f in frames]
    df = pd.concat(frames, ignore_index=True)
    df["shares"] = pd.to_numeric(df["shares"], errors="coerce")
    df["value"] = pd.to_numeric(df["value"], errors="coerce")
    out = (
        df.groupby(["as_of", "account_id", "symbol", "source"], sort=True, dropna=False)
        .agg(
            shares=("shares", lambda s: round(s.sum(), 6) if s.notna().any() else ""),
            value=("value", lambda s: round(s.sum(), 2)),
        )
        .reset_index()
    )
    out["doc_id"] = LEGACY_DOC
    return out[COLUMNS]


NET_WORTH_DOC = "legacy:net_worth"


def net_worth(rows, columns):
    """(property_values rows, loan_balances rows, notes) from the legacy Net Worth tab: columns maps a column
    header to ("value", property_id) or ("loan", loan_id). A blank or zero cell means nothing on that date (a
    home not yet bought or already sold, a loan not yet taken or paid off). Loans are kept as amounts owed."""
    values = pd.DataFrame(columns=["as_of", "property_id", "source", "value", "note", "doc_id"])
    loans = pd.DataFrame(columns=["as_of", "loan_id", "balance", "doc_id"])
    if not rows:
        return values, loans, ["net worth: the tab is empty"]
    header = [_text(h) for h in rows[0]]
    at = {h: i for i, h in enumerate(header) if h}
    missing = sorted(set(columns) - set(at))
    notes = [f"net worth: no column {m!r}" for m in missing]
    vals, owed = [], []
    for n, row in enumerate(rows[1:], start=2):
        if not row or not _text(row[0]):
            continue
        try:
            as_of = parse_date(row[0]).isoformat()
        except (TypeError, ValueError):
            notes.append(f"net worth: row {n}: {row[0]!r} is not a date, left out")
            continue
        for column, (kind, target) in columns.items():
            if column not in at or at[column] >= len(row):
                continue
            amount = parse_money(row[at[column]])
            if amount is None or abs(amount) < 0.005:
                continue
            if kind == "value":
                vals.append(
                    {
                        "as_of": as_of,
                        "property_id": target,
                        "source": "legacy",
                        "value": round(amount, 2),
                        "note": "",
                        "doc_id": NET_WORTH_DOC,
                    }
                )
            else:
                owed.append(
                    {"as_of": as_of, "loan_id": target, "balance": round(abs(amount), 2), "doc_id": NET_WORTH_DOC}
                )
    if vals:
        values = pd.DataFrame(vals, columns=values.columns).drop_duplicates(["as_of", "property_id"], keep="last")
    if owed:
        loans = pd.DataFrame(owed, columns=loans.columns).drop_duplicates(["as_of", "loan_id"], keep="last")
    return values.reset_index(drop=True), loans.reset_index(drop=True), notes
