"""Retirement: when the household can stop working, and how to draw its savings down, with the taxes and the
age rules that decide what a dollar in each kind of account is worth.

Everything is in today's dollars. The model runs a year at a time from next year to the year the younger adult
reaches the plan's last age (retirement_plan_to_age, default 95), with both alive throughout.

While working, each account grows at the year's return and takes the contributions the last 12 months show
(investment_activity: workplace plans, IRAs and HSAs, each row's money source deciding pre-tax or Roth), and
the taxable accounts take the household's taxable savings (setting retirement_taxable_savings, else the last
12 months: what the savings categories took from the bank, less what went into IRAs and 529s, plus the value
of shares that vested). Social Security credits each year worked at the last posted year's earnings.

From the retirement year on, each year:
  1. Spending is the last 12 months' actual spending (the categories that are spending: not income, savings,
     transfers or retirement_leave_out), each category or group replaced from a year on by the
     retirement_spending:<name> settings, less each loan's payments after its payoff, plus health cover:
     retirement_health_pre65 a year for the household (half for each adult under 65), and from 65 Medicare
     Part B, the income-related surcharges (IRMAA, from the return two years before) and
     retirement_health_supplement a person.
  2. Money comes in from Social Security (each adult's own benefit at their claiming age, or half the
     other's at full retirement age when that is more, once both have claimed), required minimum
     distributions from pre-tax accounts at the RMD age, a 72(t) plan's payments if the strategy has one,
     the taxable accounts' dividends (retirement_taxable_yield), and the HSA for the health costs it may pay.
  3. The rest, and the tax on everything, comes out of the accounts in the strategy's order; a Roth
     conversion strategy then fills the year's ordinary income to the top of a bracket. Taxes are worked out
     the way the forms do: federal brackets with the 65-and-over deductions, capital gains at 0/15/20%, the
     taxable part of Social Security, the net investment income tax, the 10% on money taken early, and
     Wisconsin's (no tax on Social Security, 30% of long-term gains and 24,000 a person of retirement income
     excluded from 67). Withdrawals and taxes are solved together, since each depends on the other.
  4. What is left grows at the year's return.

Before 59 1/2 money comes first from the taxable accounts, Roth contributions and conversions five years old,
the HSA's health costs, the plan of an employer left in or after the year turning 55, and a 72(t) plan; only
then from accounts that pay the 10%.

Returns come three ways, side by side: fixed yearly returns (retirement_cagrs, nominal, less
retirement_inflation) over the whole plan; each historical sequence since 1928 starting in the retirement year
(markets.py), with the years before at history's average; and Monte Carlo (each year drawn at random from
history, the stocks', bonds' and inflation's returns together) over the whole plan. The portfolio is
retirement_stock_share stocks (default: the allocation targets' share) rebalanced yearly.

A strategy is an order of withdrawal (conventional: taxable, then pre-tax, then Roth; proportional: from
each in proportion to its balance; bracket: pre-tax up to the top of the 12% bracket, then taxable, then
Roth), a Roth conversion target (none, or fill to the top of the 12%, 22% or 24% bracket), a spending rule
(constant real spending; Guyton-Klinger guardrails, cutting 10% when the withdrawal rate is 20% over its start
and raising 10% when 20% under; or variable percentage withdrawal, the portfolio's annuity payment over the
years left), and whether a 72(t) plan bridges the years before 59 1/2.

The retirement year is retirement_year if set, else the earliest year whose Monte Carlo success (no year
short of money) reaches retirement_success_target (default 0.9). The plan's strategy is retirement_strategy
if set, else the one that ends with the most after-tax wealth among those whose success is within a point of
the best.
"""

import re
from dataclasses import dataclass, field, replace
from datetime import date

import numpy as np
import pandas as pd

from . import markets, retirelaw as law, settings, taxdata, valuation

LAW_YEAR = max(taxdata.MFJ_BRACKETS)
ORDERS = ("conventional", "proportional", "bracket")
ORDER_NAMES = {"conventional": "Taxable, then pre-tax, then Roth", "proportional": "In proportion to balances",
               "bracket": "Pre-tax to the 12% bracket, then taxable"}
CONVERSIONS = (0.0, 0.12, 0.22, 0.24)
RULES = ("constant", "guardrails", "vpw")
RULE_NAMES = {"constant": "Constant real spending", "guardrails": "Guardrails (Guyton-Klinger)",
              "vpw": "Variable percentage withdrawal"}
BRACKET_ORDER_RATE = 0.12
GUARD_BAND, GUARD_STEP, GUARD_LAST_YEARS = 0.20, 0.10, 15
VPW_RETURNS = (0.050, 0.019)  # the real stock and bond returns VPW's table assumes (Bogleheads VPW)
HEIR_RATE = 0.24  # pre-tax money left at the end is counted after this much tax
GAIN_RATE_AT_END = 0.15  # and the taxable accounts' unrealized gain after this
TRUSTEES_CUT_YEAR = 2034  # the year the 2025 Trustees Report projects combined OASDI reserves run out
ITERATIONS = 8
SEED = 1928
DEFAULTS = {"plan_to_age": 95, "success_target": 0.90, "inflation": 0.03, "cagrs": (0.05, 0.07, 0.09),
            "monte_carlo_runs": 1000, "taxable_yield": 0.02, "health_pre65": 24_000, "health_supplement": 3_000,
            "claim_age": 67}


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
        bits = [ORDER_NAMES[self.order],
                f"convert to the top of {int(round(self.convert_to * 100))}%" if self.convert_to else "no conversions",
                RULE_NAMES[self.rule].lower()]
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


# --- Social Security --------------------------------------------------------------------------------------

def _claim_year(p, age):
    return p.born.year + age


def _first_year_share(p):
    """The share of the claiming year a benefit is paid for: from the birthday month on."""
    return (13 - p.born.month) / 12


def pia(p, retire_year, today_year):
    """(AIME, PIA) for a person who works until retire_year at the last posted year's earnings, figured as the
    statement figures it: earnings indexed to the AWI of two years ago, this year's bend points."""
    if not p.earnings:
        return 0, p.fra_monthly
    last = max(p.earnings)
    earnings = dict(p.earnings)
    for y in range(last + 1, retire_year):
        earnings[y] = p.earnings[last]
    index_year = min(p.born.year + 60, today_year - 2)
    a = law.aime(earnings, index_year)
    return a, law.pia(a, law.bend_points(min(p.born.year + 62, today_year)))


def benefits(h, retire_year, claims, today_year):
    """{person: {year: yearly benefit}}, each adult's own benefit or the spouse's half when more, from the
    year they claim (in part for that year), less the cut a setting may assume."""
    pias = {p.name: pia(p, retire_year, today_year)[1] for p in h.people}
    out = {}
    for p in h.people:
        age = claims.get(p.name, p.claim_age)
        own = pias[p.name] * law.claim_factor(p.born.year, age * 12)
        others = [q for q in h.people if q.name != p.name]
        by_year = {}
        for y in h.years:
            if y < _claim_year(p, age):
                continue
            monthly = own
            for q in others:
                if y >= _claim_year(q, claims.get(q.name, q.claim_age)):
                    extra = law.SPOUSE_SHARE * pias[q.name] - pias[p.name]
                    if extra > 0:
                        monthly = own + extra * law.spouse_factor(p.born.year, age * 12)
            share = _first_year_share(p) if y == _claim_year(p, age) else 1.0
            cut = 1 - h.ss_cut if y >= h.ss_cut_from else 1.0
            by_year[y] = monthly * 12 * share * cut
        out[p.name] = by_year
    return out


# --- tax, vectorized over paths ---------------------------------------------------------------------------

def _schedule(x, steps):
    tax = np.zeros_like(x)
    for i, (floor, rate) in enumerate(steps):
        top = steps[i + 1][0] if i + 1 < len(steps) else np.inf
        tax += rate * np.clip(x - floor, 0, top - floor)
    return tax


FED = taxdata.brackets(LAW_YEAR)
WI = taxdata.wi_brackets(LAW_YEAR)
P = taxdata.params(LAW_YEAR)


