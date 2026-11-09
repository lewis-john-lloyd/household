"""Searching for the retirement year and the strategy, and laying the plan out as the Reports tables."""

from dataclasses import replace

import numpy as np
import pandas as pd

from .. import retirelaw as law, settings
from .model import CONVERSIONS, ORDERS, RULES, Household, Strategy, parse_strategy
from .ssa import _claim_year, benefits, pia
from .simulation import _age_mid, fixed_paths, historical_paths, monte_carlo_paths, run_all, simulate, success

# --- the reports ------------------------------------------------------------------------------------------

YEAR_COLUMNS = [
    "retire_year",
    "ages",
    "model",
    "scenario",
    "success",
    "median_end",
    "p10_end",
    "short_from",
    "at_retirement",
    "first_spending",
    "withdrawal_rate",
    "chosen",
]
STRATEGY_COLUMNS = [
    "strategy",
    "label",
    "order",
    "convert_to",
    "rule",
    "sepp",
    "claims",
    "model",
    "success",
    "median_after_tax",
    "p10_after_tax",
    "median_tax",
    "median_irmaa",
    "median_penalty",
    "median_converted",
    "median_spent",
    "lowest_spending",
    "chosen",
]
PLAN_COLUMNS = [
    "strategy",
    "year",
    "ages",
    "phase",
    "contributions",
    "spending",
    "health",
    "social_security",
    "rmd",
    "from_taxable",
    "from_pre_tax",
    "from_roth",
    "from_hsa",
    "converted",
    "ordinary_income",
    "capital_gains",
    "federal_tax",
    "state_tax",
    "irmaa",
    "penalty",
    "short",
    "taxable",
    "pre_tax",
    "roth",
    "hsa",
    "total",
    "p10_total",
    "p50_total",
    "p90_total",
]
SS_COLUMNS = [
    "person",
    "claim_age",
    "claim_year",
    "statement_monthly",
    "computed_monthly",
    "plan_monthly",
    "plan_yearly",
    "aime",
    "pia",
    "note",
]
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
            rows.append(
                {
                    "retire_year": y,
                    "ages": _ages(h, y),
                    "model": model.split(" ")[0] if model.startswith("fixed") else model,
                    "scenario": model[len("fixed ") :] if model.startswith("fixed") else "",
                    "success": success(r),
                    "median_end": float(np.median(r["end"])),
                    "p10_end": float(np.percentile(r["end"], 10)),
                    "short_from": int(min(starts)) if starts and model.startswith("fixed") else "",
                    "at_retirement": at,
                    "first_spending": float(np.median(r["first_need"])),
                    "withdrawal_rate": float(np.median(r["first_rate"])),
                    "chosen": "",
                }
            )
        if chosen is None and success(res["monte carlo"]) >= h.success_target:
            chosen = y
    return pd.DataFrame(rows, columns=YEAR_COLUMNS), chosen


def variants(h, base, claim_choices=(62, 67, 70)):
    """The base strategy under the other spending rules, and with each adult claiming at each age."""
    out = [replace(base, rule=r) for r in RULES if r != base.rule]
    if len(h.people) <= 2:
        combos = (
            [(a,) for a in claim_choices]
            if len(h.people) == 1
            else [(a, b) for a in claim_choices for b in claim_choices]
        )
        out += [
            replace(base, claims=tuple(zip([p.name for p in h.people], c)))
            for c in combos
            if tuple(c) != tuple(p.claim_age for p in h.people)
        ]
    return out


