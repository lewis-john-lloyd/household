"""This year's federal and Wisconsin tax, projected from pay stubs, equity sales and estimates.

Year to date comes from the latest stub of each employer. The rest of the year
is projected from what the stubs show is recurring:

  - regular paychecks every 14 days after the last one, through December 31 (a
    paycheck due January 1 is paid December 31). Paychecks beyond the 26th carry
    no benefit deductions, so their taxable wages are the full gross;
  - withholding on each by the Pub 15-T method plus the W-4 adjustment the latest
    stub shows (see withholding.py), unless a scenario replaces it;
  - recurring supplemental pay (monthly RSU vests: supplemental days in the last
    90 days worth over 1,000) at its recent average, scaled by the scenario's
    stock price change, withheld at the latest supplemental rate;
  - Wisconsin withholding on each regular paycheck under the WT-4 settings the
    Wisconsin backtest decoded from the latest one, and on each vest at its flat
    percentage (wisconsin.py).

Wisconsin tax uses Form 1 with the year's Wisconsin estimates (subtractions,
exemptions); itemized deduction and property tax inputs fall back to the federal
estimates.

Each scenario also reports the extra withholding per remaining paycheck (W-4
Step 4(c)) that would bring the federal balance to zero, on the paychecks of the
person and employer with the most paychecks left.

Elections (401(k), HSA, limited-purpose or health FSA, dependent-care FSA) are projected the same way and
compared with the year's limits, with what the stubs show so far: the after-tax
401(k) room under 415(c) is the mega backdoor Roth's.

return_lines lays both returns out line by line (Form 1040 and Wisconsin Form 1),
so the tax page shows the whole return, and a filed return can be set beside it.

withholding_plan says what withholding lands the year near a target refund:
future vests withheld at a planned supplemental rate, and the extra per paycheck
(W-4 Step 4(c), and WT-4's additional amount for Wisconsin) on the paychecks of
the person and employer with the most left.
"""

from datetime import date, timedelta

import pandas as pd

from . import tax, taxdata, wisconsin, withholding

PAY_INTERVAL = timedelta(days=14)
FULL_YEAR_PAYCHECKS = 26


def _num(series):
    return pd.to_numeric(series, errors="coerce").fillna(0.0)


def remaining_pay_dates(last, year):
    out, d = [], date.fromisoformat(last) + PAY_INTERVAL
    while d.year == year or (d == date(year + 1, 1, 1)):
        out.append(min(d, date(year, 12, 31)))
        d += PAY_INTERVAL
    return out


def _select(rows, kind, names=None, contains=None):
    sel = rows[rows["kind"] == kind]
    if names is not None:
        sel = sel[sel["line_item"].isin(names)]
    if contains is not None and len(sel):
        sel = sel[sel["line_item"].map(contains).astype(bool)]
    return sel


def _ytd(rows, kind, names=None, contains=None):
    """Largest year-to-date figure per matching line item, summed (YTD only grows)."""
    sel = _select(rows, kind, names, contains)
    return float(_num(sel["ytd_amount"]).groupby(sel["line_item"]).max().sum()) if len(sel) else 0.0


def _current(rows, kind, names=None, contains=None):
    sel = _select(rows, kind, names, contains)
    return float(_num(sel["amount"]).sum()) if len(sel) else 0.0


def _is(*words, unless=()):
    def match(name):
        n = name.lower()
        return any(w in n for w in words) and not any(u in n for u in unless)
    return match


ELECTIVE = _is("401k", unless=("after", " at", "match"))
AFTER_TAX = _is("after-tax", "401k at")
HSA_EMPLOYEE = _is("hsa employee")
MEDICARE = _is("medicare", "med")
HSA_EMPLOYER = _is("hsa er update")
DEPENDENT_CARE = _is("fsa dependent", "dependent care")
HEALTH_FSA = _is("purpose fsa", "limited purpose", "lpfsa", "health fsa", "healthcare fsa", "medical fsa")