def bracket_top(rate):
    """Taxable income at the top of the federal bracket with this rate."""
    floors = [f for f, r in FED]
    return floors[[r for _, r in FED].index(rate) + 1]


def taxable_social_security(ss, other, deflator):
    """IRC 86 on a joint return: other is income before Social Security (AGI without it)."""
    b1, b2 = (b / deflator for b in law.SS_TAX_BASE_MFJ)
    prov = other + 0.5 * ss
    mid = np.minimum(0.5 * np.maximum(prov - b1, 0), 0.5 * ss)
    high = np.minimum(0.85 * ss, 0.85 * np.maximum(prov - b2, 0) + np.minimum(0.5 * (b2 - b1), 0.5 * ss))
    return np.where(prov <= b1, 0.0, np.where(prov <= b2, mid, high))


def deduction(n65, agi, year):
    d = P["standard_deduction_mfj"] + law.AGED_DEDUCTION_MFJ * n65
    if law.SENIOR_YEARS[0] <= year <= law.SENIOR_YEARS[1]:
        d = d + n65 * np.maximum(law.SENIOR_DEDUCTION - law.SENIOR_PHASE_RATE
                                 * np.maximum(agi - law.SENIOR_PHASE_START_MFJ, 0), 0)
    return d


def taxes(ordinary, gains, dividends, ss, penalized, excluded, n65, n_people, deflator, year):
    """(federal, wisconsin, agi, taxable Social Security) for a year: ordinary income other than Social
    Security, long-term gains, qualified dividends, benefits, the part that pays the 10% early, and the
    retirement income Wisconsin excludes."""
    preferred_income = gains + dividends
    ss_taxed = taxable_social_security(ss, ordinary + preferred_income, deflator)
    agi = ordinary + preferred_income + ss_taxed
    ti = np.maximum(agi - deduction(n65, agi, year), 0)
    pref = np.minimum(preferred_income, ti)
    ordinary_ti = ti - pref
    zero, fifteen = P["qdcg_zero_top_mfj"], P["qdcg_fifteen_top_mfj"]
    at0 = np.maximum(np.minimum(ti, zero) - ordinary_ti, 0)
    at15 = np.minimum(pref - at0, np.maximum(np.minimum(ti, fifteen) - np.maximum(ordinary_ti, zero), 0))
    at20 = pref - at0 - at15
    federal = _schedule(ordinary_ti, FED) + 0.15 * at15 + 0.20 * at20
    federal += law.NIIT_RATE * np.minimum(preferred_income, np.maximum(agi - law.NIIT_THRESHOLD_MFJ / deflator, 0))
    penalty = law.EARLY_PENALTY * penalized
    wi_income = np.maximum(agi - ss_taxed - law.WI_LTCG_EXCLUSION * gains - excluded, 0)
    full, start, rate = (P["wi_standard_deduction_mfj"], P["wi_standard_deduction_phase_start_mfj"],
                         P["wi_standard_deduction_phase_rate_mfj"])
    std = np.maximum(full - rate * np.maximum(wi_income - start, 0), 0)
    exemptions = (P["wi_exemption"] * n_people + law.WI_AGED_EXEMPTION * n65) / deflator
    wisconsin = _schedule(np.maximum(wi_income - std - exemptions, 0), WI) + law.WI_EARLY_SHARE * penalty
    return federal + penalty, wisconsin, agi, ss_taxed


def irmaa(magi, enrolled):
    """Yearly Part B and the Part D surcharge for the people enrolled, by the MAGI two years before."""
    floors = np.array([t[0] for t in law.IRMAA_MFJ])
    tier = np.searchsorted(floors, magi, side="right") - 1
    b = np.array([t[1] for t in law.IRMAA_MFJ])[tier]
    d = np.array([t[2] for t in law.IRMAA_MFJ])[tier]
    return (b + d) * 12 * enrolled, (b - law.IRMAA_MFJ[0][1] + d) * 12 * enrolled


# --- the simulation ---------------------------------------------------------------------------------------

def _age_mid(p, year):
    return year + 0.5 - (p.born.year + (p.born.month - 1) / 12)


def _take(need, sources, caps):
    """Take need from the sources in order, a tier at a time: a tier is a list of source names, shared in
    proportion to what each can give. Returns ({source: amount}, shortfall)."""
    taken = {}
    left = need.copy()
    for tier in sources:
        room = [caps[s] for s in tier]
        total = sum(room)
        share = np.minimum(left, total)
        for s, r in zip(tier, room):
            part = np.where(total > 0, share * r / np.where(total > 0, total, 1), 0.0)
            taken[s] = taken.get(s, 0) + part
            caps[s] = caps[s] - part
        left = left - share
    return taken, left


def _deduct(plan, ira, amount, plan_first):
    if plan_first:
        a = np.minimum(amount, plan)
        return plan - a, np.maximum(ira - (amount - a), 0)
    a = np.minimum(amount, ira)
    return np.maximum(plan - (amount - a), 0), ira - a