def strategies(h, retire_year, base, today_year, only=None):
    """Every order, conversion target and 72(t) choice at the retirement year, and the base strategy's
    variants (or only the strategies given)."""
    mc = monte_carlo_paths(h)
    hist = historical_paths(h, retire_year)
    if only is not None:
        grid = list(only)
    else:
        grid = [
            Strategy(o, c, "constant", s)
            for o in ORDERS
            for c in CONVERSIONS
            for s in (False, True)
            if s is False or any(_age_mid(p, retire_year) < law.EARLY_AGE for p in h.people)
        ]
        grid += variants(h, base)
        if base not in grid:
            grid.insert(0, base)
    results, rows = {}, []
    for s in grid:
        res = run_all(
            h, s, retire_year, today_year, {"monte_carlo": mc, ("historical", retire_year): hist}, record=True
        )
        results[s] = res
        for model in ("historical", "monte carlo"):
            r = res[model]
            rows.append(
                {
                    "strategy": s.key,
                    "label": s.label,
                    "order": s.order,
                    "convert_to": s.convert_to,
                    "rule": s.rule,
                    "sepp": s.sepp,
                    "claims": ", ".join(f"{n} {a}" for n, a in s.claims),
                    "model": model,
                    "success": success(r),
                    "median_after_tax": float(np.median(r["after_tax"])),
                    "p10_after_tax": float(np.percentile(r["after_tax"], 10)),
                    "median_tax": float(np.median(r["tax"])),
                    "median_irmaa": float(np.median(r["irmaa"])),
                    "median_penalty": float(np.median(r["penalty"])),
                    "median_converted": float(np.median(r["converted"])),
                    "median_spent": float(np.median(r["spent"])),
                    "lowest_spending": float(np.percentile(np.where(np.isfinite(r["lowest"]), r["lowest"], 1), 10)),
                    "chosen": "",
                }
            )
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
        rows.append(
            {
                **{c: "" for c in PLAN_COLUMNS},
                **row,
                "strategy": strategy.key,
                "p10_total": float(p10[j]),
                "p50_total": float(p50[j]),
                "p90_total": float(p90[j]),
            }
        )
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
        note = (
            ""
            if p.earnings
            else (
                "no statement: set social_security_monthly:" + p.name
                if not p.fra_monthly
                else "from social_security_monthly (no statement)"
            )
        )
        rows.append(
            {
                "person": p.name,
                "claim_age": age,
                "claim_year": claim_year,
                "statement_monthly": p.statement.get(age, ""),
                "computed_monthly": round(working, 2),
                "plan_monthly": round(plan_pia * factor, 2),
                "plan_yearly": round(full, 2),
                "aime": a,
                "pia": plan_pia,
                "note": note,
            }
        )
        if p.earnings:
            last = max(p.earnings)
            future = {y: p.earnings[last] for y in range(last + 1, retire_year)}
            index_year = min(p.born.year + 60, today_year - 2)
            base = law.AWI[min(index_year, max(law.AWI))]
            all_years = {**p.earnings, **future}
            indexed = {
                y: e * base / law.AWI[y] if y <= index_year and y in law.AWI else e for y, e in all_years.items()
            }
            top = set(sorted(indexed, key=lambda y: -indexed[y])[: law.COMPUTATION_YEARS])
            for y in sorted(all_years):
                earned.append(
                    {
                        "person": p.name,
                        "year": y,
                        "fica_earnings": all_years[y],
                        "indexed": round(indexed[y], 2),
                        "counted": "yes" if y in top and indexed[y] > 0 else "",
                        "projected": y > last,
                    }
                )
    earnings = pd.DataFrame(earned, columns=EARNINGS_COLUMNS + ["projected"])
    earnings["projected"] = earnings["projected"].map(lambda v: "projected" if v else "posted")
    return pd.DataFrame(rows, columns=SS_COLUMNS), earnings.rename(columns={"projected": "basis"})


SEARCH_AGAIN = 3


def success_at(years, year):
    row = years[(years["retire_year"] == year) & (years["model"] == "monte carlo")]
    return float(row["success"].iloc[0]) if len(row) else 0.0


def run(
    h: Household, params: pd.DataFrame, today: str
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame, list[str]]:
    """Every retirement table: (years, strategies, plan, social security, earnings, notes)."""
    today_year = int(today[:4])
    notes = []
    base = parse_strategy(settings.value(params, "retirement_strategy")) or Strategy()
    if settings.value(params, "retirement_strategy") and not parse_strategy(
        settings.value(params, "retirement_strategy")
    ):
        notes.append(
            "retirement: retirement_strategy isn't a strategy key (like bracket/to22/constant); using " + base.key
        )
    fixed_year = settings.value(params, "retirement_year")
    years, chosen = search(h, base, today_year)
    if fixed_year and str(fixed_year).isdigit():
        chosen = int(fixed_year)
    elif chosen is None:
        chosen = int(years["retire_year"].max())
        notes.append(
            f"retirement: no year through {chosen} reaches {h.success_target:.0%} Monte Carlo success; "
            "the plan shows the last one tried"
        )
    table, results = strategies(h, chosen, base, today_year)
    pick = parse_strategy(settings.value(params, "retirement_strategy")) or best(table) or base
    if pick != base and not fixed_year:  # the better strategy may move the year: try the years around it
        near = range(max(chosen - SEARCH_AGAIN, h.start), chosen + SEARCH_AGAIN + 1)
        redo, again = search(h, pick, today_year, near)
        years = (
            pd.concat([years[~years["retire_year"].isin(list(near))], redo])
            .sort_values(["retire_year", "model", "scenario"])
            .reset_index(drop=True)
        )
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
    notes.insert(
        0,
        f"retirement: {chosen} ({_ages(h, chosen)}) with {pick.label}: "
        f"{float(mc['success'].iloc[0]) if len(mc) else 0:.0%} of Monte Carlo runs never run short",
    )
    return years, table, plan, ss, earned, notes