def _medicare_rate(wages_so_far, year):
    p = taxdata.params(year)
    over = wages_so_far >= p["addl_medicare_withholding_threshold"]
    return p["medicare_rate"] + (p["addl_medicare_rate"] if over else 0.0)


def medicare_wages(withheld, year):
    """Medicare wages implied by Medicare tax withheld (employers add 0.9% above 200,000)."""
    p = taxdata.params(year)
    threshold = p["addl_medicare_withholding_threshold"]
    if withheld <= threshold * p["medicare_rate"]:
        return withheld / p["medicare_rate"]
    return (withheld + threshold * p["addl_medicare_rate"]) / (p["medicare_rate"] + p["addl_medicare_rate"])


def _add_medicare(out, wages, year):
    p = taxdata.params(year)
    threshold = p["addl_medicare_withholding_threshold"]
    below = max(min(wages, threshold - out["medicare_wages"]), 0)
    out["medicare_withheld"] += wages * p["medicare_rate"] + (wages - below) * p["addl_medicare_rate"]
    out["medicare_wages"] += wages


def employer_position(rows, backtest, year, overrides, wi_backtest=None):
    """Year to date and the rest of the year for one person at one employer."""
    rows = rows[rows["pay_date"].astype(str).str[:4] == str(year)]
    bt = backtest[backtest["pay_date"].str[:4] == str(year)]
    wi_bt = (wi_backtest if wi_backtest is not None else wisconsin.backtest(rows))
    wi_bt = wi_bt[wi_bt["pay_date"].str[:4] == str(year)]
    wi_regular = wi_bt[(wi_bt["kind"] == "regular") & wi_bt["settings"].notna()].sort_values("pay_date")
    wi_settings = wi_regular.iloc[-1]["settings"] if len(wi_regular) else None
    regular = bt[bt["kind"] == "regular"].sort_values("pay_date")
    # Taxable wages to date = gross to date less pre-tax deductions to date (not every stub
    # prints a year-to-date taxable figure).
    out = {"wages": _ytd(rows, "memo", ["Gross"]) - _ytd(rows, "pre_tax"),
           "federal_withheld": _ytd(rows, "tax", list(withholding.FIT_LINES)),
           "medicare_withheld": _ytd(rows, "tax", contains=MEDICARE),
           "state_withheld": _ytd(rows, "tax", contains=_is("state income", "wi")),
           "wi_withheld": _ytd(rows, "tax", list(wisconsin.WI_LINES)),
           "elective": _ytd(rows, "pre_tax", contains=ELECTIVE),
           "after_tax": _ytd(rows, "post_tax", contains=AFTER_TAX),
           "employer_401k": _ytd(rows, "employer", contains=_is("401k")),
           "hsa": _ytd(rows, "pre_tax", contains=HSA_EMPLOYEE) + _ytd(rows, "pre_tax", contains=HSA_EMPLOYER),
           "dependent_care": _ytd(rows, "pre_tax", contains=DEPENDENT_CARE),
           "health_fsa": _ytd(rows, "pre_tax", contains=HEALTH_FSA),
           "paychecks_left": 0, "vests_left": 0, "extra_withholding": 0.0, "notes": []}
    out["ytd"] = {k: out[k] for k in ("elective", "after_tax", "employer_401k", "hsa", "dependent_care", "health_fsa")}
    out["wi_settings"] = wi_settings
    out["medicare_wages"] = medicare_wages(out["medicare_withheld"], year)
    if regular.empty:
        return out

    last = regular.iloc[-1]
    stub = rows[rows["stub_id"].astype(str) == str(last["stub_id"])]
    p = taxdata.params(year)
    gross = _current(stub, "memo", ["Gross"])
    taxable = _current(stub, "memo", list(withholding.TAXABLE_MEMOS))
    gtl = _current(stub, "income", contains=withholding._is_group_term_life)
    adjustment = overrides.get("step4c", last["residual"])
    out["extra_withholding"] = float(adjustment)
    medicare_base = _current(stub, "tax", contains=MEDICARE) / _medicare_rate(out["medicare_wages"], year)
    dates = remaining_pay_dates(last["pay_date"], year)
    done = len(regular["pay_date"].unique())
    for n, _ in enumerate(dates, start=done + 1):
        full = n > FULL_YEAR_PAYCHECKS
        wages = gross if full else taxable
        out["wages"] += wages
        out["federal_withheld"] += withholding.regular_withholding(wages - gtl, 26, year) + adjustment
        _add_medicare(out, gross if full else medicare_base, year)
        if not full:
            out["elective"] += _current(stub, "pre_tax", contains=ELECTIVE)
            out["after_tax"] += _current(stub, "post_tax", contains=AFTER_TAX)
            out["employer_401k"] += _current(stub, "employer", contains=_is("401k"))
            out["hsa"] += _current(stub, "pre_tax", contains=HSA_EMPLOYEE)
            out["dependent_care"] += _current(stub, "pre_tax", contains=DEPENDENT_CARE)
            out["health_fsa"] += _current(stub, "pre_tax", contains=HEALTH_FSA)
        state = _current(stub, "tax", contains=_is("state income", "wi"))
        if wi_settings is not None:
            wi = wisconsin.regular_withholding(wages - gtl, 26, year, *wi_settings)
            out["wi_withheld"] += wi
            state += wi - _current(stub, "tax", list(wisconsin.WI_LINES))
        out["state_withheld"] += state
    out["paychecks_left"] = len(dates)
    if len(dates) + done > FULL_YEAR_PAYCHECKS:
        out["notes"].append(f"{len(dates) + done} paychecks this year; the 27th carries no deductions")

    supplemental = bt[(bt["kind"] == "supplemental")].copy()
    recent = supplemental[pd.to_datetime(supplemental["pay_date"])
                          > pd.Timestamp(last["pay_date"]) - pd.Timedelta(days=90)]
    by_day = recent.groupby("pay_date")["base"].sum()
    vest_days = by_day[by_day > 1_000]
    if len(vest_days) >= 2:
        months_left = 12 - int(vest_days.index.max()[5:7])
        amount = float(vest_days.mean()) * overrides.get("stock_price", 1.0)
        rate = overrides.get("supplemental_rate",
                             float((recent["withheld"].sum() / recent["base"].sum()).round(2)))
        out["wages"] += amount * months_left
        out["federal_withheld"] += amount * months_left * rate
        if wi_settings is not None:
            wi = amount * months_left * wisconsin.flat_rate(amount, 26)
            out["wi_withheld"] += wi
            out["state_withheld"] += wi
        for _ in range(months_left):
            _add_medicare(out, amount, year)
        out["vests_left"] = months_left
        out["vest_rate"] = rate
        out["notes"].append(f"{months_left} more monthly vests of about {amount:,.0f} at {rate:.0%}")
    return out


