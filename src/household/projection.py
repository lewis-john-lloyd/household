"""This year's federal and Wisconsin tax, projected from pay stubs, equity sales and estimates.

Year to date comes from the latest stub of each employer. The rest of the year
is projected from what the stubs show is recurring:

  - regular paychecks every 14 days after the last one, through December 31 (a
    paycheck due January 1 is paid December 31). Paychecks beyond the 26th carry
    no benefit deductions, so their taxable wages are the full gross;
  - withholding on each by the Pub 15-T method plus the W-4 adjustment the latest
    stub shows (see withholding.py), unless a scenario replaces it;
  - recurring supplemental pay (monthly RSU vests: supplemental days in the last
    90 days worth over 1,000): each vest left in the grants' vesting schedule
    (vest_schedule) at the latest share price, or without a schedule one a month at
    its recent average, scaled by the scenario's stock price change, withheld at
    the latest supplemental rate;
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
so the tax page shows the whole return. A year whose return is filed is laid out
by as_filed instead, from the return's own inputs, beside the filed lines.

A job is projected to December 31 until comp_events records the person's next job
(job_ends), and nothing is projected in a year that is over.

withholding_plan says what withholding lands the year on a refund of the target,
on no balance, and on owing the target: future vests withheld at a planned
supplemental rate, and the extra per paycheck (W-4 Step 4(c), and WT-4's
additional amount for Wisconsin) on the paychecks of the person and employer with
the most left.
"""

import re
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


def employer_position(rows, backtest, year, overrides, wi_backtest=None, ended=False, ends=None):
    """Year to date and the rest of the year for one person at one employer. ended: the employer pays no
    more this year (the year is over), so the year is its year to date; ends: the date the job ends
    (job_ends), so no paycheck or vest on or after it is projected."""
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
    if regular.empty or ended:
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
    dates = [d for d in remaining_pay_dates(last["pay_date"], year) if ends is None or d < ends]
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
    scheduled = overrides.get("vests") or {}
    if len(vest_days) >= 1 and scheduled.get("schedule") and scheduled.get("price"):
        # the grants' vesting schedule: each vest after the latest paid, this year, before the job ends
        latest_vest = date.fromisoformat(vest_days.index.max()[:10])
        price = float(scheduled["price"]) * overrides.get("stock_price", 1.0)
        by_date = {}
        for day, shares in scheduled["schedule"]:
            d = date.fromisoformat(str(day)[:10])
            if d > latest_vest and d.year == year and (ends is None or d < ends):
                by_date[d] = by_date.get(d, 0.0) + float(shares)
        rate = overrides.get("supplemental_rate",
                             float((recent["withheld"].sum() / recent["base"].sum()).round(2)))
        for d, shares in sorted(by_date.items()):
            amount = shares * price
            out["wages"] += amount
            out["federal_withheld"] += amount * rate
            if wi_settings is not None:
                wi = amount * wisconsin.flat_rate(amount, 26)
                out["wi_withheld"] += wi
                out["state_withheld"] += wi
            _add_medicare(out, amount, year)
        out["vests_left"] = len(by_date)
        out["vest_rate"] = rate
        out["notes"].append(f"{len(by_date)} more scheduled vests, {sum(by_date.values()):,.0f} shares at about "
                            f"{price:,.2f}, withheld at {rate:.0%}")
    elif len(vest_days) >= 2:
        latest_vest = date.fromisoformat(vest_days.index.max()[:10])
        # a vest on the same day of each month after the latest, before the job ends
        months_left = sum(1 for m in range(latest_vest.month + 1, 13)
                          if ends is None or date(year, m, min(latest_vest.day, 28)) < ends)
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


def _same_employer(a, b):
    """Whether two names are one employer: one's first word is a word of the other ("Acme" and "ACME
    WIDGETS, LLC")."""
    words = lambda name: re.sub(r"[^a-z0-9 ]", " ", str(name).lower()).split()  # noqa: E731
    wa, wb = words(a), words(b)
    return bool(wa and wb) and (wa[0] in wb or wb[0] in wa)


