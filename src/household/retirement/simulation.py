"""The year-by-year simulation, and the returns it runs on: fixed, historical and Monte Carlo."""

from collections.abc import Callable
from dataclasses import dataclass
from collections.abc import Iterable
from typing import Any

import numpy as np
from numpy import ndarray

from .. import markets
from .. import retirelaw as law
from .model import (
    BRACKET_ORDER_RATE,
    GAIN_RATE_AT_END,
    GUARD_BAND,
    GUARD_LAST_YEARS,
    GUARD_STEP,
    HEIR_RATE,
    ITERATIONS,
    SEED,
    VPW_RETURNS,
    Household,
    Person,
    Strategy,
)
from .ssa import benefits
from .tax import P, bracket_top, irmaa, taxes

# --- the simulation ---------------------------------------------------------------------------------------


def _age_mid(p: Person, year: int) -> float:
    return year + 0.5 - (p.born.year + (p.born.month - 1) / 12)


def _take(need: ndarray, sources: list[list[str]], caps: dict[str, ndarray]) -> tuple[dict[str, ndarray], ndarray]:
    """Take need from the sources in order, a tier at a time: a tier is a list of source names, shared in
    proportion to what each can give. Returns ({source: amount}, shortfall)."""
    taken: dict[str, ndarray] = {}
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


def _deduct(plan: ndarray, ira: ndarray, amount: ndarray, plan_first: bool) -> tuple[ndarray, ndarray]:
    if plan_first:
        a = np.minimum(amount, plan)
        return plan - a, np.maximum(ira - (amount - a), 0)
    a = np.minimum(amount, ira)
    return np.maximum(plan - (amount - a), 0), ira - a


Paths = np.ndarray  # one value per path


def _total(values: Iterable[Paths], n: int) -> Paths:
    """The sum of per-path values: zeros when there are none."""
    return sum(values, np.zeros(n))


def _got(got: dict[str, Paths], source: str, n: int) -> Paths:
    """What _take took from a source: zeros when it took nothing."""
    return got[source] if source in got else np.zeros(n)


@dataclass
class Accounts:
    """The money in each kind of account, one value per path, at a point in the plan: each person's pre-tax money
    in workplace plans and IRAs and their Roth money (with the part that comes out free before 59 1/2, and
    conversions still seasoning, by the year they come free), and the household's taxable accounts (with their
    basis) and HSA."""

    pre_plan: dict[str, Paths]
    pre_ira: dict[str, Paths]
    roth: dict[str, Paths]
    access: dict[str, Paths]
    pending: dict[str, dict[int, Paths]]
    taxable: Paths
    basis: Paths
    hsa: Paths

    @classmethod
    def at_start(cls, h: Household, n: int) -> "Accounts":
        names = [p.name for p in h.people]
        one = np.ones(n)
        return cls(
            pre_plan={k: h.pre_plan.get(k, 0.0) * one for k in names},
            pre_ira={k: h.pre_ira.get(k, 0.0) * one for k in names},
            roth={k: h.roth.get(k, 0.0) * one for k in names},
            access={k: np.minimum(h.roth_basis.get(k, 0.0), h.roth.get(k, 0.0)) * one for k in names},
            pending={k: {y: a * one for y, a in h.pending.get(k, {}).items()} for k in names},
            taxable=h.taxable * one,
            basis=h.basis * one,
            hsa=h.hsa * one,
        )

    def total(self, names: list[str]) -> Paths:
        return self.taxable + self.hsa + sum(self.pre_plan[k] + self.pre_ira[k] + self.roth[k] for k in names)

    def work_year(self, h: Household, r: Paths, infl: Paths) -> None:
        """A year of working: every account grows at the year's return and takes the year's contributions."""
        for p in h.people:
            c = h.contributions.get(p.name, {})
            self.pre_plan[p.name] = self.pre_plan[p.name] * (1 + r) + c.get("pre_plan", 0.0)
            self.pre_ira[p.name] = self.pre_ira[p.name] * (1 + r)
            self.roth[p.name] = self.roth[p.name] * (1 + r) + c.get("roth", 0.0)
            self.access[p.name] = self.access[p.name] + c.get("roth", 0.0)
        self.basis = self.basis / (1 + infl) + h.taxable_savings + h.taxable_yield * self.taxable
        self.taxable = self.taxable * (1 + r) + h.taxable_savings
        self.hsa = self.hsa * (1 + r) + h.hsa_contribution

    def take_pre_tax(self, k: str, amount: Paths) -> None:
        """A required or 72(t) withdrawal of a person's pre-tax money: from the IRA first."""
        from_ira = np.minimum(amount, self.pre_ira[k])
        self.pre_ira[k], self.pre_plan[k] = self.pre_ira[k] - from_ira, self.pre_plan[k] - (amount - from_ira)

    def grow(self, names: list[str], r: Paths, infl: Paths) -> None:
        """The year's return on what is left; the taxable basis is held in today's dollars."""
        self.taxable, self.hsa = self.taxable * (1 + r), self.hsa * (1 + r)
        self.basis = self.basis / (1 + infl)
        for k in names:
            self.pre_plan[k], self.pre_ira[k], self.roth[k] = (
                self.pre_plan[k] * (1 + r),
                self.pre_ira[k] * (1 + r),
                self.roth[k] * (1 + r),
            )