def simulate(h, strategy, retire_year, returns, inflation, today_year, record=False):
    """Run the plan over paths: returns and inflation are (years, paths) arrays for h.years. Returns a dict of
    per-path results (and per-year rows of the first path when record)."""
    years = h.years
    n = returns.shape[1]
    people = h.people
    names = [p.name for p in people]
    one = np.ones(n)
    pre_plan = {k: h.pre_plan.get(k, 0.0) * one for k in names}
    pre_ira = {k: h.pre_ira.get(k, 0.0) * one for k in names}
    roth = {k: h.roth.get(k, 0.0) * one for k in names}
    access = {k: np.minimum(h.roth_basis.get(k, 0.0), h.roth.get(k, 0.0)) * one for k in names}
    pending = {k: {y: a * one for y, a in h.pending.get(k, {}).items()} for k in names}
    tax_val, basis, hsa = h.taxable * one, h.basis * one, h.hsa * one
    deflator = one.copy()
    claims = dict(strategy.claims)
    ss = benefits(h, retire_year, claims, today_year)
    working_magi = sum(p.medicare_wages for p in people)
    magi = {}
    short = np.zeros(n, dtype=bool)
    first_short = np.full(n, 0)
    lifetime_tax, lifetime_irmaa, lifetime_penalty, converted = (np.zeros(n) for _ in range(4))
    spent, base_spent = np.zeros(n), 0.0
    lowest = np.full(n, np.inf)
    multiplier, wr0, last_withdrawal, first_need = one.copy(), np.zeros(n), np.zeros(n), np.zeros(n)
    sepp = {k: np.zeros(n) for k in names}
    sepp_until = {}
    at_retirement = None
    totals = np.zeros((len(years), n)) if record else None
    rows = []
    vpw_r = h.stock_share * VPW_RETURNS[0] + (1 - h.stock_share) * VPW_RETURNS[1]

    for i, year in enumerate(years):
        r, infl = returns[i], inflation[i]
        if year < retire_year:
            for p in people:
                c = h.contributions.get(p.name, {})
                pre_plan[p.name] = pre_plan[p.name] * (1 + r) + c.get("pre_plan", 0.0)
                pre_ira[p.name] = pre_ira[p.name] * (1 + r)
                roth[p.name] = roth[p.name] * (1 + r) + c.get("roth", 0.0)
                access[p.name] = access[p.name] + c.get("roth", 0.0)
            basis = basis / (1 + infl) + h.taxable_savings + h.taxable_yield * tax_val
            tax_val = tax_val * (1 + r) + h.taxable_savings
            hsa = hsa * (1 + r) + h.hsa_contribution
            deflator = deflator * (1 + infl)
            magi[year] = working_magi * one
            if record:
                totals[i] = tax_val + hsa + sum(pre_plan[k] + pre_ira[k] + roth[k] for k in names)
                rows.append(_row(year, people, "working", tax_val, hsa, pre_plan, pre_ira, roth,
                                 contributions=h.taxable_savings + h.hsa_contribution
                                 + sum(sum(c.values()) for c in h.contributions.values())))
            continue

        end_age = {p.name: year - p.born.year for p in people}
        mid_age = {p.name: _age_mid(p, year) for p in people}
        early = {k: mid_age[k] < law.EARLY_AGE for k in names}
        for k in names:
            access[k] = access[k] + pending[k].pop(year, 0.0)
        portfolio = tax_val + hsa + sum(pre_plan[k] + pre_ira[k] + roth[k] for k in names)
        if at_retirement is None:
            at_retirement = portfolio.copy()
            for p in people:  # a 72(t) plan starts with retirement and runs 5 years or to 59 1/2
                k = p.name
                if strategy.sepp and early[k]:
                    sepp[k] = law.sepp_payment(pre_ira[k] + pre_plan[k], end_age[k])
                    sepp_until[k] = max(year + law.SEPP_MIN_YEARS - 1,
                                        int(p.born.year + law.EARLY_AGE + (p.born.month > 6)))
        rule55 = {p.name: retire_year >= p.born.year + law.RULE_OF_55 for p in people}

        income_ss = sum(ss[k].get(year, 0.0) for k in names) * one
        rmd = {}
        for p in people:
            k = p.name
            if end_age[k] >= law.rmd_age(p.born.year):
                rmd[k] = (pre_plan[k] + pre_ira[k]) / law.distribution_period(end_age[k])
            else:
                rmd[k] = np.zeros(n)
            from_ira = np.minimum(rmd[k], pre_ira[k])
            pre_ira[k] = pre_ira[k] - from_ira
            pre_plan[k] = pre_plan[k] - (rmd[k] - from_ira)
        paid_sepp = {}
        for k in names:
            on = k in sepp_until and year <= sepp_until[k]
            amount = np.minimum(sepp[k] if on else 0.0, pre_ira[k] + pre_plan[k])
            from_ira = np.minimum(amount, pre_ira[k])
            pre_ira[k], pre_plan[k] = pre_ira[k] - from_ira, pre_plan[k] - (amount - from_ira)
            paid_sepp[k] = amount * one
        dividends = h.taxable_yield * tax_val
        tax_val = tax_val - dividends

        enrolled = sum(mid_age[k] >= law.MEDICARE_AGE for k in names)
        under = len(names) - enrolled
        past = magi.get(year - law.IRMAA_LOOKBACK, working_magi * one)
        medicare, surcharge = irmaa(past, enrolled) if enrolled else (np.zeros(n), np.zeros(n))
        health = h.health_pre65 / len(names) * under + h.health_supplement * enrolled + medicare
        hsa_pay = np.minimum(hsa, medicare + h.hsa_spending)
        hsa = hsa - hsa_pay

        base = h.spending.get(year, h.spending[max(h.spending)]) if h.spending else 0.0
        years_left = h.end - year + 1
        if strategy.rule == "guardrails" and year > retire_year:
            rate = np.where(portfolio > 0, last_withdrawal / np.maximum(portfolio, 1), np.inf)
            cut = (rate > wr0 * (1 + GUARD_BAND)) & (years_left > GUARD_LAST_YEARS)
            multiplier = np.where(cut, multiplier * (1 - GUARD_STEP),
                                  np.where(rate < wr0 * (1 - GUARD_BAND), multiplier * (1 + GUARD_STEP), multiplier))
        spend = base * multiplier if strategy.rule != "vpw" else None
        forced = sum(rmd[k] + paid_sepp[k] for k in names)
        if strategy.rule == "vpw":
            invested = tax_val + sum(pre_plan[k] + pre_ira[k] + roth[k] for k in names) + forced
            target = np.minimum(invested * vpw_r / (1 - (1 + vpw_r) ** -years_left), invested)
        cash_in = income_ss + forced + dividends + hsa_pay

        caps0 = {"taxable": tax_val}
        for p in people:
            k = p.name
            if not early[k]:
                caps0[f"pre:{k}"], caps0[f"pre_late:{k}"] = pre_plan[k] + pre_ira[k], np.zeros(n)
            elif rule55[k]:
                caps0[f"pre:{k}"], caps0[f"pre_late:{k}"] = pre_plan[k], pre_ira[k]
            else:
                caps0[f"pre:{k}"], caps0[f"pre_late:{k}"] = np.zeros(n), pre_plan[k] + pre_ira[k]
            caps0[f"roth:{k}"] = np.where(early[k], np.minimum(access[k], roth[k]), roth[k])
            caps0[f"roth_late:{k}"] = np.where(early[k], np.maximum(roth[k] - access[k], 0), 0.0)
        caps0["hsa"] = hsa
        pre = [f"pre:{k}" for k in names]
        roths = [f"roth:{k}" for k in names]
        late = [[f"pre_late:{k}" for k in names], [f"roth_late:{k}" for k in names], ["hsa"]]
        n65 = sum(end_age[k] >= 65 for k in names)
        tax = np.zeros(n)
        ordinary_fixed = sum(rmd[k] + paid_sepp[k] for k in names)
        ss_taxed = 0.85 * income_ss
        for _ in range(ITERATIONS):
            if strategy.rule == "vpw":
                need = np.maximum(target - forced, 0)
            else:
                need = np.maximum(spend + health - cash_in + tax, 0)
            caps = dict(caps0)
            deduct = P["standard_deduction_mfj"] + law.AGED_DEDUCTION_MFJ * n65
            if strategy.order == "bracket":
                room = np.maximum(bracket_top(BRACKET_ORDER_RATE) + deduct - ordinary_fixed - ss_taxed, 0)
                pre_total = sum(caps[s] for s in pre)
                capped = {s: np.minimum(caps[s], room * np.where(pre_total > 0, caps[s] / np.maximum(pre_total, 1), 0))
                          for s in pre}
                first = {f"{s}#room": capped[s] for s in pre}
                for s in pre:
                    caps[s] = caps[s] - capped[s]
                caps.update(first)
                order = [list(first), ["taxable"], roths, pre] + late
            elif strategy.order == "proportional":
                order = [["taxable"] + pre + roths] + late
            else:
                order = [["taxable"], pre, roths] + late
            got, lacking = _take(need, order, caps)
            w_pre = {k: got.get(f"pre:{k}", 0.0) + got.get(f"pre:{k}#room", 0.0) for k in names}
            w_pre_late = {k: got.get(f"pre_late:{k}", 0.0) for k in names}
            w_roth = {k: got.get(f"roth:{k}", 0.0) for k in names}
            w_roth_late = {k: got.get(f"roth_late:{k}", 0.0) for k in names}
            w_tax, w_hsa = got.get("taxable", 0.0), got.get("hsa", 0.0)
            gain_share = np.where(tax_val > 0, np.clip(1 - basis / np.maximum(tax_val, 1e-9), 0, 1), 0)
            gains = w_tax * gain_share
            ordinary = (ordinary_fixed + sum(w_pre[k] + w_pre_late[k] + w_roth_late[k] for k in names) + w_hsa)
            conv = {k: np.zeros(n) for k in names}
            if strategy.convert_to:
                room = np.maximum(bracket_top(strategy.convert_to) + deduct - ordinary - ss_taxed, 0)
                left = {k: pre_plan[k] + pre_ira[k] - w_pre[k] - w_pre_late[k] for k in names}
                total_left = sum(left.values())
                for k in names:
                    conv[k] = np.where(total_left > 0, np.minimum(room, total_left) * left[k]
                                       / np.maximum(total_left, 1e-9), 0)
                ordinary = ordinary + sum(conv.values())
            penalized = sum(w_pre_late[k] + w_roth_late[k] for k in names)
            excluded = sum(np.minimum(law.WI_RETIREMENT_EXCLUSION / deflator,
                                      rmd[k] + paid_sepp[k] + w_pre[k] + w_pre_late[k])
                           * (end_age[k] >= law.WI_RETIREMENT_AGE) for k in names)
            fed, wi, agi, ss_taxed = taxes(ordinary, gains, dividends, income_ss, penalized, excluded, n65,
                                           len(names), deflator, year)
            tax = fed + wi
        withdrawn = w_tax + w_hsa + sum(w_pre[k] + w_pre_late[k] + w_roth[k] + w_roth_late[k] for k in names)
        if strategy.rule == "vpw":  # spending is what the withdrawals and income leave after health and tax
            spend = np.maximum(withdrawn + cash_in - health - tax, 0)
            surplus = np.zeros(n)
        else:
            surplus = np.maximum(cash_in - spend - health - tax, 0) * (withdrawn <= 1e-6)
        out_now = lacking > 1
        first_short = np.where(out_now & ~short, year, first_short)
        short = short | out_now

        tax_val = tax_val - w_tax
        basis = np.maximum(basis - w_tax * (1 - gain_share), 0)
        hsa = hsa - w_hsa
        for k in names:
            # money free of the 10% through the Rule of 55 is the plan's; the rest, and conversions, come from
            # the IRA first
            pre_plan[k], pre_ira[k] = _deduct(pre_plan[k], pre_ira[k], w_pre[k], plan_first=early[k] and rule55[k])
            pre_plan[k], pre_ira[k] = _deduct(pre_plan[k], pre_ira[k], w_pre_late[k] + conv[k], plan_first=False)
            roth_out = w_roth[k] + w_roth_late[k]
            access[k] = np.maximum(access[k] - np.minimum(roth_out, access[k]), 0)
            roth[k] = np.maximum(roth[k] - roth_out, 0) + conv[k]
            pending[k][year + law.CONVERSION_WAIT] = pending[k].get(year + law.CONVERSION_WAIT, 0.0) + conv[k]
        tax_val = tax_val + surplus
        basis = basis + surplus

        tax_val, hsa = tax_val * (1 + r), hsa * (1 + r)
        basis = basis / (1 + infl)
        for k in names:
            pre_plan[k], pre_ira[k], roth[k] = pre_plan[k] * (1 + r), pre_ira[k] * (1 + r), roth[k] * (1 + r)
        deflator = deflator * (1 + infl)
        magi[year] = agi
        last_withdrawal = withdrawn + forced
        if year == retire_year:
            wr0 = np.where(portfolio > 0, last_withdrawal / np.maximum(portfolio, 1), 0)
            first_need = spend + health
        lifetime_tax += tax
        lifetime_irmaa += surcharge
        lifetime_penalty += law.EARLY_PENALTY * penalized * (1 + law.WI_EARLY_SHARE)
        converted += sum(conv.values())
        spent += spend
        base_spent += base
        lowest = np.minimum(lowest, np.where(base > 0, spend / base, 1.0))
        if record:
            totals[i] = tax_val + hsa + sum(pre_plan[k] + pre_ira[k] + roth[k] for k in names)
            rows.append(_row(year, people, "retired", tax_val, hsa, pre_plan, pre_ira, roth, spending=spend,
                             health=health, social_security=income_ss, rmd=sum(rmd.values()),
                             from_taxable=w_tax + dividends, from_pre_tax=sum(w_pre[k] + w_pre_late[k] for k in names)
                             + sum(paid_sepp.values()), from_roth=sum(w_roth[k] + w_roth_late[k] for k in names),
                             from_hsa=w_hsa + hsa_pay, converted=sum(conv.values()), ordinary_income=ordinary,
                             capital_gains=gains + dividends, federal_tax=fed, state_tax=wi, irmaa=surcharge,
                             penalty=law.EARLY_PENALTY * penalized * (1 + law.WI_EARLY_SHARE), short=lacking))

    pre_end = sum(pre_plan[k] + pre_ira[k] for k in names)
    after_tax = (tax_val - GAIN_RATE_AT_END * np.maximum(tax_val - basis, 0) + hsa
                 + sum(roth[k] for k in names) + pre_end * (1 - HEIR_RATE))
    return {"short": short, "first_short": first_short, "end": tax_val + hsa + sum(roth.values()) + pre_end,
            "after_tax": after_tax, "tax": lifetime_tax, "irmaa": lifetime_irmaa, "penalty": lifetime_penalty,
            "converted": converted, "spent": spent, "base_spent": base_spent, "lowest": lowest,
            "at_retirement": at_retirement if at_retirement is not None else np.zeros(n), "first_rate": wr0,
            "first_need": first_need,
            "totals": totals, "rows": rows}


