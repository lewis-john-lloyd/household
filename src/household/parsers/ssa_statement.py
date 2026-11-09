"""The Social Security statement's data download (my Social Security, "Download your statement data"): an XML
file in the osss schema (http://ssa.gov/osss/schemas/2.0) with the person's name and birth date, the
estimated retirement benefit at each claiming age from 62 to 70, and the earnings record year by year.

  ss_statements  who the statement is for (the first word of the name, lower case, as the pay stubs name
                 people), their birth date and the day the file was made
  ss_estimates   the monthly retirement benefit the statement estimates at each age, in today's dollars, and
                 its disability estimate (age blank)
  ss_earnings    each year's earnings taxed for Social Security (FICA) and for Medicare

Each statement's rows are kept under its date, so a later statement (a year's earnings posted, the estimates
redone) stands beside the earlier one and the latest is the one used.

The format is small and flat, so it is read with patterns rather than an XML library: nothing in it is
expanded or fetched. A file missing its name, birth date, estimates or earnings is refused.
"""

import re

import pandas as pd

from .common import ParseError, Result

NAME = "ssa_statement"
KIND = "ss_statement"
ROOT = "OnlineSocialSecurityStatementData"
NAMESPACE = "http://ssa.gov/osss/schemas/"
TAG = r"(?:[A-Za-z_][\w.-]*:)?"


def sniff(text: str) -> bool:
    head = text.lstrip("﻿")[:600]
    return ROOT in head and NAMESPACE in head


def _one(text, tag, what, required=True):
    m = re.search(rf"<{TAG}{tag}>\s*([^<]*?)\s*</{TAG}{tag}>", text)
    if not m and required:
        raise ParseError(f"{NAME}: no {what}")
    return m.group(1) if m else ""


def _whole(text, what):
    t = text.replace(",", "").strip()
    if not re.fullmatch(r"-?\d+(\.\d+)?", t):
        raise ParseError(f"{NAME}: {what} is not a number: {text!r}")
    return float(t)


def parse(text: str) -> Result:
    body = text.lstrip("﻿")
    name = _one(body, "Name", "name")
    person = name.split()[0].lower() if name.split() else ""
    if not re.fullmatch(r"[a-z][a-z'-]*", person):
        raise ParseError(f"{NAME}: the name does not start with a first name")
    born = _one(body, "DateOfBirth", "date of birth")
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", born):
        raise ParseError(f"{NAME}: date of birth is not YYYY-MM-DD: {born!r}")
    made = _one(body, "FileCreationDate", "file date", required=False)[:10]
    as_of = made if re.fullmatch(r"\d{4}-\d{2}-\d{2}", made) else ""

    estimates = []
    for m in re.finditer(rf"<{TAG}Age(\d\d)RetirementEstimate>(.*?)</{TAG}Age\1RetirementEstimate>", body, re.S):
        age, block = int(m.group(1)), m.group(2)
        estimates.append(
            {
                "person": person,
                "as_of": as_of,
                "kind": "retirement",
                "age": str(age),
                "monthly": _whole(_one(block, "Estimate", f"estimate at {age}"), f"estimate at {age}"),
            }
        )
    if not estimates:
        raise ParseError(f"{NAME}: no retirement estimates by age")
    ages = sorted(int(e["age"]) for e in estimates)
    if ages[0] > 62 or ages[-1] < 70 or len(set(ages)) != len(ages):
        raise ParseError(f"{NAME}: the estimates should run from 62 to 70 once each, not {ages}")
    disability = _one(body, "DisabilityEstimate", "disability estimate", required=False)
    if disability:
        estimates.append(
            {
                "person": person,
                "as_of": as_of,
                "kind": "disability",
                "age": "",
                "monthly": _whole(disability, "disability estimate"),
            }
        )

    earnings = []
    for m in re.finditer(
        rf'<{TAG}Earnings\s+startYear="(\d{{4}})"\s+endYear="(\d{{4}})"\s*>(.*?)</{TAG}Earnings>', body, re.S
    ):
        start, end, block = int(m.group(1)), int(m.group(2)), m.group(3)
        if start != end:
            raise ParseError(f"{NAME}: earnings for {start} to {end} are not one year's")
        earnings.append(
            {
                "person": person,
                "as_of": as_of,
                "year": str(start),
                "fica_earnings": _whole(
                    _one(block, "FicaEarnings", f"{start} FICA earnings"), f"{start} FICA earnings"
                ),
                "medicare_earnings": _whole(
                    _one(block, "MedicareEarnings", f"{start} Medicare earnings", required=False) or "0",
                    f"{start} Medicare earnings",
                ),
            }
        )
    if not earnings:
        raise ParseError(f"{NAME}: no earnings record")
    years = [int(e["year"]) for e in earnings]
    if len(set(years)) != len(years):
        raise ParseError(f"{NAME}: a year appears twice in the earnings record")

    statement = pd.DataFrame([{"person": person, "birth_date": born, "as_of": as_of}])
    return Result(
        kind=KIND,
        parser=NAME,
        label=f"ss_statement_{person}_{as_of or 'undated'}",
        tables={
            "ss_statements": statement,
            "ss_estimates": pd.DataFrame(estimates),
            "ss_earnings": pd.DataFrame(earnings),
        },
        keys={
            "ss_statements": ["person", "as_of"],
            "ss_estimates": ["person", "as_of", "kind", "age"],
            "ss_earnings": ["person", "as_of", "year"],
        },
    )
