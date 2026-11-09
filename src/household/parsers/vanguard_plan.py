"""A Vanguard workplace plan's quarterly statement (PDF): the plan's balance in each money source at the
quarter's end.

The statement's Source Activity table gives each source (Pre-Tax 401(K), Employer Matching, Employee
Rollover, After-Tax 401(K), each year's Roth In Plan Conversion...) with its opening balance, contributions,
activity and closing balance. A download of the plan's activity (OFX) tags each transaction with a source
but gives positions only for the whole plan, so this is where the plan's Roth and pre-tax money are told
apart. Each source with a closing balance becomes a holdings reading on the quarter's last day: no fund
(symbol blank), the source's name as `source` (pre_tax_401k, 2025_roth_in_plan_conversion), its closing
balance as `value`.

The fund table (Investment Activity: beginning balance, contributions, other transactions, market
gain or loss, dividends, ending balance) is read to check the statement, not kept: the plan's download
gives each fund's shares.

Checks, so a statement read wrong is refused rather than kept: each source's and each fund's row adds up,
the sources add up to the Total Value, and the funds add up to the Total Invested, which is the Total Value.

The plan is named by its number in the statement's header (PLAN 000123): its last four find the account in
the accounts table's last4, as an OFX download does (ingest.finish_investment_rows).
"""

import re
from datetime import datetime

import pandas as pd

from .common import ParseError, Result
from .fidelity import COLUMNS

NAME = "vanguard_plan"
KIND = "plan_statement"
MONEY = re.compile(r"(-\s?)?\$([\d,]+\.\d\d)")
HOLDINGS = ["as_of", "account_id", "symbol", "source", "shares", "value"]


def sniff(text):
    head = text[:20000]
    return "vanguard.com" in text and "ACCOUNT SUMMARY:" in head and "Source Activity" in text and "Investment Activity" in text


def _amounts(line):
    return [round((-1 if m.group(1) else 1) * float(m.group(2).replace(",", "")), 2) for m in MONEY.finditer(line)]


def _name(line):
    return " ".join(line[:MONEY.search(line).start()].split()).rstrip("-").strip() if MONEY.search(line) else ""


def source_key(name):
    """A source's name as a key: "Bonus Pre-Tax 401(K)" -> bonus_pre_tax_401k."""
    return re.sub(r"[^a-z0-9]+", "_", name.lower().replace("401(k)", "401k")).strip("_")


def _adds_up(parts, total, what):
    if abs(round(sum(parts), 2) - total) > 0.011:
        raise ParseError(f"{NAME}: {what}: {round(sum(parts), 2):,.2f} is not the {total:,.2f} it shows")


def parse(text):
    period = re.search(r"ACCOUNT SUMMARY:\s*(\d{2}/\d{2}/\d{4})\s*-\s*(\d{2}/\d{2}/\d{4})", text)
    if not period:
        raise ParseError(f"{NAME}: no statement period")
    start, end = (datetime.strptime(d, "%m/%d/%Y").date().isoformat() for d in period.groups())
    plan = re.search(r"PLAN\W{0,6}(\d{4,})", text)
    if not plan:
        raise ParseError(f"{NAME}: no plan number in the header")
    lines = text.splitlines()

    sources, total_value, section = [], None, ""
    for line in lines:
        if "Source Activity" in line:
            section = "sources"
        elif "Investment Activity" in line:
            section = "funds"
        if section != "sources":
            continue
        amounts, name = _amounts(line), _name(line)
        if name.startswith("Total Value") and amounts:
            total_value, section = amounts[-1], ""
        elif len(amounts) == 4 and name:
            opening, added, activity, closing = amounts
            _adds_up([opening, added, activity], closing, name)
            sources.append((name, closing))
    if not sources or total_value is None:
        raise ParseError(f"{NAME}: no Source Activity table")
    _adds_up([c for _, c in sources], total_value, "the sources")

    funds, invested = [], None
    try:
        at = next(i for i, line in enumerate(lines) if "Investment Activity" in line)
    except StopIteration as e:
        raise ParseError(f"{NAME}: no Investment Activity table") from e
    for i in range(at + 1, len(lines)):
        amounts, name = _amounts(lines[i]), _name(lines[i])
        if name.startswith("Total Invested"):
            invested = amounts
            break
        if len(amounts) == 6 and name:
            following = lines[i + 1].strip() if i + 1 < len(lines) else ""
            if following and not MONEY.search(following) and not following.startswith(("Total", "*")):
                name = f"{name} {' '.join(following.split())}"  # a fund name wrapped onto the next line
            _adds_up(amounts[:5], amounts[5], name)
            funds.append((name, amounts[5]))
    if not invested or len(invested) != 6:
        raise ParseError(f"{NAME}: no Total Invested row")
    _adds_up([e for _, e in funds], invested[5], "the funds")
    _adds_up([invested[5]], total_value, "the funds' Total Invested against the sources' Total Value")

    account = f"acct:{plan.group(1)[-4:]}"
    held = pd.DataFrame([{"as_of": end, "account_id": account, "symbol": "", "source": source_key(n), "shares": "",
                          "value": closing} for n, closing in sources if closing], columns=HOLDINGS)
    if held["source"].duplicated().any():
        raise ParseError(f"{NAME}: two sources with one name")
    roth = sum(c for n, c in sources if "roth" in n.lower())
    return Result(kind=KIND, parser=NAME, label=f"vanguard_plan_{end}", covers_from=start, covers_to=end,
                  tables={"investment_activity": pd.DataFrame(columns=COLUMNS), "holdings": held},
                  keys={"investment_activity": ["activity_id"], "holdings": ["as_of", "account_id", "source", "symbol"]},
                  notes=[f"account ..{plan.group(1)[-4:]}: plan statement {start} to {end}, {len(held)} sources "
                         f"worth {total_value:,.2f} ({roth:,.2f} Roth) in {len(funds)} funds"])