SCENARIOS = {
    "as is": {},
    "no extra withholding": {"step4c": 0.0},
    "no extra, 22% on vests": {"step4c": 0.0, "supplemental_rate": 0.22},
    "stock 20% lower": {"stock_price": 0.8},
    "stock 20% higher": {"stock_price": 1.2},
}


def break_even_extra(pos, owe):
    """Per-paycheck extra withholding that brings the federal balance to zero, or None.

    The change goes on the paychecks of the person and employer with the most left;
    it cannot go below zero.
    """
    left = [v for v in pos.values() if v["paychecks_left"]]
    if not left:
        return None
    v = max(left, key=lambda p: p["paychecks_left"])
    return round(max(v["extra_withholding"] + owe / v["paychecks_left"], 0.0), 2)


def positions(paystubs, year, overrides=None):
    """employer_position for every person and employer with stubs this year."""
    backtest = withholding.backtest(paystubs)
    wi_backtest = wisconsin.backtest(paystubs)
    year_rows = paystubs[paystubs["pay_date"].astype(str).str[:4] == str(year)]
    out = {}
    for (person, employer), rows in year_rows.groupby(["person", "employer"]):
        mine = lambda b: b[(b["person"] == person) & (b["employer"] == employer)]  # noqa: E731
        out[(person, employer)] = employer_position(rows, mine(backtest), year, overrides or {},
                                                    mine(wi_backtest))
    return out


