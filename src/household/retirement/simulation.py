"""The year-by-year simulation, and the returns it runs on: fixed, historical and Monte Carlo."""

import numpy as np

from .. import markets, retirelaw as law
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
)
from .ssa import benefits
from .tax import P, bracket_top, irmaa, taxes

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
                rows.append(
                    _row(
                        year,
                        people,
                        "working",
                        tax_val,
                        hsa,
                        pre_plan,
                        pre_ira,
                        roth,
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
            access[k] = access[k] + pending[k].pop(year, 0.0)
        portfolio = tax_val + hsa + sum(pre_plan[k] + pre_ira[k] + roth[k] for k in names)
        if at_retirement is None:
            at_retirement = portfolio.copy()
            for p in people:  # a 72(t) plan starts with retirement and runs 5 years or to 59 1/2
                k = p.name
                if strategy.sepp and early[k]:
                    sepp[k] = law.sepp_payment(pre_ira[k] + pre_plan[k], end_age[k])
                    sepp_until[k] = max(
                        year + law.SEPP_MIN_YEARS - 1, int(p.born.year + law.EARLY_AGE + (p.born.month > 6))
                    )
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
            multiplier = np.where(
                cut,
                multiplier * (1 - GUARD_STEP),
                np.where(rate < wr0 * (1 - GUARD_BAND), multiplier * (1 + GUARD_STEP), multiplier),
            )
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
            w_pre = {k: got.get(f"pre:{k}", 0.0) + got.get(f"pre:{k}#room", 0.0) for k in names}
            w_pre_late = {k: got.get(f"pre_late:{k}", 0.0) for k in names}
            w_roth = {k: got.get(f"roth:{k}", 0.0) for k in names}
            w_roth_late = {k: got.get(f"roth_late:{k}", 0.0) for k in names}
            w_tax, w_hsa = got.get("taxable", 0.0), got.get("hsa", 0.0)
            gain_share = np.where(tax_val > 0, np.clip(1 - basis / np.maximum(tax_val, 1e-9), 0, 1), 0)
            gains = w_tax * gain_share
            ordinary = ordinary_fixed + sum(w_pre[k] + w_pre_late[k] + w_roth_late[k] for k in names) + w_hsa
            conv = {k: np.zeros(n) for k in names}
            if strategy.convert_to:
                room = np.maximum(bracket_top(strategy.convert_to) + deduct - ordinary - ss_taxed, 0)
                left = {k: pre_plan[k] + pre_ira[k] - w_pre[k] - w_pre_late[k] for k in names}
                total_left = sum(left.values())
                for k in names:
                    conv[k] = np.where(
                        total_left > 0, np.minimum(room, total_left) * left[k] / np.maximum(total_left, 1e-9), 0
                    )
                ordinary = ordinary + sum(conv.values())
            penalized = sum(w_pre_late[k] + w_roth_late[k] for k in names)
            excluded = sum(
                np.minimum(law.WI_RETIREMENT_EXCLUSION / deflator, rmd[k] + paid_sepp[k] + w_pre[k] + w_pre_late[k])
                * (end_age[k] >= law.WI_RETIREMENT_AGE)
                for k in names
            )
            fed, wi, agi, ss_taxed = taxes(
                ordinary, gains, dividends, income_ss, penalized, excluded, n65, len(names), deflator, year
            )
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
            rows.append(
                _row(
                    year,
                    people,
                    "retired",
                    tax_val,
                    hsa,
                    pre_plan,
                    pre_ira,
                    roth,
                    spending=spend,
                    health=health,
                    social_security=income_ss,
                    rmd=sum(rmd.values()),
                    from_taxable=w_tax + dividends,
                    from_pre_tax=sum(w_pre[k] + w_pre_late[k] for k in names) + sum(paid_sepp.values()),
                    from_roth=sum(w_roth[k] + w_roth_late[k] for k in names),
                    from_hsa=w_hsa + hsa_pay,
                    converted=sum(conv.values()),
                    ordinary_income=ordinary,
                    capital_gains=gains + dividends,
                    federal_tax=fed,
                    state_tax=wi,
                    irmaa=surcharge,
                    penalty=law.EARLY_PENALTY * penalized * (1 + law.WI_EARLY_SHARE),
                    short=lacking,
                )
            )

    pre_end = sum(pre_plan[k] + pre_ira[k] for k in names)
    after_tax = (
        tax_val
        - GAIN_RATE_AT_END * np.maximum(tax_val - basis, 0)
        + hsa
        + sum(roth[k] for k in names)
        + pre_end * (1 - HEIR_RATE)
    )
    return {
        "short": short,
        "first_short": first_short,
        "end": tax_val + hsa + sum(roth.values()) + pre_end,
        "after_tax": after_tax,
        "tax": lifetime_tax,
        "irmaa": lifetime_irmaa,
        "penalty": lifetime_penalty,
        "converted": converted,
        "spent": spent,
        "base_spent": base_spent,
        "lowest": lowest,
        "at_retirement": at_retirement if at_retirement is not None else np.zeros(n),
        "first_rate": wr0,
        "first_need": first_need,
        "totals": totals,
        "rows": rows,
    }


def _row(year, people, phase, tax_val, hsa, pre_plan, pre_ira, roth, **values):
    names = [p.name for p in people]
    first = lambda v: float(np.atleast_1d(v)[0])  # noqa: E731
    row = {
        "year": year,
        "ages": "/".join(str(year - p.born.year) for p in people),
        "phase": phase,
        "taxable": first(tax_val),
        "pre_tax": first(sum(pre_plan[k] + pre_ira[k] for k in names)),
        "roth": first(sum(roth[k] for k in names)),
        "hsa": first(hsa),
    }
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
