"""The plan's inputs: who the household is, its money today and while working, and a strategy for drawing it down."""

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from .. import taxdata

LAW_YEAR = max(taxdata.MFJ_BRACKETS)
ORDERS = ("conventional", "proportional", "bracket")
ORDER_NAMES = {
    "conventional": "Taxable, then pre-tax, then Roth",
    "proportional": "In proportion to balances",
    "bracket": "Pre-tax to the 12% bracket, then taxable",
}
CONVERSIONS = (0.0, 0.12, 0.22, 0.24)
RULES = ("constant", "guardrails", "vpw")
RULE_NAMES = {
    "constant": "Constant real spending",
    "guardrails": "Guardrails (Guyton-Klinger)",
    "vpw": "Variable percentage withdrawal",
}
BRACKET_ORDER_RATE = 0.12
GUARD_BAND, GUARD_STEP, GUARD_LAST_YEARS = 0.20, 0.10, 15
VPW_RETURNS = (0.050, 0.019)  # the real stock and bond returns VPW's table assumes (Bogleheads VPW)
HEIR_RATE = 0.24  # pre-tax money left at the end is counted after this much tax
GAIN_RATE_AT_END = 0.15  # and the taxable accounts' unrealized gain after this
TRUSTEES_CUT_YEAR = 2034  # the year the 2025 Trustees Report projects combined OASDI reserves run out
ITERATIONS = 8
SEED = 1928
DEFAULTS: dict[str, Any] = {
    "plan_to_age": 95,
    "success_target": 0.90,
    "inflation": 0.03,
    "cagrs": (0.05, 0.07, 0.09),
    "monte_carlo_runs": 1000,
    "taxable_yield": 0.02,
    "health_pre65": 24_000,
    "health_supplement": 3_000,
    "claim_age": 67,
}


# --- inputs -----------------------------------------------------------------------------------------------


@dataclass
class Person:
    name: str
    born: date
    claim_age: int = 67
    earnings: dict = field(default_factory=dict)  # {year: FICA earnings}, the statement's record
    statement: dict = field(default_factory=dict)  # {age: monthly}, the statement's estimates
    statement_year: int = 0
    fra_monthly: float = 0.0  # a benefit to use when there is no statement (social_security_monthly:<name>)
    medicare_wages: float = 0.0  # the last posted year's, for IRMAA while working


@dataclass
class Household:
    start: int
    people: list
    pre_plan: dict  # {person: pre-tax money in workplace plans}
    pre_ira: dict
    roth: dict
    roth_basis: dict  # Roth money that comes out free before 59 1/2 (contributions, old conversions)
    taxable: float
    basis: float
    hsa: float
    contributions: dict  # {person: {"pre_plan": x, "roth": y}} a year while working
    hsa_contribution: float
    taxable_savings: float
    spending: dict  # {year: spending before health cover}
    hsa_spending: float = 0.0  # spending the HSA may pay (medical categories)
    health_pre65: float = DEFAULTS["health_pre65"]
    health_supplement: float = DEFAULTS["health_supplement"]
    stock_share: float = 0.7
    taxable_yield: float = DEFAULTS["taxable_yield"]
    inflation: float = DEFAULTS["inflation"]
    cagrs: tuple = DEFAULTS["cagrs"]
    plan_to_age: int = DEFAULTS["plan_to_age"]
    success_target: float = DEFAULTS["success_target"]
    monte_carlo_runs: int = DEFAULTS["monte_carlo_runs"]
    ss_cut: float = 0.0
    ss_cut_from: int = TRUSTEES_CUT_YEAR
    pending: dict = field(default_factory=dict)  # {person: {year free: amount}} conversions still seasoning
    budget: dict = field(default_factory=dict)  # what spending_path was built from, for budget_table

    @property
    def end(self):
        return max(p.born.year for p in self.people) + self.plan_to_age

    @property
    def years(self):
        return list(range(self.start, self.end + 1))


@dataclass(frozen=True)
class Strategy:
    order: str = "bracket"
    convert_to: float = 0.22
    rule: str = "constant"
    sepp: bool = False
    claims: tuple = ()  # ((person, age), ...) overriding the claiming ages

    @property
    def key(self):
        parts = [self.order, f"to{int(round(self.convert_to * 100))}" if self.convert_to else "noconv", self.rule]
        if self.sepp:
            parts.append("72t")
        parts += [f"{n}{a}" for n, a in self.claims]
        return "/".join(parts)

    @property
    def label(self):
        bits = [
            ORDER_NAMES[self.order],
            f"convert to the top of {int(round(self.convert_to * 100))}%" if self.convert_to else "no conversions",
            RULE_NAMES[self.rule].lower(),
        ]
        if self.sepp:
            bits.append("72(t) to 59 1/2")
        if self.claims:
            bits.append("claiming at " + ", ".join(f"{a} ({n})" for n, a in self.claims))
        return "; ".join(bits)


def parse_strategy(text):
    """A strategy from its key ("bracket/to22/constant", "proportional/noconv/vpw/72t"), or None."""
    parts = [p.strip().lower() for p in str(text or "").split("/") if p.strip()]
    if len(parts) < 3 or parts[0] not in ORDERS or parts[2] not in RULES:
        return None
    m = re.fullmatch(r"to(\d\d)", parts[1])
    if parts[1] != "noconv" and not (m and int(m.group(1)) / 100 in CONVERSIONS):
        return None
    return Strategy(parts[0], int(m.group(1)) / 100 if m else 0.0, parts[2], "72t" in parts[3:])