def _row(year, people, phase, tax_val, hsa, pre_plan, pre_ira, roth, **values):
    names = [p.name for p in people]
    first = lambda v: float(np.atleast_1d(v)[0])  # noqa: E731
    row = {"year": year, "ages": "/".join(str(year - p.born.year) for p in people), "phase": phase,
           "taxable": first(tax_val), "pre_tax": first(sum(pre_plan[k] + pre_ira[k] for k in names)),
           "roth": first(sum(roth[k] for k in names)), "hsa": first(hsa)}
    row["total"] = row["taxable"] + row["pre_tax"] + row["roth"] + row["hsa"]
    row.update({k: first(v) for k, v in values.items()})
    return row


# --- returns ----------------------------------------------------------------------------------------------

def fixed_paths(h, cagr):
    real = (1 + cagr) / (1 + h.inflation) - 1
    shape = (len(h.years), 1)
    return np.full(shape, real), np.full(shape, h.inflation)


def central(h):
    """History's average real return for the portfolio, and its average inflation, both geometric."""
    _, series = markets.real_series(h.stock_share)
    infl = np.array([markets.history()[y][2] for y in sorted(markets.RETURNS)])
    return float(np.prod(1 + series) ** (1 / len(series)) - 1), float(np.prod(1 + infl) ** (1 / len(infl)) - 1)


def historical_paths(h, retire_year):
    """(returns, inflation, start years): each historical sequence begins in the retirement year; the years
    before it earn history's average. Sequences that would run past the record wrap to its start only when
    fewer than 30 complete ones fit."""
    years = sorted(markets.RETURNS)
    hist = markets.history()
    _, series = markets.real_series(h.stock_share)
    infl = np.array([hist[y][2] for y in years])
    mean_r, mean_i = central(h)
    before = max(retire_year - h.start, 0)
    length = len(h.years) - before
    complete = len(years) - length + 1
    starts = list(range(complete)) if complete >= 30 else list(range(len(years)))
    rets = np.full((len(h.years), len(starts)), mean_r)
    infls = np.full((len(h.years), len(starts)), mean_i)
    for j, s in enumerate(starts):
        idx = [(s + t) % len(years) for t in range(length)]
        rets[before:, j] = series[idx]
        infls[before:, j] = infl[idx]
    return rets, infls, [years[s] for s in starts]


def monte_carlo_paths(h, runs=None, seed=SEED):
    """Each year of the whole plan drawn at random from history (the same draw for every plan, so runs
    compare like with like)."""
    years = sorted(markets.RETURNS)
    hist = markets.history()
    _, series = markets.real_series(h.stock_share)
    infl = np.array([hist[y][2] for y in years])
    pick = np.random.default_rng(seed).integers(0, len(years), size=(len(h.years), runs or h.monte_carlo_runs))
    return series[pick], infl[pick]


def run_all(h, strategy, retire_year, today_year, paths=None, record=False):
    """{model: results} for the fixed returns, history and Monte Carlo."""
    paths = paths or {}
    out = {}
    for c in h.cagrs:
        r, i = fixed_paths(h, c)
        out[f"fixed {c:.0%}"] = simulate(h, strategy, retire_year, r, i, today_year, record=record)
    r, i, starts = paths.get(("historical", retire_year)) or historical_paths(h, retire_year)
    out["historical"] = simulate(h, strategy, retire_year, r, i, today_year)
    out["historical"]["starts"] = starts
    r, i = paths.get("monte_carlo") or monte_carlo_paths(h)
    out["monte carlo"] = simulate(h, strategy, retire_year, r, i, today_year, record=record)
    return out


def success(result):
    return float(1 - result["short"].mean())


# --- the reports ------------------------------------------------------------------------------------------

YEAR_COLUMNS = ["retire_year", "ages", "model", "scenario", "success", "median_end", "p10_end", "short_from",
                "at_retirement", "first_spending", "withdrawal_rate", "chosen"]
STRATEGY_COLUMNS = ["strategy", "label", "order", "convert_to", "rule", "sepp", "claims", "model", "success",
                    "median_after_tax", "p10_after_tax", "median_tax", "median_irmaa", "median_penalty",
                    "median_converted", "median_spent", "lowest_spending", "chosen"]