@dataclass
class Lifetime:
    """What the plan adds up over the retirement years, one value per path."""

    tax: Paths
    irmaa: Paths
    penalty: Paths
    converted: Paths
    spent: Paths
    base_spent: float
    lowest: Paths
    short: Paths
    first_short: Paths

    @classmethod
    def empty(cls, n: int) -> "Lifetime":
        return cls(
            tax=np.zeros(n),
            irmaa=np.zeros(n),
            penalty=np.zeros(n),
            converted=np.zeros(n),
            spent=np.zeros(n),
            base_spent=0.0,
            lowest=np.full(n, np.inf),
            short=np.zeros(n, dtype=bool),
            first_short=np.full(n, 0),
        )


@dataclass
class Year:
    """One retirement year's money, once withdrawals and taxes are solved together."""

    w_pre: dict[str, Paths]  # pre-tax, free of the 10%
    w_pre_late: dict[str, Paths]  # pre-tax that pays the 10% (before 59 1/2)
    w_roth: dict[str, Paths]
    w_roth_late: dict[str, Paths]
    w_tax: Paths
    w_hsa: Paths
    gain_share: Paths
    gains: Paths
    ordinary: Paths
    conv: dict[str, Paths]
    penalized: Paths
    fed: Paths
    wi: Paths
    agi: Paths
    tax: Paths
    lacking: Paths

    def withdrawn(self, names: list[str]) -> Paths:
        return (
            self.w_tax
            + self.w_hsa
            + sum(self.w_pre[k] + self.w_pre_late[k] + self.w_roth[k] + self.w_roth_late[k] for k in names)
        )


@dataclass
class Outcome:
    """What a plan comes to, one value per path (and the first path's years, when recorded)."""

    short: Paths  # ran short of money in some year
    first_short: Paths  # the first such year, or 0
    end: Paths  # everything left at the end
    after_tax: Paths  # what is left after the tax heirs would pay on it
    tax: Paths  # lifetime tax, federal and state
    irmaa: Paths  # lifetime Medicare surcharges
    penalty: Paths  # lifetime 10% on money taken early
    converted: Paths  # lifetime Roth conversions
    spent: Paths
    base_spent: float  # what the spending as set would have been
    lowest: Paths  # the lowest year's spending, as a share of the spending as set
    at_retirement: Paths  # invested on retiring
    first_rate: Paths  # the first year's withdrawals as a share of what is invested
    first_need: Paths  # the first year's spending and health cover
    totals: np.ndarray | None  # (years, paths): everything invested each year, when recorded
    rows: list[dict[str, str | int | float]]  # the first path's years, when recorded
    starts: list[int] | None = None  # for history: the first year of each sequence


