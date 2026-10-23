"""The accounts seed: a CSV whose header is exactly the ledger's accounts columns."""

import csv
import io

import pandas as pd

from .common import ParseError, Result
from .. import schema

NAME = "accounts_seed"
ACCOUNTS = schema.table(schema.LEDGER, "accounts")


def sniff(text):
    first = text.lstrip("﻿").splitlines()[0] if text.strip() else ""
    return tuple(c.strip() for c in first.split(",")) == ACCOUNTS.columns


def parse(text):
    df = pd.DataFrame(list(csv.DictReader(io.StringIO(text.lstrip("﻿")))), dtype=str).fillna("")
    if df["account_id"].duplicated().any():
        raise ParseError(f"{NAME}: duplicate account_id")
    for column, allowed in ACCOUNTS.enums.items():
        bad = sorted(set(df[column]) - set(allowed))
        if bad:
            raise ParseError(f"{NAME}: {column} has {bad}")
    return Result(kind="accounts_seed", parser=NAME, label="accounts_seed",
                  tables={"accounts": df}, keys={"accounts": ["account_id"]})
