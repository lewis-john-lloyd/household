"""Taxes over the years: each filed return's income split three ways, for the Taxes page's history chart.

Built from return_facts alone (the filed lines and the figures the return was made from), so a year shows
as soon as its return is in the ledger, whether or not its tax law is on file. Nothing is recomputed.

For each year and return (federal, pa, wi) the table has three splits of one total:

  came   where the income came from: wages by person, interest and dividends, capital gains, other
  taxed  how it was taxed: the taxable income and what was deducted (and adjusted) before it
  went   where it went: each tax after credits, the credits (part of what was kept), and what was kept

Federal "went" includes the state income taxes, so it is the whole household's income tax; a state's
"went" is that state's alone. A capital loss is a negative part of "came" (the most a return deducts).
"""

import pandas as pd

COLUMNS = ["year", "jurisdiction", "split", "part", "order", "value", "total"]
KEPT = "Kept after income tax"
CREDITS = "Credits (part of what was kept)"


def _year_facts(facts, year):
    rows = facts[facts["year"].astype(str) == str(year)]
    num = lambda s: pd.to_numeric(s, errors="coerce").fillna(0.0)  # noqa: E731
    filed = dict(zip(rows.loc[rows["kind"] == "filed", "key"], num(rows.loc[rows["kind"] == "filed", "value"])))
    inputs = rows[rows["kind"] == "input"].assign(value=lambda d: num(d["value"]))
    total = inputs.groupby("key")["value"].sum().to_dict()
    wages = inputs[inputs["key"] == "wages"]
    by_person = [(str(p), float(v)) for p, v in zip(wages["person"], wages["value"]) if str(p)]
    return filed, total, by_person


def _state_tax(filed):
    """The state income taxes a year's returns show, after their credits: {jurisdiction: (tax, credits)}."""
    out = {}
    if "pa40_12" in filed:
        credits = filed.get("pa40_22", 0) + filed.get("pa40_23", 0)
        out["pa"] = (max(filed["pa40_12"] - credits, 0), min(credits, filed["pa40_12"]))
    if "wi1npr_52" in filed or "wi_net_tax" in filed:
        net = filed.get("wi1npr_52", filed.get("wi_net_tax", 0))
        credits = sum(filed.get(k, 0) for k in ("wi1npr_40", "wi1npr_43c", "wi1npr_48", "wi_itemized_credit",
                                                 "wi_school_property_credit", "wi_married_couple_credit"))
        out["wi"] = (net, credits)
    return out


def history(facts):
    """The history table (COLUMNS), one row per year, return, split and part; total is the split's whole."""
    if facts is None or facts.empty:
        return pd.DataFrame(columns=COLUMNS)
    rows = []
    years = sorted(facts.loc[facts["kind"] == "filed", "year"].astype(str).unique())
    for year in years:
        filed, i, people = _year_facts(facts, year)
        g = lambda k: float(i.get(k, 0.0))  # noqa: E731

        def add(jurisdiction, split, parts, total):
            for n, (part, value) in enumerate(parts):
                if round(value, 2):
                    rows.append({"year": year, "jurisdiction": jurisdiction, "split": split, "part": part, "order": n,
                                 "value": round(value, 2), "total": round(total, 2)})

        states = _state_tax(filed)
        if "11a" in filed and "24" in filed:
            income = filed["11a"] + g("adjustments")
            wages = filed.get("1z", g("wages"))
            gains = max(g("capital_gain_short") + g("capital_gain_long"), -3000.0)
            came = [(f"Wages, {p}", v) for p, v in people] if people else [("Wages", wages)]
            came += [("Interest & dividends", g("taxable_interest") + g("ordinary_dividends")),
                     ("Capital gains", gains)]
            other = income - sum(v for _, v in came)  # what is left: 529 earnings, pensions, IRAs, other income
            came.append(("Other income", other))
            add("federal", "came", came, income)
            add("federal", "taxed", [("Taxable income", filed.get("15", 0.0)),
                                     ("Deducted", filed.get("12e", 0.0)), ("Adjustments", g("adjustments"))], income)
            other_taxes = filed.get("23", 0.0)
            after = filed["24"] - other_taxes
            credits = filed.get("20", max(filed.get("16", after) - after, 0.0))
            state = sum(t for t, _ in states.values())
            kept = income - after - other_taxes - state
            add("federal", "went", [(KEPT, kept - credits), (CREDITS, credits), ("State income tax", state),
                                    ("Other federal taxes", other_taxes), ("Federal income tax", after)], income)
        if "pa" in states:
            total = filed.get("pa40_9", 0.0)
            came = [("Compensation", max(g("pa_compensation"), 0)),
                    ("Interest & dividends", max(g("pa_interest"), 0) + max(g("pa_dividends"), 0)),
                    ("Capital gains", max(g("pa_gains"), 0))]
            came.append(("Other income", total - sum(v for _, v in came)))
            add("pa", "came", came, total)
            add("pa", "taxed", [("Taxable income", filed.get("pa40_11", 0.0)),
                                ("Deducted", total - filed.get("pa40_11", total))], total)
            tax, credits = states["pa"]
            add("pa", "went", [(KEPT, total - tax - credits), (CREDITS, credits), ("Pennsylvania income tax", tax)],
                total)
        if "wi" in states:
            total = g("wi_income") or filed.get("wi_income", 0.0)
            tax, credits = states["wi"]
            add("wi", "came", [("Wisconsin income", total)], total)
            add("wi", "went", [(KEPT, total - tax - credits), (CREDITS, credits), ("Wisconsin income tax", tax)],
                total)
    return pd.DataFrame(rows, columns=COLUMNS)