def _forced(
    acc: Accounts,
    people: list[Person],
    year: int,
    end_age: dict[str, int],
    sepp: dict[str, ndarray],
    sepp_until: dict[str, int],
    one: ndarray,
) -> tuple[dict[str, ndarray], dict[str, ndarray]]:
    """Required minimum distributions from the RMD age, and a 72(t) plan's payments while it runs: taken from
    the accounts, and returned by person."""
    n = len(one)
    rmd = {}
    for p in people:
        k = p.name
        if end_age[k] >= law.rmd_age(p.born.year):
            rmd[k] = (acc.pre_plan[k] + acc.pre_ira[k]) / law.distribution_period(end_age[k])
        else:
            rmd[k] = np.zeros(n)
        acc.take_pre_tax(k, rmd[k])
    paid_sepp = {}
    for p in people:
        k = p.name
        on = k in sepp_until and year <= sepp_until[k]
        amount = np.minimum(sepp[k] if on else 0.0, acc.pre_ira[k] + acc.pre_plan[k])
        acc.take_pre_tax(k, amount)
        paid_sepp[k] = amount * one
    return rmd, paid_sepp


def _health(
    h: Household,
    acc: Accounts,
    names: list[str],
    mid_age: dict[str, float],
    magi: dict[int, ndarray],
    year: int,
    working_magi: float,
    one: ndarray,
) -> tuple[ndarray, ndarray, ndarray]:
    """Health cover for the year (before 65, and Medicare with its income surcharges from 65), and what the HSA
    pays of it. Returns (health, the surcharge, the HSA's part)."""
    n = len(one)
    enrolled = sum(mid_age[k] >= law.MEDICARE_AGE for k in names)
    under = len(names) - enrolled
    past = magi.get(year - law.IRMAA_LOOKBACK, working_magi * one)
    medicare, surcharge = irmaa(past, enrolled) if enrolled else (np.zeros(n), np.zeros(n))
    health = h.health_pre65 / len(names) * under + h.health_supplement * enrolled + medicare
    hsa_pay = np.minimum(acc.hsa, medicare + h.hsa_spending)
    acc.hsa = acc.hsa - hsa_pay
    return health, surcharge, hsa_pay


def _caps(
    acc: Accounts, people: list[Person], early: dict[str, bool], rule55: dict[str, bool], n: int
) -> dict[str, ndarray]:
    """What each source can give this year: pre-tax and Roth money free of the 10% (by person), the rest that
    pays it, the taxable accounts and the HSA."""
    caps0 = {"taxable": acc.taxable}
    for p in people:
        k = p.name
        if not early[k]:
            caps0[f"pre:{k}"], caps0[f"pre_late:{k}"] = acc.pre_plan[k] + acc.pre_ira[k], np.zeros(n)
        elif rule55[k]:
            caps0[f"pre:{k}"], caps0[f"pre_late:{k}"] = acc.pre_plan[k], acc.pre_ira[k]
        else:
            caps0[f"pre:{k}"], caps0[f"pre_late:{k}"] = np.zeros(n), acc.pre_plan[k] + acc.pre_ira[k]
        caps0[f"roth:{k}"] = np.where(early[k], np.minimum(acc.access[k], acc.roth[k]), acc.roth[k])
        caps0[f"roth_late:{k}"] = np.where(early[k], np.maximum(acc.roth[k] - acc.access[k], 0), 0.0)
    caps0["hsa"] = acc.hsa
    return caps0


