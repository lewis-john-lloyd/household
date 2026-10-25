"""Federal income tax for a married-filing-jointly household, line by line like Form 1040.

Pure functions over plain numbers. `federal_return` takes the year's inputs
(W-2 boxes, investment income, itemized deduction inputs, credits) and returns
the lines a filed return shows, so a computed year can be checked line by line
against the real return.

Covered: ordinary brackets; the qualified dividends and capital gain worksheet;
itemized deductions with the SALT cap and its phase-down, the 0.5% charitable
floor and the 2/37 limitation where the year has them; Additional Medicare Tax
(Form 8959); net investment income tax (Form 8960, with state income tax
allocated by the investment share of AGI); excess Social Security withholding.
Wisconsin lines (wisconsin.py) and Pennsylvania lines (pennsylvania.py) are added when a
year's facts include inputs for that state.
Not covered: AMT (zero at these incomes with these deductions; reported as an
assumption), self-employment, foreign tax, education and child credits (phased
out at these incomes).
"""

import pandas as pd

from . import taxdata


def whole(x):
    """Round half away from zero to whole dollars, as the forms do."""
    return int(x + 0.5) if x >= 0 else -int(-x + 0.5)


def bracket_tax(taxable, year):
    """Tax on ordinary income under the year's MFJ rate schedule (exact, not rounded)."""
    tax, steps = 0.0, taxdata.brackets(year)
    for i, (floor, rate) in enumerate(steps):
        top = steps[i + 1][0] if i + 1 < len(steps) else float("inf")
        if taxable > floor:
            tax += (min(taxable, top) - floor) * rate
    return tax


def qdcg_tax(taxable, qualified_dividends, net_capital_gain, year):
    """Qualified Dividends and Capital Gain Tax Worksheet (line 16), whole dollars."""
    p = taxdata.params(year)
    preferred = min(taxable, qualified_dividends + max(net_capital_gain, 0))
    ordinary = taxable - preferred
    if preferred <= 0:
        return whole(bracket_tax(taxable, year))
    zero_band = max(min(taxable, p["qdcg_zero_top_mfj"]) - ordinary, 0)
    fifteen_room = max(min(taxable, p["qdcg_fifteen_top_mfj"]) - max(ordinary, p["qdcg_zero_top_mfj"]), 0)
    at_fifteen = min(preferred - zero_band, fifteen_room)
    at_twenty = preferred - zero_band - at_fifteen
    worksheet = whole(at_fifteen * 0.15) + whole(at_twenty * 0.20) + whole(bracket_tax(ordinary, year))
    return min(worksheet, whole(bracket_tax(taxable, year)))


def salt_deduction(state_income_tax, real_estate_tax, magi, year):
    p = taxdata.params(year)
    cap = p["salt_cap"] - p["salt_phase_rate"] * max(magi - p["salt_phase_threshold"], 0)
    return whole(min(state_income_tax + real_estate_tax, max(cap, p["salt_floor"])))