def inputs_for(year, paystubs, sales, facts, overrides=None):
    """federal_return inputs for the year: stubs plus projection, equity sales, estimates."""
    pos = positions(paystubs, year, overrides)
    sales = sales[sales["sale_date"].astype(str).str[:4] == str(year)] if not sales.empty else sales
    gain = _num(sales["gain"]) if not sales.empty else pd.Series(dtype=float)
    term = sales["term"] if not sales.empty else pd.Series(dtype=str)
    i = tax.inputs_from_facts(facts, year, kinds=("estimate",)) if not facts.empty else {}
    i.pop("ss_withheld_by_person", None)
    earned = {}
    for (person, _), p in pos.items():
        earned[person] = earned.get(person, 0.0) + p["wages"]
    i["wi_earned_income_by_person"] = sorted(earned.items())
    i["wi_withheld"] = sum(p["wi_withheld"] for p in pos.values())
    i.update({
        "wages": sum(p["wages"] for p in pos.values()),
        "federal_withheld": sum(p["federal_withheld"] for p in pos.values()),
        "medicare_wages": sum(p["medicare_wages"] for p in pos.values()),
        "medicare_withheld": sum(p["medicare_withheld"] for p in pos.values()),
        "capital_gain_short": float(gain[term != "long_term"].sum()),
        "capital_gain_long": float(gain[term == "long_term"].sum()),
    })
    i["state_income_tax"] = i.get("state_income_tax", 0) or sum(p["state_withheld"] for p in pos.values())
    return i, pos


def project(year, paystubs, sales, facts):
    """Projected lines per scenario, plus the safe-harbor target from last year's return.

    Returns (summary, notes, detail): one summary row per scenario, the notes, and every line of both
    returns per scenario (return_lines), each beside the filed value once the year's return is in
    return_facts.
    """
    rows, detail = [], []
    filed = facts[(facts["year"].astype(str) == str(year)) & (facts["kind"] == "filed")] if not facts.empty else facts
    filed = dict(zip(filed["key"], _num(filed["value"]))) if len(filed) else {}
    prior = facts[(facts["year"].astype(str) == str(year - 1)) & (facts["kind"] == "filed")] \
        if not facts.empty else facts
    prior_lines = dict(zip(prior["key"], _num(prior["value"]))) if len(prior) else {}
    harbor = (tax.safe_harbor(prior_lines["24"], prior_lines.get("11a", 0))
              if "24" in prior_lines else None)
    notes = []
    for name, overrides in SCENARIOS.items():
        i, pos = inputs_for(year, paystubs, sales, facts, overrides)
        r = tax.federal_return(i, year)
        w = wisconsin.wisconsin_return({**i, "federal_agi": r["11a"]}, year)
        if name == "as is":
            notes = [f"{p}/{e}: {n}" for (p, e), v in pos.items() for n in v["notes"]]
            notes += _wisconsin_notes(year)
        for order, row in enumerate(return_lines(i, r, w, year)):
            detail.append({"scenario": name, "order": order, **row,
                           "filed": filed.get(row["key"]) if name == "as is" else None})
        rows.append({"scenario": name, "agi": r["11a"], "deduction": r["12e"], "taxable": r["15"],
                     "total_tax": r["24"], "withheld": r["33"], "owe_or_refund": r["37"],
                     "safe_harbor": harbor,
                     "safe_harbor_met": None if harbor is None else r["25a"] + r["25c"] >= harbor,
                     "extra_to_break_even": break_even_extra(pos, r["37"]),
                     "wi_taxable": w["wi_taxable"], "wi_tax": w["wi_net_tax"],
                     "wi_withheld": w["wi_withheld"], "wi_owe_or_refund": w["wi_owe_or_refund"]})
    return pd.DataFrame(rows), notes, pd.DataFrame(detail, columns=DETAIL_COLUMNS)


