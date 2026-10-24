"""Wisconsin income tax and withholding for a married-filing-jointly household.

Tax follows Form 1 (full-year residents) and Form 1NPR (part-year residents):

    Wisconsin income   federal AGI + additions - subtractions (Form 1), or the
                       Wisconsin column of Form 1NPR, given as an input
    taxable            income - sliding-scale standard deduction - exemptions
                       (Form 1NPR taxes the larger of Wisconsin or federal income,
                       then prorates the tax by Wisconsin / federal income)
    tax                the tax table below 100,000 (tax at the middle of the table
                       row), the tax computation worksheet above
    credits            itemized deduction credit (5% of qualifying federal itemized
                       deductions above the standard deduction), school property
                       tax credit, married couple credit (3% of the lower earner's
                       Wisconsin earned income up to 16,000); the first two are
                       prorated on Form 1NPR

Withholding follows Pub W-166's alternate method: annualized wages less a sliding
deduction and 400 per exemption, taxed on the withholding schedule. Supplemental
pay paid on its own is withheld at the flat percentage for its annual gross salary
band; employers seen so far place the payment in a band by annualizing the
payment itself (amount x pay periods), which is what the backtest checks.

Not covered: the additional child and dependent care credit (zero when the
dependent care FSA covers the federal expense limit), the earned income and
homestead credits (not available at these incomes), and renter's credits.
"""

import pandas as pd

from . import taxdata, withholding
from .tax import whole

TABLE_LIMIT = 100_000
LOW_ROWS = ((0, 20), (20, 40), (40, 100))  # then the table's rows are 100 wide
WI_LINES = ("WI State Income Tax", "WI")
CENT = withholding.CENT
RATIO_LINES = ("wi1npr_32",)  # compared to four decimal places


def _schedule(amount, steps):
    tax = 0.0
    for i, (floor, rate) in enumerate(steps):
        top = steps[i + 1][0] if i + 1 < len(steps) else float("inf")
        if amount > floor:
            tax += (min(amount, top) - floor) * rate
    return tax


def bracket_tax(taxable, year):
    """Tax on taxable income under the year's married-filing-jointly rates (exact)."""
    return _schedule(taxable, taxdata.wi_brackets(year))


