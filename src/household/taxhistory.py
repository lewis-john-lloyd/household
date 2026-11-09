"""Taxes over the years: each filed return's income split three ways, for the Taxes page's history chart.

Built from return_facts alone (the filed lines and the figures the return was made from), so a year shows
as soon as its return is in the ledger, whether or not its tax law is on file. Nothing is recomputed.

For each year and return (federal, pa, wi) the table has three splits of one total:

  came   where the income came from: wages by person, interest and dividends, capital gains, other
  taxed  how it was taxed: the taxable income and what was deducted (and adjusted) before it
  went   where it went: each tax after credits, the credits (part of what was kept), and what was kept

Federal "went" includes the state income taxes, so it is the whole household's income tax; a state's
"went" is that state's alone. Credits are the nonrefundable ones (line 20's) and any refundable ones a year
records as `refundable_credits` (2021's child and dependent care credits, say, paid partly in advance): a
refundable credit lowers the income tax paid, so it is taken off the federal income tax and counted as kept. A capital loss is a negative part of "came" (the most a return deducts).
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
            refundable = filed.get("refundable_credits", 0.0)
            credits = filed.get("20", max(filed.get("16", after) - after, 0.0)) + refundable
            federal = after - refundable
            state = sum(t for t, _ in states.values())
            kept = income - federal - other_taxes - state
            add("federal", "went", [(KEPT, kept - credits), (CREDITS, credits), ("State income tax", state),
                                    ("Other federal taxes", other_taxes), ("Federal income tax", federal)], income)
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
            add("pa", "went", [(KEPT, total - tax - credits), (CREDITS, credits), ("State income tax", tax)],
                total)
        if "wi" in states:
            total = g("wi_income") or filed.get("wi_income", 0.0)
            tax, credits = states["wi"]
            add("wi", "came", [("Wisconsin income", total)], total)
            add("wi", "went", [(KEPT, total - tax - credits), (CREDITS, credits), ("State income tax", tax)],
                total)
    return pd.DataFrame(rows, columns=COLUMNS)


INPUT_KEYS = ("taxable_interest", "ordinary_dividends", "capital_gain_short", "capital_gain_long", "adjustments",
              "wi_income")


def projected(year, lines):
    """The history rows for a year not yet filed, from its projected return lines (projection.project's detail
    as things stand): each line's key read as the filed line, and the projection's inputs among them (interest,
    dividends, gains, adjustments, Wisconsin income) as the return's inputs."""
    if lines is None or lines.empty:
        return pd.DataFrame(columns=COLUMNS)
    rows = []
    for key, value in zip(lines["key"], lines["value"]):
        rows.append({"year": str(year), "kind": "filed", "key": key, "person": "", "value": value})
        if key in INPUT_KEYS:
            rows.append({"year": str(year), "kind": "input", "key": key, "person": "", "value": value})
    return history(pd.DataFrame(rows))


# A filed return's lines as the forms number them: (form, line, key, label), in the order they read. A key a year
# filed that isn't here is listed after its form's lines under its own name.
FEDERAL = "Form 1040"
FILED_LINES = [(FEDERAL, line, key, label) for line, key, label in (
    ("1z", "1z", "Wages (W-2 box 1)"), ("2b", "2b", "Taxable interest"), ("3a", "3a", "Qualified dividends"),
    ("3b", "3b", "Ordinary dividends"), ("4b", "4b", "IRA distributions, taxable"), ("5b", "5b", "Pensions, taxable"),
    ("7", "7", "Capital gain or loss"), ("7", "7a", "Capital gain or loss"), ("8", "8", "Other income"),
    ("9", "9", "Total income"), ("10", "10", "Adjustments to income"), ("11", "11", "Adjusted gross income"),
    ("11", "11a", "Adjusted gross income"), ("12", "12", "Deductions"), ("12", "12e", "Deductions"),
    ("13", "13", "Qualified business income deduction"), ("14", "14", "Total deductions"),
    ("15", "15", "Taxable income"), ("16", "16", "Tax"), ("17", "17", "Additional taxes (Schedule 2)"),
    ("18", "18", "Tax and additional taxes"), ("19", "19", "Child tax credit"), ("20", "20", "Other credits"),
    ("21", "21", "Total credits"), ("22", "22", "Tax after credits"), ("23", "23", "Other taxes"),
    ("24", "24", "Total tax"), ("25a", "25a", "Withheld, W-2s"), ("25b", "25b", "Withheld, 1099s"),
    ("25c", "25c", "Withheld, other"), ("25d", "25d", "Withheld, total"), ("26", "26", "Estimated payments"),
    ("", "refundable_credits", "Refundable credits"), ("31", "31", "Excess Social Security withheld"),
    ("32", "32", "Other payments and refundable credits"), ("33", "33", "Total payments"),
    ("34", "34", "Overpaid"), ("35a", "35a", "Refund"), ("36", "36", "Applied to next year's estimated tax"),
    ("37", "37", "Amount owed"))]