def _solve(strategy: Strategy, acc: Accounts, names: list[str], caps0: dict[str, Paths],
           need_of: Callable[[Paths], Paths], n65: int, ordinary_fixed: Paths, income_ss: Paths, dividends: Paths,
           rmd: dict[str, Paths], paid_sepp: dict[str, Paths], end_age: dict[str, int], deflator: Paths, year: int,
           n: int) -> Year:  # fmt: skip
    """Withdrawals and the taxes on them, solved together (each depends on the other) by iterating: take what
    the year needs in the strategy's order, convert to the top of a bracket if the strategy does, and work the
    tax out the way the forms do."""
    pre = [f"pre:{k}" for k in names]
    roths = [f"roth:{k}" for k in names]
    late = [[f"pre_late:{k}" for k in names], [f"roth_late:{k}" for k in names], ["hsa"]]
    tax = np.zeros(n)
    ss_taxed = 0.85 * income_ss
    for _ in range(ITERATIONS):
        need = need_of(tax)
        caps = dict(caps0)
        deduct = P["standard_deduction_mfj"] + law.AGED_DEDUCTION_MFJ * n65
        if strategy.order == "bracket":
            room = np.maximum(bracket_top(BRACKET_ORDER_RATE) + deduct - ordinary_fixed - ss_taxed, 0)
            pre_total = _total((caps[s] for s in pre), n)
            capped = {
                s: np.minimum(caps[s], room * np.where(pre_total > 0, caps[s] / np.maximum(pre_total, 1), 0))
                for s in pre
            }
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
        w_pre = {k: _got(got, f"pre:{k}", n) + _got(got, f"pre:{k}#room", n) for k in names}
        w_pre_late = {k: _got(got, f"pre_late:{k}", n) for k in names}
        w_roth = {k: _got(got, f"roth:{k}", n) for k in names}
        w_roth_late = {k: _got(got, f"roth_late:{k}", n) for k in names}
        w_tax, w_hsa = _got(got, "taxable", n), _got(got, "hsa", n)
        gain_share = np.where(acc.taxable > 0, np.clip(1 - acc.basis / np.maximum(acc.taxable, 1e-9), 0, 1), 0)
        gains = w_tax * gain_share
        ordinary = ordinary_fixed + _total((w_pre[k] + w_pre_late[k] + w_roth_late[k] for k in names), n) + w_hsa
        conv = {k: np.zeros(n) for k in names}
        if strategy.convert_to:
            room = np.maximum(bracket_top(strategy.convert_to) + deduct - ordinary - ss_taxed, 0)
            left = {k: acc.pre_plan[k] + acc.pre_ira[k] - w_pre[k] - w_pre_late[k] for k in names}
            total_left = _total(left.values(), n)
            for k in names:
                conv[k] = np.where(
                    total_left > 0, np.minimum(room, total_left) * left[k] / np.maximum(total_left, 1e-9), 0
                )
            ordinary = ordinary + _total(conv.values(), n)
        penalized = _total((w_pre_late[k] + w_roth_late[k] for k in names), n)
        excluded = _total(
            (
                np.minimum(law.WI_RETIREMENT_EXCLUSION / deflator, rmd[k] + paid_sepp[k] + w_pre[k] + w_pre_late[k])
                * (end_age[k] >= law.WI_RETIREMENT_AGE)
                for k in names
            ),
            n,
        )
        fed, wi, agi, ss_taxed = taxes(
            ordinary, gains, dividends, income_ss, penalized, excluded, n65, len(names), deflator, year
        )
        tax = fed + wi
    return Year(w_pre, w_pre_late, w_roth, w_roth_late, w_tax, w_hsa, gain_share, gains, ordinary, conv, penalized,
                fed, wi, agi, tax, lacking)  # fmt: skip


def _settle(
    acc: Accounts,
    y: Year,
    names: list[str],
    early: dict[str, bool],
    rule55: dict[str, bool],
    year: int,
    surplus: ndarray,
) -> None:
    """The year's withdrawals and conversions taken from the accounts, and any surplus saved in taxable."""
    acc.taxable = acc.taxable - y.w_tax
    acc.basis = np.maximum(acc.basis - y.w_tax * (1 - y.gain_share), 0)
    acc.hsa = acc.hsa - y.w_hsa
    for k in names:
        # money free of the 10% through the Rule of 55 is the plan's; the rest, and conversions, come from
        # the IRA first
        acc.pre_plan[k], acc.pre_ira[k] = _deduct(
            acc.pre_plan[k], acc.pre_ira[k], y.w_pre[k], plan_first=early[k] and rule55[k]
        )
        acc.pre_plan[k], acc.pre_ira[k] = _deduct(
            acc.pre_plan[k], acc.pre_ira[k], y.w_pre_late[k] + y.conv[k], plan_first=False
        )
        roth_out = y.w_roth[k] + y.w_roth_late[k]
        acc.access[k] = np.maximum(acc.access[k] - np.minimum(roth_out, acc.access[k]), 0)
        acc.roth[k] = np.maximum(acc.roth[k] - roth_out, 0) + y.conv[k]
        acc.pending[k][year + law.CONVERSION_WAIT] = acc.pending[k].get(year + law.CONVERSION_WAIT, 0.0) + y.conv[k]
    acc.taxable = acc.taxable + surplus
    acc.basis = acc.basis + surplus


