"""The engine end to end on fakes: ingest the inbox, compute Reports, run again."""

import csv
from pathlib import Path

import gspread
import pandas as pd
import pytest
from freezegun import freeze_time

from household import engine, explore, inbox, logs, schema, sheets
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
        legacy.tabs = {
            "2001-data": FakeWorksheet("2001-data", legacy_rows("legacy_data.csv")),
            "2001": FakeWorksheet("2001", legacy_rows("legacy_rollup.csv")),
        }
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
    computed = engine.compute_all(session, session.root, "legacy-key", legacy_year=2001, run_id="run-2")
    assert computed["mismatches"] == 0 and computed["possible_duplicates"] == 0
    assert computed["tax_check_mismatches"] == 0
    assert computed["withholding_unexplained"] == 0  # the synthetic stub is from 2001: no tables
    assert set(computed["categories"]) == {"since", "decided", "waiting", "open_lines"}  # for the status page

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
    ledger.worksheet("transactions").append_rows(
        [
            ["b1", "2025-08-01", "card", -12.5, "QUILL & INK", "", "FALSE", "", "ofx", "ledger", "", "t"],
            ["b2", "2025-09-01", "card", -14.0, "QUILL & INK #2", "", "FALSE", "", "ofx", "ledger", "", "t"],
        ]
    )

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


def test_answers_written_before_the_review_tab_failed_to_rewrite_are_not_written_twice(session, monkeypatch):
    engine.ingest_legacy_year(session, session.root, "legacy-key", 2001, run_id="run-1")
    ledger = session.sheets.files["ledger"]
    ledger.worksheet("categories").append_rows([["food", "food", "high"], ["household", "household", "medium"]])
    ledger.worksheet("transactions").append_rows(
        [["b1", "2025-08-01", "card", -12.5, "QUILL & INK", "", "FALSE", "", "ofx", "ledger", "", "t"]]
    )
    engine.review(session, session.root, log=None)
    tab = ledger.worksheet("review").rows
    tab[1][tab[0].index("answer")] = "household"
    real = sheets.rewrite_review

    def fails(*a, **k):
        raise RuntimeError("503: the service is unavailable")

    monkeypatch.setattr(sheets, "rewrite_review", fails)
    with pytest.raises(RuntimeError):
        engine.review(session, session.root, log=None, now="2026-11-01T00:00:00")
    assert len(rows(ledger, "proposal_decisions")) == 1  # written; the tab still holds the answer
    monkeypatch.setattr(sheets, "rewrite_review", real)
    again = engine.review(session, session.root, log=None, now="2026-11-01T00:05:00")
    assert again["questions"] == 0
    assert rows(ledger, "proposal_decisions")[["proposal_key", "note"]].values.tolist() == [
        ["payee:QUILL & INK", "household"]
    ]


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

    for name, mime in (
        ("pay_statement.pdf", "application/pdf"),
        ("schwab_equity.csv", "text/csv"),
        ("accounts_seed.csv", "text/csv"),
        ("return_facts.csv", "text/csv"),
    ):
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
    # the weekly email's list and the FSA balances are there for the weekly script, with their columns
    for name in ("nudges", "fsa_balances"):
        table = sheets.read_table(reports, schema.table(schema.REPORTS, name))
        assert list(table.columns) == list(schema.table(schema.REPORTS, name).columns)
    assert summary.get("inbox") is None  # the ingest stage, not compute, says where the inbox is
    assert json.loads(runs.loc[runs["stage"] == "ingest", "detail"].item())["inbox"]
    ingest = json.loads(runs.loc[runs["stage"] == "ingest", "detail"].item())
    assert all(set(f) == {"file", "status", "reason", "appended", "written"} for f in ingest["files"])
    written = sheets.read_table(reports, schema.table(schema.REPORTS, "changes_written"))
    assert list(written.columns) == ["file_id", "file", "written_at"]  # no change sets in this inbox: empty, but there
    notes = sheets.read_table(reports, schema.table(schema.REPORTS, "run_notes"))
    assert set(notes["run_id"]) == {"run-2", "run-3"}
    compute_lines = notes[notes["stage"] == "compute"]["message"].tolist()
    assert compute_lines[0] == "run run-2: reading the ledger"
    assert any(line.startswith("transfers:") for line in compute_lines)


