"""Shared pieces for document parsers: text tokens with positions, numbers, results."""

import re
from dataclasses import dataclass, field
from datetime import datetime

import pandas as pd

from ..legacy import parse_money

NUMBER = re.compile(r"^\(?-?\$?-?[\d,]*\d(\.\d+)?\)?$")
FIELD = re.compile(r"\S+(?: \S+)*")  # runs of text separated by two or more spaces
TOLERANCE = 0.005


class ParseError(Exception):
    """The document matched a parser but failed its self-checks; nothing is written."""


@dataclass
class Token:
    start: int
    end: int
    text: str

    @property
    def numeric(self):
        return bool(NUMBER.match(self.text))

    @property
    def value(self):
        return parse_money(self.text)


def tokens(line):
    return [Token(m.start(), m.end(), m.group()) for m in FIELD.finditer(line)]


def nearest(token, edges):
    """Index of the column whose right edge is closest to this token's right edge."""
    return min(range(len(edges)), key=lambda i: abs(edges[i] - token.end))


def us_date(text, fmt="%m/%d/%Y"):
    return datetime.strptime(text.strip(), fmt).date().isoformat()


def close(a, b):
    return abs(round(a, 2) - round(b, 2)) < TOLERANCE


def require(checks, what):
    """Raise ParseError listing every failed (name, expected, actual) check."""
    failed = [f"{name}: expected {exp:.2f}, got {act:.2f}" for name, exp, act in checks
              if not close(exp, act)]
    if failed:
        raise ParseError(f"{what} failed self-checks: " + "; ".join(failed))


@dataclass
class Result:
    """What a parser found in one document.

    tables maps a ledger table name to new rows (without doc_id); keys gives the
    columns that identify a row, so re-ingesting an overlapping document appends
    nothing twice.
    """
    kind: str
    parser: str
    label: str
    covers_from: str = ""
    covers_to: str = ""
    account_id: str = ""
    tables: dict = field(default_factory=dict)
    keys: dict = field(default_factory=dict)
    notes: list = field(default_factory=list)

    def frame(self, name):
        return self.tables.get(name, pd.DataFrame())