def job_ends(comp_events, person, employer, first, last):
    """The date a person's job at an employer ends, from comp_events, or None while it goes on.

    A job goes on until the person's next job change: its stint starts at the latest comp_events row for
    this employer on or before its last stub (or at its first stub this year, when no row names it), and
    ends at the first row after that start for another employer. A raise at the same employer ends nothing.
    """
    if comp_events is None or comp_events.empty:
        return None
    mine = comp_events[comp_events["person"].astype(str) == str(person)].copy()
    mine["effective"] = mine["effective"].astype(str).str[:10]
    mine = mine[mine["effective"].str.match(r"\d{4}-\d{2}-\d{2}$")].sort_values("effective")
    here = mine["employer"].map(lambda e: _same_employer(e, employer))
    starts = mine[here & (mine["effective"] <= last.isoformat())]["effective"]
    start = starts.iloc[-1] if len(starts) else first.isoformat()
    after = mine[~here & (mine["effective"] > start)]
    return date.fromisoformat(after["effective"].iloc[0]) if len(after) else None


def positions(paystubs, year, overrides=None, comp_events=None):
    """employer_position for every person and employer with stubs this year.

    A job is projected to December 31 until comp_events says the person changed jobs (job_ends), and
    none in a year before the latest stub's (that year is over).
    """
    backtest = withholding.backtest(paystubs)
    wi_backtest = wisconsin.backtest(paystubs)
    latest = date.fromisoformat(str(paystubs["pay_date"].astype(str).max())[:10])
    year_rows = paystubs[paystubs["pay_date"].astype(str).str[:4] == str(year)]
    out = {}
    for (person, employer), rows in year_rows.groupby(["person", "employer"]):
        mine = lambda b: b[(b["person"] == person) & (b["employer"] == employer)]  # noqa: E731
        paid = rows["pay_date"].astype(str).str[:10]
        first, last = date.fromisoformat(paid.min()), date.fromisoformat(paid.max())
        ends = job_ends(comp_events, person, employer, first, last)
        out[(person, employer)] = employer_position(rows, mine(backtest), year, overrides or {},
                                                    mine(wi_backtest), ended=year < latest.year, ends=ends)
        if ends is not None and year == latest.year:
            out[(person, employer)]["notes"].append(f"ends {ends.isoformat()} (a new job in comp_events): "
                                                    "nothing projected from then")
    return out


def scheduled_vests(schedule, vested=None, prices=None):
    """The projection's vests: each grant's schedule in force (parsers/schwab_awards.in_force) and a share
    price, the latest of a vest's fair market value (equity_vests) and a close for the grants' symbol
    (prices); None with no schedule or no price."""
    from .parsers import schwab_awards
    s = schwab_awards.in_force(schedule)
    if s.empty:
        return None
    seen = []
    if vested is not None and len(vested):
        v = vested.assign(fmv=_num(vested["fmv"]))
        v = v[v["fmv"] > 0]
        seen += list(zip(v["vest_date"].astype(str), v["fmv"]))
    if prices is not None and len(prices):
        p = prices[prices["symbol"].isin(set(s["symbol"]))].assign(close=lambda d: pd.to_numeric(d["close"], errors="coerce"))
        seen += list(zip(p["date"].astype(str), p["close"].dropna()))
    if not seen:
        return None
    return {"schedule": list(zip(s["vest_date"].astype(str), _num(s["shares"]))), "price": float(max(seen)[1])}


def inputs_for(year, paystubs, sales, facts, overrides=None, comp_events=None, vests=None):
    """federal_return inputs for the year: stubs plus projection, equity sales, estimates. vests: the grants'
    schedule and the share price ({"schedule": [(vest_date, shares)], "price": p}), for the vests left."""
    overrides = {**(overrides or {}), **({"vests": vests} if vests else {})}
    pos = positions(paystubs, year, overrides, comp_events)
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