def test_compute_lists_what_its_checks_found_for_the_status_page(session, monkeypatch):
    import json

    import household
    from household import checks, valuation

    for name, mime in (("pay_statement.pdf", "application/pdf"), ("accounts_seed.csv", "text/csv")):
        drop(session, name, mime)
    engine.ingest_inbox(session, session.root, run_id="run-1", log=None)
    unpaired = pd.DataFrame(
        [
            [
                "2001-03-02",
                "sam",
                "Acme Widgets",
                90.0,
                "t9",
                "checking",
                "ACME WIDGETS PAYROLL",
                "missing stub: did you forget it?",
            ],
            ["2001-02-16", "alex", "Acme Widgets", 80.0, "", "", "", "no deposit"],
        ],
        columns=checks.PAYCHECK_COLUMNS,
    )
    monkeypatch.setattr(checks, "paychecks", lambda stubs, categorized: unpaired)
    funds = [{"symbol": "PAGEX", "accounts": ["brokerage", "ira"], "value": 2000.0}]
    monkeypatch.setattr(valuation, "unclassified", lambda values: funds)
    result = engine.compute_all(session, session.root, run_id="run-2", log=None)
    assert (result["stubs_missing"], result["pay_not_deposited"], result["funds_unclassified"]) == (1, 1, 1)
    runs = sheets.read_table(session.sheets.files[sheets.REPORTS_TITLE], schema.table(schema.REPORTS, "run_log"))
    summary = json.loads(runs.loc[runs["stage"] == "compute", "detail"].item())
    # each thing as the Status page shows it: what it is, what about it, and where it opens
    assert summary["things"] == {
        "stubs_missing": [
            {"title": "sam, Acme Widgets", "date": "2001-03-02", "detail": "paid 90.00 into checking", "link": "txn:t9"}
        ],
        "pay_not_deposited": [
            {
                "title": "alex, Acme Widgets",
                "date": "2001-02-16",
                "detail": "net pay of 80.00 never arrived in an account",
                "link": "",
            }
        ],
        "funds_unclassified": [{"title": "PAGEX", "detail": "2,000 in brokerage, ira", "link": ""}],
    }
    # and the release that made these reports, from each stage
    assert summary["version"] == household.__version__
    assert json.loads(runs.loc[runs["stage"] == "ingest", "detail"].item())["version"] == household.__version__
    # a check that found nothing lists nothing
    monkeypatch.setattr(checks, "paychecks", lambda stubs, categorized: unpaired.iloc[:0])
    monkeypatch.setattr(valuation, "unclassified", lambda values: [])
    assert engine.compute_all(session, session.root, run_id="run-3", log=None)["things"] == {}


def test_the_lines_kept_for_the_status_page_are_exactly_the_lines_printed(session):
    for name, mime in (
        ("pay_statement.pdf", "application/pdf"),
        ("schwab_equity.csv", "text/csv"),
        ("accounts_seed.csv", "text/csv"),
        ("return_facts.csv", "text/csv"),
    ):
        drop(session, name, mime)
    printed = {"ingest": [], "compute": []}
    engine.ingest_inbox(session, session.root, run_id="run-1", log=printed["ingest"].append)
    engine.ingest_legacy_year(session, session.root, "legacy-key", 2001, run_id="run-1b")
    engine.compute_all(session, session.root, run_id="run-2", log=printed["compute"].append)
    notes = sheets.read_table(session.sheets.files[sheets.REPORTS_TITLE], schema.table(schema.REPORTS, "run_notes"))
    for stage, lines in printed.items():
        kept = notes[notes["stage"] == stage].sort_values("n", key=lambda n: n.astype(int))["message"].tolist()
        assert kept[: len(lines)] == [str(line) for line in lines]  # ingest adds the parsers' notes after
    assert len(notes[notes["stage"] == "compute"]) == len(printed["compute"])


