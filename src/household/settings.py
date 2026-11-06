"""Household settings: the parameters table's jurisdiction "household" rows, kept in the ledger and edited in the
views' Settings tab like any other edit, so nothing about the household lives in the code or a script.

SETTINGS lists the settings the engine and the weekly email read, with what each is for; the views show them
even before they are set. check() says which values can't be used (a link that isn't https, a date that isn't
a date), so compute can say so rather than send a broken link.

A setting can be narrowed with one ":" part, as order_payee:books already is; a claim deadline for one plan
year is lpfsa_claim_deadline:2026.
"""

import re
from datetime import date

import pandas as pd

from . import categorize

SETTINGS = {
    "target_refund": "The federal and Wisconsin refund to aim withholding at, in dollars (default 2000)",
    "planned_supplemental_rate": "The federal rate vests and bonuses are withheld at from now on (default 0.35)",
    "after_tax_401k_converts_to_roth": "TRUE when the 401(k) plan converts after-tax money to Roth on its own",
    "withholding_tolerance": "How far, in dollars, a projected refund may miss target_refund before the weekly "
                             "email says so (default 500)",
    "views_url": "The views' web app link, for links in the weekly email",
    "payroll_url": "Where W-4, WT-4 and supplemental withholding are changed",
    "benefits_url": "Where 401(k) contributions are changed",
    "fsa_claims_url": "Where FSA claims are filed",
    "fsa_payee_pattern": "A pattern matching the FSA administrator on reimbursement deposits",
    "lpfsa_categories": "Categories whose reimbursements come from the limited-purpose FSA, separated by commas",
    "dcfsa_categories": "Categories whose reimbursements come from the dependent care FSA, separated by commas",
    "lpfsa_claim_deadline": "The last day to claim limited-purpose FSA money: MM-DD of the year after the plan "
                            "year (lpfsa_claim_deadline:2026 with a full date for one plan year)",
    "dcfsa_claim_deadline": "The last day to claim dependent care FSA money: MM-DD of the year after the plan "
                            "year (dcfsa_claim_deadline:2026 with a full date for one plan year)",
}

LINKS = ("views_url", "payroll_url", "benefits_url", "fsa_claims_url")
NUMBERS = ("target_refund", "planned_supplemental_rate", "withholding_tolerance")
DEADLINES = ("lpfsa_claim_deadline", "dcfsa_claim_deadline")


def value(params, key, default=""):
    """A household setting's value, or default when it is not set."""
    return categorize.household_parameter(params, key, default) or default


def number(params, key, default):
    """A numeric setting, or default when it is unset or not a number."""
    try:
        return float(value(params, key, default))
    except ValueError:
        return float(default)


def link(params, key):
    """An https link from the settings, or "" when it is unset or not one."""
    url = value(params, key)
    return url if url.startswith("https://") else ""


def names(params, key):
    """A comma-separated setting as a set of lower-case names."""
    return {n.strip().lower() for n in value(params, key).split(",") if n.strip()}


def claim_deadline(params, key, plan_year):
    """The claim deadline for a plan year: key:plan_year as a full date, else key as MM-DD of the next year.
    None when neither is set or usable."""
    specific = value(params, f"{key}:{plan_year}")
    try:
        if specific:
            return date.fromisoformat(specific)
        every = value(params, key)
        if re.fullmatch(r"\d{2}-\d{2}", every):
            return date.fromisoformat(f"{plan_year + 1}-{every}")
    except ValueError:
        return None
    return None


def check(params):
    """[(key, value, problem)] for each setting that is set but can't be used."""
    if params is None or params.empty:
        return []
    household = params[params["jurisdiction"].astype(str) == "household"]
    latest = household.drop_duplicates("key", keep="last")
    out = []
    for key, v in zip(latest["key"].astype(str), latest["value"].astype(str).str.strip()):
        base = key.split(":")[0]
        if not v:
            continue
        if base in LINKS and not v.startswith("https://"):
            out.append((key, v, "is not an https link"))
        elif base in NUMBERS:
            try:
                float(v)
            except ValueError:
                out.append((key, v, "is not a number"))
        elif base == "fsa_payee_pattern":
            try:
                re.compile(v)
            except re.error as e:
                out.append((key, v, f"is not a pattern ({e})"))
        elif base in DEADLINES:
            full = ":" in key
            ok = re.fullmatch(r"\d{4}-\d{2}-\d{2}" if full else r"\d{2}-\d{2}", v)
            try:
                date.fromisoformat(v if full else f"2001-{v}")
            except ValueError:
                ok = False
            if not ok:
                out.append((key, v, "is not a date (YYYY-MM-DD)" if full else "is not a month and day (MM-DD)"))
    return out


def known(editor_rows):
    """The editor's settings plus every setting in SETTINGS that isn't set yet, each with what it is for."""
    rows = editor_rows.copy()
    have = set(rows["key"].astype(str))
    missing = [{"key": k, "value": "", "source": "not set"} for k in SETTINGS if k not in have]
    if missing:
        rows = pd.concat([rows, pd.DataFrame(missing)], ignore_index=True)
    rows["about"] = rows["key"].astype(str).str.split(":").str[0].map(SETTINGS).fillna("")
    return rows.sort_values("key").reset_index(drop=True)