def federal_return(i, year):
    """Compute the return's lines from inputs i (a dict; missing inputs count as zero)."""
    p = taxdata.params(year)
    g = lambda k: float(i.get(k, 0) or 0)  # noqa: E731
    lines = {}

    capital = g("capital_gain_short") + g("capital_gain_long")
    lines["1z"] = g("wages")
    lines["7a"] = max(capital, -3000)
    lines["9"] = (lines["1z"] + g("taxable_interest") + g("ordinary_dividends") + g("ira_taxable")
                  + lines["7a"] + g("other_income"))
    agi = lines["11a"] = lines["9"] - g("adjustments")

    salt = salt_deduction(g("state_income_tax"), g("real_estate_tax"), agi, year)
    charity = max(g("charity") - p["charity_floor_rate"] * agi, 0)
    itemized = salt + g("mortgage_interest") + charity
    if p["itemized_cap_applies"]:  # 2/37 of the lesser of itemized or income in the 37% bracket
        top = taxdata.MFJ_BRACKETS[year][-1]
        itemized -= 2 / 37 * min(itemized, max(agi - top, 0))
    lines["schA_5e"] = salt
    lines["12e"] = whole(max(itemized, p["standard_deduction_mfj"]))
    lines["15"] = max(agi - lines["12e"], 0)
    lines["16"] = qdcg_tax(lines["15"], g("qualified_dividends"), min(g("capital_gain_long"), capital),
                           year)

    lines["20"] = g("nonrefundable_credits")
    lines["22"] = max(lines["16"] - lines["20"], 0)

    addl_medicare = whole(p["addl_medicare_rate"]
                          * max(g("medicare_wages") - p["addl_medicare_threshold_mfj"], 0))
    nii_gross = g("taxable_interest") + g("ordinary_dividends") + max(capital, 0)
    share = round(nii_gross / agi, 4) if agi else 0.0
    nii = max(nii_gross - whole(g("state_income_tax") * share), 0)
    niit = whole(p["niit_rate"] * min(nii, max(agi - p["niit_threshold_mfj"], 0)))
    lines["s2_11"], lines["s2_12"] = addl_medicare, niit
    lines["23"] = addl_medicare + niit + g("additional_tax_5329")
    lines["24"] = lines["22"] + lines["23"]

    medicare_withheld_regular = whole(p["medicare_rate"] * g("medicare_wages"))
    lines["25c"] = max(whole(g("medicare_withheld")) - medicare_withheld_regular, 0)
    lines["25a"] = g("federal_withheld")
    ss_max = whole(p["ss_wage_base"] * p["ss_rate"])
    lines["31"] = sum(max(whole(withheld) - ss_max, 0)  # only with two or more employers
                      for withheld, employers in i.get("ss_withheld_by_person", ()) if employers > 1)
    lines["33"] = lines["25a"] + lines["25c"] + g("estimated_payments") + lines["31"]
    lines["37"] = lines["24"] - lines["33"]  # positive: owed; negative: refund
    return {k: (whole(v) if isinstance(v, float) else v) for k, v in lines.items()}


def safe_harbor(prior_year_total_tax, prior_agi):
    """Withholding that avoids the underpayment penalty, from the prior year alone."""
    return whole(prior_year_total_tax * (1.10 if prior_agi > 150_000 else 1.00))


FILED_LINES = ("11a", "12e", "15", "16", "s2_11", "s2_12", "23", "24", "25c", "31", "33", "37")


PER_PERSON = ("ss_withheld", "employers", "wi_earned_income")  # kept per person, not added up


def inputs_from_facts(facts, year, kinds=("input",)):
    """Build federal_return inputs for one year from return_facts rows."""
    rows = facts[(facts["year"].astype(str) == str(year)) & facts["kind"].isin(kinds)]
    inputs, by_person = {}, {}
    for r in rows.to_dict("records"):
        value = float(r["value"])
        if r["key"] in PER_PERSON:
            by_person.setdefault(r["person"], {})[r["key"]] = value
        else:
            inputs[r["key"]] = inputs.get(r["key"], 0.0) + value  # per-person inputs add up
    inputs["ss_withheld_by_person"] = [(p.get("ss_withheld", 0), p.get("employers", 1))
                                       for p in by_person.values() if "ss_withheld" in p]
    earned = [(person, p["wi_earned_income"]) for person, p in by_person.items() if "wi_earned_income" in p]
    if earned:
        inputs["wi_earned_income_by_person"] = earned
    return inputs


def has_wisconsin(inputs):
    return any(k.startswith("wi_") for k in inputs)


def check_against_return(facts):
    """Recompute every year that has filed lines and compare line by line."""
    out = []
    from . import pennsylvania, wisconsin  # both import this module

    for year in sorted(facts.loc[facts["kind"] == "filed", "year"].astype(int).unique()):
        inputs = inputs_from_facts(facts, year)
        computed = federal_return(inputs, year)
        if has_wisconsin(inputs):
            computed.update(wisconsin.wisconsin_return({**inputs, "federal_agi": computed["11a"]}, year))
        if pennsylvania.has_pennsylvania(inputs):
            computed.update(pennsylvania.pa_return(inputs, year))
        filed = facts[(facts["year"].astype(int) == year) & (facts["kind"] == "filed")]
        for r in filed.to_dict("records"):
            value = float(r["value"])
            got = computed.get(r["key"])
            diff = None if got is None else round(got - value, 4)
            tolerance = 0.00005 if r["key"] in wisconsin.RATIO_LINES else 1
            out.append({"year": year, "line": r["key"], "filed": value, "computed": got,
                        "diff": diff, "match": diff is not None and abs(diff) <= tolerance})
    return pd.DataFrame(out, columns=["year", "line", "filed", "computed", "diff", "match"])