def test_a_change_set_from_the_views_reaches_the_ledger_and_the_editor_tables(session):
    import json

    seed = "category,group,priority\nfood,living,high\nkids,family,high\n"
    session._drive.add("categories.csv", session.box, "text/csv", seed.encode())
    engine.ingest_inbox(session, session.root, run_id="run-1", log=None)
    edits = session._drive.folder("web_edits", session.box)
    change = {
        "format": "household_changes",
        "version": 1,
        "created_at": "2026-01-31T12:00:00Z",
        "base_run": "run-1",
        "edits": [
            {
                "kind": "budget_line",
                "line": "groceries",
                "effective": "2026-01-01",
                "rule": "fixed",
                "category": "food",
                "amount": "50",
                "per": "weekly",
                "was": None,
            },
            {
                "kind": "category",
                "category": "food",
                "group": "groceries",
                "priority": "high",
                "was": {"group": "living", "priority": "high"},
            },
            {"kind": "parameter", "key": "transfer_account", "value": "checking", "was": None},
        ],
    }
    session._drive.add("changes_2026-01-31T120000Z.json", edits, "application/json", json.dumps(change).encode())
    lines = []
    ingested = engine.ingest_inbox(session, session.root, run_id="run-2", log=lines.append)
    assert ingested["counts"] == {"ingested": 1}
    assert "budget line groceries: fixed 50 weekly for food from 2026-01-01" in "\n".join(lines)

    engine.compute_all(session, session.root, run_id="run-3", log=None)
    reports = session.sheets.files["reports"]
    read = lambda name: sheets.read_table(reports, schema.table(schema.REPORTS, name))
    line = read("editor_budget_lines").iloc[0]
    assert (line["line"], line["in_words"], line["in_force"]) == ("groceries", "50.00 a week", "yes")
    assert read("editor_categories").set_index("category").at["food", "group"] == "groceries"
    settings_now = read("editor_parameters")
    assert settings_now[settings_now["value"] != ""][["key", "value"]].values.tolist() == [
        ["transfer_account", "checking"]
    ]


def test_change_sets_written_come_from_the_ledger_and_reach_back_further_than_a_discard_is_remembered():
    import pandas as pd
    from datetime import datetime, timezone

    sources = pd.DataFrame(
        [
            {
                "kind": "changes",
                "original_name": "changes_2026-01-31T120000.000Z_e5.json",
                "drive_link": "https://drive.example/file/d/e5",
                "received_at": "2026-01-31T12:10:00+00:00",
            },
            {
                "kind": "changes",
                "original_name": "changes_2025-11-01T120000.000Z_a1.json",
                "drive_link": "https://drive.example/file/d/a1",
                "received_at": "2025-11-01T12:10:00+00:00",
            },
            {
                "kind": "paystub",
                "original_name": "pay_statement.pdf",
                "drive_link": "https://drive.example/file/d/p1",
                "received_at": "2026-01-31T12:10:00+00:00",
            },
        ]
    )
    out = engine.changes_written(sources, now=datetime(2026, 2, 1, tzinfo=timezone.utc))
    assert out.values.tolist() == [["e5", "changes_2026-01-31T120000.000Z_e5.json", "2026-01-31T12:10:00+00:00"]]
    assert engine.CHANGES_WRITTEN_DAYS > 30
    assert engine.changes_written(sources.iloc[0:0]).empty


