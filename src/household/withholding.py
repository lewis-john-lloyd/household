"""Federal income tax withholding, recomputed for every pay stub in the ledger.

Each stub is either supplemental pay (bonus, RSU vest, gross-up) withheld at a
flat rate, or regular pay withheld by Publication 15-T's percentage method:

    annual = (FIT taxable wages - group-term life) * periods - line 1g
    tentative = tax(annual - table floor) / periods
    withheld = tentative - Step 3 / periods + Step 4(c)

The backtest compares that with what the stub shows. Supplemental stubs must
match a flat rate exactly. For regular stubs the residual is the W-4's own
adjustment, Step 4(c) extra minus Step 3 credits per period, so it is decoded
back into whole-dollar W-4 entries (for example "Step 4(c) 525, Step 3 4,000").
A residual that decodes is explained, and the report shows when the settings
changed; one that does not decode is flagged.
"""

from datetime import date

import pandas as pd

from . import tax, taxdata

FLAT_RATES = (0.22, 0.35, 0.37)
TAXABLE_MEMOS = ("FIT Taxable Wages", "Fed Taxable Income")
FIT_LINES = ("Federal Income Tax", "FITW")
CENT = 0.015
DECODE_TOLERANCE = 0.0075  # withholding is rounded to cents, so a true setting lands within half a cent


def _is_group_term_life(name):
    return name.lower().startswith(("group term", "group te"))


def periods_per_year(period_start, period_end):
    days = (date.fromisoformat(period_end) - date.fromisoformat(period_start)).days + 1
    return {7: 52, 14: 26}.get(days, 24 if 14 < days < 17 else 12 if days > 27 else 26)


def regular_withholding(base, periods, year, step3=0.0, step4c=0.0):
    """Percentage-method withholding for one paycheck (MFJ, Step 2 box unchecked)."""
    p = taxdata.params(year)
    annual = max(base * periods - p["withholding_line_1g_mfj"], 0)
    tentative = tax.bracket_tax(max(annual - p["withholding_mfj_table_floor"], 0), year) / periods
    return round(max(tentative - step3 / periods, 0) + step4c, 2)


def stub_summary(rows):
    """One row per stub from paystub line items: base wages, federal withheld, kind."""
    out = []
    for (pay_date, person, employer, stub_id), x in rows.groupby(
            ["pay_date", "person", "employer", "stub_id"], sort=True):
        x = x.copy()
        x["amount"] = pd.to_numeric(x["amount"], errors="coerce").fillna(0.0)
        taxable = x[(x["kind"] == "memo") & x["line_item"].isin(TAXABLE_MEMOS)]["amount"].sum()
        gtl = x[(x["kind"] == "income") & x["line_item"].map(_is_group_term_life)]["amount"].sum()
        fit = x[(x["kind"] == "tax") & x["line_item"].isin(FIT_LINES)]["amount"].sum()
        earned = x[(x["kind"] == "income") & (x["amount"] != 0)]["line_item"]
        out.append({"pay_date": str(pay_date), "person": person, "employer": employer,
                    "stub_id": str(stub_id), "period_start": str(x["period_start"].iloc[0]),
                    "period_end": str(x["period_end"].iloc[0]), "base": round(taxable - gtl, 2),
                    "withheld": round(fit, 2), "lines": "+".join(earned)})
    return pd.DataFrame(out)


def backtest(paystubs):
    """Expected vs actual federal withholding for every stub; returns one row per stub."""
    cols = ["pay_date", "person", "employer", "stub_id", "kind", "base", "withheld", "expected",
            "residual", "explanation"]
    internal = ["period_start", "period_end"]
    if paystubs.empty:
        return pd.DataFrame(columns=cols)
    stubs = stub_summary(paystubs)
    rows = []
    for s in stubs.to_dict("records"):
        year = int(s["pay_date"][:4])
        row = {**s, "kind": "", "expected": None, "residual": None, "explanation": ""}
        if year not in taxdata.PARAMS:
            row.update(kind="unchecked", explanation=f"no {year} withholding tables")
        elif s["base"] == 0 and s["withheld"] == 0:
            row.update(kind="empty", expected=0.0, residual=0.0, explanation="nothing to withhold")
        else:
            flat = next((r for r in FLAT_RATES if abs(round(r * s["base"], 2) - s["withheld"]) < CENT),
                        None)
            if flat is not None:
                row.update(kind="supplemental", expected=round(flat * s["base"], 2), residual=0.0,
                           explanation=f"flat {flat:.0%}")
            else:
                periods = periods_per_year(s["period_start"], s["period_end"])
                expected = regular_withholding(s["base"], periods, year)
                row.update(kind="regular", expected=expected,
                           residual=round(s["withheld"] - expected, 2))
        rows.append(row)
    df = pd.DataFrame(rows, columns=cols + internal)
    _explain_regular(df)
    return df[cols]


# Step 3 is a sum of per-dependent credits (500 for other dependents; 2,000 or 2,200 per
# child), so only those combinations are tried; arbitrary amounts would fit any residual.
STEP3_CANDIDATES = sorted({500 * a + 2000 * b + 2200 * c
                           for a in range(9) for b in range(6) for c in range(6)} - {0}
                          | {0})


def decode_w4(residual, periods, prefer=()):
    """Whole-dollar (Step 4(c), Step 3) that produce this residual.

    Step 3 amounts in prefer (ones this person's W-4 is known to have used) are
    tried first, then the rest smallest first: several pairs can produce the same
    residual, and the one consistent with the person's other paychecks is the
    likely truth.
    """
    for step3 in list(prefer) + [c for c in STEP3_CANDIDATES if c not in prefer]:
        step4c = residual + step3 / periods
        if step4c > -CENT and abs(step4c - round(step4c)) < DECODE_TOLERANCE:
            return int(round(step4c)), step3
    return None


def _explain_regular(df):
    regular = df.index[df["kind"] == "regular"]
    # First pass: residuals that are a Step 3 credit alone pin down the Step 3 values in use.
    prefer = {}
    for i in regular:
        r, periods = df.at[i, "residual"], periods_per_year(*_period(df, i))
        decoded = decode_w4(r, periods) if r < -CENT else None
        if decoded and decoded[0] == 0:
            prefer.setdefault(df.at[i, "person"], []).append(decoded[1])
    for i in regular:
        r = df.at[i, "residual"]
        if abs(r) < CENT:
            df.at[i, "explanation"] = "matches the table"
            continue
        periods = periods_per_year(*_period(df, i))
        decoded = decode_w4(r, periods, prefer=sorted(set(prefer.get(df.at[i, "person"], []))))
        if decoded is None:
            df.at[i, "explanation"] = f"unexplained {r:+.2f}"
            continue
        step4c, step3 = decoded
        parts = ([f"Step 4(c) {step4c:,}"] if step4c else []) + ([f"Step 3 {step3:,}"] if step3 else [])
        df.at[i, "explanation"] = "W-4: " + ", ".join(parts)


def _period(df, i):
    return df.at[i, "period_start"], df.at[i, "period_end"]
