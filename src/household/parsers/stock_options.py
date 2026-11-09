"""A stock option grant's vesting schedule, copied as text from the plan's Stock Options page (Fidelity Stock
Plan Services' layout), saved as a .txt in the inbox.

Each grant: its ID, "Granted:" and its date, "Expires:" and its date, then its grant (exercise) price and the
total granted, and the vesting schedule, one tranche a line: vesting date, status (Unvested, Vested,
Exercised...), quantity and, for unvested ones, outstanding value. The page lists the latest tranche first, on
the grant's own line.

Each tranche becomes a vest_schedule row (award_type OPTION, the grant price and expiry with it). The
schedule must add up to the total granted, or the page is refused. An option's vesting is no income, so the
tax projection leaves it out; exercising one is.
"""

import re
from datetime import datetime

import pandas as pd

from .common import ParseError, Result
from .schwab_awards import COLUMNS

NAME = "stock_options"
KIND = "vest_schedule"
DATE = r"[A-Z][a-z]{2}-\d{2}-\d{4}"
TRANCHE = re.compile(
    rf"({DATE})\s+(Unvested|Vested|Exercised|Expired|Canceled|Cancelled|Forfeited)\s+([\d,]+(?:\.\d+)?)"
)


def sniff(text):
    head = text[:3000]
    return "STOCK OPTIONS" in head and "Grant price" in head and "Vesting date" in head and "Granted:" in text


def _day(text):
    return datetime.strptime(text, "%b-%d-%Y").date().isoformat()


def _num(text):
    return float(text.replace(",", "").replace("$", ""))


def parse(text):
    starts = [m.start() for m in re.finditer(r"(?m)^\s*(\S+)\s*\n\s*Granted:", text)]
    if not starts:
        raise ParseError(f"{NAME}: no grant (an ID followed by Granted:)")
    rows, notes = [], []
    for n, at in enumerate(starts):
        block = text[at : starts[n + 1] if n + 1 < len(starts) else len(text)]
        grant_id = block.split()[0]
        granted = re.search(rf"Granted:\s*({DATE})", block)
        expires = re.search(rf"Expires:\s*({DATE})", block)
        terms = re.search(r"\$([\d,]+\.\d+)\s+([\d,]+(?:\.\d+)?)\s", block[expires.end() :] if expires else "")
        if not (granted and expires and terms):
            raise ParseError(f"{NAME}: {grant_id}: no grant date, expiry, price and total")
        price, total = _num(terms.group(1)), _num(terms.group(2))
        tranches = [(_day(d), status, _num(q)) for d, status, q in TRANCHE.findall(block)]
        if abs(sum(q for _, _, q in tranches) - total) > 0.001:
            raise ParseError(
                f"{NAME}: {grant_id}: the tranches' {sum(q for _, _, q in tranches):g} shares are not "
                f"the {total:g} granted"
            )
        rows += [
            {
                "grant_id": grant_id,
                "award_date": _day(granted.group(1)),
                "symbol": "",
                "award_type": "OPTION",
                "grant_price": price,
                "expires": _day(expires.group(1)),
                "vest_date": d,
                "shares": q,
            }
            for d, _, q in sorted(tranches)
        ]
        unvested = sum(q for _, s, q in tranches if s == "Unvested")
        notes.append(
            f"{grant_id} (options at {price:,.2f}, granted {_day(granted.group(1))}, expire "
            f"{_day(expires.group(1))}): {total:g} granted, {unvested:g} unvested, {len(tranches)} tranches "
            f"to {max(d for d, _, _ in tranches)}"
        )
    df = pd.DataFrame(rows, columns=COLUMNS)
    if df.duplicated(["grant_id", "vest_date"]).any():
        raise ParseError(f"{NAME}: a grant lists one vesting date twice")
    return Result(
        kind=KIND,
        parser=NAME,
        label=f"stock_options_{df['vest_date'].max()}",
        covers_from=df["vest_date"].min(),
        covers_to=df["vest_date"].max(),
        tables={KIND: df},
        keys={KIND: ["grant_id", "vest_date"]},
        notes=notes,
    )
