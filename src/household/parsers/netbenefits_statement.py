"""Fidelity NetBenefits' statement for a workplace plan (Statement Details, saved as a PDF), for any period the
person picks: what the plan held in each fund at the period's start and at its end.

The Market Value of Your Account table gives each fund's shares, price and market value as of the day before
the period starts and as of its last day. Each fund held at either date becomes a holdings reading on that
date: the fund's name as the plan prints it is its symbol ("S&P 500 Index", a name wrapped onto the next line
joined back), with its shares and value. A run of quarterly statements gives a reading every quarter.

The plan's name is in the header but not its number, so the file's name finds the account (an account_id in
it, ingest.finish_investment_rows), as for other single-account downloads.

Checks, so a statement read wrong is refused rather than kept: every fund row has two shares, two prices and
two values; shares times price is the value (to the cent the price is cut to); and the funds add up to the Account
Totals at both dates.
"""

import re
from datetime import datetime, timedelta

import pandas as pd

from .common import ParseError, Result
from .fidelity import COLUMNS

NAME = "netbenefits_statement"
KIND = "plan_statement"
HOLDINGS = ["as_of", "account_id", "symbol", "source", "shares", "value"]
NUM = r"([\d,]+\.\d{3})"
MONEY = r"\$([\d,]+\.\d\d)"
FUND_ROW = re.compile(rf"^(?P<name>\S.*?)\s+{NUM}\s+{NUM}\s+{MONEY}\s+{MONEY}\s+{MONEY}\s+{MONEY}\s*$")
TOTALS = re.compile(rf"^Account Totals\s+{MONEY}\s+{MONEY}\s*$", re.M)
PERIOD = re.compile(r"Statement Period:\s*(\d\d/\d\d/\d{4})\s+to\s+(\d\d/\d\d/\d{4})")


def sniff(text):
    # a page printed from the browser may carry its title ("Fidelity NetBenefits - Statement Details") first
    head = text.lstrip()[:3000]
    return (
        "Statement Details" in head[:200]
        and "Retirement Savings Statement" in head
        and "Market Value of Your Account" in text
    )


def _money(text):
    return float(text.replace(",", ""))


def _day(text):
    return datetime.strptime(text, "%m/%d/%Y").date()


def _fragment(line):
    """A line that only carries the rest of a fund's name: words, no amounts."""
    s = line.strip()
    return bool(s) and "$" not in s and not re.search(r"\d+\.\d", s) and len(s) <= 40 and not s.isupper()


def funds(text):
    """[(name, shares then, shares now, price then, price now, value then, value now)] from the market value
    table: a name that wraps onto the next line is joined to it."""
    start = text.find("Market Value of Your Account")
    end = text.find("Account Totals", start)
    if start < 0 or end < 0:
        raise ParseError(f"{NAME}: no Market Value of Your Account table with its Account Totals")
    lines = text[start:end].splitlines()
    out = []
    for i, line in enumerate(lines):
        m = FUND_ROW.match(line.strip())
        if not m:
            continue
        name = m.group("name")
        if i + 1 < len(lines) and _fragment(lines[i + 1]) and not FUND_ROW.match(lines[i + 1].strip()):
            name = f"{name} {lines[i + 1].strip()}"
        out.append((re.sub(r"\s+", " ", name),) + tuple(_money(m.group(k)) for k in range(2, 8)))
    return out


def parse(text):
    period = PERIOD.search(text)
    if not period:
        raise ParseError(f"{NAME}: no Statement Period")
    first, last = _day(period.group(1)), _day(period.group(2))
    if last < first:
        raise ParseError(f"{NAME}: the period ends before it starts")
    before = (first - timedelta(days=1)).isoformat()
    after = last.isoformat()
    rows = funds(text)
    if not rows:
        raise ParseError(f"{NAME}: no fund rows in the market value table")
    totals = TOTALS.search(text)
    if not totals:
        raise ParseError(f"{NAME}: no Account Totals")
    total_then, total_now = _money(totals.group(1)), _money(totals.group(2))
    held = []
    for name, shares0, shares1, price0, price1, value0, value1 in rows:
        for shares, price, value in ((shares0, price0, value0), (shares1, price1, value1)):
            # the price is shown cut to the cent (a fund at 12.3456 shows 12.34): allow a cent on every share
            if abs(shares * price - value) > 0.01 * shares + 0.02:
                raise ParseError(f"{NAME}: {name}: {shares} shares at {price} is not {value}")
        if shares0 or value0:
            held.append(
                {"as_of": before, "account_id": "", "symbol": name, "source": "", "shares": shares0, "value": value0}
            )
        if shares1 or value1:
            held.append(
                {"as_of": after, "account_id": "", "symbol": name, "source": "", "shares": shares1, "value": value1}
            )
    for day, total in ((before, total_then), (after, total_now)):
        got = sum(r["value"] for r in held if r["as_of"] == day)
        if abs(got - total) > 0.05:
            raise ParseError(f"{NAME}: the funds come to {got:.2f} on {day}, not the Account Totals' {total:.2f}")
    plan = re.search(r"^\s*(\S.*?)\s{2,}Retirement Savings Statement", text, re.M)
    name = plan.group(1).strip() if plan else "plan"
    return Result(
        kind=KIND,
        parser=NAME,
        label=f"netbenefits_{after}",
        covers_from=before,
        covers_to=after,
        tables={"investment_activity": pd.DataFrame(columns=COLUMNS), "holdings": pd.DataFrame(held, columns=HOLDINGS)},
        keys={"investment_activity": ["activity_id"], "holdings": ["as_of", "account_id", "source", "symbol"]},
        notes=[
            f"{name}: statement {first.isoformat()} to {after}, {len(rows)} funds, "
            f"{total_then:,.2f} then and {total_now:,.2f} at the end"
        ],
    )