FILED_LINES += [("Wisconsin Form 1", "", key, label) for key, label in (
    ("wi_income", "Wisconsin income"), ("wi_standard_deduction", "Standard deduction"), ("wi_exemptions", "Exemptions"),
    ("wi_taxable", "Taxable income"), ("wi_tax", "Tax"), ("wi_itemized_credit", "Itemized deduction credit"),
    ("wi_school_property_credit", "School property tax credit"), ("wi_married_couple_credit", "Married couple credit"),
    ("wi_net_tax", "Net tax"), ("wi_withheld", "Wisconsin tax withheld"), ("wi_owe_or_refund", "Refund or amount owed"))]
FILED_LINES += [("Wisconsin Form 1NPR", line, "wi1npr_" + line, label) for line, label in (
    ("32", "Wisconsin share of income"), ("34c", "Standard deduction"), ("36c", "Exemptions"), ("37", "Taxable income"),
    ("38", "Tax on all income"), ("39", "Tax on the Wisconsin share"), ("40", "Itemized deduction credit"),
    ("43c", "School property tax credit"), ("45", "Tax after those credits"), ("48", "Married couple credit"),
    ("52", "Net tax"), ("58", "Wisconsin tax withheld"))]
FILED_LINES += [("Pennsylvania PA-40", line, "pa40_" + line, label) for line, label in (
    ("9", "Total PA taxable income"), ("11", "Adjusted PA taxable income"), ("12", "Tax"), ("13", "PA tax withheld"),
    ("22", "Resident credit"), ("23", "Other credits"), ("24", "Total payments and credits"))]
FILED_COLUMNS = ["year", "form", "order", "line", "key", "label", "value"]
PREFIXES = (("wi1npr_", "Wisconsin Form 1NPR"), ("wi_", "Wisconsin Form 1"), ("pa40_", "Pennsylvania PA-40"))


def filed_lines(facts):
    """FILED_COLUMNS: every filed year's lines as filed (return_facts), form by form, for the Taxes page."""
    if facts is None or facts.empty:
        return pd.DataFrame(columns=FILED_COLUMNS)
    filed = facts[facts["kind"] == "filed"]
    known = {key: (n, form, line, label) for n, (form, line, key, label) in enumerate(FILED_LINES)}
    rows = []
    for year, g in filed.groupby(filed["year"].astype(str)):
        values = dict(zip(g["key"].astype(str), pd.to_numeric(g["value"], errors="coerce")))
        for key, value in values.items():
            if pd.isna(value):
                continue
            if key in known:
                n, form, line, label = known[key]
            else:
                form = next((f for p, f in PREFIXES if key.startswith(p)), FEDERAL)
                n, line, label = len(FILED_LINES), "", key
            rows.append({"year": year, "form": form, "order": n, "line": line, "key": key, "label": label,
                         "value": round(float(value), 4)})
    out = pd.DataFrame(rows, columns=FILED_COLUMNS)
    return out.sort_values(["year", "form", "order", "key"]).reset_index(drop=True)
