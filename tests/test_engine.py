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
    drop(session, "return_facts.csv", "text/csv")

    first = engine.ingest_inbox(session, session.root, run_id="run-1")
    assert first["counts"] == {"ingested": 4}
    assert {"backup", "parse", "write ledger"} <= set(first["seconds"])
    ledger = session.sheets.files["ledger"]
    assert len(rows(ledger, "paystubs")) == 18 and len(rows(ledger, "source_documents")) == 4
    backups = [f["name"] for f in session._drive.items.values()]
    assert "ledger_run-1.zip" in backups

    legacy = engine.ingest_legacy_year(session, session.root, "legacy-key", 2001, run_id="run-1b")
    assert legacy["appended"] == legacy["read"] == 6
    computed = engine.compute_all(session, session.root, "legacy-key", legacy_year=2001,
                                  run_id="run-2")
    assert computed["mismatches"] == 0 and computed["possible_duplicates"] == 0
    assert computed["tax_check_mismatches"] == 0
    assert computed["withholding_unexplained"] == 0  # the synthetic stub is from 2001: no tables

    actuals = explore.monthly_actuals(session, session.root)
    assert actuals["spend"].dtype == float and len(actuals) == computed["rows"]

    again = engine.ingest_inbox(session, session.root, run_id="run-3")
    assert again["files"] == [] and again["counts"] == {}
    assert len(rows(ledger, "paystubs")) == 18


def test_progress_is_logged_line_by_line(session):
    drop(session, "pay_statement.pdf", "application/pdf")
    session._drive.add("notes.txt", session.box, "text/plain", b"nothing")
    lines = []
    engine.ingest_inbox(session, session.root, run_id="run-1", log=lines.append)
    text = "\n".join(lines)
    assert "backup:" in text and "inbox: 2 files" in text
    assert any(l.startswith("[1/2] ") or l.startswith("[2/2] ") for l in lines)
    assert "unrecognized" in text and "writing to ledger: 18 rows to paystubs" in text
    assert "filed (" in text
    quiet = []
    engine.ingest_inbox(session, session.root, run_id="run-2", log=None)
    assert quiet == []


def test_review_reads_answers_and_rewrites_the_queue(session):
    engine.ingest_legacy_year(session, session.root, "legacy-key", 2001, run_id="run-1")
    ledger = session.sheets.files["ledger"]
    ledger.worksheet("categories").append_rows([["food", "food", "high"], ["household", "household", "medium"]])
    ledger.worksheet("transactions").append_rows([
        ["b1", "2025-08-01", "card", -12.5, "QUILL & INK", "", "FALSE", "", "ofx", "ledger", "", "t"],
        ["b2", "2025-09-01", "card", -14.0, "QUILL & INK #2", "", "FALSE", "", "ofx", "ledger", "", "t"]])

    first = engine.review(session, session.root, log=None)
    queue = rows(ledger, "review")
    assert (first["questions"], first["open_lines"]) == (1, 2)
    assert queue.loc[0, "key"] == "QUILL & INK"

    tab = ledger.worksheet("review").rows
    tab[1][tab[0].index("answer")] = "household"
    second = engine.review(session, session.root, log=None, now="2026-11-01T00:00:00")
    assert (second["answered"], second["questions"]) == (1, 0)
    made = rows(ledger, "proposal_decisions")
    assert made[["proposal_key", "note"]].values.tolist() == [["payee:QUILL & INK", "household"]]
    assert rows(ledger, "review").empty


def test_compute_writes_the_mortgage_schedule_when_a_loan_is_on_file(session):
    engine.ingest_legacy_year(session, session.root, "legacy-key", 2001, run_id="run-1")
    ledger = session.sheets.files["ledger"]
    ledger.worksheet("loans").append_rows([["home", "Lender", "2000-12-15", "1000", "12", "12", ""]])
    said = []
    engine.compute_all(session, session.root, run_id="run-2", log=said.append)
    sched = session.sheets.files["reports"].worksheet("mortgage").get_values()
    assert sched[0][:3] == ["month", "loan_id", "payment"] and len(sched) > 1
    assert any(line.startswith("mortgage: home balance") for line in said)


def test_each_stage_keeps_its_latest_run_in_reports_for_the_status_page(session):
    import json
    for name, mime in (("pay_statement.pdf", "application/pdf"), ("schwab_equity.csv", "text/csv"),
                       ("accounts_seed.csv", "text/csv"), ("return_facts.csv", "text/csv")):
        drop(session, name, mime)
    engine.ingest_inbox(session, session.root, run_id="run-1", log=None)
    engine.ingest_legacy_year(session, session.root, "legacy-key", 2001, run_id="run-1b")
    engine.compute_all(session, session.root, run_id="run-2", log=None)
    engine.ingest_inbox(session, session.root, run_id="run-3", log=None)  # replaces only the ingest stage
    reports = session.sheets.files[sheets.REPORTS_TITLE]
    runs = sheets.read_table(reports, schema.table(schema.REPORTS, "run_log"))
    assert dict(zip(runs["stage"], runs["run_id"])) == {"compute": "run-2", "ingest": "run-3"}
    summary = json.loads(runs.loc[runs["stage"] == "compute", "detail"].item())
    assert "review_open_lines" in summary and "versions" in summary
    notes = sheets.read_table(reports, schema.table(schema.REPORTS, "run_notes"))
    assert set(notes["run_id"]) == {"run-2", "run-3"}
    compute_lines = notes[notes["stage"] == "compute"]["message"].tolist()
    assert compute_lines[0] == "run run-2: reading the ledger"
    assert any(line.startswith("transfers:") for line in compute_lines)


def test_the_lines_kept_for_the_status_page_are_exactly_the_lines_printed(session):
    for name, mime in (("pay_statement.pdf", "application/pdf"), ("schwab_equity.csv", "text/csv"),
                       ("accounts_seed.csv", "text/csv"), ("return_facts.csv", "text/csv")):
        drop(session, name, mime)
    printed = {"ingest": [], "compute": []}
    engine.ingest_inbox(session, session.root, run_id="run-1", log=printed["ingest"].append)
    engine.ingest_legacy_year(session, session.root, "legacy-key", 2001, run_id="run-1b")
    engine.compute_all(session, session.root, run_id="run-2", log=printed["compute"].append)
    notes = sheets.read_table(session.sheets.files[sheets.REPORTS_TITLE], schema.table(schema.REPORTS, "run_notes"))
    for stage, lines in printed.items():
        kept = notes[notes["stage"] == stage].sort_values("n", key=lambda n: n.astype(int))["message"].tolist()
        assert kept[:len(lines)] == [str(line) for line in lines]  # ingest adds the parsers' notes after
    assert len(notes[notes["stage"] == "compute"]) == len(printed["compute"])