FEDERAL = "Form 1040"
WISCONSIN = "Wisconsin Form 1"
DETAIL_COLUMNS = ["scenario", "order", "form", "line", "key", "label", "value", "filed"]


def return_lines(i, r, w, year):
    """Both returns line by line, as a person reads them: form, line number, key (the return_facts key a
    filed value is stored under), label and value. Inputs a return only shows when they are not zero
    (interest, dividends, IRA distributions) are left out when they are."""
    g = lambda k: float(i.get(k, 0) or 0)  # noqa: E731
    itemized = r["12e"] > taxdata.params(year)["standard_deduction_mfj"]
    out = []

    def add(form, line, key, label, value, always=True):
        if always or value:
            out.append({"form": form, "line": line, "key": key, "label": label, "value": round(float(value), 2)})

    add(FEDERAL, "1z", "1z", "Wages (W-2 box 1)", r["1z"])
    add(FEDERAL, "2b", "taxable_interest", "Taxable interest", g("taxable_interest"), always=False)
    add(FEDERAL, "3a", "qualified_dividends", "Qualified dividends", g("qualified_dividends"), always=False)
    add(FEDERAL, "3b", "ordinary_dividends", "Ordinary dividends", g("ordinary_dividends"), always=False)
    add(FEDERAL, "4b", "ira_taxable", "IRA distributions, taxable", g("ira_taxable"), always=False)
    add(FEDERAL, "7", "7a", "Capital gain or loss", r["7a"])
    add(FEDERAL, "Sch D 7", "capital_gain_short", "of which short-term", g("capital_gain_short"), always=False)
    add(FEDERAL, "Sch D 15", "capital_gain_long", "of which long-term", g("capital_gain_long"), always=False)
    add(FEDERAL, "8", "other_income", "Other income", g("other_income"), always=False)
    add(FEDERAL, "9", "9", "Total income", r["9"])
    add(FEDERAL, "10", "adjustments", "Adjustments to income", g("adjustments"), always=False)
    add(FEDERAL, "11", "11a", "Adjusted gross income", r["11a"])
    add(FEDERAL, "12", "12e", "Itemized deductions" if itemized else "Standard deduction", r["12e"])
    if itemized:
        add(FEDERAL, "Sch A 5e", "schA_5e", "state and local taxes, capped", r["schA_5e"])
        add(FEDERAL, "Sch A 8a", "mortgage_interest", "mortgage interest", g("mortgage_interest"), always=False)
        add(FEDERAL, "Sch A 11", "charity", "gifts to charity", g("charity"), always=False)
    add(FEDERAL, "15", "15", "Taxable income", r["15"])
    add(FEDERAL, "16", "16", "Tax", r["16"])
    add(FEDERAL, "20", "20", "Credits", r["20"], always=False)
    add(FEDERAL, "22", "22", "Tax after credits", r["22"])
    add(FEDERAL, "Sch 2 11", "s2_11", "Additional Medicare Tax", r["s2_11"])
    add(FEDERAL, "Sch 2 12", "s2_12", "Net investment income tax", r["s2_12"])
    add(FEDERAL, "23", "23", "Other taxes", r["23"])
    add(FEDERAL, "24", "24", "Total tax", r["24"])
    add(FEDERAL, "25a", "25a", "Withheld, W-2s", r["25a"])
    add(FEDERAL, "25c", "25c", "Withheld, Additional Medicare Tax", r["25c"])
    add(FEDERAL, "26", "estimated_payments", "Estimated payments", g("estimated_payments"), always=False)
    add(FEDERAL, "31", "31", "Excess Social Security withheld", r["31"], always=False)
    add(FEDERAL, "33", "33", "Total payments", r["33"])
    add(FEDERAL, "34 or 37", "37", "Refund" if r["37"] < 0 else "Amount owed", abs(r["37"]))
    for key, label in (("wi_income", "Wisconsin income"), ("wi_standard_deduction", "Standard deduction"),
                       ("wi_exemptions", "Exemptions"), ("wi_taxable", "Taxable income"), ("wi_tax", "Tax"),
                       ("wi_itemized_credit", "Itemized deduction credit"),
                       ("wi_school_property_credit", "School property tax credit"),
                       ("wi_married_couple_credit", "Married couple credit"), ("wi_net_tax", "Net tax"),
                       ("wi_withheld", "Wisconsin tax withheld")):
        add(WISCONSIN, taxdata.form_line(year, key)[0], key, label, w.get(key, 0))
    refund = w["wi_owe_or_refund"] < 0
    add(WISCONSIN, taxdata.form_line(year, "wi_overpaid" if refund else "wi_owe")[0], "wi_owe_or_refund",
        "Refund" if refund else "Amount owed", abs(w["wi_owe_or_refund"]))
    return out