def simulate(
    h: Household,
    strategy: Strategy,
    retire_year: int,
    returns: ndarray,
    inflation: ndarray,
    today_year: int,
    record: bool = False,
) -> Outcome:
    """Run the plan over paths: returns and inflation are (years, paths) arrays for h.years. Returns the
    per-path results (and per-year rows of the first path when record).

    Each working year grows the accounts and adds the contributions (Accounts.work_year). Each retirement year:
    Roth conversions come free after five years; RMDs and a 72(t) plan's payments come out (_forced); health
    cover is paid, the HSA first (_health); spending is set by the strategy's rule; withdrawals and taxes are
    solved together (_solve) from what each source can give (_caps); the accounts are settled (_settle) and
    grow (Accounts.grow); and the year adds to the lifetime totals."""
    years = h.years
    n = returns.shape[1]
    people = h.people
    names = [p.name for p in people]
    one = np.ones(n)
    acc = Accounts.at_start(h, n)
    deflator = one.copy()
    claims = dict(strategy.claims)
    ss = benefits(h, retire_year, claims, today_year)
    working_magi = sum(p.medicare_wages for p in people)
    magi: dict[int, Paths] = {}
    life = Lifetime.empty(n)
    multiplier, wr0, last_withdrawal, first_need = one.copy(), np.zeros(n), np.zeros(n), np.zeros(n)
    sepp = {k: np.zeros(n) for k in names}
    sepp_until: dict[str, int] = {}
    at_retirement: Paths | None = None
    totals = np.zeros((len(years), n)) if record else None
    rows: list[dict[str, str | int | float]] = []
    vpw_r = h.stock_share * VPW_RETURNS[0] + (1 - h.stock_share) * VPW_RETURNS[1]

    for i, year in enumerate(years):
        r, infl = returns[i], inflation[i]
        if year < retire_year:
            acc.work_year(h, r, infl)
            deflator = deflator * (1 + infl)
            magi[year] = working_magi * one
            if totals is not None:
                totals[i] = acc.total(names)
                rows.append(
                    _row(
                        year,
                        people,
                        "working",
                        acc.taxable,
                        acc.hsa,
                        acc.pre_plan,
                        acc.pre_ira,
                        acc.roth,
                        contributions=h.taxable_savings
                        + h.hsa_contribution
                        + sum(sum(c.values()) for c in h.contributions.values()),
                    )
                )
            continue

        end_age = {p.name: year - p.born.year for p in people}
        mid_age = {p.name: _age_mid(p, year) for p in people}
        early = {k: mid_age[k] < law.EARLY_AGE for k in names}
        for k in names:
            acc.access[k] = acc.access[k] + acc.pending[k].pop(year, 0.0)
        portfolio = acc.total(names)
        if at_retirement is None:
            at_retirement = portfolio.copy()
            for p in people:  # a 72(t) plan starts with retirement and runs 5 years or to 59 1/2
                k = p.name
                if strategy.sepp and early[k]:
                    sepp[k] = law.sepp_payment(acc.pre_ira[k] + acc.pre_plan[k], end_age[k])
                    sepp_until[k] = max(
                        year + law.SEPP_MIN_YEARS - 1, int(p.born.year + law.EARLY_AGE + (p.born.month > 6))
                    )
        rule55 = {p.name: retire_year >= p.born.year + law.RULE_OF_55 for p in people}

        income_ss = sum(ss[k].get(year, 0.0) for k in names) * one
        rmd, paid_sepp = _forced(acc, people, year, end_age, sepp, sepp_until, one)
        dividends = h.taxable_yield * acc.taxable
        acc.taxable = acc.taxable - dividends
        health, surcharge, hsa_pay = _health(h, acc, names, mid_age, magi, year, working_magi, one)

        base = h.spending.get(year, h.spending[max(h.spending)]) if h.spending else 0.0
        years_left = h.end - year + 1
        if strategy.rule == "guardrails" and year > retire_year:
            rate = np.where(portfolio > 0, last_withdrawal / np.maximum(portfolio, 1), np.inf)
            cut = (rate > wr0 * (1 + GUARD_BAND)) & (years_left > GUARD_LAST_YEARS)
            multiplier = np.where(
                cut,
                multiplier * (1 - GUARD_STEP),
                np.where(rate < wr0 * (1 - GUARD_BAND), multiplier * (1 + GUARD_STEP), multiplier),
            )
        spend = base * multiplier  # under vpw, set once the withdrawals are known
        forced = _total((rmd[k] + paid_sepp[k] for k in names), n)
        cash_in = income_ss + forced + dividends + hsa_pay
        need_of: Callable[[Paths], Paths]
        if strategy.rule == "vpw":
            invested = acc.taxable + sum(acc.pre_plan[k] + acc.pre_ira[k] + acc.roth[k] for k in names) + forced
            target = np.minimum(invested * vpw_r / (1 - (1 + vpw_r) ** -years_left), invested)

            def vpw_need(tax: Paths, target: Paths = target, forced: Paths = forced) -> Paths:
                return np.maximum(target - forced, 0)

            need_of = vpw_need
        else:

            def spending_need(
                tax: Paths, spend: Paths = spend, health: Paths = health, cash_in: Paths = cash_in
            ) -> Paths:
                return np.maximum(spend + health - cash_in + tax, 0)

            need_of = spending_need

        caps0 = _caps(acc, people, early, rule55, n)
        n65 = sum(end_age[k] >= 65 for k in names)
        ordinary_fixed = sum(rmd[k] + paid_sepp[k] for k in names)
        y = _solve(strategy, acc, names, caps0, need_of, n65, ordinary_fixed, income_ss, dividends, rmd, paid_sepp,
                   end_age, deflator, year, n)  # fmt: skip
        withdrawn = y.withdrawn(names)
        if strategy.rule == "vpw":  # spending is what the withdrawals and income leave after health and tax
            spend = np.maximum(withdrawn + cash_in - health - y.tax, 0)
            surplus = np.zeros(n)
        else:
            surplus = np.maximum(cash_in - spend - health - y.tax, 0) * (withdrawn <= 1e-6)
        out_now = y.lacking > 1
        life.first_short = np.where(out_now & ~life.short, year, life.first_short)
        life.short = life.short | out_now

        _settle(acc, y, names, early, rule55, year, surplus)
        acc.grow(names, r, infl)
        deflator = deflator * (1 + infl)
        magi[year] = y.agi
        last_withdrawal = withdrawn + forced
        if year == retire_year:
            wr0 = np.where(portfolio > 0, last_withdrawal / np.maximum(portfolio, 1), 0)
            first_need = spend + health
        penalty = law.EARLY_PENALTY * y.penalized * (1 + law.WI_EARLY_SHARE)
        life.tax += y.tax
        life.irmaa += surcharge
        life.penalty += penalty
        life.converted += sum(y.conv.values())
        life.spent += spend
        life.base_spent += base
        life.lowest = np.minimum(life.lowest, np.where(base > 0, spend / base, 1.0))
        if totals is not None:
            totals[i] = acc.total(names)
            rows.append(
                _row(
                    year,
                    people,
                    "retired",
                    acc.taxable,
                    acc.hsa,
                    acc.pre_plan,
                    acc.pre_ira,
                    acc.roth,
                    spending=spend,
                    health=health,
                    social_security=income_ss,
                    rmd=sum(rmd.values()),
                    from_taxable=y.w_tax + dividends,
                    from_pre_tax=sum(y.w_pre[k] + y.w_pre_late[k] for k in names) + sum(paid_sepp.values()),
                    from_roth=sum(y.w_roth[k] + y.w_roth_late[k] for k in names),
                    from_hsa=y.w_hsa + hsa_pay,
                    converted=sum(y.conv.values()),
                    ordinary_income=y.ordinary,
                    capital_gains=y.gains + dividends,
                    federal_tax=y.fed,
                    state_tax=y.wi,
                    irmaa=surcharge,
                    penalty=penalty,
                    short=y.lacking,
                )
            )

    pre_end = sum(acc.pre_plan[k] + acc.pre_ira[k] for k in names)
    after_tax = (
        acc.taxable
        - GAIN_RATE_AT_END * np.maximum(acc.taxable - acc.basis, 0)
        + acc.hsa
        + sum(acc.roth[k] for k in names)
        + pre_end * (1 - HEIR_RATE)
    )
    return Outcome(
        short=life.short,
        first_short=life.first_short,
        end=acc.taxable + acc.hsa + sum(acc.roth.values()) + pre_end,
        after_tax=after_tax,
        tax=life.tax,
        irmaa=life.irmaa,
        penalty=life.penalty,
        converted=life.converted,
        spent=life.spent,
        base_spent=life.base_spent,
        lowest=life.lowest,
        at_retirement=at_retirement if at_retirement is not None else np.zeros(n),
        first_rate=wr0,
        first_need=first_need,
        totals=totals,
        rows=rows,
    )