PLAN_COLUMNS = ["strategy", "year", "ages", "phase", "contributions", "spending", "health", "social_security",
                "rmd", "from_taxable", "from_pre_tax", "from_roth", "from_hsa", "converted", "ordinary_income",
                "capital_gains", "federal_tax", "state_tax", "irmaa", "penalty", "short", "taxable", "pre_tax", "roth",
                "hsa", "total", "p10_total", "p50_total", "p90_total"]
SS_COLUMNS = ["person", "claim_age", "claim_year", "statement_monthly", "computed_monthly", "plan_monthly",
              "plan_yearly", "aime", "pia", "note"]
EARNINGS_COLUMNS = ["person", "year", "fica_earnings", "indexed", "counted"]


def _ages(h, year):
    return "/".join(str(year - p.born.year) for p in h.people)


def first_spending(h, year):
    return h.spending.get(year, h.spending[max(h.spending)]) if h.spending else 0.0


def search(h, strategy, today_year, candidates=None):
    """The retirement years table and the year chosen."""
    mc = monte_carlo_paths(h)
    rows, chosen = [], None
    last = min(max(p.born.year for p in h.people) + 70, h.end - 5)
    for y in candidates or range(h.start, last + 1):
        res = run_all(h, strategy, y, today_year, {"monte_carlo": mc})
        for model, r in res.items():
            at = float(np.median(r["at_retirement"]))
            starts = [s for s in r["first_short"] if s]
            rows.append({"retire_year": y, "ages": _ages(h, y), "model": model.split(" ")[0] if model.startswith("fixed")
                         else model, "scenario": model[len("fixed "):] if model.startswith("fixed") else "",
                         "success": success(r), "median_end": float(np.median(r["end"])),
                         "p10_end": float(np.percentile(r["end"], 10)),
                         "short_from": int(min(starts)) if starts and model.startswith("fixed") else "",
                         "at_retirement": at, "first_spending": float(np.median(r["first_need"])),
                         "withdrawal_rate": float(np.median(r["first_rate"])), "chosen": ""})
        if chosen is None and success(res["monte carlo"]) >= h.success_target:
            chosen = y
    return pd.DataFrame(rows, columns=YEAR_COLUMNS), chosen


def variants(h, base, claim_choices=(62, 67, 70)):
    """The base strategy under the other spending rules, and with each adult claiming at each age."""
    out = [replace(base, rule=r) for r in RULES if r != base.rule]
    if len(h.people) <= 2:
        combos = [(a,) for a in claim_choices] if len(h.people) == 1 else \
            [(a, b) for a in claim_choices for b in claim_choices]
        out += [replace(base, claims=tuple(zip([p.name for p in h.people], c))) for c in combos
                if tuple(c) != tuple(p.claim_age for p in h.people)]
    return out


def strategies(h, retire_year, base, today_year, only=None):
    """Every order, conversion target and 72(t) choice at the retirement year, and the base strategy's
    variants (or only the strategies given)."""
    mc = monte_carlo_paths(h)
    hist = historical_paths(h, retire_year)
    if only is not None:
        grid = list(only)
    else:
        grid = [Strategy(o, c, "constant", s) for o in ORDERS for c in CONVERSIONS for s in (False, True)
                if s is False or any(_age_mid(p, retire_year) < law.EARLY_AGE for p in h.people)]
        grid += variants(h, base)
        if base not in grid:
            grid.insert(0, base)
    results, rows = {}, []
    for s in grid:
        res = run_all(h, s, retire_year, today_year, {"monte_carlo": mc, ("historical", retire_year): hist},
                      record=True)
        results[s] = res
        for model in ("historical", "monte carlo"):
            r = res[model]
            rows.append({"strategy": s.key, "label": s.label, "order": s.order, "convert_to": s.convert_to,
                         "rule": s.rule, "sepp": s.sepp, "claims": ", ".join(f"{n} {a}" for n, a in s.claims),
                         "model": model, "success": success(r), "median_after_tax": float(np.median(r["after_tax"])),
                         "p10_after_tax": float(np.percentile(r["after_tax"], 10)),
                         "median_tax": float(np.median(r["tax"])), "median_irmaa": float(np.median(r["irmaa"])),
                         "median_penalty": float(np.median(r["penalty"])),
                         "median_converted": float(np.median(r["converted"])),
                         "median_spent": float(np.median(r["spent"])),
                         "lowest_spending": float(np.percentile(np.where(np.isfinite(r["lowest"]), r["lowest"], 1), 10)),
                         "chosen": ""})
    return pd.DataFrame(rows, columns=STRATEGY_COLUMNS), results


def best(table):
    """Among constant-spending strategies with the usual claiming ages: those within a point of the best
    Monte Carlo success, the one with the most after-tax wealth at the end."""
    mc = table[(table["model"] == "monte carlo") & (table["rule"] == "constant") & (table["claims"] == "")]
    if mc.empty:
        return None
    near = mc[mc["success"] >= mc["success"].max() - 0.01]
    return parse_strategy(near.sort_values("median_after_tax", ascending=False).iloc[0]["strategy"])