PLAN_COLUMNS = ["return", "person", "employer", "paychecks_left", "vest_rate", "projected", "target", "current_extra",
                "suggested_extra", "lands_at", "note"]


def withholding_plan(year, paystubs, sales, facts, target=2000.0, wi_target=0.0, supplemental_rate=0.35):
    """What lands the year near target: projected (refund positive, amount owed negative) with future vests
    withheld at supplemental_rate and today's W-4 and WT-4, and the extra per paycheck that would bring
    the refund to target (federal) and wi_target (Wisconsin)."""
    i, pos = inputs_for(year, paystubs, sales, facts, {"supplemental_rate": supplemental_rate})
    r = tax.federal_return(i, year)
    w = wisconsin.wisconsin_return({**i, "federal_agi": r["11a"]}, year)
    left = [(k, v) for k, v in pos.items() if v["paychecks_left"]]
    vest = next((v.get("vest_rate") for v in pos.values() if v.get("vest_rate") is not None), None)
    if not left:
        return pd.DataFrame([{"return": "federal", "projected": -r["37"], "target": target, "vest_rate": vest,
                              "note": "no paychecks left this year to change"}], columns=PLAN_COLUMNS)
    (person, employer), v = max(left, key=lambda kv: kv[1]["paychecks_left"])
    n = v["paychecks_left"]
    fed_now = v["extra_withholding"]
    wi_now = float(v["wi_settings"][2]) if v.get("wi_settings") else 0.0
    rows = [
        {"return": "federal", "person": person, "employer": employer, "paychecks_left": n, "vest_rate": vest,
         "projected": -r["37"], "target": target, "current_extra": round(fed_now, 2),
         "suggested_extra": round(max(fed_now + (r["37"] + target) / n, 0.0), 2),
         "note": "W-4 Step 4(c): extra withholding per paycheck"},
        {"return": "wisconsin", "person": person, "employer": employer, "paychecks_left": n, "vest_rate": vest,
         "projected": -w["wi_owe_or_refund"], "target": wi_target, "current_extra": round(wi_now, 2),
         "suggested_extra": round(max(wi_now + (w["wi_owe_or_refund"] + wi_target) / n, 0.0), 2),
         "note": "WT-4: additional amount per paycheck" if v.get("wi_settings") else
                 "no Wisconsin settings decoded from the stubs; additional amount assumed zero"},
    ]
    for row in rows:  # withholding can't go below none: then the year lands short of the target
        row["lands_at"] = round(row["projected"] + (row["suggested_extra"] - row["current_extra"]) * n, 2)
        if abs(row["lands_at"] - row["target"]) > 1:
            row["note"] += f"; with no extra at all the refund is still {row['lands_at']:,.0f}"
    return pd.DataFrame(rows, columns=PLAN_COLUMNS)


