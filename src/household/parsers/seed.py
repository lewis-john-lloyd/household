"""Seed files: a CSV whose header is exactly the columns of one seedable ledger table.

The accounts, the category set and the aliases that map old category names onto
it are short lists a person decides once and edits rarely, so they arrive as a
file in the inbox like everything else. Rows are keyed on the first column: a
seed that repeats a key adds nothing, so changing an existing row is a
correction, not a new seed.
"""

import csv
import io

import pandas as pd

from .common import ParseError, Result
from .. import schema

NAME = "seed"
SEEDABLE = ("accounts", "categories", "category_aliases")
TABLES = {name: schema.table(schema.LEDGER, name) for name in SEEDABLE}


def _table(text):
    first = text.lstrip("﻿").splitlines()[0] if text.strip() else ""
    header = tuple(c.strip() for c in first.split(","))
    return next((t for t in TABLES.values() if header == t.columns), None)


def sniff(text):
    return _table(text) is not None


def parse(text):
    table = _table(text)
    df = pd.DataFrame(list(csv.DictReader(io.StringIO(text.lstrip("﻿")))), dtype=str).fillna("")
    df = df.apply(lambda c: c.str.strip())
    key = table.columns[0]
    if df[key].duplicated().any():
        raise ParseError(f"{NAME}: duplicate {key} in {table.name}: {sorted(set(df.loc[df[key].duplicated(), key]))}")
    for column, allowed in table.enums.items():
        bad = sorted(set(df[column]) - set(allowed) - {""})
        if bad:
            raise ParseError(f"{NAME}: {table.name}.{column} has {bad}")
    if table.name == "category_aliases":
        blank = df[df["category"] == ""]
        if not blank.empty:
            raise ParseError(f"{NAME}: alias with no category: {list(blank['alias'])}")
    return Result(kind=f"{table.name}_seed", parser=NAME, label=f"{table.name}_seed",
                  tables={table.name: df}, keys={table.name: [key]},
                  notes=[f"{table.name}: {len(df)} rows"])
