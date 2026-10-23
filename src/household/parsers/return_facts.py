"""Return facts: a CSV of a tax year's inputs and filed lines, typed in once from the return.

kind is "input" (a figure the calculation starts from: a W-2 box, a 1099 total,
a Schedule A input, a credit), "filed" (a line the return shows, which the
calculation must reproduce) or "estimate" (a household estimate for a year in
progress). source_doc is the doc_id of the document the figure came from.
"""

import csv
import io

import pandas as pd

from .common import ParseError, Result
from .. import schema

NAME = "return_facts"
FACTS = schema.table(schema.LEDGER, "return_facts")
HEADER = tuple(c for c in FACTS.columns if c != "doc_id")


def sniff(text):
    first = text.lstrip("﻿").splitlines()[0] if text.strip() else ""
    return tuple(c.strip() for c in first.split(",")) == HEADER


def parse(text):
    df = pd.DataFrame(list(csv.DictReader(io.StringIO(text.lstrip("﻿")))), dtype=str).fillna("")
    bad = sorted(set(df["kind"]) - set(FACTS.enums["kind"]))
    if bad:
        raise ParseError(f"{NAME}: kind has {bad}")
    if df.duplicated(["year", "kind", "key", "person"]).any():
        raise ParseError(f"{NAME}: duplicate year/kind/key/person")
    try:
        df["value"].astype(float)
    except ValueError as e:
        raise ParseError(f"{NAME}: non-numeric value ({e})")
    years = sorted(df["year"].unique())
    return Result(kind="return_facts", parser=NAME, label=f"return_facts_{'_'.join(years)}",
                  tables={"return_facts": df},
                  keys={"return_facts": ["year", "kind", "key", "person"]})