def table_row(taxable):
    """The tax table row (at least, less than) that holds this taxable income."""
    for low, high in LOW_ROWS:
        if taxable < high:
            return low, high
    low = int(taxable // 100 * 100)
    return low, low + 100


def tax(taxable, year):
    """Form 1 line 12 / Form 1NPR line 38, whole dollars."""
    if taxable <= 0:
        return 0
    if taxable < TABLE_LIMIT:
        low, high = table_row(taxable)
        return whole(bracket_tax((low + high) / 2, year))
    return whole(bracket_tax(taxable, year))


def standard_deduction(income, year, row=500):
    """Married filing jointly, as the Standard Deduction Table gives it: computed at the
    middle of its 500-wide income rows, the last row ending where the deduction reaches zero."""
    p = taxdata.params(year)
    full, start, rate = (p["wi_standard_deduction_mfj"], p["wi_standard_deduction_phase_start_mfj"],
                         p["wi_standard_deduction_phase_rate_mfj"])
    zero = int(start + full / rate)
    if income >= zero:
        return 0
    low = income // row * row
    middle = (low + min(low + row, zero)) / 2
    return whole(max(full - rate * max(middle - start, 0), 0))


def school_property_credit(property_tax, year, row=25):
    """Homeowner's school property tax credit, as its table gives it (rate x row middle, capped)."""
    p = taxdata.params(year)
    if property_tax <= 0:
        return 0
    middle = property_tax // row * row + row / 2
    return min(whole(p["wi_school_property_credit_rate"] * middle), p["wi_school_property_credit_max"])


def married_couple_credit(earned, year):
    """earned: each spouse's Wisconsin earned income. Both must have some."""
    p = taxdata.params(year)
    if len(earned) < 2 or min(earned) <= 0:
        return 0
    return whole(p["wi_married_couple_rate"] * min(min(earned), p["wi_married_couple_max_earned"]))


def wisconsin_return(i, year):
    """Form 1 (or Form 1NPR when i has "wi_income") lines from inputs i.

    i holds federal_agi and the wi_ inputs (missing ones count as zero); the
    itemized and property tax inputs fall back to the federal ones.
    """
    p = taxdata.params(year)
    g = lambda k, default=0.0: float(i.get(k, default) or 0)  # noqa: E731
    federal = g("federal_agi")
    part_year = "wi_income" in i
    if part_year:
        income = g("wi_income")
        base = max(income, federal)
        ratio = round(min(max(income / federal, 0), 1), 4) if income and federal else 1.0
    else:
        income = base = federal + g("wi_additions") - g("wi_subtractions")
        ratio = 1.0
    std = standard_deduction(federal if part_year else income, year)
    exemptions = p["wi_exemption"] * int(g("wi_exemptions"))
    taxable = max(base - std - exemptions, 0)
    gross = tax(taxable, year)
    prorated = whole(gross * ratio)

    itemized = (g("wi_itemized_interest", i.get("mortgage_interest", 0))
                + g("wi_itemized_charity", i.get("charity", 0)) + g("wi_itemized_medical"))
    itemized_credit = whole(whole(p["wi_itemized_credit_rate"] * max(itemized - std, 0)) * ratio)
    school = whole(school_property_credit(g("wi_property_tax", i.get("real_estate_tax", 0)), year) * ratio)
    after_credits = max(prorated - itemized_credit - school, 0)
    married = married_couple_credit([e for _, e in i.get("wi_earned_income_by_person", ())], year)
    net = max(after_credits - married, 0)
    withheld = g("wi_withheld")

    lines = {"wi_income": whole(income), "wi_ratio": ratio, "wi_standard_deduction": std,
             "wi_exemptions": exemptions, "wi_taxable": whole(taxable), "wi_tax": gross,
             "wi_itemized_credit": itemized_credit, "wi_school_property_credit": school,
             "wi_married_couple_credit": married, "wi_net_tax": net, "wi_withheld": whole(withheld),
             "wi_owe_or_refund": whole(net - withheld)}
    if part_year:
        lines.update({"wi1npr_32": ratio, "wi1npr_34c": std, "wi1npr_36c": exemptions,
                      "wi1npr_37": whole(taxable), "wi1npr_38": gross, "wi1npr_39": prorated,
                      "wi1npr_40": itemized_credit, "wi1npr_43c": school, "wi1npr_45": after_credits,
                      "wi1npr_48": married, "wi1npr_52": net, "wi1npr_58": whole(withheld),
                      "wi1npr_70": max(whole(withheld) - net, 0), "wi1npr_73": max(net - whole(withheld), 0)})
    return lines


# --- withholding -----------------------------------------------------------------------------

def regular_withholding(base, periods, year, married=True, exemptions=0, additional=0.0):
    """Pub W-166 alternate method for one paycheck."""
    p = taxdata.params(year)
    gross = base * periods
    kind = "married" if married else "single"
    full, start, rate = (p[f"wi_wh_{kind}_deduction"], p[f"wi_wh_{kind}_phase_start"],
                         p[f"wi_wh_{kind}_phase_rate"])
    deduction = max(full - rate * max(gross - start, 0), 0)
    net = max(gross - deduction - p["wi_wh_exemption"] * exemptions, 0)
    return round(_schedule(net, taxdata.wi_withholding_brackets()) / periods + additional, 2)


def flat_rate(base, periods):
    """Supplemental flat percentage for a payment, by its annualized amount's band."""
    annual = base * periods
    return [rate for floor, rate in taxdata.wi_withholding_brackets() if annual >= floor][-1]


def stub_withholding(rows):
    """Wisconsin tax withheld per stub_id."""
    wi = rows[(rows["kind"] == "tax") & rows["line_item"].isin(WI_LINES)]
    return pd.to_numeric(wi["amount"], errors="coerce").fillna(0.0).groupby(wi["stub_id"].astype(str)).sum()


def _decode(base, periods, year, withheld, prefer=()):
    """(married, exemptions, additional) reproducing withheld, or None."""
    tried = list(prefer) + [(m, e) for e in range(11) for m in (True, False) if (m, e) not in prefer]
    for married, exemptions in tried:
        if abs(regular_withholding(base, periods, year, married, exemptions) - withheld) < CENT:
            return married, exemptions, 0
    for married, exemptions in tried:
        extra = withheld - regular_withholding(base, periods, year, married, exemptions)
        if extra > CENT and abs(extra - round(extra)) < withholding.DECODE_TOLERANCE:
            return married, exemptions, int(round(extra))
    return None


def describe(base, periods, year, married, exemptions, additional):
    """WT-4 settings in words; marital status only when it changes this paycheck."""
    status = regular_withholding(base, periods, year, not married, exemptions) != \
        regular_withholding(base, periods, year, married, exemptions)
    parts = ([("married" if married else "single")] if status else []) + [f"{exemptions} exemptions"]
    if additional:
        parts.append(f"{additional:,} additional")
    return "WT-4: " + ", ".join(parts)


def backtest(paystubs):
    """Expected vs actual Wisconsin withholding for every stub that shows a Wisconsin line.

    Regular pay is decoded into WT-4 settings (marital status, exemptions, additional
    amount); expected is withholding under those settings. Supplemental pay must
    match the flat percentage of its band.
    """
    cols = ["pay_date", "person", "employer", "stub_id", "kind", "base", "withheld", "expected",
            "residual", "explanation", "settings"]
    if paystubs.empty:
        return pd.DataFrame(columns=cols)
    withheld = stub_withholding(paystubs)
    rows = paystubs[paystubs["stub_id"].astype(str).isin(set(withheld.index))]
    if rows.empty:
        return pd.DataFrame(columns=cols)
    stubs = withholding.stub_summary(rows).to_dict("records")
    out, pending = [], []
    for s in stubs:
        year, w = int(s["pay_date"][:4]), round(float(withheld.get(str(s["stub_id"]), 0.0)), 2)
        periods = withholding.periods_per_year(s["period_start"], s["period_end"])
        row = {**{k: s[k] for k in cols[:4]}, "base": s["base"], "withheld": w, "settings": None,
               "periods": periods, "year": year}
        if year not in taxdata.PARAMS:
            row.update(kind="unchecked", explanation=f"no {year} Wisconsin withholding tables")
        elif s["base"] == 0 and w == 0:
            row.update(kind="empty", expected=0.0, residual=0.0, explanation="nothing to withhold")
        else:
            rate = flat_rate(s["base"], periods)
            if abs(round(rate * s["base"], 2) - w) < CENT:
                band = f"band for {s['base'] * periods:,.0f} annualized"
                row.update(kind="supplemental", expected=round(rate * s["base"], 2), residual=0.0,
                           explanation=f"flat {rate * 100:g}% ({band})")
            else:
                row["kind"] = "regular"
                pending.append(row)
        out.append(row)
    # Settings that reproduce a paycheck with no additional amount are preferred for that person.
    prefer = {}
    for row in pending:
        d = _decode(row["base"], row["periods"], row["year"], row["withheld"])
        if d and d[2] == 0:
            prefer.setdefault(row["person"], []).append(d[:2])
    for row in pending:
        d = _decode(row["base"], row["periods"], row["year"], row["withheld"],
                    prefer=list(dict.fromkeys(prefer.get(row["person"], []))))
        if d is None:  # compare with the person's usual settings, else married with none
            married, exemptions = (prefer.get(row["person"]) or [(True, 0)])[-1]
            expected = regular_withholding(row["base"], row["periods"], row["year"], married, exemptions)
            row.update(expected=expected, residual=round(row["withheld"] - expected, 2),
                       explanation=f"unexplained {row['withheld'] - expected:+.2f} against "
                                   + describe(row["base"], row["periods"], row["year"], married,
                                              exemptions, 0))
            continue
        expected = regular_withholding(row["base"], row["periods"], row["year"], *d)
        row.update(expected=expected, residual=round(row["withheld"] - expected, 2), settings=d,
                   explanation=describe(row["base"], row["periods"], row["year"], *d))
    return pd.DataFrame(out, columns=cols + ["periods", "year"])[cols]