def _wisconsin_notes(year):
    stale = sorted(key[1] for key in taxdata.UNCITED
                   if isinstance(key, tuple) and key[0] == year and key[1].startswith("wi_"))
    out = [f"Wisconsin {year}: {len(stale)} amounts are the prior year's until {year}'s are published"] if stale else []
    _, form_year = taxdata.form_line(year, "wi_net_tax")
    if form_year is not None and form_year != year:
        out.append(f"Wisconsin {year}: Form 1 line numbers are the {form_year} form's until the {year} form is on file")
    return out


ELECTION_COLUMNS = ["year", "person", "employer", "plan", "ytd", "projected", "limit", "gap", "status"]
MEGA_BACKDOOR = "Mega backdoor Roth (after-tax 401(k))"
HEALTH_FSA_PLAN = "Limited-purpose FSA"
BACKDOOR_ROTH = "Backdoor Roth IRA"


def elections(year, paystubs, after_tax_converts=False):
    """401(k), HSA and dependent-care FSA so far and projected to year end against the year's limits.

    The mega backdoor Roth row is the after-tax 401(k): its limit is what 415(c) leaves after elective
    deferrals and the employer's contributions, projected. Its status says whether the plan converts
    after-tax money to Roth on its own (after_tax_converts, the household setting
    after_tax_401k_converts_to_roth), since the stubs can't show a conversion. A backdoor Roth IRA is not
    on a pay stub, so each person gets a row saying it is not tracked yet.
    """
    p = taxdata.params(year)
    pos = positions(paystubs, year)
    out, people = [], []
    for (person, employer), v in pos.items():
        people.append(person)
        y = v["ytd"]
        if v["elective"]:
            out.append(_election(year, person, employer, "401(k) elective deferral", y["elective"], v["elective"],
                                 p["elective_deferral_402g"]))
        if v["health_fsa"]:  # the limit is per employee, per employer's plan
            out.append(_election(year, person, employer, HEALTH_FSA_PLAN, y["health_fsa"], v["health_fsa"],
                                 p["health_fsa"]))
        if v["after_tax"]:
            room = p["annual_additions_415c"] - v["elective"] - v["employer_401k"]
            mega = _election(year, person, employer, MEGA_BACKDOOR, y["after_tax"], v["after_tax"], room)
            mega["status"] += ("; the plan converts it to Roth automatically" if after_tax_converts
                               else "; conversion to Roth not tracked (set after_tax_401k_converts_to_roth if the plan does it)")
            out.append(mega)
            out.append(_election(year, person, employer, "401(k) total additions (415(c))",
                                 y["elective"] + y["after_tax"] + y["employer_401k"],
                                 v["elective"] + v["after_tax"] + v["employer_401k"], p["annual_additions_415c"]))
    household = {"HSA, family (employee + employer)": ("hsa", p["hsa_family"]),
                 "dependent-care FSA": ("dependent_care", p["dependent_care_fsa_mfj"])}
    for plan, (key, limit) in household.items():
        projected = sum(v[key] for v in pos.values())
        if projected:
            out.append(_election(year, "household", "", plan, sum(v["ytd"][key] for v in pos.values()), projected, limit))
    for person in sorted(set(people)):
        out.append({"year": year, "person": person, "employer": "", "plan": BACKDOOR_ROTH, "ytd": None,
                    "projected": None, "limit": None, "gap": None,
                    "status": "not tracked: no IRA contributions or conversions in the ledger"})
    return pd.DataFrame(out, columns=ELECTION_COLUMNS)


def _election(year, person, employer, plan, ytd, projected, limit):
    gap = round(limit - projected, 2)
    status = "at limit" if abs(gap) < 1 else ("room left" if gap > 0 else "over limit")
    return {"year": year, "person": person, "employer": employer, "plan": plan, "ytd": round(ytd, 2),
            "projected": round(projected, 2), "limit": limit, "gap": gap, "status": status}