def test_review_answers_the_ledger_already_holds_are_not_written_again():
    # the answers were written, then rewriting the review tab failed: the next run reads them again
    import pandas as pd
    from household.engine import _not_yet

    held = pd.DataFrame(
        [
            {
                "proposal_key": "payee:ACME GROCER",
                "kind": "payee",
                "decided_at": "t1",
                "decision": "approve",
                "note": "food",
            },
            {
                "proposal_key": "payee:WILLOW ST",
                "kind": "payee",
                "decided_at": "t1",
                "decision": "approve",
                "note": "gifts",
            },
            {
                "proposal_key": "payee:WILLOW ST",
                "kind": "payee",
                "decided_at": "t2",
                "decision": "approve",
                "note": "kids",
            },
        ]
    )
    again = pd.DataFrame(
        [
            {
                "proposal_key": "payee:ACME GROCER",
                "kind": "payee",
                "decided_at": "t3",
                "decision": "approve",
                "note": "food",
            },
            {
                "proposal_key": "payee:WILLOW ST",
                "kind": "payee",
                "decided_at": "t3",
                "decision": "approve",
                "note": "gifts",
            },
            {
                "proposal_key": "payee:QUILL & INK",
                "kind": "payee",
                "decided_at": "t3",
                "decision": "approve",
                "note": "hobby",
            },
        ]
    )
    out = _not_yet(again, held, ["proposal_key"], ["kind", "decision", "note"])
    # the same as the latest is skipped; back to an earlier answer, or a new payee, is written
    assert out["proposal_key"].tolist() == ["payee:WILLOW ST", "payee:QUILL & INK"]
    assert _not_yet(again, held.iloc[0:0], ["proposal_key"], ["kind", "decision", "note"]).equals(again)


# --- one run: the spreadsheets opened once, each tab read once, Reports written together ----------------------

FILES = (
    ("pay_statement.pdf", "application/pdf"),
    ("schwab_equity.csv", "text/csv"),
    ("accounts_seed.csv", "text/csv"),
    ("return_facts.csv", "text/csv"),
)


def reports_of(session):
    """Every Reports table but the run's own bookkeeping, as text that leaves out what differs run to run."""
    from test_golden import BOOKKEEPING, digest

    return {
        name: digest(pd.DataFrame(tab.rows[1:], columns=tab.rows[0]) if tab.rows else pd.DataFrame())
        for name, tab in session.sheets.files["reports"].tabs.items()
        if name not in BOOKKEEPING
    }


@freeze_time("2026-11-09 12:00:00")
def test_a_whole_run_asks_sheets_for_little_and_makes_what_the_stages_make_one_by_one(monkeypatch):
    from household import runner
    from test_sheets import CALLS

    monkeypatch.setattr(inbox, "download", lambda d, fid: d.content[fid])

    def fresh():
        s = FakeSession()
        s.root = s._drive.folder("household_finances")
        s.box = s._drive.folder("inbox", s.root)
        s._drive.folder("record_store", s.root)
        for name, mime in FILES:
            drop(s, name, mime)
        return s

    together, apart = fresh(), fresh()
    CALLS.clear()
    runner.run(together, together.root, log=None)
    with_files = sum(CALLS.values())
    CALLS.clear()
    again = runner.run(together, together.root, log=None)
    nothing_new = sum(CALLS.values())
    # 0.65.0 made about 500 calls for the same run: several for every table read and written
    assert with_files <= 45 and nothing_new <= 30, (with_files, nothing_new)
    assert CALLS["worksheet"] == 0  # no listing of the tabs per lookup
    assert again["ingest"]["sheets_calls"] + again["compute"]["sheets_calls"] <= nothing_new

    for _ in range(2):
        engine.ingest_inbox(apart, apart.root, log=None)
        engine.fetch_prices(apart, apart.root, log=None)
        engine.compute_all(apart, apart.root, log=None)
    assert reports_of(together) == reports_of(apart)
    ledgers = [
        {n: [[str(c) for c in r] for r in t.rows] for n, t in s.sheets.files["ledger"].tabs.items()}
        for s in (together, apart)
    ]
    for name in ledgers[0]:
        if name not in ("source_documents", "paystubs", "equity_vests", "equity_sales", "return_facts", "accounts"):
            assert ledgers[0][name] == ledgers[1][name], name  # the rest carry no ids of the files they came from
        assert len(ledgers[0][name]) == len(ledgers[1][name]), name