def _row(
    year: int,
    people: list[Person],
    phase: str,
    tax_val: ndarray,
    hsa: ndarray,
    pre_plan: dict[str, ndarray],
    pre_ira: dict[str, ndarray],
    roth: dict[str, ndarray],
    **values: Paths | float,
) -> dict[str, str | int | float]:
    names = [p.name for p in people]

    def first(v: Paths | float) -> float:
        return float(np.atleast_1d(v)[0])

    amounts = {
        "taxable": first(tax_val),
        "pre_tax": first(sum(pre_plan[k] + pre_ira[k] for k in names)),
        "roth": first(sum(roth[k] for k in names)),
        "hsa": first(hsa),
    }
    amounts["total"] = amounts["taxable"] + amounts["pre_tax"] + amounts["roth"] + amounts["hsa"]
    row: dict[str, str | int | float] = {
        "year": year,
        "ages": "/".join(str(year - p.born.year) for p in people),
        "phase": phase,
        **amounts,
    }
    row.update({k: first(v) for k, v in values.items()})
    return row


# --- returns ----------------------------------------------------------------------------------------------


def fixed_paths(h: Household, cagr: float) -> tuple[ndarray, ndarray]:
    real = (1 + cagr) / (1 + h.inflation) - 1
    shape = (len(h.years), 1)
    return np.full(shape, real), np.full(shape, h.inflation)


