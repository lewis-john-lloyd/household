"""Pennsylvania personal income tax (PA-40) and employer withholding.

Pennsylvania taxes eight classes of income at one flat rate, with no standard
deduction or exemptions:

    line 9    total of the positive classes (compensation, interest, dividends,
              business, net gains, rents, estates, gambling); a loss in one
              class does not offset another
    line 11   line 9 less other deductions (for example college savings
              contributions, code T)
    line 12   flat rate x line 11
    line 22   resident credit for tax paid to another state (Schedule G-L)

A part-year resident reports the income of the resident period (and any
Pennsylvania-source income after), given as inputs. Withholding is the flat rate
on Pennsylvania compensation: gross pay less group-term life and Section 125
benefits (health premiums, flexible spending, HSA), with 401(k) deferrals still
included.

Not covered: tax forgiveness (Schedule SP; nowhere near these incomes), estimated
payments other than as an input, local earned income taxes (filed separately).
"""

import pandas as pd

from . import taxdata, withholding
from .tax import whole

PA_LINES = ("PA", "PA State Income Tax")
CLASSES = ("compensation", "interest", "dividends", "business", "gains", "rents", "estates", "gambling")
GL_CLASSES = ("compensation", "interest", "dividends", "business", "gains", "rents", "estates", "gambling")
RETIREMENT_DEFERRAL = ("401k", "403b", "457")  # deferrals Pennsylvania still taxes
CENT = withholding.CENT


def schedule_gl(i, year):
    """Schedule G-L resident credit, with its Section I-III amounts."""
    rate = taxdata.params(year)["pa_rate"]
    g = lambda k: float(i.get(k, 0) or 0)  # noqa: E731
    in_pa = [g(f"pa_gl_{c}_pa") for c in GL_CLASSES]
    in_other = [g(f"pa_gl_{c}_other") for c in GL_CLASSES]
    both = sum(min(a, b) for a, b in zip(in_pa, in_other))  # Section I line 3
    other_total = sum(in_other)  # Section III line 2
    paid = min(g("pa_gl_other_tax_due"), g("pa_gl_other_tax_paid"))  # line 4c
    ratio = round(both / other_total, 6) if other_total else 1.0
    adjustment = whole((1 - ratio) * paid) if ratio < 1 else 0  # line 4d
    adjusted = paid - adjustment  # line 4e
    limit = whole(rate * both)  # line 5
    return {"gl_3": whole(both), "gl_4c": whole(paid), "gl_4d": adjustment, "gl_4e": whole(adjusted),
            "gl_5": limit, "gl_6": whole(min(adjusted, limit))}


def pa_return(i, year):
    """PA-40 lines from inputs i (missing inputs count as zero)."""
    rate = taxdata.params(year)["pa_rate"]
    g = lambda k: float(i.get(k, 0) or 0)  # noqa: E731
    total = sum(max(g(f"pa_{c}"), 0) for c in CLASSES)
    taxable = max(total - g("pa_other_deductions"), 0)
    tax = whole(rate * taxable)
    credit = schedule_gl(i, year)["gl_6"]
    payments = whole(g("pa_withheld")) + whole(g("pa_estimated_payments")) + credit
    return {"pa40_9": whole(total), "pa40_11": whole(taxable), "pa40_12": tax,
            "pa40_13": whole(g("pa_withheld")), "pa40_22": credit, "pa40_24": payments,
            "pa40_26": max(tax - payments, 0), "pa40_29": max(payments - tax, 0)}


def has_pennsylvania(inputs):
    return any(k.startswith("pa_") for k in inputs)


# --- withholding -----------------------------------------------------------------------------

def _is_deferral(name):
    n = name.lower().replace(" ", "")
    return any(d in n for d in RETIREMENT_DEFERRAL) and not any(w in n for w in ("after", "match", "at"))


def stub_compensation(rows):
    """Pennsylvania compensation on one stub: gross less group-term life and Section 125 items,
    plus the per-item Section 125 amounts (for naming a one-off)."""
    amount = pd.to_numeric(rows["amount"], errors="coerce").fillna(0.0)
    gross = amount[(rows["kind"] == "memo") & (rows["line_item"] == "Gross")].sum()
    gtl = amount[(rows["kind"] == "income") & rows["line_item"].map(withholding._is_group_term_life)].sum()
    pre = rows["kind"] == "pre_tax"
    excluded = amount[pre & ~rows["line_item"].map(_is_deferral)].groupby(rows["line_item"]).sum()
    excluded = excluded[excluded != 0]
    return round(gross - gtl - excluded.sum(), 2), excluded.to_dict()


def backtest(paystubs):
    """Expected vs actual Pennsylvania withholding for every stub with a Pennsylvania line."""
    cols = ["pay_date", "person", "employer", "stub_id", "kind", "base", "withheld", "expected",
            "residual", "explanation"]
    if paystubs.empty:
        return pd.DataFrame(columns=cols)
    pa = paystubs[(paystubs["kind"] == "tax") & paystubs["line_item"].isin(PA_LINES)]
    if pa.empty:
        return pd.DataFrame(columns=cols)
    amounts = pd.to_numeric(pa["amount"], errors="coerce").fillna(0.0)
    withheld = amounts.groupby(pa["stub_id"].astype(str)).sum()
    out = []
    for stub_id, rows in paystubs[paystubs["stub_id"].astype(str).isin(set(withheld.index))].groupby(
            paystubs["stub_id"].astype(str)):
        year = int(str(rows["pay_date"].iloc[0])[:4])
        w = round(float(withheld[stub_id]), 2)
        base, excluded = stub_compensation(rows)
        row = {"pay_date": str(rows["pay_date"].iloc[0]), "person": rows["person"].iloc[0],
               "employer": rows["employer"].iloc[0], "stub_id": stub_id, "base": base, "withheld": w}
        if year not in taxdata.PARAMS:
            row.update(kind="unchecked", explanation=f"no {year} Pennsylvania rate")
        elif w == 0:
            row.update(kind="none", expected=0.0, residual=0.0, explanation="no Pennsylvania tax withheld")
        else:
            rate = taxdata.params(year)["pa_rate"]
            expected = round(rate * base, 2)
            row.update(kind="regular", expected=expected, residual=round(w - expected, 2),
                       explanation=f"{rate * 100:g}% of Pennsylvania compensation")
            if abs(w - expected) >= CENT:
                taxed = [k for k, v in excluded.items() if abs(round(rate * (base + v), 2) - w) < CENT]
                if taxed:
                    row.update(expected=round(rate * (base + excluded[taxed[0]]), 2), residual=0.0,
                               explanation=f"{rate * 100:g}% of Pennsylvania compensation, with "
                                           f"{taxed[0]} taxed on this stub")
                else:
                    row["explanation"] = f"unexplained {w - expected:+.2f}"
        out.append(row)
    return pd.DataFrame(out, columns=cols).sort_values(["pay_date", "stub_id"]).reset_index(drop=True)
