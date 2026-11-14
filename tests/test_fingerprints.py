"""What a part of a run rested on: the same things give the same fingerprint, and Reports keeps one per part."""

from dataclasses import dataclass, field
from datetime import date

import numpy as np
import pandas as pd

from household import fingerprints, schema, sheets
from test_sheets import FakeSpreadsheet


@dataclass
class Thing:
    name: str
    born: date
    held: dict = field(default_factory=dict)


def test_a_fingerprint_does_not_depend_on_the_order_things_were_built_in():
    a = fingerprints.of("x", {"b": 2.0, "a": 1.0}, {"food", "kids"}, Thing("alex", date(1980, 6, 1), {"k": [1, 2]}))
    b = fingerprints.of("x", {"a": 1.0, "b": 2.0}, {"kids", "food"}, Thing("alex", date(1980, 6, 1), {"k": [1, 2]}))
    assert a == b and len(a) == 16
    assert a != fingerprints.of(
        "x", {"a": 1.0, "b": 2.5}, {"kids", "food"}, Thing("alex", date(1980, 6, 1), {"k": [1, 2]})
    )
    assert a != fingerprints.of(
        "y", {"a": 1.0, "b": 2.0}, {"kids", "food"}, Thing("alex", date(1980, 6, 1), {"k": [1, 2]})
    )


def test_a_sum_added_in_another_order_is_the_same_sum_and_a_cent_is_not():
    parts = [10.1, 20.2, 30.3, 0.07]
    assert sum(parts) != sum(reversed(parts))  # the last bit differs
    assert fingerprints.of(sum(parts)) == fingerprints.of(sum(reversed(parts)))
    assert fingerprints.of(60.67) != fingerprints.of(60.68)
    assert fingerprints.of(np.float64(5.0), np.int64(3)) == fingerprints.of(5.0, 3)
    assert fingerprints.of(float("nan")) == fingerprints.of(float("nan")) != fingerprints.of(0.0)


def test_a_table_is_fingerprinted_by_what_it_holds():
    df = pd.DataFrame({"category": ["food", "kids"], "amount": [10.0, None]})
    same = pd.DataFrame({"category": ["food", "kids"], "amount": [10.0, np.nan]})
    assert fingerprints.of(df) == fingerprints.of(same)
    assert fingerprints.of(df) != fingerprints.of(df.iloc[::-1])  # its rows in another order are another table
    assert fingerprints.of(df) != fingerprints.of(df.rename(columns={"amount": "spend"}))


def reports():
    raw = FakeSpreadsheet("reports")
    sheets.ensure_tables(raw, schema.REPORTS)
    return raw, sheets.Book(raw)


def test_reports_keeps_one_fingerprint_per_part_with_what_its_run_said():
    raw, book = reports()
    assert fingerprints.read(book) == {}
    fingerprints.write(book, "review", "aaaa", "run-1", "2026-11-01T00:00:00", {"questions": 3})
    fingerprints.write(book, "retirement", "bbbb", "run-1", "2026-11-01T00:00:00", {"said": ["retirement: 2040"]})
    fingerprints.write(book, "review", "cccc", "run-2", "2026-11-02T00:00:00", {"questions": 0})
    got = fingerprints.read(book)
    assert got["review"] == {
        "fingerprint": "cccc",
        "run_id": "run-2",
        "at": "2026-11-02T00:00:00",
        "detail": {"questions": 0},
    }
    assert got["retirement"]["detail"]["said"] == ["retirement: 2040"]
    fingerprints.forget(book, "review")
    assert set(fingerprints.read(book)) == {"retirement"}


def test_a_part_keeps_its_tables_only_while_they_are_as_its_run_wrote_them():
    raw, book = reports()
    years = schema.table(schema.REPORTS, "retirement_years")
    row = {c: "" for c in years.columns} | {"retire_year": 2040, "run_id": "run-1"}
    sheets.replace_table(book, years, pd.DataFrame([row]))
    wrote = {"retirement_years": 1, "retirement_earnings": 0}
    assert fingerprints.holds(book, wrote, "run-1")
    assert not fingerprints.holds(book, wrote, "run-0")  # another run's rows
    assert not fingerprints.holds(book, {"retirement_years": 1, "retirement_earnings": 2}, "run-1")  # rows gone
    raw.tabs["retirement_years"].rows = raw.tabs["retirement_years"].rows[:1]  # the tab was cleared
    assert not fingerprints.holds(book, wrote, "run-1")
    assert fingerprints.holds(book, {"retirement_earnings": 0}, "run-1")  # it wrote none there, and none is there