def test_a_run_trains_the_model_once_and_stages_run_alone_each_train_it(session, monkeypatch):
    from household import books, runner

    trained = []
    real = books.categorize_books
    monkeypatch.setattr(books, "categorize_books", lambda b: trained.append(1) or real(b))
    for name, mime in FILES:
        drop(session, name, mime)
    runner.run(session, session.root, log=None)
    assert len(trained) == 1  # ingest's review; compute was given its categories
    engine.ingest_legacy_year(session, session.root, "legacy-key", 2001, run_id="run-1b")  # the books change
    engine.ingest_inbox(session, session.root, log=None)
    engine.compute_all(session, session.root, log=None)
    assert len(trained) == 3  # each stage by itself reads and works out its own


def test_the_review_is_rebuilt_only_when_something_it_rests_on_changed(session):
    from test_sheets import CALLS

    engine.ingest_legacy_year(session, session.root, "legacy-key", 2001, run_id="run-1")
    ledger = session.sheets.files["ledger"]
    ledger.worksheet("categories").append_rows([["food", "food", "high"], ["household", "household", "medium"]])
    ledger.worksheet("transactions").append_rows(
        [["b1", "2025-08-01", "card", -12.5, "QUILL & INK", "", "FALSE", "", "ofx", "ledger", "", "t"]]
    )
    first = engine.review(session, session.root, log=None)
    assert first["questions"] == 1 and "kept" not in first
    said = []
    CALLS.clear()
    second = engine.review(session, session.root, log=said.append)
    assert second == {"answered": 0, "unusable": 0, "questions": 1, "open_lines": 1, "kept": True}
    assert CALLS["clear"] == CALLS["update"] == 0  # the tab was left as it is
    assert "nothing its questions rest on has changed" in said[-1] and "1 questions open (1 lines)" in said[-1]

    ledger.worksheet("transactions").append_rows(
        [["b2", "2025-09-01", "card", -14.0, "PAGE TURNER BOOKS", "", "FALSE", "", "ofx", "ledger", "", "t"]]
    )
    assert engine.review(session, session.root, log=None)["questions"] == 2  # a new line: rebuilt
    tab = ledger.worksheet("review").rows
    del tab[2]  # someone removed a question by hand: the tab is not what was written
    rebuilt = engine.review(session, session.root, log=None)
    assert rebuilt["questions"] == 2 and "kept" not in rebuilt and len(ledger.worksheet("review").rows) == 3
    tab = ledger.worksheet("review").rows
    tab[1][tab[0].index("answer")] = "household"
    answered = engine.review(session, session.root, log=None, now="2026-11-01T00:00:00")
    assert (answered["answered"], answered["questions"]) == (1, 1) and "kept" not in answered
    assert engine.review(session, session.root, log=None)["kept"]


def test_a_compute_that_fails_leaves_reports_as_the_last_run_left_it(session, monkeypatch):
    from household import stage_compute

    for name, mime in FILES:
        drop(session, name, mime)
    engine.ingest_inbox(session, session.root, run_id="run-1", log=None)
    engine.compute_all(session, session.root, run_id="run-2", log=None)
    reports = session.sheets.files["reports"]
    before = {n: [list(r) for r in t.rows] for n, t in reports.tabs.items()}

    def broken(*args, **kwargs):
        raise RuntimeError("no nudges today")

    monkeypatch.setattr(stage_compute, "_nudges", broken)  # near the end, after most tables are worked out
    with pytest.raises(RuntimeError, match="no nudges today"):
        engine.compute_all(session, session.root, run_id="run-3", log=None)
    assert {n: [list(r) for r in t.rows] for n, t in reports.tabs.items()} == before


