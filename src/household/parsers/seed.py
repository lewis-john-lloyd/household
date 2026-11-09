"""Seed files: a CSV whose header is exactly the columns of one seedable ledger table.

The accounts, the category set, the aliases that map old category names onto
it, the payee rules, household parameters, loans and the history of pay changes, homes, their values and
improvements, allocation targets, securities and stock splits are short lists a person
decides once and edits rarely, so they arrive as a file in the inbox like
everything else. Rows are keyed on the first column (a payee rule on its
pattern and effective date, a parameter on its year, jurisdiction and key): a
seed that repeats a key adds nothing, so changing an existing row is a
correction, not a new seed.
"""

import csv
import io
import re

import pandas as pd

from .common import ParseError, Result
from .. import schema

NAME = "seed"
SEEDABLE = (
    "accounts",
    "categories",
    "category_aliases",
    "payee_rules",
    "parameters",
    "budget_rules",
    "loans",
    "comp_events",
    "loan_balances",
    "properties",
    "property_values",
    "improvements",
    "allocation_targets",
    "securities",
    "property_taxes",
    "stock_splits",
    "loan_facts",
    "activity_corrections",
)
TABLES = {name: schema.table(schema.LEDGER, name) for name in SEEDABLE}
# a payee rule changes over time: one pattern, several effective dates
KEYS = {
    "payee_rules": ["pattern", "effective"],
    "parameters": ["year", "jurisdiction", "key"],
    "budget_rules": ["line", "effective"],
    "comp_events": ["effective", "person", "employer"],
    "loan_balances": ["as_of", "loan_id"],
    "property_values": ["as_of", "property_id", "source"],
    "improvements": ["date", "property_id", "description"],
    "property_taxes": ["tax_year", "property_id"],
    "stock_splits": ["symbol", "date"],
    "loan_facts": ["loan_id", "key", "as_of"],
    "allocation_targets": ["effective", "node"],
    "activity_corrections": ["activity_id", "field", "value"],
    # a security's later row with another class or quote is its new description (the last row wins)
    "securities": ["symbol", "name", "asset_class", "expense_ratio", "quote", "wash_group"],
}


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
    keys = KEYS.get(table.name, [table.columns[0]])
    repeated = df.duplicated(keys)
    if repeated.any():
        raise ParseError(
            f"{NAME}: duplicate {', '.join(keys)} in {table.name}: "
            f"{df.loc[repeated, keys].astype(str).agg('/'.join, axis=1).tolist()}"
        )
    for column, allowed in table.enums.items():
        bad = sorted(set(df[column]) - set(allowed) - {""})
        if bad:
            raise ParseError(f"{NAME}: {table.name}.{column} has {bad}")
    if table.name == "payee_rules":
        for pattern in df["pattern"]:
            try:
                re.compile(pattern)
            except re.error as e:
                raise ParseError(f"{NAME}: payee_rules pattern {pattern!r}: {e}") from e
    if table.name == "category_aliases":
        blank = df[df["category"] == ""]
        if not blank.empty:
            raise ParseError(f"{NAME}: alias with no category: {list(blank['alias'])}")
    return Result(
        kind=f"{table.name}_seed",
        parser=NAME,
        label=f"{table.name}_seed",
        tables={table.name: df},
        keys={table.name: keys},
        notes=[f"{table.name}: {len(df)} rows"],
    )
