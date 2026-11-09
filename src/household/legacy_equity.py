"""Read the legacy tax workbook's stock-plan tabs: each vest and each sale, for the years before the plan's own
records begin.

The grants tab has a row per vest date: the market value of a share (fmv_vest), the shares vested and the
shares left after the shares withheld for tax (shares_gross, shares_net), then a column per grant (headed by
the year it was awarded) with the shares that grant vested. The vest's net shares are split among its grants
in proportion to those columns; grant_columns maps each column header to the plan's grant id.

The sales tab is Form 8949 by lot: a row per part of a lot sold, with the date acquired (the vest), the shares,
the date sold, the price, the basis before any wash sale and the proceeds. The parts the old sheet split a lot
into for its wash sales are added back together, so each (sale, vest) is one sale of shares; the wash sales
are worked out again from these facts (lots.py).

Rows are read unformatted: dates are serial day counts and money plain numbers.
"""

import re

import pandas as pd

from .legacy import parse_date, parse_money

LEGACY_DOC = "legacy:equity"
VEST_COLUMNS = ["vest_date", "grant_id", "award_date", "net_shares", "fmv", "deposit_date", "doc_id"]
SALE_COLUMNS = [
    "sale_date",
    "grant_id",
    "vest_date",
    "shares",
    "sale_price",
    "proceeds",
    "basis",
    "gain",
    "term",
    "doc_id",
]


def _text(v):
    return re.sub(r"\s+", " ", str(v)).strip().lower() if v is not None else ""


def _date(v):
    if v is None or str(v).strip() == "":
        return ""
    try:
        return parse_date(v).isoformat()
    except ValueError:
        return ""


def _money(v):
    if v is None or str(v).strip() == "":
        return None
    try:
        return parse_money(v)
    except ValueError:
        return None


def _column(header, *words):
    """The index of the first header cell containing every word, or None."""
    for i, h in enumerate(header):
        if all(w in _text(h) for w in words):
            return i
    return None


def _cell(row, i):
    return row[i] if i is not None and i < len(row) else ""


def vests(rows, grant_columns):
    """The grants tab's rows (header first) to equity_vests rows, and notes."""
    if not rows:
        return pd.DataFrame(columns=VEST_COLUMNS), ["legacy equity: the grants tab is empty"]
    header = [str(h).strip() for h in rows[0]]
    day, fmv, net = _column(header, "date"), _column(header, "fmv"), _column(header, "shares_net")
    gross = {g: header.index(c) for c, g in grant_columns.items() if c in header}
    notes = [f"legacy equity: no grant column '{c}' in the grants tab" for c in grant_columns if c not in header]
    out = []
    for row in rows[1:]:
        d, price, shares = _date(_cell(row, day)), _money(_cell(row, fmv)), _money(_cell(row, net))
        if not (d and price and shares):
            continue
        parts = {g: _money(_cell(row, i)) or 0.0 for g, i in gross.items()}
        total = sum(parts.values())
        if total <= 0:
            notes.append(f"legacy equity: the {d} vest names no grant it came from; left out")
            continue
        for g, n in parts.items():
            if n > 0:
                out.append(
                    {
                        "vest_date": d,
                        "grant_id": g,
                        "award_date": "",
                        "net_shares": round(shares * n / total, 3),
                        "fmv": price,
                        "deposit_date": "",
                        "doc_id": LEGACY_DOC,
                    }
                )
    return pd.DataFrame(out, columns=VEST_COLUMNS), notes


def sales(rows, vested):
    """The sales tab's rows (header first) to equity_sales rows, one per (sale date, vest date, grant), and
    notes. vested: equity_vests rows, for the grant each vest came from (shared out when several vested that day)."""
    if not rows:
        return pd.DataFrame(columns=SALE_COLUMNS), ["legacy equity: the sales tab is empty"]
    header = [str(h) for h in rows[0]]
    acq, sold = _column(header, "date acquired"), _column(header, "date sold")
    shares_at, price_at = _column(header, "shares"), _column(header, "sale $")
    basis_at, proceeds_at = _column(header, "unadjusted basis"), _column(header, "proceeds")
    parts = {}
    for row in rows[1:]:
        a, s, n = _date(_cell(row, acq)), _date(_cell(row, sold)), _money(_cell(row, shares_at))
        if not (a and s and n):
            continue
        p = parts.setdefault(
            (s, a), {"shares": 0.0, "proceeds": 0.0, "basis": 0.0, "price": _money(_cell(row, price_at))}
        )
        p["shares"] += n
        p["proceeds"] += _money(_cell(row, proceeds_at)) or 0.0
        p["basis"] += _money(_cell(row, basis_at)) or 0.0
    grants = {}
    for r in vested.to_dict("records"):
        grants.setdefault(r["vest_date"], []).append((r["grant_id"], float(r["net_shares"])))
    out, notes = [], []
    for (s, a), p in sorted(parts.items()):
        who = grants.get(a)
        if not who:
            notes.append(f"legacy equity: the {s} sale of shares vested {a} has no vest to come from; left out")
            continue
        total = sum(n for _, n in who)
        for g, n in who:
            share = n / total
            proceeds, basis = p["proceeds"] * share, p["basis"] * share
            long = s > a.replace(a[:4], str(int(a[:4]) + 1), 1)
            out.append(
                {
                    "sale_date": s,
                    "grant_id": g,
                    "vest_date": a,
                    "shares": round(p["shares"] * share, 3),
                    "sale_price": p["price"] if p["price"] is not None else "",
                    "proceeds": round(proceeds, 2),
                    "basis": round(basis, 2),
                    "gain": round(proceeds - basis, 2),
                    "term": "long_term" if long else "short_term",
                    "doc_id": LEGACY_DOC,
                }
            )
    return pd.DataFrame(out, columns=SALE_COLUMNS), notes


def before(df, have, column):
    """df's rows dated before the earliest of have[column] (all of df when have is empty): the plan's own
    records, where they exist, win."""
    dates = [str(d) for d in have[column] if str(d).strip()] if have is not None and len(have) else []
    return df[df[column] < min(dates)] if dates else df