def test_compute_says_where_its_time_went(session):
    for name, mime in FILES:
        drop(session, name, mime)
    engine.ingest_inbox(session, session.root, run_id="run-1", log=None)
    result = engine.compute_all(session, session.root, run_id="run-2", log=None)
    assert {"read the ledger", "books and categories", "categories", "budget", "retirement"} <= set(result["seconds"])
    assert "equity" not in result["seconds"]  # a part of lots, counted there
    assert result["sheets_calls"] <= 15


def test_the_retirement_simulation_is_kept_while_nothing_it_rests_on_changes(session, monkeypatch):
    from household import stage_compute
    from household.ledgerio import LedgerReading, LedgerView
    from test_retirement import household, params

    ledger, reports, _ = engine.bootstrap(session, session.root)
    h = {"now": household()}
    ran = []

    def build(*args, **kwargs):
        return h["now"], [logs.warn("retirement: a note from the inputs")], pd.DataFrame()

    def columns(name):
        return [c for c in schema.table(schema.REPORTS, "retirement_" + name).columns if c != "run_id"]

    def run(given, p, today):
        ran.append(given.taxable)
        years = pd.DataFrame([{c: "" for c in columns("years")} | {"retire_year": 2040, "chosen": "yes"}])
        table = pd.DataFrame([{c: "" for c in columns("strategies")}] * 2)
        plan = pd.DataFrame([{c: "" for c in columns("plan")}])
        none = pd.DataFrame(columns=columns("social_security")), pd.DataFrame(columns=columns("earnings"))
        return years, table, plan, *none, ["retirement: 2040 with a strategy: 93% never run short"]

    monkeypatch.setattr(stage_compute.retirement, "build", build)
    monkeypatch.setattr(stage_compute.retirement, "run", run)
    monkeypatch.setattr(
        stage_compute.retirement, "budget_table", lambda given, year: pd.DataFrame(columns=columns("budget"))
    )
    books = {"parameters": params(), "accounts": pd.DataFrame(), "categories": pd.DataFrame()}

    def compute(run_id):
        said = []
        c = stage_compute.Compute(
            session, LedgerView(ledger, LedgerReading()), reports, run_id, "2026-11-09T12:00:00+00:00", said.append
        )
        with sheets.batched(reports):
            found = engine._retirement(c, books, pd.DataFrame(), pd.DataFrame())
        return found, [str(line) for line in said]

    raw = session.sheets.files["reports"]
    found, said = compute("run-1")
    assert found == {"retirement_year": 2040} and len(ran) == 1 and "modeled in" in said[-1]
    assert raw.tabs["retirement_years"].rows[1][-1] == "run-1"

    found, said = compute("run-2")  # the same household: the simulation is not run again
    assert found == {"retirement_year": 2040} and len(ran) == 1
    assert said[:2] == ["retirement: a note from the inputs", "retirement: 2040 with a strategy: 93% never run short"]
    assert "nothing the plan rests on has changed since run run-1" in said[-1] and "1 strategies" in said[-1]
    assert raw.tabs["retirement_years"].rows[1][-1] == "run-1"  # its tables stand as that run wrote them
    assert raw.tabs["retirement_assumptions"].rows[0][-1] == "run_id"  # the cheap tables are written every run

    h["now"] = household(budget={"base": {"food": 1.0}})  # only the budget's breakdown: the plan is the same
    assert compute("run-3")[0] == {"retirement_year": 2040} and len(ran) == 1
    h["now"] = household(taxable=500_000.0)  # more in the taxable accounts: modeled again
    found, said = compute("run-4")
    assert len(ran) == 2 and "modeled in" in said[-1] and raw.tabs["retirement_years"].rows[1][-1] == "run-4"
    raw.tabs["retirement_plan"].rows = raw.tabs["retirement_plan"].rows[:1]  # a tab cleared since
    compute("run-5")
    assert len(ran) == 3 and len(raw.tabs["retirement_plan"].rows) == 2
