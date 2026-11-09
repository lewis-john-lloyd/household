"""Schwab Equity Awards: the Awards page's grant details, copied as text (each grant's award, its vested and
unvested shares, and its vesting schedule), saved as a .txt in the inbox.

The page copies as cells separated by tabs and line breaks. A grant starts with its award date, symbol and
award type (03/04/2001, ACME, RSU), then its price, shares granted, unvested market value and agreement
status; then Award ID, Vested, Unvested, Canceled and their values; then Vesting Schedule: each vest date
and its shares. The dividend equivalents that follow are left out.

Each schedule row becomes a vest_schedule row (grant_id, award_date, symbol, award_type, grant_price, expires,
vest_date, shares),
keyed by grant and vest date. A later copy of the page is the schedule as it stands, so the views and the
projection read each grant's rows from its latest document.

Checks: vested, unvested and canceled shares add up to the shares granted, and the schedule adds up to the
shares granted (less any canceled).
"""

import re
from datetime import datetime

import pandas as pd

from .common import ParseError, Result

NAME = "schwab_awards"
KIND = "vest_schedule"
COLUMNS = ["grant_id", "award_date", "symbol", "award_type", "grant_price", "expires", "vest_date", "shares"]
SHARE_AWARDS = ("RSU", "PSU", "RS")  # shares delivered at vest, so a vest is pay; an option's vest is not
DATE = re.compile(r"^\d{2}/\d{2}/\d{4}$")
SYMBOL = re.compile(r"^[A-Z][A-Z.]{0,5}$")
TYPES = ("RSU", "PSU", "ISO", "NSO", "NQ", "ESPP", "RS")


def sniff(text):
    head = text[:5000]
    return "Vesting Schedule" in head and "Award ID" in head and "Grant Agreement Status" in head


def _day(text):
    return datetime.strptime(text, "%m/%d/%Y").date().isoformat()


def _count(text, what, grant):
    t = text.replace(",", "").replace("$", "").strip()
    try:
        return float(t)
    except ValueError as e:
        raise ParseError(f"{NAME}: {grant or 'a grant'}: {what} is not a number: {text!r}") from e


def _starts(cells, i):
    return i + 2 < len(cells) and DATE.match(cells[i]) and SYMBOL.match(cells[i + 1]) and cells[i + 2].upper() in TYPES


def parse(text):
    cells = [c.strip() for c in re.split(r"[\t\r\n]+", text) if c.strip()]
    starts = [i for i in range(len(cells)) if _starts(cells, i)]
    if not starts:
        raise ParseError(f"{NAME}: no grant found (an award date, symbol and award type)")
    rows, notes = [], []
    for n, at in enumerate(starts):
        block = cells[at : starts[n + 1] if n + 1 < len(starts) else len(cells)]
        award_date, symbol, award_type = _day(block[0]), block[1], block[2].upper()
        grant_price = _count(block[3], "Award Price", "")
        granted = _count(block[4], "Granted", "")
        try:
            k = block.index("Canceled")
            grant_id = block[k + 1]
            vested, unvested, canceled = (
                _count(block[k + j], what, grant_id) for j, what in ((2, "Vested"), (3, "Unvested"), (4, "Canceled"))
            )
            s = block.index("Vesting Schedule")
        except (ValueError, IndexError) as e:
            raise ParseError(f"{NAME}: the grant of {block[0]} has no Award ID or Vesting Schedule") from e
        if abs(vested + unvested + canceled - granted) > 0.001:
            raise ParseError(
                f"{NAME}: {grant_id}: {vested:g} vested, {unvested:g} unvested and {canceled:g} canceled "
                f"are not the {granted:g} granted"
            )
        schedule, j = [], s + 1
        while j < len(block) and block[j] in ("Vest Date", "# of Shares"):
            j += 1
        while j + 1 < len(block) and DATE.match(block[j]):
            schedule.append((_day(block[j]), _count(block[j + 1], "shares", grant_id)))
            j += 2
        total = sum(sh for _, sh in schedule)
        if abs(total - granted) > 0.001 and abs(total - (granted - canceled)) > 0.001:
            raise ParseError(f"{NAME}: {grant_id}: the schedule's {total:g} shares are not the {granted:g} granted")
        rows += [
            {
                "grant_id": grant_id,
                "award_date": award_date,
                "symbol": symbol,
                "award_type": award_type,
                "grant_price": grant_price,
                "expires": "",
                "vest_date": d,
                "shares": sh,
            }
            for d, sh in schedule
        ]
        notes.append(
            f"{grant_id} ({symbol} {award_type} of {award_date}): {granted:g} granted, {vested:g} vested, "
            f"{unvested:g} unvested, {len(schedule)} vests to {schedule[-1][0] if schedule else 'none'}"
        )
    df = pd.DataFrame(rows, columns=COLUMNS)
    if df.duplicated(["grant_id", "vest_date"]).any():
        raise ParseError(f"{NAME}: a grant lists one vest date twice")
    return Result(
        kind=KIND,
        parser=NAME,
        label=f"schwab_awards_{df['vest_date'].max()}",
        covers_from=df["vest_date"].min(),
        covers_to=df["vest_date"].max(),
        tables={KIND: df},
        keys={KIND: ["grant_id", "vest_date"]},
        notes=notes,
    )


def in_force(schedule):
    """Each grant's schedule from its latest document (rows in ledger order: the last doc_id of a grant wins)."""
    if schedule is None or schedule.empty:
        return pd.DataFrame(columns=COLUMNS + ["doc_id"])
    latest = schedule.groupby("grant_id")["doc_id"].last()
    return schedule[schedule["doc_id"] == schedule["grant_id"].map(latest)]