def project(year, paystubs, sales, facts, comp_events=None, vests=None):
    """Projected lines per scenario, plus the safe-harbor target from last year's return.

    Returns (summary, notes, detail): one summary row per scenario, the notes, and every line of both
    returns as things stand (return_lines), each beside the filed value once the year's return is in
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
        i, pos = inputs_for(year, paystubs, sales, facts, overrides, comp_events, vests)
        r = tax.federal_return(i, year)
        w = wisconsin.wisconsin_return({**i, "federal_agi": r["11a"]}, year)
        if name == "as is":
            notes = [f"{p}/{e}: {n}" for (p, e), v in pos.items() for n in v["notes"]]
            notes += _wisconsin_notes(year)
        if name == "as is":  # the forms show the year as it stands; scenarios differ only in their totals
            for order, row in enumerate(return_lines(i, r, w, year)):
                detail.append({"scenario": name, "order": order, **row, "filed": filed.get(row["key"])})
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


def _adder(out):
    def add(form, line, key, label, value, always=True):
        if always or value:
            out.append({"form": form, "line": line, "key": key, "label": label, "value": round(float(value), 4 if key in RATIO_KEYS else 2)})
    return add


def federal_lines(i, r, year):
    """Form 1040 line by line, as a person reads it: form, line number, key (the return_facts key a filed
    value is stored under), label and value. Inputs a return only shows when they are not zero (interest,
    dividends, IRA distributions) are left out when they are."""
    g = lambda k: float(i.get(k, 0) or 0)  # noqa: E731
    itemized = r["12e"] > taxdata.params(year)["standard_deduction_mfj"]
    out = []
    add = _adder(out)
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
    return out


def wisconsin_lines(w, year):
    """Wisconsin Form 1 (a full-year resident's) line by line."""
    out = []
    add = _adder(out)
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


# Form 1NPR (a part-year resident's) and PA-40: the keys are the line numbers, as in return_facts
WISCONSIN_NPR = "Wisconsin Form 1NPR"
NPR_LINES = (("32", "Wisconsin share of income"), ("34c", "Standard deduction"), ("36c", "Exemptions"),
             ("37", "Taxable income"), ("38", "Tax on all income"), ("39", "Tax on the Wisconsin share"),
             ("40", "Itemized deduction credit"), ("43c", "School property tax credit"),
             ("45", "Tax after those credits"), ("48", "Married couple credit"), ("52", "Net tax"),
             ("58", "Wisconsin tax withheld"))
PENNSYLVANIA = "Pennsylvania PA-40"
PA_LINES = (("9", "Total PA taxable income"), ("11", "Adjusted PA taxable income"), ("12", "Tax"),
            ("13", "PA tax withheld"), ("22", "Resident credit"), ("24", "Total payments and credits"))
RATIO_KEYS = ("wi1npr_32",)  # a share, not dollars


def wisconsin_npr_lines(w):
    out = []
    add = _adder(out)
    for line, label in NPR_LINES:
        add(WISCONSIN_NPR, line, f"wi1npr_{line}", label, w[f"wi1npr_{line}"])
    if w["wi1npr_73"]:
        add(WISCONSIN_NPR, "73", "wi1npr_73", "Amount owed", w["wi1npr_73"])
    else:
        add(WISCONSIN_NPR, "70", "wi1npr_70", "Refund", w["wi1npr_70"])
    return out


def pennsylvania_lines(pa):
    out = []
    add = _adder(out)
    for line, label in PA_LINES:
        add(PENNSYLVANIA, line, f"pa40_{line}", label, pa[f"pa40_{line}"])
    if pa["pa40_26"]:
        add(PENNSYLVANIA, "26", "pa40_26", "Amount owed", pa["pa40_26"])
    else:
        add(PENNSYLVANIA, "29", "pa40_29", "Refund", pa["pa40_29"])
    return out


def return_lines(i, r, w, year):
    """Form 1040 and Wisconsin Form 1 line by line, for a year projected from stubs."""
    return federal_lines(i, r, year) + wisconsin_lines(w, year)


def has_return(facts, year):
    """Whether return_facts holds the year's return inputs (the figures the filed return was made from)."""
    return not facts.empty and ((facts["year"].astype(str) == str(year)) & (facts["kind"] == "input")).any()


def as_filed(year, facts):
    """A filed year's returns line by line, computed from the return's own inputs in return_facts (the
    W-2s and the other figures on it), each beside the filed line: Form 1040, then Wisconsin Form 1 or
    Form 1NPR and PA-40 when the inputs have them. The same computation tax_check compares."""
    from . import pennsylvania
    i = tax.inputs_from_facts(facts, year)
    r = tax.federal_return(i, year)
    lines = federal_lines(i, r, year)
    if tax.has_wisconsin(i):
        w = wisconsin.wisconsin_return({**i, "federal_agi": r["11a"]}, year)
        lines += wisconsin_npr_lines(w) if "wi_income" in i else wisconsin_lines(w, year)
    if pennsylvania.has_pennsylvania(i):
        lines += pennsylvania_lines(pennsylvania.pa_return(i, year))
    filed = facts[(facts["year"].astype(str) == str(year)) & (facts["kind"] == "filed")]
    filed = dict(zip(filed["key"], _num(filed["value"])))
    return pd.DataFrame([{"scenario": "as is", "order": n, **row, "filed": filed.get(row["key"])}
                         for n, row in enumerate(lines)], columns=DETAIL_COLUMNS)


PLAN_COLUMNS = ["return", "person", "employer", "paychecks_left", "vest_rate", "projected", "target", "current_extra",
                "suggested_extra", "lands_at", "penalty_free", "note"]


def _harbor(year, facts):
    """The prior-year safe harbor from last year's filed return, or None when it isn't in return_facts."""
    prior = facts[(facts["year"].astype(str) == str(year - 1)) & (facts["kind"] == "filed")] if not facts.empty else facts
    lines = dict(zip(prior["key"], _num(prior["value"]))) if len(prior) else {}
    return tax.safe_harbor(lines["24"], lines.get("11a", 0)) if "24" in lines else None


def withholding_plan(year, paystubs, sales, facts, target=2000.0, supplemental_rate=0.35, comp_events=None,
                     vests=None):
    """For each return, the extra per paycheck that lands the year on a refund of target, on no balance, and
    on owing target, from the year so far plus the projection with future vests withheld at
    supplemental_rate. projected and lands_at: refund positive, owed negative. The change goes on the
    paychecks of the person and employer with the most left (W-4 Step 4(c); WT-4's additional amount). A
    federal balance owed says whether it escapes the underpayment penalty (tax.penalty_free)."""
    i, pos = inputs_for(year, paystubs, sales, facts, {"supplemental_rate": supplemental_rate}, comp_events, vests)
    r = tax.federal_return(i, year)
    w = wisconsin.wisconsin_return({**i, "federal_agi": r["11a"]}, year)
    left = [(k, v) for k, v in pos.items() if v["paychecks_left"]]
    vest = next((v.get("vest_rate") for v in pos.values() if v.get("vest_rate") is not None), None)
    if not left:
        return pd.DataFrame([{"return": "federal", "projected": -r["37"], "target": target, "vest_rate": vest,
                              "note": "no paychecks left this year to change"}], columns=PLAN_COLUMNS)
    (person, employer), v = max(left, key=lambda kv: kv[1]["paychecks_left"])
    n = v["paychecks_left"]
    harbor = _harbor(year, facts)
    returns = (("federal", -r["37"], v["extra_withholding"], "W-4 Step 4(c): extra withholding per paycheck"),
               ("wisconsin", -w["wi_owe_or_refund"], float(v["wi_settings"][2]) if v.get("wi_settings") else 0.0,
                "WT-4: additional amount per paycheck" if v.get("wi_settings") else
                "no Wisconsin settings decoded from the stubs; additional amount assumed zero"))
    rows = []
    for name, projected, now, note in returns:
        for goal in (target, 0.0, -target):  # a refund of target, no balance, owing target
            extra = round(max(now + (goal - projected) / n, 0.0), 2)  # withholding can't go below none
            lands = round(projected + (extra - now) * n, 2)
            row = {"return": name, "person": person, "employer": employer, "paychecks_left": n, "vest_rate": vest,
                   "projected": projected, "target": goal, "current_extra": round(now, 2), "suggested_extra": extra,
                   "lands_at": lands, "penalty_free": None, "note": note}
            if name == "federal":
                row["penalty_free"] = tax.penalty_free(-lands, r["25a"] + r["25c"] + (extra - now) * n, r["24"], harbor)
            rows.append(row)
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


def elections(year, paystubs, after_tax_converts=False, comp_events=None):
    """401(k), HSA and dependent-care FSA so far and projected to year end against the year's limits.

    The mega backdoor Roth row is the after-tax 401(k): its limit is what 415(c) leaves after elective
    deferrals and the employer's contributions, projected. Its status says whether the plan converts
    after-tax money to Roth on its own (after_tax_converts, the household setting
    after_tax_401k_converts_to_roth), since the stubs can't show a conversion. A backdoor Roth IRA is not
    on a pay stub, so each person gets a row saying it is not tracked yet.
    """
    p = taxdata.params(year)
    pos = positions(paystubs, year, comp_events=comp_events)
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
                               else "; Roth conversion not tracked")
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