def plan_rows(h, strategy, retire_year, today_year, results=None):
    """Year by year for one strategy at the middle fixed return, with the Monte Carlo spread of the total."""
    mid = h.cagrs[len(h.cagrs) // 2]
    det = (results or {}).get(f"fixed {mid:.0%}")
    if det is None or not det.get("rows"):
        r, i = fixed_paths(h, mid)
        det = simulate(h, strategy, retire_year, r, i, today_year, record=True)
    mc = (results or {}).get("monte carlo")
    if mc is None or mc.get("totals") is None:
        r, i = monte_carlo_paths(h)
        mc = simulate(h, strategy, retire_year, r, i, today_year, record=True)
    p10, p50, p90 = (np.percentile(mc["totals"], q, axis=1) for q in (10, 50, 90))
    rows = []
    for j, row in enumerate(det["rows"]):
        rows.append({**{c: "" for c in PLAN_COLUMNS}, **row, "strategy": strategy.key,
                     "p10_total": float(p10[j]), "p50_total": float(p50[j]), "p90_total": float(p90[j])})
    return pd.DataFrame(rows, columns=PLAN_COLUMNS).fillna("")


def social_security(h, retire_year, today_year, claims=None):
    """Each adult's benefit: the statement's estimate at their claiming age, the same figured here (working to
    the claim at the last earnings, which should agree), and what retiring in the plan's year leaves."""
    claims = claims or {}
    rows, earned = [], []
    plan = benefits(h, retire_year, claims, today_year)
    for p in h.people:
        age = claims.get(p.name, p.claim_age)
        factor = law.claim_factor(p.born.year, age * 12)
        working = pia(p, p.born.year + age, today_year)[1] * factor
        a, plan_pia = pia(p, retire_year, today_year)
        claim_year = _claim_year(p, age)
        full = plan[p.name].get(claim_year + 1, 0.0)
        note = "" if p.earnings else ("no statement: set social_security_monthly:" + p.name if not p.fra_monthly
                                      else "from social_security_monthly (no statement)")
        rows.append({"person": p.name, "claim_age": age, "claim_year": claim_year,
                     "statement_monthly": p.statement.get(age, ""), "computed_monthly": round(working, 2),
                     "plan_monthly": round(plan_pia * factor, 2), "plan_yearly": round(full, 2), "aime": a,
                     "pia": plan_pia, "note": note})
        if p.earnings:
            last = max(p.earnings)
            future = {y: p.earnings[last] for y in range(last + 1, retire_year)}
            index_year = min(p.born.year + 60, today_year - 2)
            base = law.AWI[min(index_year, max(law.AWI))]
            all_years = {**p.earnings, **future}
            indexed = {y: e * base / law.AWI[y] if y <= index_year and y in law.AWI else e for y, e in all_years.items()}
            top = set(sorted(indexed, key=lambda y: -indexed[y])[:law.COMPUTATION_YEARS])
            for y in sorted(all_years):
                earned.append({"person": p.name, "year": y, "fica_earnings": all_years[y],
                               "indexed": round(indexed[y], 2), "counted": "yes" if y in top and indexed[y] > 0
                               else "", "projected": y > last})
    earnings = pd.DataFrame(earned, columns=EARNINGS_COLUMNS + ["projected"])
    earnings["projected"] = earnings["projected"].map(lambda v: "projected" if v else "posted")
    return pd.DataFrame(rows, columns=SS_COLUMNS), earnings.rename(columns={"projected": "basis"})


# --- building the inputs from the ledger ------------------------------------------------------------------

BUCKET_MAP = {"401k_pretax": "pre_plan", "ira_pretax": "pre_ira", "401k_roth": "roth", "ira_roth": "roth",
              "hsa": "hsa", "taxable": "taxable", "ibonds": "taxable", "other": "taxable"}
STOCK_CLASSES = ("SDT", "SDS", "SIT", "SIE", "REIT")


def _num(s):
    return pd.to_numeric(s if isinstance(s, pd.Series) else pd.Series(s, dtype=object), errors="coerce").fillna(0.0)


def _owners(owner, adults):
    o = str(owner).strip().lower()
    if o in adults:
        return {o: 1.0}
    if o in ("joint", "") and adults:
        return {a: 1 / len(adults) for a in adults}
    return {}


def spending_steps(text):
    """[(from year or None, amount)] from a retirement_spending value: "12000", "0 from 2032",
    "18000; 9000 from 2040; 0 from 2050". None when it can't be read."""
    out = []
    for part in str(text).split(";"):
        m = re.fullmatch(r"\s*(-?[\d,]+(?:\.\d+)?)(?:\s+from\s+(\d{4}))?\s*", part)
        if not m:
            return None
        out.append((int(m.group(2)) if m.group(2) else None, float(m.group(1).replace(",", ""))))
    return out


LUMPY_MIN, LUMPY_SHARE = 5_000, 0.6


def baseline(actuals, categories, today, leave_out=("taxes",)):
    """(spending by category over the last 12 full months, {category: group}, the months used, {category: the
    share of its spending in its two biggest months} for the lumpy ones)."""
    month = today[:7]
    groups = dict(zip(categories["category"].astype(str), categories["group"].astype(str))) if len(categories) else {}
    if actuals is None or actuals.empty:
        return {}, groups, [], {}
    a = actuals.assign(spend=_num(actuals["spend"]), month=actuals["month"].astype(str))
    months = sorted(m for m in a["month"].unique() if m < month)[-12:]
    a = a[a["month"].isin(months)]
    skip = {"", "transfer"} | set(leave_out)
    keep = [c for c in a["category"].astype(str).unique()
            if c not in skip and groups.get(c, "") not in ({"income", "savings", "transfer"} | set(leave_out))]
    kept = a[a["category"].isin(keep)]
    by = kept.groupby("category")["spend"].sum()
    scale = 12 / len(months) if months else 1
    # a category whose year came mostly in a month or two (a renovation, a year's tuition) may not recur
    lumpy = {}
    for c, g in kept.groupby("category"):
        total = g["spend"].sum()
        top = g["spend"].nlargest(2).sum()
        if total > LUMPY_MIN and top > LUMPY_SHARE * total:
            lumpy[c] = top / total
    return {c: float(v) * scale for c, v in by.items()}, groups, months, lumpy


def spending_path(base, groups, years, changes, loans):
    """({year: spending}, the adjustment rows): each category's last-12-months amount, a category or group
    named in changes replaced by its steps from their years, a new name added, each loan's payments taken
    off after its payoff year."""
    rows = []
    path = {}
    by_group = {}
    for c, v in base.items():
        by_group.setdefault(groups.get(c, c), []).append(c)
    for y in years:
        total = 0.0
        handled = set()
        for name, steps in changes.items():
            members = [name] if name in base else by_group.get(name, [])
            current = sum(base[c] for c in members) if members else 0.0
            amount = current
            for start, value in steps:
                if start is None or y >= start:
                    amount = value
            total += amount
            handled |= set(members)
        total += sum(v for c, v in base.items() if c not in handled)
        for loan_id, (payoff_year, yearly) in loans.items():
            if y > payoff_year and loan_id not in changes:
                total -= yearly
        path[y] = max(total, 0.0)
    for name, steps in changes.items():
        members = [name] if name in base else by_group.get(name, [])
        now = sum(base[c] for c in members)
        rows.append({"key": name, "now": now, "steps": "; ".join(
            f"{v:,.0f}" + (f" from {s}" if s else "") for s, v in steps),
            "basis": "a category" if name in base else ("a group" if members else "added")})
    for loan_id, (payoff_year, yearly) in loans.items():
        if loan_id not in changes:
            rows.append({"key": loan_id, "now": yearly, "steps": f"0 from {payoff_year + 1}",
                         "basis": "the loan's payments end when it is paid off"})
    return path, rows


def build(params, people_names, statements, estimates, earnings, accounts, values, activity, lots, actuals,
          categories, schedule, vests, allocation_share, today):
    """(Household, notes, assumption rows) from the ledger and Reports frames; Household None when an adult's
    birth date is missing."""
    today_year = int(today[:4])
    notes, assume = [], []
    value = lambda key, default="": settings.value(params, key, default)  # noqa: E731
    number = lambda key, default: settings.number(params, key, default)  # noqa: E731

    def note(section, key, label, val, source, setting=""):
        assume.append({"section": section, "key": key, "label": label, "value": val, "source": source,
                       "setting": setting})

    if not people_names:
        return None, ["retirement: no adults (household_adults, or the people on pay stubs)"], pd.DataFrame(assume)
    st = statements.copy() if statements is not None else pd.DataFrame(columns=["person", "birth_date", "as_of"])
    people = []
    for name in people_names:
        mine = st[st["person"].astype(str) == name].sort_values("as_of")
        born = value(f"birth_date:{name}") or (mine["birth_date"].iloc[-1] if len(mine) else "")
        try:
            born_d = date.fromisoformat(str(born).strip())
        except ValueError:
            notes.append(f"retirement: no birth date for {name}: set birth_date:{name} (YYYY-MM-DD) or add their "
                         "Social Security statement")
            return None, notes, pd.DataFrame(assume)
        as_of = str(mine["as_of"].iloc[-1]) if len(mine) else ""
        e = earnings[(earnings["person"].astype(str) == name) & (earnings["as_of"].astype(str) == as_of)] \
            if len(mine) and earnings is not None and len(earnings) else pd.DataFrame()
        est = estimates[(estimates["person"].astype(str) == name) & (estimates["as_of"].astype(str) == as_of)
                        & (estimates["kind"].astype(str) == "retirement")] \
            if len(mine) and estimates is not None and len(estimates) else pd.DataFrame()
        claim = int(number(f"social_security_claim_age:{name}", DEFAULTS["claim_age"]))
        claim = min(max(claim, 62), 70)
        person = Person(name, born_d, claim,
                        {int(y): float(v) for y, v in zip(e.get("year", []), _num(e.get("fica_earnings", [])))},
                        {int(a): float(v) for a, v in zip(est.get("age", []), _num(est.get("monthly", [])))},
                        int(as_of[:4]) if as_of else 0,
                        number(f"social_security_monthly:{name}", 0.0),
                        float(_num(e.get("medicare_earnings", pd.Series(dtype=float))).iloc[-1]) if len(e) else 0.0)
        people.append(person)
        note("people", f"born:{name}", f"{name} born", born_d.isoformat(),
             "the setting" if value(f"birth_date:{name}") else "the Social Security statement", f"birth_date:{name}")
        note("people", f"claim:{name}", f"{name} claims Social Security at", claim, "default (full retirement age)"
             if not value(f"social_security_claim_age:{name}") else "the setting", f"social_security_claim_age:{name}")
        if not person.earnings and not person.fra_monthly:
            notes.append(f"retirement: no Social Security statement for {name}; their benefit counts as none until "
                         f"one is added (or social_security_monthly:{name} is set)")

    adults = [p.name for p in people]
    accts = accounts.set_index("account_id") if len(accounts) else pd.DataFrame()
    v = values[values["date"].astype(str) == today] if len(values) else values
    bal = {k: {a: 0.0 for a in adults} for k in ("pre_plan", "pre_ira", "roth")}
    taxable = hsa = 0.0
    taxable_accounts = set()
    for r in v.to_dict("records"):
        kind = BUCKET_MAP.get(str(r["bucket"]))
        amount = float(pd.to_numeric(r["value"], errors="coerce") or 0.0)
        if not kind:
            continue
        shares = _owners(r["owner"], adults)
        if not shares:
            continue
        if kind == "taxable":
            taxable += amount
            taxable_accounts.add(r["account_id"])
        elif kind == "hsa":
            hsa += amount
        else:
            for a, s in shares.items():
                bal[kind][a] += amount * s
    lot_rows = lots[lots["account_id"].isin(taxable_accounts)] if lots is not None and len(lots) else pd.DataFrame()
    lot_value, lot_basis = (float(_num(lot_rows["value"]).sum()), float(_num(lot_rows["basis"]).sum())) \
        if len(lot_rows) else (0.0, 0.0)
    basis = lot_basis + max(taxable - lot_value, 0.0)

    since = (pd.Timestamp(today) - pd.DateOffset(years=1)).date().isoformat()
    contributions = {a: {"pre_plan": 0.0, "roth": 0.0} for a in adults}
    roth_basis = {a: 0.0 for a in adults}
    pending = {a: {} for a in adults}
    hsa_in = into_ira_529 = 0.0
    if activity is not None and len(activity):
        act = activity.assign(amount=_num(activity["amount"]), date=activity["date"].astype(str))
        for r in act.to_dict("records"):
            if r["account_id"] not in accts.index:
                continue
            acct = accts.loc[r["account_id"]]
            kind = BUCKET_MAP.get(valuation.bucket(valuation.wrapper({**acct.to_dict(), "account_id": r["account_id"]}),
                                                   acct["tax_treatment"],
                                                   r.get("source", "")))
            shares = _owners(acct["owner"], adults)
            recent = r["date"] > since
            if str(acct["type"]) in ("ira", "education") and recent and r["kind"] in ("contribution", "withdrawal"):
                into_ira_529 += r["amount"]
            if kind == "roth" and r["kind"] in ("contribution", "conversion_in"):
                for a, s in shares.items():
                    roth_basis[a] += r["amount"] * s
            if not recent or not shares:
                continue
            if r["kind"] in ("contribution", "conversion_in", "conversion_out") and kind in ("pre_plan", "pre_ira", "roth"):
                for a, s in shares.items():
                    key = "roth" if kind == "roth" else ("pre_plan" if kind == "pre_plan" else "pre_ira")
                    if key == "pre_ira":
                        continue  # money through an IRA on its way to a Roth: the Roth row counts it
                    contributions[a][key] += r["amount"] * s
            if r["kind"] == "contribution" and kind == "hsa":
                hsa_in += r["amount"]

    base, groups, months, lumpy = baseline(actuals, categories, today,
                                           [x.strip() for x in value("retirement_leave_out", "taxes").split(",")
                                            if x.strip()])
    leave_out = [x.strip() for x in value("retirement_leave_out", "taxes").split(",") if x.strip()]
    came_in = left_out = 0.0
    if actuals is not None and len(actuals) and months:
        a = actuals.assign(spend=_num(actuals["spend"]))
        a = a[a["month"].astype(str).isin(months)]
        scale = 12 / len(months)
        came_in = -float(a[a["category"].map(lambda c: groups.get(str(c)) == "income")]["spend"].sum()) * scale
        left_out = float(a[a["category"].map(lambda c: str(c) in leave_out or groups.get(str(c)) in leave_out)]
                         ["spend"].sum()) * scale
    vested = 0.0
    if vests is not None and len(vests):
        vv = vests[vests["vest_date"].astype(str) > since]
        vested = float((_num(vv["net_shares"]) * _num(vv["fmv"])).sum())

    changes = {}
    for key in set(params.loc[params["jurisdiction"].astype(str) == "household", "key"].astype(str)) if len(params) else ():
        base_key, _, name = key.partition(":")
        if base_key == "retirement_spending" and name:
            steps = spending_steps(value(key))
            if steps is None:
                notes.append(f"retirement: {key} can't be read ({value(key)!r}); write it like 12000 or "
                             "0 from 2032 or 18000; 9000 from 2040")
            elif steps:
                changes[name] = steps
    loans = {}
    if schedule is not None and len(schedule):
        s = schedule.assign(interest=_num(schedule["interest"]), principal=_num(schedule["principal"]),
                            extra=_num(schedule.get("extra_principal", 0)), month=schedule["month"].astype(str))
        for loan_id, g in s.groupby("loan_id"):
            last12 = g[(g["month"] < today[:7])].tail(12)
            if len(last12) and g["month"].max() >= today[:7]:
                loans[str(loan_id)] = (int(g["month"].max()[:4]),
                                       float((last12["interest"] + last12["principal"] - last12["extra"]).sum()))
    start = today_year + 1
    end = max(p.born.year for p in people) + int(number("retirement_plan_to_age", DEFAULTS["plan_to_age"]))
    path, adjust = spending_path(base, groups, range(start, end + 1), changes, loans)
    # what working leaves for taxable accounts: pay and other income into the bank and the shares that vest,
    # less spending as the settings have it from next year, the taxes paid and what goes to IRAs and 529s
    derived = came_in + vested - path.get(start, 0.0) - left_out - into_ira_529
    taxable_savings = number("retirement_taxable_savings", derived) if value("retirement_taxable_savings") else derived
    taxable_savings = max(taxable_savings, 0.0)
    for a in adults:
        for kind, key in (("pre_plan", "retirement_pre_tax_contributions"), ("roth", "retirement_roth_contributions")):
            if value(f"{key}:{a}"):
                contributions[a][kind] = number(f"{key}:{a}", contributions[a][kind])
    hsa_names = [x.strip() for x in value("hsa_spending_categories", "medical").split(",") if x.strip()]
    hsa_spending = sum(v for c, v in base.items() if c in hsa_names or groups.get(c) in hsa_names)

    cagr_text = value("retirement_cagrs")
    try:
        cagrs = tuple(float(x) for x in cagr_text.split(",")) if cagr_text else DEFAULTS["cagrs"]
    except ValueError:
        cagrs = DEFAULTS["cagrs"]
        notes.append(f"retirement: retirement_cagrs isn't numbers separated by commas ({cagr_text!r}); using the default")
    stock = number("retirement_stock_share", allocation_share if allocation_share is not None else 0.7)
    h = Household(start=start, people=people, pre_plan=bal["pre_plan"], pre_ira=bal["pre_ira"], roth=bal["roth"],
                  roth_basis=roth_basis, taxable=taxable, basis=basis, hsa=hsa, contributions=contributions,
                  hsa_contribution=hsa_in, taxable_savings=taxable_savings, spending=path, hsa_spending=hsa_spending,
                  health_pre65=number("retirement_health_pre65", DEFAULTS["health_pre65"]),
                  health_supplement=number("retirement_health_supplement", DEFAULTS["health_supplement"]),
                  stock_share=min(max(stock, 0.0), 1.0),
                  taxable_yield=number("retirement_taxable_yield", DEFAULTS["taxable_yield"]),
                  inflation=number("retirement_inflation", DEFAULTS["inflation"]), cagrs=cagrs,
                  plan_to_age=int(number("retirement_plan_to_age", DEFAULTS["plan_to_age"])),
                  success_target=number("retirement_success_target", DEFAULTS["success_target"]),
                  monte_carlo_runs=int(number("retirement_monte_carlo_runs", DEFAULTS["monte_carlo_runs"])),
                  ss_cut=number("social_security_cut", 0.0),
                  ss_cut_from=int(number("social_security_cut_from", TRUSTEES_CUT_YEAR)), pending=pending)

    for a in adults:
        for kind, label in (("pre_plan", "pre-tax in workplace plans"), ("pre_ira", "pre-tax IRAs"), ("roth", "Roth")):
            note("today", f"{kind}:{a}", f"{a}: {label}", round(bal[kind][a], 2), "investment values today")
        note("today", f"roth_basis:{a}", f"{a}: Roth contributions and conversions (free before 59 1/2)",
             round(roth_basis[a], 2), "investment activity (conversions taken as backdoor ones, already taxed)")
    note("today", "taxable", "Taxable accounts", round(taxable, 2), "investment values today")
    note("today", "basis", "Their cost basis", round(basis, 2), "open lots, and cash at its value")
    note("today", "hsa", "HSA", round(hsa, 2), "investment values today")
    for a in adults:
        for kind, label in (("pre_plan", "pre-tax"), ("roth", "Roth")):
            note("saving", f"{kind}:{a}", f"{a}: {label} contributions a year", round(contributions[a][kind], 2),
                 "investment activity, last 12 months")
    note("saving", "hsa", "HSA contributions a year", round(hsa_in, 2), "investment activity, last 12 months")
    for a in adults:
        for kind, key in (("pre_plan", "retirement_pre_tax_contributions"), ("roth", "retirement_roth_contributions")):
            assume[[i for i, x in enumerate(assume) if x["key"] == f"{kind}:{a}" and x["section"] == "saving"][0]][
                "setting"] = f"{key}:{a}"
    note("saving", "taxable", "Taxable savings a year", round(taxable_savings, 2),
         "the setting" if value("retirement_taxable_savings") else
         f"last 12 months: income {came_in:,.0f} and shares vested {vested:,.0f}, less spending as set for "
         f"{start} {path.get(start, 0.0):,.0f}, {', '.join(leave_out) or 'nothing left out'} {left_out:,.0f}, "
         f"IRAs and 529s {into_ira_529:,.0f}" + (" (below zero: none)" if derived < 0 else ""),
         "retirement_taxable_savings")
    for c in sorted(base, key=lambda c: (groups.get(c, ""), c)):
        note("spending", c, f"{groups.get(c, '')}: {c}", round(base[c], 2),
             (f"actual, {months[0]} to {months[-1]}" if months else "")
             + (f"; {lumpy[c]:.0%} of it in two months, so it may not recur" if c in lumpy else ""),
             f"retirement_spending:{c}")
    note("spending", "total", "Spending now, a year", round(sum(base.values()), 2), "the categories above")
    for r in adjust:
        note("changes", r["key"], f"{r['key']} ({r['basis']})", r["steps"], f"now {r['now']:,.0f} a year",
             f"retirement_spending:{r['key']}")
    note("health", "pre65", "Health cover before 65, a year for the household", h.health_pre65, "setting or default",
         "retirement_health_pre65")
    note("health", "supplement", "Medigap and Part D, a year a person from 65", h.health_supplement,
         "setting or default", "retirement_health_supplement")
    note("health", "hsa_spending", "Spending the HSA pays (medical)", round(hsa_spending, 2),
         "categories or groups named in hsa_spending_categories (default medical)", "hsa_spending_categories")
    note("returns", "stock_share", "Stocks in the portfolio", round(h.stock_share, 4),
         "setting" if value("retirement_stock_share") else "the allocation targets", "retirement_stock_share")
    note("returns", "cagrs", "Fixed yearly returns (nominal)", ", ".join(f"{c:.1%}" for c in cagrs), "setting or default",
         "retirement_cagrs")
    note("returns", "inflation", "Inflation for the fixed returns", h.inflation, "setting or default",
         "retirement_inflation")
    mean_r, mean_i = central(h)
    note("returns", "history", "History's real return for this mix, and inflation, a year (geometric)",
         f"{mean_r:.2%}, {mean_i:.2%}", f"markets.py, {markets.FIRST} to {markets.LAST}")
    note("returns", "yield", "Taxable accounts' dividends", h.taxable_yield, "setting or default",
         "retirement_taxable_yield")
    note("returns", "runs", "Monte Carlo runs", h.monte_carlo_runs, "setting or default", "retirement_monte_carlo_runs")
    note("plan", "to_age", "The plan runs until the younger is", h.plan_to_age, "setting or default",
         "retirement_plan_to_age")
    note("plan", "target", "Success needed to retire", h.success_target, "setting or default",
         "retirement_success_target")
    note("plan", "ss_cut", "Social Security cut assumed", f"{h.ss_cut:.0%} from {h.ss_cut_from}", "setting or none",
         "social_security_cut")
    for p in people:
        note("law", f"rmd:{p.name}", f"{p.name}: RMDs from age", law.rmd_age(p.born.year), "IRC 401(a)(9), SECURE 2.0")
        note("law", f"fra:{p.name}", f"{p.name}: full retirement age (months)", law.fra_months(p.born.year),
             "Social Security Act 216(l)")
    note("law", "tax_year", "Tax law used (held in today's dollars)", LAW_YEAR, "taxdata.py")
    return h, notes, pd.DataFrame(assume)


def allocation_stock_share(allocation):
    """The stocks' share of the targets (the allocation report's stocks node), or None."""
    if allocation is None or allocation.empty:
        return None
    a = allocation.assign(target_share=_num(allocation["target_share"]))
    row = a[a["node"].astype(str) == "stocks"]
    return float(row["target_share"].iloc[0]) if len(row) and row["target_share"].iloc[0] > 0 else None


SEARCH_AGAIN = 3


def success_at(years, year):
    row = years[(years["retire_year"] == year) & (years["model"] == "monte carlo")]
    return float(row["success"].iloc[0]) if len(row) else 0.0


def run(h, params, today):
    """Every retirement table: (years, strategies, plan, social security, earnings, notes)."""
    today_year = int(today[:4])
    notes = []
    base = parse_strategy(settings.value(params, "retirement_strategy")) or Strategy()
    if settings.value(params, "retirement_strategy") and not parse_strategy(settings.value(params, "retirement_strategy")):
        notes.append("retirement: retirement_strategy isn't a strategy key (like bracket/to22/constant); using "
                     + base.key)
    fixed_year = settings.value(params, "retirement_year")
    years, chosen = search(h, base, today_year)
    if fixed_year and str(fixed_year).isdigit():
        chosen = int(fixed_year)
    elif chosen is None:
        chosen = int(years["retire_year"].max())
        notes.append(f"retirement: no year through {chosen} reaches {h.success_target:.0%} Monte Carlo success; "
                     "the plan shows the last one tried")
    table, results = strategies(h, chosen, base, today_year)
    pick = parse_strategy(settings.value(params, "retirement_strategy")) or best(table) or base
    if pick != base and not fixed_year:  # the better strategy may move the year: try the years around it
        near = range(max(chosen - SEARCH_AGAIN, h.start), chosen + SEARCH_AGAIN + 1)
        redo, again = search(h, pick, today_year, near)
        years = pd.concat([years[~years["retire_year"].isin(list(near))], redo]).sort_values(
            ["retire_year", "model", "scenario"]).reset_index(drop=True)
        if again is None and success_at(redo, chosen + SEARCH_AGAIN) < h.success_target:
            again = chosen + SEARCH_AGAIN  # still short: the earliest year past the window that the first search met
        if again and again != chosen:
            chosen = again
            table, results = strategies(h, chosen, pick, today_year)
        else:  # the same year: only the spending rules and claiming ages change, around the new strategy
            old = set(variants(h, base))
            more, extra = strategies(h, chosen, pick, today_year, only=variants(h, pick))
            table = pd.concat([table[~table["strategy"].isin({s.key for s in old})], more], ignore_index=True)
            results = {**{s: r for s, r in results.items() if s not in old}, **extra}
    years.loc[years["retire_year"] == chosen, "chosen"] = "yes"
    table.loc[table["strategy"] == pick.key, "chosen"] = "yes"
    plan = pd.concat([plan_rows(h, s, chosen, today_year, results.get(s)) for s in results], ignore_index=True)
    ss, earned = social_security(h, chosen, today_year, dict(pick.claims))
    mc = table[(table["strategy"] == pick.key) & (table["model"] == "monte carlo")]
    notes.insert(0, f"retirement: {chosen} ({_ages(h, chosen)}) with {pick.label}: "
                    f"{float(mc['success'].iloc[0]) if len(mc) else 0:.0%} of Monte Carlo runs never run short")
    return years, table, plan, ss, earned, notes
