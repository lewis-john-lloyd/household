"""The engine end to end on fakes: ingest the inbox, compute Reports, run again."""

import csv
from pathlib import Path

import gspread
import pytest

from household import engine, explore, inbox, schema, sheets
from test_inbox import FakeDrive, rows
from test_sheets import FakeSpreadsheet, FakeWorksheet

FIXTURES = Path(__file__).parent / "fixtures"


def legacy_rows(name):
    with open(FIXTURES / name, newline="") as f:
        out = list(csv.reader(f))
    return [[int(c) if c.strip().isdigit() and len(c.strip()) == 5 else c for c in r] for r in out]


class FakeClient:
    def __init__(self):
        self.files = {}
        legacy = FakeSpreadsheet("budget-reports")
        legacy.tabs = {"2001-data": FakeWorksheet("2001-data", legacy_rows("legacy_data.csv")),
                       "2001": FakeWorksheet("2001", legacy_rows("legacy_rollup.csv"))}
        self.legacy = legacy

    def open(self, title, folder_id=None):
        if title not in self.files:
            raise gspread.SpreadsheetNotFound
        return self.files[title]

    def create(self, title, folder_id=None):
        self.files[title] = FakeSpreadsheet(title)
        return self.files[title]

    def open_by_key(self, key):
        return self.legacy


class FakeSession:
    def __init__(self):
        self.sheets, self._drive = FakeClient(), FakeDrive()

    def drive(self):
        return self._drive


@pytest.fixture
def session(monkeypatch):
    s = FakeSession()
    s.root = s._drive.folder("household_finances")
    s.box = s._drive.folder("inbox", s.root)
    s._drive.folder("record_store", s.root)
    monkeypatch.setattr(inbox, "download", lambda d, fid: d.content[fid])
    return s


def drop(session, name, mime):
    session._drive.add(name, session.box, mime, (FIXTURES / name).read_bytes())


def test_ingest_then_compute_then_rerun(session):
    drop(session, "pay_statement.pdf", "application/pdf")  # real PDF through pypdf
    drop(session, "schwab_equity.csv", "text/csv")
    drop(session, "accounts_seed.csv", "text/csv")

    first = engine.ingest_inbox(session, session.root, run_id="run-1")
    assert first["counts"] == {"ingested": 3}
    assert {"backup", "parse", "write ledger"} <= set(first["seconds"])
    ledger = session.sheets.files["ledger"]
    assert len(rows(ledger, "paystubs")) == 18 and len(rows(ledger, "source_documents")) == 3
    backups = [f["name"] for f in session._drive.items.values()]
    assert "run-1" in backups and "manifest.json" in backups

    legacy = engine.ingest_legacy_year(session, session.root, "legacy-key", 2001, run_id="run-1b")
    assert legacy["appended"] == legacy["read"] == 6
    computed = engine.compute_all(session, session.root, "legacy-key", legacy_year=2001,
                                  run_id="run-2")
    assert computed["mismatches"] == 0 and computed["possible_duplicates"] == 0

    actuals = explore.monthly_actuals(session, session.root)
    assert actuals["spend"].dtype == float and len(actuals) == computed["rows"]

    again = engine.ingest_inbox(session, session.root, run_id="run-3")
    assert again["files"] == [] and again["counts"] == {}
    assert len(rows(ledger, "paystubs")) == 18