def central(h: Household) -> tuple[float, float]:
    """History's average real return for the portfolio, and its average inflation, both geometric."""
    _, series = markets.real_series(h.stock_share)
    infl = np.array([markets.history()[y][2] for y in sorted(markets.RETURNS)])
    return float(np.prod(1 + series) ** (1 / len(series)) - 1), float(np.prod(1 + infl) ** (1 / len(infl)) - 1)


def historical_paths(h: Household, retire_year: int) -> tuple[ndarray, ndarray, list[int]]:
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


def monte_carlo_paths(h: Household, runs: None = None, seed: int = SEED) -> tuple[ndarray, ndarray]:
    """Each year of the whole plan drawn at random from history (the same draw for every plan, so runs
    compare like with like)."""
    years = sorted(markets.RETURNS)
    hist = markets.history()
    _, series = markets.real_series(h.stock_share)
    infl = np.array([hist[y][2] for y in years])
    pick = np.random.default_rng(seed).integers(0, len(years), size=(len(h.years), runs or h.monte_carlo_runs))
    return series[pick], infl[pick]


def run_all(
    h: Household,
    strategy: Strategy,
    retire_year: int,
    today_year: int,
    paths: dict[Any, Any] | None = None,
    record: bool = False,
) -> dict[str, Outcome]:
    """{model: results} for the fixed returns, history and Monte Carlo."""
    paths = paths or {}
    out: dict[str, Outcome] = {}
    for c in h.cagrs:
        r, i = fixed_paths(h, c)
        out[f"fixed {c:.0%}"] = simulate(h, strategy, retire_year, r, i, today_year, record=record)
    r, i, starts = paths.get(("historical", retire_year)) or historical_paths(h, retire_year)
    out["historical"] = simulate(h, strategy, retire_year, r, i, today_year)
    out["historical"].starts = starts
    r, i = paths.get("monte_carlo") or monte_carlo_paths(h)
    out["monte carlo"] = simulate(h, strategy, retire_year, r, i, today_year, record=record)
    return out


def success(result: Outcome) -> float:
    return float(1 - result.short.mean())
